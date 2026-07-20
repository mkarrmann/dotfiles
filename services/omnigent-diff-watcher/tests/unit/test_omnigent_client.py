from __future__ import annotations

import json

import httpx
import pytest

from omnigent_diff_watcher.domain import EventDeliveryStatus
from omnigent_diff_watcher.omnigent_client import (
    OmnigentClient,
    OmnigentDeliveryService,
    desired_watch,
)


def _client(handler: httpx.MockTransport) -> tuple[OmnigentClient, httpx.AsyncClient]:
    raw = httpx.AsyncClient(transport=handler, base_url="http://server")
    return OmnigentClient("http://unused", client=raw), raw


async def test_lists_every_session_page() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        after = request.url.params.get("after")
        if after is None:
            return httpx.Response(
                200,
                json={
                    "data": [{"id": "conv_one", "labels": {}}],
                    "has_more": True,
                    "last_id": "conv_one",
                },
            )
        return httpx.Response(
            200,
            json={"data": [{"id": "conv_two", "labels": {}}], "has_more": False},
        )

    client, raw = _client(httpx.MockTransport(handler))
    try:
        assert [item["id"] for item in await client.list_sessions()] == [
            "conv_one",
            "conv_two",
        ]
    finally:
        await raw.aclose()


@pytest.mark.parametrize(
    ("updates", "reachable", "can_accept"),
    [
        ({"status": "idle", "runner_online": True}, True, True),
        ({"status": "running", "runner_online": True}, True, False),
        ({"status": "idle", "runner_online": False, "host_online": True}, True, True),
        ({"status": "failed", "runner_online": False, "host_online": False}, False, False),
        ({"status": "idle", "runner_online": True, "pending_inputs": [{}]}, True, False),
    ],
)
async def test_projects_session_lifecycle(
    updates: dict[str, object], reachable: bool, can_accept: bool
) -> None:
    payload: dict[str, object] = {
        "id": "conv_test",
        "status": "idle",
        "labels": {},
        "runner_id": "runner",
        "host_id": "host",
        **updates,
    }
    client, raw = _client(httpx.MockTransport(lambda _request: httpx.Response(200, json=payload)))
    try:
        snapshot = await client.get("conv_test")
        assert snapshot.reachable is reachable
        assert snapshot.can_accept_input is can_accept
    finally:
        await raw.aclose()


async def test_delivery_deduplicates_a_persisted_batch_marker() -> None:
    posted = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal posted
        if request.url.path.endswith("/items"):
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "type": "message",
                            "role": "user",
                            "content": [
                                {"type": "input_text", "text": "[Diff watcher dwb_1] update"}
                            ],
                        }
                    ]
                },
            )
        if request.method == "POST":
            posted = True
        return httpx.Response(
            200,
            json={
                "id": "conv_test",
                "status": "idle",
                "labels": {},
                "runner_id": "runner",
                "runner_online": True,
            },
        )

    client, raw = _client(httpx.MockTransport(handler))
    try:
        delivery = OmnigentDeliveryService(client, mode="enabled", allowlist=frozenset())
        result = await delivery.deliver_message("conv_test", "dwb_1", "[Diff watcher dwb_1] update")
        assert result.status is EventDeliveryStatus.ALREADY_ACCEPTED
        assert posted is False
    finally:
        await raw.aclose()


async def test_delivery_posts_the_existing_hidden_event_shape_once() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/items"):
            return httpx.Response(200, json={"data": []})
        if request.method == "POST":
            return httpx.Response(202, json={"accepted": True})
        return httpx.Response(
            200,
            json={
                "id": "conv_test",
                "status": "idle",
                "labels": {},
                "runner_id": "runner",
                "runner_online": True,
            },
        )

    client, raw = _client(httpx.MockTransport(handler))
    try:
        delivery = OmnigentDeliveryService(client, mode="enabled", allowlist=frozenset())
        result = await delivery.deliver_message("conv_test", "dwb_2", "[Diff watcher dwb_2] update")
        assert result.status is EventDeliveryStatus.ACCEPTED
        post = next(request for request in requests if request.method == "POST")
        assert post.url.path == "/v1/sessions/conv_test/events"
        assert json.loads(post.content) == {
            "type": "message",
            "data": {
                "role": "user",
                "content": [{"type": "input_text", "text": "[Diff watcher dwb_2] update"}],
            },
        }
    finally:
        await raw.aclose()


def test_desired_watch_requires_a_diff_and_valid_preferences() -> None:
    assert desired_watch({"labels": {"omnigent.diff.number": "D1"}}) is None
    assert desired_watch(
        {
            "labels": {
                "omnigent.diff.number": "D1",
                "omnigent.diff.watch": "ci_failure,review_comment",
            }
        }
    ) == ("D1", frozenset({"ci_failure", "review_comment"}))
    assert (
        desired_watch(
            {
                "labels": {
                    "omnigent.diff.number": "D1",
                    "omnigent.diff.watch": "unknown",
                }
            }
        )
        is None
    )
