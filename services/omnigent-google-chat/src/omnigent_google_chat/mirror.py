from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import Callable
from typing import Any

from omnigent_google_chat.meta_chat import GoogleChatSender, MetaChatError
from omnigent_google_chat.models import MappingState
from omnigent_google_chat.omnigent import OmnigentClient, OmnigentError, OmnigentNotFoundError
from omnigent_google_chat.store import SQLiteStore
from omnigent_google_chat.text import format_user_item, item_id, item_role, item_text

_STREAM_END = object()


class SessionMirror:
    def __init__(
        self,
        *,
        session_id: str,
        store: SQLiteStore,
        omnigent: OmnigentClient,
        sender: GoogleChatSender,
        mirror_mode: str,
        mention_unixname: str,
        mention_on_completion: bool,
        max_session_chars: int,
        status_changed: Callable[[str, str], None],
    ) -> None:
        self.session_id = session_id
        self._store = store
        self._omnigent = omnigent
        self._sender = sender
        self._mirror_mode = mirror_mode
        self._mention_unixname = mention_unixname
        self._mention_on_completion = mention_on_completion
        self._max_session_chars = max_session_chars
        self._status_changed = status_changed
        self._logger = logging.getLogger(__name__)

    async def run(self, stop: asyncio.Event) -> None:
        failures = 0
        while not stop.is_set():
            mapping = await self._store.get_thread(self.session_id)
            if mapping is None or mapping.state is not MappingState.ACTIVE:
                return
            try:
                await self._run_connected(stop)
                failures = 0
            except OmnigentNotFoundError:
                await self._store.set_thread_state(self.session_id, MappingState.ARCHIVED)
                return
            except asyncio.CancelledError:
                raise
            except Exception:
                failures += 1
                delay = min(2 ** min(failures, 6), 60) + random.uniform(0, 0.5)
                self._logger.warning(
                    "Session mirror disconnected session_id=%s; retrying in %.1fs",
                    self.session_id,
                    delay,
                    exc_info=True,
                )
                try:
                    await asyncio.wait_for(stop.wait(), timeout=delay)
                except TimeoutError:
                    pass

    async def _run_connected(self, stop: asyncio.Event) -> None:
        queue: asyncio.Queue[dict[str, Any] | BaseException | object] = asyncio.Queue(maxsize=1000)
        ready = asyncio.Event()
        stream_task = asyncio.create_task(self._buffer_stream(queue, ready))
        try:
            await ready.wait()
            await self.reconcile_items()
            while not stop.is_set():
                event = await queue.get()
                if event is _STREAM_END:
                    raise OmnigentError("Omnigent SSE stream ended")
                if isinstance(event, BaseException):
                    raise event
                if not isinstance(event, dict):
                    raise OmnigentError("invalid buffered SSE event")
                await self._handle_event(event)
        finally:
            stream_task.cancel()
            await asyncio.gather(stream_task, return_exceptions=True)

    async def _buffer_stream(
        self,
        queue: asyncio.Queue[dict[str, Any] | BaseException | object],
        ready: asyncio.Event,
    ) -> None:
        try:
            async with self._omnigent.stream_session_events(self.session_id) as events:
                ready.set()
                async for event in events:
                    await queue.put(event)
            await queue.put(_STREAM_END)
        except asyncio.CancelledError:
            raise
        except BaseException as exc:
            ready.set()
            await queue.put(exc)

    async def reconcile_items(self) -> None:
        mapping = await self._store.get_thread(self.session_id)
        if mapping is None or mapping.state is not MappingState.ACTIVE:
            return
        cursor = mapping.last_item_position
        while True:
            page = await self._omnigent.list_items(self.session_id, after=cursor)
            if page.has_more and not page.items:
                raise OmnigentError("item pagination made no progress")
            for item in page.items:
                durable_id = item_id(item)
                if durable_id is None:
                    raise OmnigentError("durable item is missing id")
                added_chars = await self._mirror_item(mapping.thread_name, durable_id, item)
                await self._store.update_thread_cursor(
                    self.session_id, durable_id, added_chars=added_chars
                )
                cursor = durable_id
            if not page.has_more:
                return
            if page.last_id is None or page.last_id == cursor and not page.items:
                raise OmnigentError("item pagination returned an invalid cursor")

    async def _mirror_item(self, thread_name: str, durable_id: str, item: dict[str, Any]) -> int:
        if self._mirror_mode == "status-only":
            return 0
        role = item_role(item)
        text: str | None = None
        if role == "assistant":
            text = item_text(item)
        elif role == "user" and not await self._store.is_chat_origin_item(durable_id):
            text = format_user_item(item)
        if not text:
            return 0

        mapping = await self._store.get_thread(self.session_id)
        if mapping is None:
            return 0
        capped_key = f"mirror_cap:{self.session_id}"
        if await self._store.get_state(capped_key) == "1":
            return 0
        if mapping.mirrored_chars + len(text) > self._max_session_chars:
            notice = (
                "Further transcript output is available in Omnigent. "
                "This thread reached its configured mirror limit."
            )
            await self._sender.send(
                session_id=self.session_id,
                source_kind="notice",
                source_id=f"mirror-cap:{durable_id}",
                text=notice,
                thread_name=thread_name,
            )
            await self._store.set_state(capped_key, "1")
            return len(notice)

        await self._sender.send(
            session_id=self.session_id,
            source_kind="item",
            source_id=durable_id,
            text=text,
            thread_name=thread_name,
        )
        return len(text)

    async def _handle_event(self, event: dict[str, Any]) -> None:
        event_type = str(event.get("type") or "")
        if event_type == "response.output_item.done":
            await self.reconcile_items()
            return
        if event_type == "response.completed":
            return
        if event_type == "session.status":
            status = _event_status(event)
            if not status:
                return
            self._status_changed(self.session_id, status)
            transition_source = await self._store.observe_session_status(self.session_id, status)
            if status == "running":
                return
            if status in {"idle", "failed"}:
                await self.reconcile_items()
            if status == "failed":
                await self._send_status(
                    event,
                    status="failed",
                    text="Session failed. Open Omnigent for details.",
                    mention=True,
                    fallback_source=transition_source,
                )
            elif status == "idle" and self._mention_on_completion:
                await self._send_status(
                    event,
                    status="idle",
                    text="Session completed.",
                    mention=True,
                    fallback_source=transition_source,
                )
            elif status in {"waiting", "blocked"}:
                session = await self._omnigent.get_session(self.session_id)
                if status == "blocked" or session.pending_elicitations_count:
                    await self._send_status(
                        event,
                        status=status,
                        text="Session needs attention in Omnigent.",
                        mention=True,
                        fallback_source=transition_source,
                    )
            return
        if event_type == "response.elicitation_request":
            await self._send_status(
                event,
                status="approval-needed",
                text="Session is waiting for an approval in Omnigent.",
                mention=True,
            )
            return
        if event_type in {"response.failed", "turn.failed"}:
            await self.reconcile_items()
            await self._send_status(
                event,
                status="failed",
                text="Session failed. Open Omnigent for details.",
                mention=True,
            )
            return
        if event_type == "session.interrupted":
            await self._send_status(
                event,
                status="interrupted",
                text="Session was interrupted.",
                mention=False,
            )

    async def _send_status(
        self,
        event: dict[str, Any],
        *,
        status: str,
        text: str,
        mention: bool,
        fallback_source: str | None = None,
    ) -> None:
        mapping = await self._store.get_thread(self.session_id)
        if mapping is None:
            return
        source_id = _stable_event_id(event)
        if source_id is None:
            source_id = fallback_source
        if source_id is None:
            self._logger.warning(
                "Skipping status without stable identity session_id=%s status=%s",
                self.session_id,
                status,
            )
            return
        try:
            await self._sender.send(
                session_id=self.session_id,
                source_kind="status",
                source_id=f"{source_id}:{status}",
                text=text,
                thread_name=mapping.thread_name,
                mention_unixname=self._mention_unixname if mention else None,
            )
        except MetaChatError:
            self._logger.warning(
                "Could not mirror status session_id=%s status=%s",
                self.session_id,
                status,
                exc_info=True,
            )


def _event_status(event: dict[str, Any]) -> str | None:
    status = event.get("status")
    if isinstance(status, str):
        return status
    data = event.get("data")
    if isinstance(data, dict):
        data_status = data.get("status")
        if isinstance(data_status, str):
            return data_status
    return None


def _stable_event_id(event: dict[str, Any]) -> str | None:
    for key in ("id", "event_id", "response_id", "elicitation_id", "request_id"):
        value = event.get(key)
        if isinstance(value, str) and value:
            return value
    data = event.get("data")
    if isinstance(data, dict):
        nested = _stable_event_id(data)
        if nested:
            return nested
    response = event.get("response")
    if isinstance(response, dict):
        value = response.get("id")
        if isinstance(value, str) and value:
            return value
    return None
