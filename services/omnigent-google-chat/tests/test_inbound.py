from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from omnigent_google_chat.inbound import GoogleChatPoller, InboundProcessor
from omnigent_google_chat.meta_chat import MetaChatError
from omnigent_google_chat.models import (
    GoogleChatMessage,
    InboundState,
    MappingState,
    SentMessage,
    SessionSummary,
)
from omnigent_google_chat.omnigent import (
    OmnigentAmbiguousDeliveryError,
    OmnigentPreDeliveryError,
    OmnigentRejectedError,
    RunnerUnavailableError,
)
from omnigent_google_chat.store import SQLiteStore
from omnigent_google_chat.text import text_sha256


class FakeOmnigent:
    def __init__(self) -> None:
        self.messages: list[tuple[str, str]] = []
        self.interrupts: list[str] = []
        self.recoveries: list[str] = []
        self.outcomes: list[BaseException | str | None] = []
        self.get_error: BaseException | None = None
        self.recovery_error: BaseException | None = None

    async def submit_message(
        self,
        session_id: str,
        text: str,
        *,
        source_message_name: str | None = None,
    ) -> str | None:
        assert source_message_name is not None
        self.messages.append((session_id, text))
        if self.outcomes:
            outcome = self.outcomes.pop(0)
            if isinstance(outcome, BaseException):
                raise outcome
            return outcome
        return "item_phone"

    async def interrupt(self, session_id: str) -> None:
        self.interrupts.append(session_id)

    async def get_session(self, session_id: str) -> SessionSummary:
        if self.get_error:
            raise self.get_error
        return SessionSummary(
            id=session_id,
            title="Session",
            status="running",
            runner_online=True,
        )

    async def recover_bound_runner(self, session_id: str) -> str:
        self.recoveries.append(session_id)
        if self.recovery_error:
            raise self.recovery_error
        return "runner"


class FakeSender:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.error: BaseException | None = None

    async def send(self, **kwargs: Any) -> list[SentMessage]:
        if self.error:
            raise self.error
        self.calls.append(kwargs)
        return [SentMessage(name="messages/notice", thread_name=kwargs["thread_name"])]


def message(
    name: str = "messages/phone",
    *,
    text: str = "hello",
    actor: str = "users/human",
    actor_type: str = "HUMAN",
    thread: str = "threads/mapped",
    created: str = "2026-01-01T00:00:00Z",
    attachments: bool = False,
    raw: dict[str, object] | None = None,
) -> GoogleChatMessage:
    return GoogleChatMessage(
        name=name,
        space_name="spaces/s",
        thread_name=thread,
        actor_id=actor,
        actor_type=actor_type,
        create_time=created,
        text=text,
        has_attachments=attachments,
        raw=raw or {},
    )


async def setup_processor(
    tmp_path: Path,
) -> tuple[SQLiteStore, FakeOmnigent, FakeSender, InboundProcessor]:
    store = SQLiteStore(tmp_path / "bridge.sqlite3")
    await store.initialize()
    await store.create_thread("conv", "spaces/s", "threads/mapped", "messages/root", "Session")
    omnigent = FakeOmnigent()
    sender = FakeSender()
    processor = InboundProcessor(
        store=store,
        omnigent=omnigent,  # type: ignore[arg-type]
        sender=sender,  # type: ignore[arg-type]
        space_name="spaces/s",
        allowed_actor_id="users/human",
        meta_bot_actor_id="users/bot",
        max_input_chars=100,
        pre_delivery_attempts=3,
    )
    return store, omnigent, sender, processor


async def inbound_state(store: SQLiteStore, value: GoogleChatMessage) -> InboundState | None:
    claim = await store.claim_inbound(
        message_name=value.name,
        thread_name=value.thread_name,
        actor_id=value.actor_id,
        created_at_google=value.create_time,
        text_sha256=text_sha256(value.text),
    )
    return claim.state


