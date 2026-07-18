from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

import httpx

from omnigent_google_chat.models import ItemPage, SessionSummary


class OmnigentError(RuntimeError):
    pass


class OmnigentNotFoundError(OmnigentError):
    pass


class OmnigentPreDeliveryError(OmnigentError):
    """The connection was never established, so the event was not delivered."""


class OmnigentRejectedError(OmnigentError):
    """The server definitively rejected the event."""


class OmnigentAmbiguousDeliveryError(OmnigentError):
    """The event may have been accepted and must not be retried automatically."""


class RunnerUnavailableError(OmnigentRejectedError):
    pass


@dataclass(frozen=True, slots=True)
class OmnigentAuth:
    email: str | None = None
    header_name: str = "X-Forwarded-Email"
    session_cookie: str | None = None

    def headers(self) -> dict[str, str]:
        headers: dict[str, str] = {}
        if self.email:
            headers[self.header_name] = self.email
        if self.session_cookie:
            headers["Cookie"] = (
                self.session_cookie
                if "=" in self.session_cookie
                else f"ap_session={self.session_cookie}"
            )
        return headers


class OmnigentClient:
    def __init__(
        self,
        *,
        base_url: str,
        auth: OmnigentAuth | None = None,
        timeout_seconds: float = 30.0,
        configured_host_id: str | None,
        runner_launch_timeout_seconds: float = 60.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._timeout_seconds = timeout_seconds
        self._configured_host_id = configured_host_id
        self._runner_launch_timeout_seconds = runner_launch_timeout_seconds
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers=(auth or OmnigentAuth()).headers(),
            timeout=httpx.Timeout(timeout_seconds),
            trust_env=False,
        )
        self._logger = logging.getLogger(__name__)

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def list_sessions(self) -> list[SessionSummary]:
        sessions: list[SessionSummary] = []
        after: str | None = None
        seen_cursors: set[str] = set()
        while True:
            params: dict[str, str | int | bool] = {
                "limit": 1000,
                "order": "asc",
                "sort_by": "updated_at",
                "include_archived": True,
                "kind": "default",
            }
            if after:
                params["after"] = after
            payload = await self._get_json("/v1/sessions", params=params)
            data = payload.get("data") if isinstance(payload, dict) else None
            if not isinstance(data, list):
                raise OmnigentError("session list response has no data list")
            sessions.extend(
                SessionSummary.from_payload(item) for item in data if isinstance(item, dict)
            )
            has_more = payload.get("has_more") is True
            last_id = payload.get("last_id")
            if not has_more:
                break
            if not isinstance(last_id, str) or not last_id or last_id in seen_cursors:
                raise OmnigentError("session list returned an invalid pagination cursor")
            seen_cursors.add(last_id)
            after = last_id
        return sessions

    async def get_session(self, session_id: str) -> SessionSummary:
        payload = await self._get_json(
            f"/v1/sessions/{session_id}",
            params={"include_items": False, "include_liveness": True},
        )
        if not isinstance(payload, dict):
            raise OmnigentError("session response must be an object")
        return SessionSummary.from_payload(payload)

    async def list_items(self, session_id: str, *, after: str | None) -> ItemPage:
        params: dict[str, str | int] = {"limit": 1000, "order": "asc"}
        if after:
            params["after"] = after
        payload = await self._get_json(f"/v1/sessions/{session_id}/items", params=params)
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, list):
            raise OmnigentError("item list response has no data list")
        items = [item for item in data if isinstance(item, dict)]
        last_id = payload.get("last_id")
        if last_id is not None and not isinstance(last_id, str):
            raise OmnigentError("item list returned an invalid cursor")
        return ItemPage(
            items=items,
            last_id=last_id,
            has_more=payload.get("has_more") is True,
        )

    async def submit_message(
        self,
        session_id: str,
        text: str,
        *,
        source_message_name: str | None = None,
    ) -> str | None:
        content: dict[str, Any] = {"type": "input_text", "text": text}
        if source_message_name:
            content["source"] = {
                "type": "google_chat",
                "message_name": source_message_name,
            }
        return await self.submit_event(
            session_id,
            {
                "type": "message",
                "data": {
                    "role": "user",
                    "content": [content],
                },
            },
        )

    async def interrupt(self, session_id: str) -> None:
        await self.submit_event(session_id, {"type": "interrupt", "data": {}})

    async def submit_event(self, session_id: str, event: dict[str, Any]) -> str | None:
        try:
            response = await self._client.post(f"/v1/sessions/{session_id}/events", json=event)
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout) as exc:
            raise OmnigentPreDeliveryError("could not connect to Omnigent") from exc
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            raise OmnigentAmbiguousDeliveryError(
                "Omnigent delivery became uncertain after dispatch began"
            ) from exc

        if response.status_code == 503 and _error_code(response) == "runner_unavailable":
            raise RunnerUnavailableError("the session runner is unavailable")
        if 400 <= response.status_code < 500:
            raise OmnigentRejectedError(
                f"Omnigent rejected the event with HTTP {response.status_code}"
            )
        if response.status_code >= 500:
            raise OmnigentAmbiguousDeliveryError(
                f"Omnigent returned HTTP {response.status_code}; acceptance is uncertain"
            )
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise OmnigentRejectedError(
                f"Omnigent rejected the event with HTTP {response.status_code}"
            ) from exc
        payload = response.json()
        if not isinstance(payload, dict):
            return None
        item_id = payload.get("item_id")
        return item_id if isinstance(item_id, str) else None

    async def validate_host(self) -> dict[str, Any]:
        if self._configured_host_id is None:
            raise OmnigentError("no configured host is available to validate")
        payload = await self._get_json(f"/v1/hosts/{self._configured_host_id}")
        if not isinstance(payload, dict):
            raise OmnigentError("configured host response must be an object")
        host_id = payload.get("id") or payload.get("host_id")
        if host_id is not None and host_id != self._configured_host_id:
            raise OmnigentError("configured host lookup returned a different host")
        return payload

    async def recover_bound_runner(self, session_id: str) -> str:
        session = await self.get_session(session_id)
        if self._configured_host_id is not None and session.host_id != self._configured_host_id:
            raise OmnigentRejectedError(
                "refusing runner recovery on a host other than the configured host"
            )
        if not session.host_id:
            raise OmnigentRejectedError("session has no bound host for runner recovery")
        if not session.workspace:
            raise OmnigentRejectedError("session has no workspace for runner recovery")
        if session.host_online is False:
            raise OmnigentRejectedError("the session host is offline")
        if session.runner_online is True and session.runner_id:
            return session.runner_id

        payload = await self._post_control_json(
            f"/v1/hosts/{session.host_id}/runners",
            {"session_id": session_id, "workspace": session.workspace},
        )
        runner_id = payload.get("runner_id") if isinstance(payload, dict) else None
        if not isinstance(runner_id, str) or not runner_id:
            raise OmnigentError("runner launch response is missing runner_id")
        await self._wait_for_runner(runner_id)
        return runner_id

    async def _wait_for_runner(self, runner_id: str) -> None:
        deadline = asyncio.get_running_loop().time() + self._runner_launch_timeout_seconds
        while True:
            payload = await self._get_json(f"/v1/runners/{runner_id}/status")
            if isinstance(payload, dict) and payload.get("online") is True:
                return
            if asyncio.get_running_loop().time() >= deadline:
                raise OmnigentError(f"timed out waiting for runner {runner_id}")
            await asyncio.sleep(1)

    @asynccontextmanager
    async def stream_session_events(
        self, session_id: str
    ) -> AsyncIterator[AsyncIterator[dict[str, Any]]]:
        timeout = httpx.Timeout(
            connect=self._timeout_seconds,
            read=None,
            write=self._timeout_seconds,
            pool=self._timeout_seconds,
        )
        try:
            async with self._client.stream(
                "GET",
                f"/v1/sessions/{session_id}/stream",
                params={"idle": "false"},
                timeout=timeout,
            ) as response:
                await _raise_read_status(response)
                yield iter_sse_events(response.aiter_lines())
        except httpx.HTTPError as exc:
            raise OmnigentError("Omnigent SSE stream failed") from exc

    async def _get_json(
        self, path: str, *, params: dict[str, str | int | bool] | None = None
    ) -> Any:
        try:
            response = await self._client.get(path, params=params)
        except httpx.HTTPError as exc:
            raise OmnigentError(f"Omnigent GET failed for {path}") from exc
        await _raise_read_status(response)
        return response.json()

    async def _post_control_json(self, path: str, payload: dict[str, Any]) -> Any:
        try:
            response = await self._client.post(path, json=payload)
        except httpx.HTTPError as exc:
            raise OmnigentError(f"Omnigent control request failed for {path}") from exc
        await _raise_read_status(response)
        return response.json()


