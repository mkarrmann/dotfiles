from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from functools import partial

from omnigent_google_chat.meta_chat import GoogleChatSender, MetaChatOutputError
from omnigent_google_chat.mirror import SessionMirror
from omnigent_google_chat.models import MappingState, SessionSummary
from omnigent_google_chat.omnigent import OmnigentClient
from omnigent_google_chat.store import SQLiteStore
from omnigent_google_chat.text import format_root


class SessionReconciler:
    def __init__(
        self,
        *,
        store: SQLiteStore,
        omnigent: OmnigentClient,
        sender: GoogleChatSender,
        space_name: str,
        host_id: str,
        discovery_mode: str,
        discovery_label: str,
        lookback_hours: int,
        interval_seconds: float,
        recent_active_seconds: float,
        mirror_mode: str,
        mention_unixname: str,
        mention_on_root: bool,
        mention_on_completion: bool,
        meta_bot_actor_id: str,
        max_session_chars: int,
        poll_trigger: Callable[[], None],
    ) -> None:
        self._store = store
        self._omnigent = omnigent
        self._sender = sender
        self._space_name = space_name
        self._host_id = host_id
        self._discovery_mode = discovery_mode
        self._discovery_label = discovery_label
        self._lookback_seconds = lookback_hours * 60 * 60
        self._interval_seconds = interval_seconds
        self._recent_active_seconds = recent_active_seconds
        self._mirror_mode = mirror_mode
        self._mention_unixname = mention_unixname
        self._mention_on_root = mention_on_root
        self._mention_on_completion = mention_on_completion
        self._meta_bot_actor_id = meta_bot_actor_id
        self._max_session_chars = max_session_chars
        self._poll_trigger = poll_trigger
        self._mirror_tasks: dict[str, asyncio.Task[None]] = {}
        self._statuses: dict[str, str] = {}
        self._last_active_at: float | None = None
        self._logger = logging.getLogger(__name__)

    def has_active_sessions(self) -> bool:
        active = {"running", "waiting", "blocked"}
        if any(status in active for status in self._statuses.values()):
            return True
        return (
            self._last_active_at is not None
            and time.monotonic() - self._last_active_at <= self._recent_active_seconds
        )

    async def run(self, stop: asyncio.Event) -> None:
        try:
            while not stop.is_set():
                try:
                    await self.reconcile_once(stop)
                except Exception:
                    self._logger.warning("Session discovery failed; retrying", exc_info=True)
                try:
                    await asyncio.wait_for(stop.wait(), timeout=self._interval_seconds)
                except TimeoutError:
                    pass
        finally:
            for task in self._mirror_tasks.values():
                task.cancel()
            await asyncio.gather(*self._mirror_tasks.values(), return_exceptions=True)
            self._mirror_tasks.clear()

    async def reconcile_once(self, stop: asyncio.Event) -> None:
        sessions = await self._omnigent.list_sessions()
        by_id = {session.id: session for session in sessions}
        eligible = {session.id: session for session in sessions if self._eligible(session)}

        for mapping in await self._store.list_active_threads():
            session = by_id.get(mapping.omnigent_session_id)
            if session is None or session.archived:
                await self._archive_mapping(mapping.omnigent_session_id, mapping.thread_name)
                self._cancel_mirror(mapping.omnigent_session_id)

        for session in eligible.values():
            current_mapping = await self._store.get_thread(session.id)
            if current_mapping is None:
                await self._create_mapping(session)
                current_mapping = await self._store.get_thread(session.id)
            if current_mapping is not None and current_mapping.state is MappingState.ACTIVE:
                self._statuses[session.id] = session.status
                if session.status in {"running", "waiting", "blocked"}:
                    self._last_active_at = time.monotonic()
                self._ensure_mirror(session.id, stop)

        for session_id in list(self._mirror_tasks):
            current_mapping = await self._store.get_thread(session_id)
            if current_mapping is None or current_mapping.state is not MappingState.ACTIVE:
                self._cancel_mirror(session_id)

    def _eligible(self, session: SessionSummary) -> bool:
        if session.archived:
            return False
        if session.permission_level is not None and session.permission_level < 2:
            return False
        label_value = session.labels.get(self._discovery_label)
        if self._discovery_mode == "label":
            return session.host_id == self._host_id and _label_bool(label_value) is True
        if _label_bool(label_value) is False:
            return False
        cutoff = int(time.time()) - self._lookback_seconds
        return session.host_id == self._host_id and session.updated_at >= cutoff

    async def _create_mapping(self, session: SessionSummary) -> None:
        sent = await self._sender.send(
            session_id=session.id,
            source_kind="root",
            source_id=session.id,
            text=format_root(session),
            thread_name=None,
            mention_unixname=self._mention_unixname if self._mention_on_root else None,
        )
        if len(sent) != 1:
            raise MetaChatOutputError("session root unexpectedly required multiple messages")
        root = sent[0]
        if root.actor_id is not None and root.actor_id != self._meta_bot_actor_id:
            raise MetaChatOutputError("session root was not authored by the configured Meta Bot")
        if root.actor_type is not None and root.actor_type.upper() == "HUMAN":
            raise MetaChatOutputError("session root has a human sender; refusing echo risk")
        await self._store.create_thread(
            session.id,
            self._space_name,
            root.thread_name,
            root.name,
            session.title,
        )
        self._poll_trigger()
        self._logger.info(
            "Mapped Omnigent session to Google Chat session_id=%s thread=%s",
            session.id,
            root.thread_name,
        )

    async def _archive_mapping(self, session_id: str, thread_name: str) -> None:
        try:
            await self._sender.send(
                session_id=session_id,
                source_kind="notice",
                source_id="session-archived",
                text="The Omnigent session was archived or deleted. This thread is detached.",
                thread_name=thread_name,
            )
        finally:
            await self._store.set_thread_state(session_id, MappingState.ARCHIVED)

    def _ensure_mirror(self, session_id: str, stop: asyncio.Event) -> None:
        existing = self._mirror_tasks.get(session_id)
        if existing is not None and not existing.done():
            return
        mirror = SessionMirror(
            session_id=session_id,
            store=self._store,
            omnigent=self._omnigent,
            sender=self._sender,
            mirror_mode=self._mirror_mode,
            mention_unixname=self._mention_unixname,
            mention_on_completion=self._mention_on_completion,
            max_session_chars=self._max_session_chars,
            status_changed=self._status_changed,
        )
        task = asyncio.create_task(mirror.run(stop), name=f"gchat-mirror-{session_id}")
        task.add_done_callback(partial(self._mirror_done, session_id))
        self._mirror_tasks[session_id] = task

    def _cancel_mirror(self, session_id: str) -> None:
        task = self._mirror_tasks.pop(session_id, None)
        if task is not None:
            task.cancel()
        self._statuses.pop(session_id, None)

    def _mirror_done(self, session_id: str, task: asyncio.Task[None]) -> None:
        if self._mirror_tasks.get(session_id) is task:
            self._mirror_tasks.pop(session_id, None)
        if not task.cancelled() and (error := task.exception()) is not None:
            self._logger.error("Session mirror stopped session_id=%s", session_id, exc_info=error)

    def _status_changed(self, session_id: str, status: str) -> None:
        self._statuses[session_id] = status
        if status in {"running", "waiting", "blocked"}:
            self._last_active_at = time.monotonic()


def _label_bool(value: object) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "on"}:
            return True
        if normalized in {"false", "0", "no", "off"}:
            return False
    return None