async def test_human_reply_submits_once_and_tracks_item_for_echo_suppression(
    tmp_path: Path,
) -> None:
    store, omnigent, _, processor = await setup_processor(tmp_path)
    try:
        value = message()
        await processor.process(value)
        await processor.process(value)
        assert omnigent.messages == [("conv", "hello")]
        assert await inbound_state(store, value) is InboundState.SUBMITTED
        assert await store.is_chat_origin_item("item_phone")
    finally:
        store.close()


@pytest.mark.parametrize(
    "value",
    [
        message(name="messages/wrong", actor="users/other"),
        message(name="messages/bot", actor="users/bot", actor_type="BOT"),
        message(name="messages/thread", thread="threads/unmapped"),
        message(name="messages/attachment", attachments=True),
        message(name="messages/empty", text="  "),
        message(name="messages/prefix", text="[Omnigent] output"),
        message(name="messages/oversized", text="x" * 101),
        message(
            name="messages/forwarded",
            raw={"forwardedMessageMetadata": {"originalSender": "users/other"}},
        ),
    ],
)
async def test_unauthorized_or_unsupported_input_never_contacts_omnigent(
    tmp_path: Path, value: GoogleChatMessage
) -> None:
    store, omnigent, _, processor = await setup_processor(tmp_path)
    try:
        await processor.process(value)
        assert omnigent.messages == []
        assert await inbound_state(store, value) is InboundState.REJECTED
    finally:
        store.close()


async def test_attachment_and_oversized_input_receive_safe_explanations(
    tmp_path: Path,
) -> None:
    store, omnigent, sender, processor = await setup_processor(tmp_path)
    try:
        await processor.process(message(name="messages/attachment", attachments=True))
        assert "Attachments are not supported" in sender.calls[-1]["text"]
        await processor.process(message(name="messages/oversized", text="x" * 101))
        assert "too long" in sender.calls[-1]["text"]
        assert omnigent.messages == []
    finally:
        store.close()


async def test_ambiguous_delivery_is_not_retried(tmp_path: Path) -> None:
    store, omnigent, sender, processor = await setup_processor(tmp_path)
    try:
        omnigent.outcomes = [OmnigentAmbiguousDeliveryError("timeout")]
        value = message()
        await processor.process(value)
        assert len(omnigent.messages) == 1
        assert await inbound_state(store, value) is InboundState.AMBIGUOUS
        assert "will not be retried" in sender.calls[-1]["text"]
    finally:
        store.close()


async def test_preconnect_failure_retries_then_submits(tmp_path: Path) -> None:
    store, omnigent, _, processor = await setup_processor(tmp_path)
    try:
        omnigent.outcomes = [OmnigentPreDeliveryError("connect"), "item_ok"]
        value = message()
        await processor.process(value)
        assert len(omnigent.messages) == 2
        assert await inbound_state(store, value) is InboundState.SUBMITTED
    finally:
        store.close()


async def test_exhausted_preconnect_failures_are_definitively_rejected(tmp_path: Path) -> None:
    store, omnigent, _, processor = await setup_processor(tmp_path)
    try:
        omnigent.outcomes = [OmnigentPreDeliveryError("connect")] * 3
        value = message()
        await processor.process(value)
        assert len(omnigent.messages) == 3
        assert await inbound_state(store, value) is InboundState.REJECTED
    finally:
        store.close()


async def test_definitive_rejection_is_not_retried(tmp_path: Path) -> None:
    store, omnigent, _, processor = await setup_processor(tmp_path)
    try:
        omnigent.outcomes = [OmnigentRejectedError("bad request")]
        value = message()
        await processor.process(value)
        assert len(omnigent.messages) == 1
        assert await inbound_state(store, value) is InboundState.REJECTED
    finally:
        store.close()


