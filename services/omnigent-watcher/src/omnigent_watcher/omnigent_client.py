"""Narrow Omnigent 0.5.1 REST adapter used by the sidecar."""

from __future__ import annotations

import asyncio
import logging
import math
import re

import httpx

from .domain import EventDeliveryResult, EventDeliveryStatus, SessionSnapshot

_logger = logging.getLogger(__name__)

_DIFF_ID = re.compile(r"D[1-9][0-9]*")


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
        self._client = client or httpx.AsyncClient(base_url=server_url, timeout=30, trust_env=False)

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

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
        return await self.delivery_receipt(session_id, delivery_id) is not None

    async def delivery_receipt(
        self, session_id: str, delivery_id: str
    ) -> EventDeliveryResult | None:
        response = await self._client.get(
            f"/v1/sessions/{session_id}/items",
            params={"limit": 1000, "order": "desc"},
        )
        if response.status_code == 404:
            return None
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
            raise OmnigentAPIError("session items response was malformed")
        # Both spellings. The prefix was "[Diff watcher " until the service was
        # renamed, and this check is what stops a batch already delivered from
        # being delivered again -- so for one restart it has to recognise a wake
        # this build would never have written.
        markers = (f"[Watcher {delivery_id}]", f"[Diff watcher {delivery_id}]")
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
                and any(marker in block["text"] for marker in markers)
                for block in content
            ):
                created_at = raw_item.get("created_at")
                accepted_at = (
                    float(created_at)
                    if isinstance(created_at, (int, float))
                    and not isinstance(created_at, bool)
                    and math.isfinite(created_at)
                    and created_at > 0
                    else None
                )
                return EventDeliveryResult(EventDeliveryStatus.ALREADY_ACCEPTED, accepted_at)
        return None

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

    async def delivery_receipt(
        self, session_id: str, delivery_id: str
    ) -> EventDeliveryResult | None:
        if self._mode == "log_only":
            return None
        return await self._client.delivery_receipt(session_id, delivery_id)

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
            return EventDeliveryResult(EventDeliveryStatus.NOT_SENT)
        try:
            receipt = await self._client.delivery_receipt(session_id, delivery_id)
            if receipt is not None:
                return receipt
            session = await self._client.get(session_id)
        except (httpx.HTTPError, OmnigentAPIError):
            return EventDeliveryResult(EventDeliveryStatus.NOT_SENT)
        if session.terminal:
            return EventDeliveryResult(EventDeliveryStatus.TERMINAL)
        if not session.can_accept_input:
            return EventDeliveryResult(EventDeliveryStatus.NOT_SENT)
        try:
            response = await self._client.post_message(session_id, content)
        except httpx.TransportError:
            return await self._verify_uncertain_delivery(session_id, delivery_id)
        if response.status_code in {404, 410}:
            return EventDeliveryResult(EventDeliveryStatus.TERMINAL)
        if response.status_code in {409, 423, 429} or response.status_code >= 500:
            return EventDeliveryResult(EventDeliveryStatus.DEFERRED)
        response.raise_for_status()
        try:
            payload = response.json()
        except ValueError:
            payload = None
        if isinstance(payload, dict) and payload.get("denied") is True:
            return EventDeliveryResult(EventDeliveryStatus.NOT_SENT)
        return EventDeliveryResult(EventDeliveryStatus.ACCEPTED)

    async def _verify_uncertain_delivery(
        self,
        session_id: str,
        delivery_id: str,
    ) -> EventDeliveryResult:
        for delay in (0.1, 0.5, 1.0):
            await asyncio.sleep(delay)
            try:
                receipt = await self._client.delivery_receipt(session_id, delivery_id)
                if receipt is not None:
                    return receipt
            except (httpx.HTTPError, OmnigentAPIError):
                continue
        return EventDeliveryResult(EventDeliveryStatus.DEFERRED)


def _labels(value: object) -> dict[str, str]:
    """Session labels, string-valued only.

    Watches are no longer declared through labels, but liveness still reads
    ``omnigent.closed`` -- a closed session must stop being polled and woken.
    """
    if not isinstance(value, dict):
        return {}
    return {str(key): item for key, item in value.items() if isinstance(item, str)}


def _string_dict(value: object, name: str) -> dict[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise OmnigentAPIError(f"{name} must be an object with string keys")
    return {str(key): item for key, item in value.items()}