async def iter_sse_events(lines: AsyncIterator[str]) -> AsyncIterator[dict[str, Any]]:
    event_name: str | None = None
    data_lines: list[str] = []
    async for raw_line in lines:
        line = raw_line.rstrip("\r")
        if line == "":
            event = _decode_sse_event(event_name, data_lines)
            event_name = None
            data_lines = []
            if event == "[DONE]":
                break
            if isinstance(event, dict):
                yield event
            continue
        if line.startswith(":"):
            continue
        field, separator, value = line.partition(":")
        if separator and value.startswith(" "):
            value = value[1:]
        if field == "event":
            event_name = value
        elif field == "data":
            data_lines.append(value)
    event = _decode_sse_event(event_name, data_lines)
    if isinstance(event, dict):
        yield event


def _decode_sse_event(event_name: str | None, data_lines: list[str]) -> dict[str, Any] | str | None:
    if not data_lines:
        return None
    data = "\n".join(data_lines)
    if data == "[DONE]":
        return data
    try:
        payload = json.loads(data)
    except json.JSONDecodeError as exc:
        raise OmnigentError("invalid JSON in Omnigent SSE stream") from exc
    if not isinstance(payload, dict):
        return None
    if event_name and "type" not in payload:
        payload["type"] = event_name
    return payload


async def _raise_read_status(response: httpx.Response) -> None:
    if response.status_code == 404:
        raise OmnigentNotFoundError("Omnigent resource was not found")
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise OmnigentError(f"Omnigent request failed with HTTP {response.status_code}") from exc


def _error_code(response: httpx.Response) -> str | None:
    try:
        payload = response.json()
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    error = payload.get("error")
    if not isinstance(error, dict):
        return None
    code = error.get("code")
    return code if isinstance(code, str) else None