async def test_runner_unavailable_recovers_once_then_retries(tmp_path: Path) -> None:
    store, omnigent, _, processor = await setup_processor(tmp_path)
    try:
        omnigent.outcomes = [RunnerUnavailableError("offline"), "item_ok"]
        value = message()
        await processor.process(value)
        assert omnigent.recoveries == ["conv"]
        assert len(omnigent.messages) == 2
        assert await inbound_state(store, value) is InboundState.SUBMITTED
    finally:
        store.close()


async def test_runner_recovery_failure_rejects_without_resubmitting(tmp_path: Path) -> None:
    store, omnigent, _, processor = await setup_processor(tmp_path)
    try:
        omnigent.outcomes = [RunnerUnavailableError("offline")]
        omnigent.recovery_error = RuntimeError("host offline")
        value = message()
        await processor.process(value)
        assert len(omnigent.messages) == 1
        assert await inbound_state(store, value) is InboundState.REJECTED
    finally:
        store.close()


async def test_status_stop_detach_and_unknown_commands(tmp_path: Path) -> None:
    store, omnigent, sender, processor = await setup_processor(tmp_path)
    try:
        status = message(name="messages/status", text="!status")
        await processor.process(status)
        assert sender.calls[-1]["source_id"].endswith(":status")

        stop = message(name="messages/stop", text="!stop")
        await processor.process(stop)
        assert omnigent.interrupts == ["conv"]

        unknown = message(name="messages/unknown", text="!approve")
        await processor.process(unknown)
        assert await inbound_state(store, unknown) is InboundState.REJECTED

        detach = message(name="messages/detach", text="!detach")
        await processor.process(detach)
        assert (await store.get_thread("conv")).state is MappingState.DETACHED  # type: ignore[union-attr]
        after = message(name="messages/after")
        await processor.process(after)
        assert omnigent.messages == []
    finally:
        store.close()


async def test_status_read_failure_is_rejected_and_notice_failure_is_best_effort(
    tmp_path: Path,
) -> None:
    store, omnigent, sender, processor = await setup_processor(tmp_path)
    try:
        omnigent.get_error = RuntimeError("unavailable")
        value = message(text="!status")
        await processor.process(value)
        assert await inbound_state(store, value) is InboundState.REJECTED
        assert sender.calls[-1]["source_id"].endswith(":status-error")

        sender.error = MetaChatError("chat unavailable")
        ambiguous = message(name="messages/ambiguous-quiet")
        omnigent.outcomes = [OmnigentAmbiguousDeliveryError("timeout")]
        await processor.process(ambiguous)
        assert await inbound_state(store, ambiguous) is InboundState.AMBIGUOUS
    finally:
        store.close()


class FakeChat:
    def __init__(self) -> None:
        self.messages: list[GoogleChatMessage] = []
        self.error: BaseException | None = None
        self.created_after: list[str] = []

    async def list_all_messages(self, *, created_after: str) -> list[GoogleChatMessage]:
        self.created_after.append(created_after)
        if self.error:
            raise self.error
        return self.messages


class RecordingProcessor:
    def __init__(self) -> None:
        self.names: list[str] = []
        self.error: BaseException | None = None

    async def process(self, value: GoogleChatMessage) -> None:
        if self.error:
            raise self.error
        self.names.append(value.name)


