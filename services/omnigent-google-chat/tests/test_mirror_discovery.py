from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from omnigent_google_chat.discovery import SessionReconciler
from omnigent_google_chat.meta_chat import MetaChatError, MetaChatOutputError
from omnigent_google_chat.mirror import SessionMirror
from omnigent_google_chat.models import ItemPage, SentMessage, SessionSummary
from omnigent_google_chat.omnigent import OmnigentNotFoundError
from omnigent_google_chat.store import SQLiteStore


class FakeSender:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.error: BaseException | None = None
        self.root_actor_id = "users/bot"
        self.root_actor_type = "BOT"

    async def send(self, **kwargs: Any) -> list[SentMessage]:
        if self.error:
            raise self.error
        self.calls.append(kwargs)
        return [
            SentMessage(
                name=f"messages/{len(self.calls)}",
                thread_name=kwargs["thread_name"] or "threads/new",
                actor_id=self.root_actor_id,
                actor_type=self.root_actor_type,
            )
        ]


class FakeOmnigent:
    def __init__(self) -> None:
        self.items: list[dict[str, Any]] = []
        self.sessions: list[SessionSummary] = []
        self.stream_events: list[dict[str, Any]] = []
        self.item_calls: list[str | None] = []

    async def list_items(self, session_id: str, *, after: str | None) -> ItemPage:
        self.item_calls.append(after)
        start = 0
        if after:
            ids = [str(item["id"]) for item in self.items]
            start = ids.index(after) + 1 if after in ids else len(ids)
        page = self.items[start:]
        return ItemPage(
            items=page,
            last_id=str(page[-1]["id"]) if page else after,
            has_more=False,
        )

    async def get_session(self, session_id: str) -> SessionSummary:
        for session in self.sessions:
            if session.id == session_id:
                return session
        return SessionSummary(
            id=session_id,
            title="Session",
            status="idle",
            updated_at=10,
            runner_online=True,
        )

    async def list_sessions(self) -> list[SessionSummary]:
        return self.sessions

    @asynccontextmanager
    async def stream_session_events(
        self, session_id: str
    ) -> AsyncIterator[AsyncIterator[dict[str, Any]]]:
        async def events() -> AsyncIterator[dict[str, Any]]:
            for event in self.stream_events:
                yield event

        yield events()


async def setup_mirror(
    tmp_path: Path,
    *,
    mode: str = "concise",
    mention_completion: bool = True,
    max_chars: int = 1000,
) -> tuple[SQLiteStore, FakeOmnigent, FakeSender, SessionMirror]:
    store = SQLiteStore(tmp_path / "bridge.sqlite3")
    await store.initialize()
    await store.create_thread("conv", "spaces/s", "threads/t", "messages/root", "Session")
    omnigent = FakeOmnigent()
    sender = FakeSender()
    mirror = SessionMirror(
        session_id="conv",
        store=store,
        omnigent=omnigent,  # type: ignore[arg-type]
        sender=sender,  # type: ignore[arg-type]
        mirror_mode=mode,
        mention_unixname="owner",
        mention_on_completion=mention_completion,
        max_session_chars=max_chars,
        status_changed=lambda session_id, status: None,
    )
    return store, omnigent, sender, mirror


def user_item(item_id: str, text: str) -> dict[str, Any]:
    return {
        "id": item_id,
        "type": "message",
        "data": {"role": "user", "content": [{"type": "input_text", "text": text}]},
    }


def assistant_item(item_id: str, text: str) -> dict[str, Any]:
    return {
        "id": item_id,
        "type": "message",
        "data": {
            "role": "assistant",
            "content": [
                {"type": "output_text", "text": text},
                {"type": "reasoning", "text": "private reasoning"},
            ],
        },
    }


