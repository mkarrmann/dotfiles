from __future__ import annotations

import json

import httpx
import pytest

from omnigent_watcher.domain import EventDeliveryStatus
from omnigent_watcher.omnigent_client import (
    OmnigentClient,
    OmnigentDeliveryService,
)


def _client(handler: httpx.MockTransport) -> tuple[OmnigentClient, httpx.AsyncClient]:
    raw = httpx.AsyncClient(transport=handler, base_url="http://server")
    return OmnigentClient("http://unused", client=raw), raw


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
                            "content": [{"type": "input_text", "text": "[Watcher dwb_1] update"}],
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
        result = await delivery.deliver_message("conv_test", "dwb_1", "[Watcher dwb_1] update")
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
        result = await delivery.deliver_message("conv_test", "dwb_2", "[Watcher dwb_2] update")
        assert result.status is EventDeliveryStatus.ACCEPTED
        post = next(request for request in requests if request.method == "POST")
        assert post.url.path == "/v1/sessions/conv_test/events"
        assert json.loads(post.content) == {
            "type": "message",
            "data": {
                "role": "user",
                "content": [{"type": "input_text", "text": "[Watcher dwb_2] update"}],
            },
        }
    finally:
        await raw.aclose()


@pytest.mark.parametrize("reason", ["allowlist", "busy", "preflight_error", "malformed", "denied"])
async def test_definite_non_delivery_is_distinct_from_uncertain_acceptance(reason: str) -> None:
    posts: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            posts.append(request)
            return httpx.Response(202, json={"queued": False, "denied": True})
        if reason == "preflight_error":
            raise httpx.ConnectError("offline", request=request)
        if request.url.path.endswith("/items"):
            return httpx.Response(200, json={} if reason == "malformed" else {"data": []})
        return httpx.Response(
            200, json={"status": "running" if reason == "busy" else "idle", "labels": {}}
        )

    client, raw = _client(httpx.MockTransport(handler))
    try:
        delivery = OmnigentDeliveryService(
            client,
            mode="enabled",
            allowlist=frozenset({"another-session"}) if reason == "allowlist" else frozenset(),
        )
        result = await delivery.deliver_message("conv_test", "dwb_3", "[Watcher dwb_3] update")
        assert result.status is EventDeliveryStatus.NOT_SENT
        assert len(posts) == (1 if reason == "denied" else 0)
    finally:
        await raw.aclose()


@pytest.mark.parametrize("status", [409, 423, 429, 500, 503])
async def test_post_rejection_does_not_claim_definite_non_acceptance(status: int) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(status)
        if request.url.path.endswith("/items"):
            return httpx.Response(200, json={"data": []})
        return httpx.Response(200, json={"status": "idle", "labels": {}})

    client, raw = _client(httpx.MockTransport(handler))
    try:
        delivery = OmnigentDeliveryService(client, mode="enabled", allowlist=frozenset())
        result = await delivery.deliver_message("conv_test", "dwb_4", "[Watcher dwb_4] update")
        assert result.status is EventDeliveryStatus.DEFERRED
    finally:
        await raw.aclose()


@pytest.mark.parametrize("created_at", [1760000000, None, "invalid", True])
async def test_receipt_recovers_acceptance_time_even_when_session_is_busy(
    created_at: object,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/items")
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "type": "message",
                        "role": "user",
                        "created_at": created_at,
                        "content": [{"text": "[Watcher dwb_5] update"}],
                    }
                ]
            },
        )

    client, raw = _client(httpx.MockTransport(handler))
    try:
        delivery = OmnigentDeliveryService(client, mode="enabled", allowlist=frozenset())
        result = await delivery.deliver_message("conv_test", "dwb_5", "[Watcher dwb_5] update")
        assert result.status is EventDeliveryStatus.ALREADY_ACCEPTED
        assert result.accepted_at == (1760000000.0 if type(created_at) is int else None)
    finally:
        await raw.aclose()


async def test_receipt_lookup_failure_is_not_an_absent_receipt() -> None:
    client, raw = _client(httpx.MockTransport(lambda _request: httpx.Response(503)))
    try:
        delivery = OmnigentDeliveryService(client, mode="enabled", allowlist=frozenset())
        with pytest.raises(httpx.HTTPStatusError):
            await delivery.delivery_receipt("conv_test", "dwb_6")
    finally:
        await raw.aclose()


@pytest.mark.parametrize("receipt_visible", [True, False])
async def test_transport_failure_after_post_preserves_acceptance_uncertainty(
    receipt_visible: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    posted = False

    async def no_delay(seconds: float) -> None:
        pass

    monkeypatch.setattr("omnigent_watcher.omnigent_client.asyncio.sleep", no_delay)

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal posted
        if request.method == "POST":
            posted = True
            raise httpx.ReadTimeout("response lost", request=request)
        if request.url.path.endswith("/items"):
            item = {
                "type": "message",
                "role": "user",
                "created_at": 1760000000,
                "content": [{"text": "[Watcher dwb_7] update"}],
            }
            return httpx.Response(200, json={"data": [item] if posted and receipt_visible else []})
        return httpx.Response(200, json={"status": "idle", "labels": {}})

    client, raw = _client(httpx.MockTransport(handler))
    try:
        delivery = OmnigentDeliveryService(client, mode="enabled", allowlist=frozenset())
        result = await delivery.deliver_message("conv_test", "dwb_7", "[Watcher dwb_7] update")
        assert posted
        assert result.status is (
            EventDeliveryStatus.ALREADY_ACCEPTED
            if receipt_visible
            else EventDeliveryStatus.DEFERRED
        )
        assert result.accepted_at == (1760000000.0 if receipt_visible else None)
    finally:
        await raw.aclose()
