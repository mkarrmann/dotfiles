from __future__ import annotations

import sqlite3
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from omnigent_google_chat.models import InboundState, MappingState, OutboundState
from omnigent_google_chat.store import (
    SpaceMismatchError,
    SQLiteStore,
    StoreLockedError,
)


@pytest.fixture
async def store(tmp_path: Path) -> AsyncIterator[SQLiteStore]:
    instance = SQLiteStore(tmp_path / "bridge.sqlite3")
    await instance.initialize()
    try:
        yield instance
    finally:
        instance.close()


async def test_store_uses_wal_and_private_files(store: SQLiteStore) -> None:
    with sqlite3.connect(store.path) as db:
        mode = db.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode.lower() == "wal"
    assert store.path.stat().st_mode & 0o777 == 0o600


async def test_single_process_lock(tmp_path: Path) -> None:
    first = SQLiteStore(tmp_path / "bridge.sqlite3")
    second = SQLiteStore(tmp_path / "bridge.sqlite3")
    await first.initialize()
    try:
        with pytest.raises(StoreLockedError):
            await second.initialize()
    finally:
        first.close()
        second.close()


async def test_space_binding_fails_closed(store: SQLiteStore) -> None:
    await store.bind_space("spaces/one")
    await store.bind_space("spaces/one")
    with pytest.raises(SpaceMismatchError):
        await store.bind_space("spaces/two")


async def test_unique_mapping_and_state(store: SQLiteStore) -> None:
    await store.create_thread("conv_1", "spaces/one", "threads/one", "messages/root", "One")
    mapping = await store.get_thread_by_name("threads/one")
    assert mapping is not None
    assert mapping.omnigent_session_id == "conv_1"
    await store.set_thread_state("conv_1", MappingState.DETACHED)
    assert (await store.get_thread("conv_1")).state is MappingState.DETACHED  # type: ignore[union-attr]


async def test_overlapping_inbound_claim_and_changed_hash(store: SQLiteStore) -> None:
    first = await store.claim_inbound(
        message_name="messages/1",
        thread_name="threads/1",
        actor_id="users/human",
        created_at_google="2026-01-01T00:00:00Z",
        text_sha256="aaa",
    )
    duplicate = await store.claim_inbound(
        message_name="messages/1",
        thread_name="threads/1",
        actor_id="users/human",
        created_at_google="2026-01-01T00:00:00Z",
        text_sha256="aaa",
    )
    changed = await store.claim_inbound(
        message_name="messages/1",
        thread_name="threads/1",
        actor_id="users/human",
        created_at_google="2026-01-01T00:00:00Z",
        text_sha256="bbb",
    )
    assert first.claimed
    assert not duplicate.claimed and not duplicate.changed_content
    assert not changed.claimed and changed.changed_content


async def test_stale_dispatching_becomes_ambiguous(tmp_path: Path) -> None:
    path = tmp_path / "bridge.sqlite3"
    first = SQLiteStore(path)
    await first.initialize()
    await first.claim_inbound(
        message_name="messages/1",
        thread_name="threads/1",
        actor_id="users/human",
        created_at_google="2026-01-01T00:00:00Z",
        text_sha256="aaa",
    )
    await first.set_inbound_state("messages/1", InboundState.DISPATCHING)
    first.close()

    second = SQLiteStore(path)
    await second.initialize()
    try:
        duplicate = await second.claim_inbound(
            message_name="messages/1",
            thread_name="threads/1",
            actor_id="users/human",
            created_at_google="2026-01-01T00:00:00Z",
            text_sha256="aaa",
        )
        assert duplicate.state is InboundState.AMBIGUOUS
        assert not duplicate.claimed
        assert await second.list_restart_ambiguous() == [("messages/1", "threads/1")]
        await second.mark_restart_ambiguous_notified("messages/1")
        assert await second.list_restart_ambiguous() == []
    finally:
        second.close()


async def test_status_transition_generation_is_durable_and_changes_per_cycle(
    store: SQLiteStore,
) -> None:
    assert await store.observe_session_status("conv", "running") == "status-transition-1"
    assert await store.observe_session_status("conv", "running") == "status-transition-1"
    assert await store.observe_session_status("conv", "idle") == "status-transition-2"
    assert await store.observe_session_status("conv", "idle") == "status-transition-2"
    assert await store.observe_session_status("conv", "running") == "status-transition-3"
    assert await store.observe_session_status("conv", "idle") == "status-transition-4"


async def test_outbound_retry_preserves_request_and_message(store: SQLiteStore) -> None:
    state = await store.prepare_outbound(
        request_id="request-1",
        session_id="conv_1",
        source_kind="item",
        source_id="item_1",
        part_index=0,
        char_count=4,
    )
    assert state is OutboundState.PENDING
    await store.mark_outbound_attempt("request-1")
    await store.mark_outbound_failed("request-1", "temporary")
    state = await store.prepare_outbound(
        request_id="request-1",
        session_id="conv_1",
        source_kind="item",
        source_id="item_1",
        part_index=0,
        char_count=4,
    )
    assert state is OutboundState.FAILED
    await store.mark_outbound_sent("request-1", "messages/1")
    assert await store.get_outbound_message_name("request-1") == "messages/1"
    assert await store.is_outbound_message("messages/1")


async def test_poll_cursor_round_trip(store: SQLiteStore) -> None:
    assert await store.get_poll_cursor() is None
    await store.set_poll_cursor(("2026-01-01T00:00:00Z", "messages/1"))
    assert await store.get_poll_cursor() == (
        "2026-01-01T00:00:00Z",
        "messages/1",
    )