async def test_reconcile_mirrors_durable_messages_and_suppresses_phone_echo(
    tmp_path: Path,
) -> None:
    store, omnigent, sender, mirror = await setup_mirror(tmp_path)
    try:
        await store.claim_inbound(
            message_name="messages/phone",
            thread_name="threads/t",
            actor_id="users/human",
            created_at_google="2026-01-01T00:00:00Z",
            text_sha256="hash",
        )
        from omnigent_google_chat.models import InboundState

        await store.set_inbound_state(
            "messages/phone", InboundState.SUBMITTED, omnigent_item_id="item_phone"
        )
        omnigent.items = [
            user_item("item_external", "from CodeCompanion"),
            user_item("item_phone", "from phone"),
            assistant_item("item_answer", "final answer"),
            {"id": "item_tool", "type": "function_call", "data": {"arguments": "secret"}},
        ]
        await mirror.reconcile_items()
        assert [call["text"] for call in sender.calls] == [
            "Omnigent client: from CodeCompanion",
            "final answer",
        ]
        assert all("private reasoning" not in call["text"] for call in sender.calls)
        mapping = await store.get_thread("conv")
        assert mapping is not None and mapping.last_item_position == "item_tool"
    finally:
        store.close()


async def test_status_only_advances_cursor_without_transcript(tmp_path: Path) -> None:
    store, omnigent, sender, mirror = await setup_mirror(tmp_path, mode="status-only")
    try:
        omnigent.items = [assistant_item("item_1", "answer")]
        await mirror.reconcile_items()
        assert sender.calls == []
        assert (await store.get_thread("conv")).last_item_position == "item_1"  # type: ignore[union-attr]
    finally:
        store.close()


async def test_send_failure_does_not_advance_item_cursor(tmp_path: Path) -> None:
    store, omnigent, sender, mirror = await setup_mirror(tmp_path)
    try:
        omnigent.items = [assistant_item("item_1", "answer")]
        sender.error = MetaChatError("unavailable")
        with pytest.raises(MetaChatError):
            await mirror.reconcile_items()
        assert (await store.get_thread("conv")).last_item_position is None  # type: ignore[union-attr]
    finally:
        store.close()


async def test_output_cap_posts_one_notice_and_suppresses_future_content(tmp_path: Path) -> None:
    store, omnigent, sender, mirror = await setup_mirror(tmp_path, max_chars=5)
    try:
        omnigent.items = [assistant_item("item_1", "too long")]
        await mirror.reconcile_items()
        assert len(sender.calls) == 1
        assert "mirror limit" in sender.calls[0]["text"]
        omnigent.items.append(assistant_item("item_2", "another"))
        await mirror.reconcile_items()
        assert len(sender.calls) == 1
    finally:
        store.close()


async def test_notification_policy_ignores_response_completed_and_mentions_attention(
    tmp_path: Path,
) -> None:
    store, omnigent, sender, mirror = await setup_mirror(tmp_path)
    try:
        omnigent.sessions = [
            SessionSummary(
                id="conv",
                title="Session",
                status="waiting",
                updated_at=10,
                pending_elicitations_count=1,
            )
        ]
        await mirror._handle_event({"type": "response.completed", "response": {"id": "r1"}})
        assert sender.calls == []
        await mirror._handle_event(
            {"type": "response.elicitation_request", "elicitation_id": "elicit_1"}
        )
        assert sender.calls[-1]["mention_unixname"] == "owner"
        await mirror._handle_event({"type": "session.interrupted", "data": {"response_id": "r1"}})
        assert sender.calls[-1]["mention_unixname"] is None
    finally:
        store.close()


async def test_completion_setting_controls_mention(tmp_path: Path) -> None:
    store, _, sender, mirror = await setup_mirror(tmp_path, mention_completion=False)
    try:
        await mirror._handle_event({"type": "session.status", "status": "idle", "id": "s1"})
        assert sender.calls[-1]["mention_unixname"] is None
    finally:
        store.close()


