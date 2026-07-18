from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable

import httpx
import pytest

from omnigent_google_chat.omnigent import (
    OmnigentAmbiguousDeliveryError,
    OmnigentClient,
    OmnigentPreDeliveryError,
    OmnigentRejectedError,
    RunnerUnavailableError,
    iter_sse_events,
)


def client(handler: Callable[[httpx.Request], httpx.Response]) -> OmnigentClient:
    http = httpx.AsyncClient(
        base_url="http://omnigent.test",
        transport=httpx.MockTransport(handler),
    )
    return OmnigentClient(
        base_url="http://omnigent.test",
        configured_host_id="host_1",
        client=http,
    )


def session_payload(session_id: str, **overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "id": session_id,
        "title": session_id,
        "status": "idle",
        "labels": {},
        "updated_at": 1,
        "host_id": "host_1",
        "workspace": "/repo",
        "runner_id": "runner_1",
        "runner_online": True,
        "host_online": True,
    }
    payload.update(overrides)
    return payload


async def test_list_sessions_follows_cursor_pagination() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.params.get("after") == "conv_1":
            return httpx.Response(
                200,
                json={
                    "data": [session_payload("conv_2")],
                    "last_id": "conv_2",
                    "has_more": False,
                },
            )
        return httpx.Response(
            200,
            json={
                "data": [session_payload("conv_1")],
                "last_id": "conv_1",
                "has_more": True,
            },
        )

    omnigent = client(handler)
    sessions = await omnigent.list_sessions()
    assert [session.id for session in sessions] == ["conv_1", "conv_2"]
    assert requests[0].url.params["include_archived"] == "true"


async def test_list_items_uses_exclusive_after_cursor() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["after"] == "item_1"
        return httpx.Response(
            200,
            json={
                "data": [{"id": "item_2", "type": "message"}],
                "last_id": "item_2",
                "has_more": False,
            },
        )

    page = await client(handler).list_items("conv", after="item_1")
    assert page.last_id == "item_2"


async def test_submit_message_uses_session_event_shape_and_returns_item_id() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        assert request.url.path == "/v1/sessions/conv/events"
        return httpx.Response(202, json={"queued": True, "item_id": "item_1"})

    item_id = await client(handler).submit_message("conv", "hello")
    assert item_id == "item_1"
    assert captured == {
        "type": "message",
        "data": {
            "role": "user",
            "content": [{"type": "input_text", "text": "hello"}],
        },
    }


@pytest.mark.parametrize(
    ("response", "error_type"),
    [
        (httpx.Response(400, json={"error": {"code": "bad"}}), OmnigentRejectedError),
        (httpx.Response(500, json={}), OmnigentAmbiguousDeliveryError),
        (
            httpx.Response(503, json={"error": {"code": "runner_unavailable"}}),
            RunnerUnavailableError,
        ),
    ],
)
async def test_submit_classifies_http_outcomes(
    response: httpx.Response, error_type: type[Exception]
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        response.request = request
        return response

    with pytest.raises(error_type):
        await client(handler).submit_message("conv", "hello")


async def test_submit_classifies_connect_as_proven_pre_delivery() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(OmnigentPreDeliveryError):
        await client(handler).submit_message("conv", "hello")


@pytest.mark.parametrize("error_type", [httpx.ConnectTimeout, httpx.PoolTimeout])
async def test_submit_classifies_pre_request_timeouts_as_proven_pre_delivery(
    error_type: type[httpx.TimeoutException],
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise error_type("not dispatched", request=request)

    with pytest.raises(OmnigentPreDeliveryError):
        await client(handler).submit_message("conv", "hello")


async def test_submit_classifies_read_timeout_as_ambiguous() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("lost", request=request)

    with pytest.raises(OmnigentAmbiguousDeliveryError):
        await client(handler).submit_message("conv", "hello")


async def test_runner_recovery_stays_on_configured_session_host() -> None:
    requests: list[tuple[str, dict[str, object] | None]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        requests.append((request.url.path, body))
        if request.url.path == "/v1/sessions/conv":
            return httpx.Response(
                200,
                json=session_payload(
                    "conv", runner_id=None, runner_online=False, workspace="/safe/workspace"
                ),
            )
        if request.url.path == "/v1/hosts/host_1/runners":
            return httpx.Response(200, json={"runner_id": "runner_new"})
        if request.url.path == "/v1/runners/runner_new/status":
            return httpx.Response(200, json={"online": True})
        raise AssertionError(request.url.path)

    runner = await client(handler).recover_bound_runner("conv")
    assert runner == "runner_new"
    assert requests[1] == (
        "/v1/hosts/host_1/runners",
        {"session_id": "conv", "workspace": "/safe/workspace"},
    )


async def test_runner_recovery_rejects_other_host() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=session_payload("conv", host_id="host_other"))

    with pytest.raises(OmnigentRejectedError, match="other than"):
        await client(handler).recover_bound_runner("conv")


async def test_sse_parser_handles_multiline_comments_and_done() -> None:
    async def lines() -> AsyncIterator[str]:
        for line in (
            ": heartbeat",
            "event: session.status",
            'data: {"status":',
            'data: "idle"}',
            "",
            "data: [DONE]",
            "",
        ):
            yield line

    events = [event async for event in iter_sse_events(lines())]
    assert events == [{"type": "session.status", "status": "idle"}]