async def test_poller_install_floor_overlap_and_old_unseen_message(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "bridge.sqlite3")
    await store.initialize()
    chat = FakeChat()
    processor = RecordingProcessor()
    poller = GoogleChatPoller(
        client=chat,  # type: ignore[arg-type]
        store=store,
        processor=processor,  # type: ignore[arg-type]
        active_poll_seconds=10,
        idle_poll_seconds=30,
        overlap_seconds=120,
        health_stale_seconds=120,
        inbound_retention_seconds=30 * 24 * 60 * 60,
        is_active=lambda: False,
    )
    try:
        await poller.poll_once()
        floor = await store.get_state("gchat_poll_floor")
        assert floor is not None
        floor_dt = datetime.fromisoformat(floor.replace("Z", "+00:00"))
        chat.messages = [
            message(
                name="messages/before-install",
                created=(floor_dt - timedelta(seconds=1)).isoformat().replace("+00:00", "Z"),
            ),
            message(
                name="messages/new",
                created=(floor_dt + timedelta(seconds=1)).isoformat().replace("+00:00", "Z"),
            ),
        ]
        await poller.poll_once()
        assert processor.names == ["messages/new"]

        cursor = await store.get_poll_cursor()
        assert cursor is not None
        old_unseen_time = (floor_dt + timedelta(milliseconds=500)).astimezone(UTC)
        chat.messages = [
            message(
                name="messages/old-unseen",
                created=old_unseen_time.isoformat().replace("+00:00", "Z"),
            )
        ]
        await poller.poll_once()
        assert processor.names[-1] == "messages/old-unseen"
        assert chat.created_after[-1] < cursor[0]
    finally:
        store.close()


async def test_poller_does_not_advance_cursor_when_processing_fails(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "bridge.sqlite3")
    await store.initialize()
    now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    await store.set_state("gchat_poll_floor", now)
    await store.set_poll_cursor((now, ""))
    chat = FakeChat()
    chat.messages = [message(created=now)]
    processor = RecordingProcessor()
    processor.error = RuntimeError("database unavailable")
    poller = GoogleChatPoller(
        client=chat,  # type: ignore[arg-type]
        store=store,
        processor=processor,  # type: ignore[arg-type]
        active_poll_seconds=10,
        idle_poll_seconds=30,
        overlap_seconds=120,
        health_stale_seconds=120,
        inbound_retention_seconds=30 * 24 * 60 * 60,
        is_active=lambda: False,
    )
    try:
        with pytest.raises(RuntimeError):
            await poller.poll_once()
        assert await store.get_poll_cursor() == (now, "")
    finally:
        store.close()


async def test_poller_backs_off_then_recovers_without_overlapping(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = SQLiteStore(tmp_path / "bridge.sqlite3")
    await store.initialize()
    chat = FakeChat()
    processor = RecordingProcessor()
    poller = GoogleChatPoller(
        client=chat,  # type: ignore[arg-type]
        store=store,
        processor=processor,  # type: ignore[arg-type]
        active_poll_seconds=10,
        idle_poll_seconds=30,
        overlap_seconds=120,
        health_stale_seconds=120,
        inbound_retention_seconds=30 * 24 * 60 * 60,
        is_active=lambda: False,
    )
    original_list = chat.list_all_messages
    calls = 0

    async def flaky_list(*, created_after: str) -> list[GoogleChatMessage]:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("temporary list failure")
        return await original_list(created_after=created_after)

    intervals: list[float] = []
    stop = asyncio.Event()

    async def fake_wait(stop_event: asyncio.Event, seconds: float) -> None:
        intervals.append(seconds)
        if len(intervals) == 2:
            stop.set()

    monkeypatch.setattr(chat, "list_all_messages", flaky_list)
    monkeypatch.setattr(poller, "_wait", fake_wait)
    try:
        await poller.run(stop)
        assert calls == 2
        assert intervals[0] >= 2
        assert intervals[1] >= 30
    finally:
        store.close()


async def test_immediate_trigger_wakes_wait_without_sleeping(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "bridge.sqlite3")
    await store.initialize()
    poller = GoogleChatPoller(
        client=FakeChat(),  # type: ignore[arg-type]
        store=store,
        processor=RecordingProcessor(),  # type: ignore[arg-type]
        active_poll_seconds=10,
        idle_poll_seconds=30,
        overlap_seconds=120,
        health_stale_seconds=120,
        inbound_retention_seconds=30 * 24 * 60 * 60,
        is_active=lambda: False,
    )
    stop = asyncio.Event()
    try:
        poller.trigger()
        await poller._wait(stop, 60)
        assert not poller._immediate.is_set()
    finally:
        store.close()
