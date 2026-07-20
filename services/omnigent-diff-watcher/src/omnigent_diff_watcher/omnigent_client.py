"""Narrow Omnigent 0.5.1 REST adapter used by the sidecar."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping

import httpx

from .domain import EventDeliveryResult, EventDeliveryStatus, SessionSnapshot

_logger = logging.getLogger(__name__)


class OmnigentAPIError(RuntimeError):
    """A redacted server API failure safe for service logs."""


class OmnigentClient:
    def __init__(
        self,
        server_url: str,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(base_url=server_url, timeout=30)

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def list_sessions(self) -> list[dict[str, object]]:
        sessions: list[dict[str, object]] = []
        after: str | None = None
        while True:
            params: dict[str, str | int] = {"limit": 1000, "order": "asc"}
            if after is not None:
                params["after"] = after
            response = await self._client.get("/v1/sessions", params=params)
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
                raise OmnigentAPIError("session list response was malformed")
            page = [_string_dict(item, "session") for item in payload["data"]]
            sessions.extend(page)
            if payload.get("has_more") is not True:
                return sessions
            last_id = payload.get("last_id")
            if not isinstance(last_id, str) or not last_id or last_id == after:
                raise OmnigentAPIError("session list pagination did not advance")
            after = last_id

    async def get(self, session_id: str) -> SessionSnapshot:
        response = await self._client.get(f"/v1/sessions/{session_id}")
        if response.status_code == 404:
            return SessionSnapshot(session_id=session_id, labels={}, exists=False)
        response.raise_for_status()
        payload = _string_dict(response.json(), "session")
        labels = _labels(payload.get("labels"))
        archived = payload.get("archived") is True
        closed = labels.get("omnigent.closed") == "true" or ":closed:" in str(
            payload.get("title") or ""
        )
        runner_id = payload.get("runner_id")
        host_id = payload.get("host_id")
        locally_bound = runner_id is None and host_id is None
        reachable = (
            payload.get("runner_online") is True
            or payload.get("host_online") is True
            or locally_bound
        )
        status = payload.get("status")
        waiting = status in {"running", "waiting"}
        pending = bool(payload.get("pending_elicitations")) or bool(payload.get("pending_inputs"))
        terminal_pending = payload.get("terminal_pending") is True
        return SessionSnapshot(
            session_id=session_id,
            labels=labels,
            archived=archived,
            closed=closed,
            reachable=reachable,
            can_accept_input=(
                reachable
                and not archived
                and not closed
                and not waiting
                and not pending
                and not terminal_pending
            ),
        )

    async def has_delivery_marker(self, session_id: str, delivery_id: str) -> bool:
        response = await self._client.get(
            f"/v1/sessions/{session_id}/items",
            params={"limit": 1000, "order": "desc"},
        )
        if response.status_code == 404:
            return False
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
            raise OmnigentAPIError("session items response was malformed")
        marker = f"[Diff watcher {delivery_id}]"
        for raw_item in payload["data"]:
            if not isinstance(raw_item, dict) or raw_item.get("type") != "message":
                continue
            if raw_item.get("role") != "user":
                continue
            content = raw_item.get("content")
            if not isinstance(content, list):
                continue
            if any(
                isinstance(block, dict)
                and isinstance(block.get("text"), str)
                and marker in block["text"]
                for block in content
            ):
                return True
        return False

    async def post_message(self, session_id: str, content: str) -> httpx.Response:
        return await self._client.post(
            f"/v1/sessions/{session_id}/events",
            json={
                "type": "message",
                "data": {
                    "role": "user",
                    "content": [{"type": "input_text", "text": content}],
                },
            },
        )


class OmnigentDeliveryService:
    def __init__(
        self,
        client: OmnigentClient,
        *,
        mode: str,
        allowlist: frozenset[str],
    ) -> None:
        if mode not in {"log_only", "enabled"}:
            raise ValueError("delivery mode must be log_only or enabled")
        self._client = client
        self._mode = mode
        self._allowlist = allowlist

    async def deliver_message(
        self,
        session_id: str,
        delivery_id: str,
        content: str,
    ) -> EventDeliveryResult:
        if self._mode == "log_only":
            _logger.info("would deliver batch=%s session=%s", delivery_id, session_id)
            return EventDeliveryResult(EventDeliveryStatus.ACCEPTED)
        if self._allowlist and session_id not in self._allowlist:
            return EventDeliveryResult(EventDeliveryStatus.DEFERRED)
        session = await self._client.get(session_id)
        if session.terminal:
            return EventDeliveryResult(EventDeliveryStatus.TERMINAL)
        if not session.can_accept_input:
            return EventDeliveryResult(EventDeliveryStatus.DEFERRED)
        if await self._client.has_delivery_marker(session_id, delivery_id):
            return EventDeliveryResult(EventDeliveryStatus.ALREADY_ACCEPTED)
        try:
            response = await self._client.post_message(session_id, content)
        except httpx.TransportError:
            return await self._verify_uncertain_delivery(session_id, delivery_id)
        if response.status_code in {404, 410}:
            return EventDeliveryResult(EventDeliveryStatus.TERMINAL)
        if response.status_code in {409, 423, 429} or response.status_code >= 500:
            return EventDeliveryResult(EventDeliveryStatus.DEFERRED)
        response.raise_for_status()
        return EventDeliveryResult(EventDeliveryStatus.ACCEPTED)

    async def _verify_uncertain_delivery(
        self,
        session_id: str,
        delivery_id: str,
    ) -> EventDeliveryResult:
        for delay in (0.1, 0.5, 1.0):
            await asyncio.sleep(delay)
            try:
                if await self._client.has_delivery_marker(session_id, delivery_id):
                    return EventDeliveryResult(EventDeliveryStatus.ALREADY_ACCEPTED)
            except httpx.HTTPError:
                continue
        return EventDeliveryResult(EventDeliveryStatus.DEFERRED)


def desired_watch(item: Mapping[str, object]) -> tuple[str, frozenset[str]] | None:
    labels = _labels(item.get("labels"))
    diff_id = labels.get("omnigent.diff.number")
    preference = labels.get("omnigent.diff.watch")
    if diff_id is None or preference in {None, "", "off"}:
        return None
    event_types = frozenset(part for part in preference.split(",") if part)
    if not event_types or event_types - {"review_comment", "ci_failure"}:
        return None
    return diff_id, event_types


def _labels(value: object) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {str(key): item for key, item in value.items() if isinstance(item, str)}


def _string_dict(value: object, name: str) -> dict[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise OmnigentAPIError(f"{name} must be an object with string keys")
    return {str(key): item for key, item in value.items()}