async def test_status_without_event_id_uses_durable_transition_generation(
    tmp_path: Path,
) -> None:
    store, _, sender, mirror = await setup_mirror(tmp_path)
    try:
        await mirror._handle_event({"type": "session.status", "status": "running"})
        await mirror._handle_event({"type": "session.status", "status": "idle"})
        first_source = sender.calls[-1]["source_id"]
        assert first_source == "status-transition-2:idle"

        await mirror._handle_event({"type": "session.status", "status": "idle"})
        assert sender.calls[-1]["source_id"] == first_source

        await mirror._handle_event({"type": "session.status", "status": "running"})
        await mirror._handle_event({"type": "session.status", "status": "failed"})
        assert sender.calls[-1]["source_id"] == "status-transition-4:failed"
        assert sender.calls[-1]["mention_unixname"] == "owner"
    finally:
        store.close()


async def test_waiting_and_failure_statuses_reconcile_and_notify(tmp_path: Path) -> None:
    store, omnigent, sender, mirror = await setup_mirror(tmp_path)
    try:
        omnigent.sessions = [
            SessionSummary(
                id="conv",
                title="Session",
                status="waiting",
                updated_at=10,
                pending_elicitations_count=1,
            )
        ]
        await mirror._handle_event(
            {"type": "session.status", "data": {"status": "waiting"}, "id": "wait_1"}
        )
        assert sender.calls[-1]["mention_unixname"] == "owner"
        await mirror._handle_event({"type": "response.failed", "response": {"id": "response_1"}})
        assert sender.calls[-1]["source_id"] == "response_1:failed"
        assert sender.calls[-1]["mention_unixname"] == "owner"
    finally:
        store.close()


async def test_mirror_marks_missing_session_archived(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, _, _, mirror = await setup_mirror(tmp_path)

    async def missing(stop: asyncio.Event) -> None:
        raise OmnigentNotFoundError("gone")

    monkeypatch.setattr(mirror, "_run_connected", missing)
    try:
        await mirror.run(asyncio.Event())
        assert (await store.get_thread("conv")).state.value == "archived"  # type: ignore[union-attr]
    finally:
        store.close()


async def test_stream_opens_before_reconcile_and_terminal_repair_is_deduplicated(
    tmp_path: Path,
) -> None:
    store, omnigent, sender, mirror = await setup_mirror(tmp_path, mention_completion=False)
    stop = asyncio.Event()
    try:
        omnigent.items = [assistant_item("item_1", "answer")]
        omnigent.stream_events = [
            {"type": "response.output_item.done", "item": omnigent.items[0]},
            {"type": "session.status", "status": "idle", "id": "status_1"},
        ]
        with pytest.raises(Exception, match="stream ended"):
            await mirror._run_connected(stop)
        item_calls = [call for call in sender.calls if call["source_kind"] == "item"]
        assert len(item_calls) == 1
        assert omnigent.item_calls[0] is None
        assert all(cursor == "item_1" for cursor in omnigent.item_calls[1:])
    finally:
        store.close()


def make_reconciler(
    store: SQLiteStore,
    omnigent: FakeOmnigent,
    sender: FakeSender,
    *,
    mode: str,
    host_id: str | None = "host_1",
) -> SessionReconciler:
    return SessionReconciler(
        store=store,
        omnigent=omnigent,  # type: ignore[arg-type]
        sender=sender,  # type: ignore[arg-type]
        space_name="spaces/s",
        host_id=host_id,
        discovery_mode=mode,
        discovery_label="omnigent.google_chat.enabled",
        lookback_hours=24,
        interval_seconds=10,
        recent_active_seconds=120,
        mirror_mode="concise",
        mention_unixname="owner",
        mention_on_root=True,
        mention_on_completion=True,
        meta_bot_actor_id="users/bot",
        max_session_chars=1000,
        poll_trigger=lambda: None,
    )


async def test_discovery_label_and_host_active_filters(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "bridge.sqlite3")
    await store.initialize()
    omnigent = FakeOmnigent()
    sender = FakeSender()
    try:
        label = make_reconciler(store, omnigent, sender, mode="label")
        opted_in = SessionSummary(
            id="in",
            title="In",
            status="idle",
            host_id="host_1",
            labels={"omnigent.google_chat.enabled": True},
        )
        opted_out = SessionSummary(
            id="out", title="Out", status="idle", labels={"omnigent.google_chat.enabled": False}
        )
        assert label._eligible(opted_in)
        assert not label._eligible(opted_out)
        assert label._eligible(replace(opted_in, labels={"omnigent.google_chat.enabled": "yes"}))
        assert not label._eligible(replace(opted_in, host_id="other"))
        assert not label._eligible(replace(opted_in, permission_level=1))
        assert not label._eligible(replace(opted_in, archived=True))

        host = make_reconciler(store, omnigent, sender, mode="host-active")
        recent = SessionSummary(
            id="recent",
            title="Recent",
            status="idle",
            host_id="host_1",
            updated_at=2**31,
        )
        assert host._eligible(recent)
        assert not host._eligible(replace(recent, host_id="other"))

        all_hosts = make_reconciler(
            store,
            omnigent,
            sender,
            mode="host-active",
            host_id=None,
        )
        assert all_hosts._eligible(replace(recent, host_id="other"))
        assert not all_hosts._eligible(
            replace(
                recent,
                host_id="other",
                labels={"omnigent.google_chat.enabled": False},
            )
        )
    finally:
        store.close()


async def test_mapping_requires_distinct_bot_identity(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "bridge.sqlite3")
    await store.initialize()
    omnigent = FakeOmnigent()
    sender = FakeSender()
    reconciler = make_reconciler(store, omnigent, sender, mode="label")
    session = SessionSummary(id="conv", title="Session", status="idle")
    try:
        await reconciler._create_mapping(session)
        mapping = await store.get_thread("conv")
        assert mapping is not None and mapping.thread_name == "threads/new"

        sender.root_actor_type = "HUMAN"
        with pytest.raises(MetaChatOutputError, match="human"):
            await reconciler._create_mapping(
                SessionSummary(id="conv_2", title="Other", status="idle")
            )
        sender.root_actor_type = "BOT"
        sender.root_actor_id = "users/other-bot"
        with pytest.raises(MetaChatOutputError, match="configured Meta Bot"):
            await reconciler._create_mapping(
                SessionSummary(id="conv_3", title="Wrong bot", status="idle")
            )
    finally:
        store.close()


async def test_recently_active_session_keeps_fast_polling(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "bridge.sqlite3")
    await store.initialize()
    reconciler = make_reconciler(store, FakeOmnigent(), FakeSender(), mode="label")
    try:
        reconciler._status_changed("conv", "running")
        reconciler._status_changed("conv", "idle")
        assert reconciler.has_active_sessions()
    finally:
        store.close()


async def test_reconcile_creates_eligible_mapping_then_archives_missing_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = SQLiteStore(tmp_path / "bridge.sqlite3")
    await store.initialize()
    omnigent = FakeOmnigent()
    sender = FakeSender()
    reconciler = make_reconciler(store, omnigent, sender, mode="label")
    started: list[str] = []
    monkeypatch.setattr(
        reconciler,
        "_ensure_mirror",
        lambda session_id, stop: started.append(session_id),
    )
    omnigent.sessions = [
        SessionSummary(
            id="conv",
            title="Session",
            status="running",
            host_id="host_1",
            labels={"omnigent.google_chat.enabled": True},
        )
    ]
    try:
        await reconciler.reconcile_once(asyncio.Event())
        assert started == ["conv"]
        assert (await store.get_thread("conv")) is not None
        omnigent.sessions = []
        await reconciler.reconcile_once(asyncio.Event())
        assert (await store.get_thread("conv")).state.value == "archived"  # type: ignore[union-attr]
        assert any(call["source_id"] == "session-archived" for call in sender.calls)
    finally:
        store.close()
