from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Literal

import pytest

from omnigent_watcher.command_source import CommandSpec
from omnigent_watcher.domain import (
    COMMAND_EVENT_KINDS,
    BatchState,
    EventDeliveryResult,
    EventDeliveryStatus,
    PollResult,
    SessionSnapshot,
    WatcherConfig,
)
from omnigent_watcher.repository import WatcherRepository
from omnigent_watcher.watcher import Watcher
from tests.support import FakeClock, FakeSessionService, command_poll

SUBJECT = "job:delivery-recovery"
SESSION = "session-1"
SPEC = CommandSpec(["test-probe"]).to_json()
Interruption = Literal["crash", "exception", "deferred"]


class WorkerCrash(BaseException):
    pass


class ChangingSource:
    name = "command"
    event_kinds = COMMAND_EVENT_KINDS

    def __init__(self) -> None:
        self.value = "A"
        self.unavailable = False

    def validate_subject(self, subject: str, spec: str | None) -> None:
        del subject, spec

    async def poll(
        self, subject: str, cursor: str | None = None, spec: str | None = None
    ) -> PollResult:
        del cursor, spec
        if self.unavailable:
            raise RuntimeError("source unavailable")
        return command_poll(subject, fingerprint=self.value)


class AcceptedThenInterrupted:
    def __init__(self, interruption: Interruption) -> None:
        self.interruption = interruption
        self.accepted: dict[str, str] = {}
        self.attempts: list[str] = []
        self.receipts_visible = False

    async def delivery_receipt(
        self, session_id: str, delivery_id: str
    ) -> EventDeliveryResult | None:
        if self.receipts_visible and delivery_id in self.accepted:
            return EventDeliveryResult(EventDeliveryStatus.ALREADY_ACCEPTED)
        return None

    async def deliver_message(
        self, session_id: str, delivery_id: str, content: str
    ) -> EventDeliveryResult:
        assert session_id == SESSION
        self.attempts.append(delivery_id)
        if delivery_id in self.accepted:
            assert content == self.accepted[delivery_id]
            return EventDeliveryResult(EventDeliveryStatus.ALREADY_ACCEPTED)
        self.accepted[delivery_id] = content
        if len(self.accepted) == 1:
            if self.interruption == "crash":
                raise WorkerCrash
            if self.interruption == "exception":
                raise RuntimeError("connection lost after acceptance")
            return EventDeliveryResult(EventDeliveryStatus.DEFERRED)
        return EventDeliveryResult(EventDeliveryStatus.ACCEPTED)


def _watcher(
    path: Path,
    source: ChangingSource,
    sessions: FakeSessionService,
    delivery: AcceptedThenInterrupted,
    clock: FakeClock,
) -> Watcher:
    return Watcher(
        WatcherRepository(path),
        (source,),
        sessions,
        delivery,
        clock=clock,
        config=WatcherConfig(
            batch_window_seconds=1,
            minimum_delivery_interval_seconds=1,
            poll_interval_override_seconds=10,
            delivery_retry_seconds=2,
        ),
    )


def _batch_value(repository: WatcherRepository, batch_id: str) -> str:
    with sqlite3.connect(repository.path) as connection:
        row = connection.execute(
            "SELECT fingerprint FROM batch_events WHERE batch_id = ?",
            (batch_id,),
        ).fetchone()
    assert row is not None
    return str(row[0])


@pytest.mark.parametrize("interruption", ["crash", "exception", "deferred"])
@pytest.mark.parametrize("latest_value", ["C", "A"])
async def test_retry_acknowledges_only_the_original_snapshot(
    tmp_path: Path,
    interruption: Interruption,
    latest_value: str,
) -> None:
    path = tmp_path / "watcher.sqlite3"
    clock = FakeClock()
    source = ChangingSource()
    sessions = FakeSessionService(SessionSnapshot(SESSION, {}))
    delivery = AcceptedThenInterrupted(interruption)
    watcher = _watcher(path, source, sessions, delivery, clock)
    subscription, _ = await watcher.subscribe(
        SESSION, SUBJECT, COMMAND_EVENT_KINDS, source_name=source.name, spec=SPEC
    )

    source.value = "B"
    clock.advance(10)
    await watcher.run_iteration()
    original = watcher.repository.open_batch_for_session(SESSION)
    assert original is not None
    clock.advance(2)
    if interruption == "crash":
        with pytest.raises(WorkerCrash):
            await watcher.run_iteration()
    else:
        await watcher.run_iteration()
    assert delivery.attempts == [original.batch_id]

    watcher = _watcher(path, source, sessions, delivery, clock)
    source.value = latest_value
    sessions.snapshots[SESSION] = SessionSnapshot(SESSION, {}, can_accept_input=False)
    clock.advance(10)
    await watcher.run_iteration()
    assert _batch_value(watcher.repository, original.batch_id) == "B"

    sessions.snapshots[SESSION] = SessionSnapshot(SESSION, {})
    delivery.receipts_visible = True
    clock.advance(3)
    await watcher.run_iteration()
    assert delivery.attempts == [original.batch_id]
    acknowledged = watcher.repository.batch(original.batch_id)
    assert acknowledged is not None and acknowledged.state is BatchState.DELIVERED
    with sqlite3.connect(path) as connection:
        handled = connection.execute(
            "SELECT fingerprint FROM subscription_events WHERE subscription_id = ?",
            (subscription.id,),
        ).fetchall()
    assert handled == [("B",)]

    subsequent = watcher.repository.open_batch_for_session(SESSION)
    assert subsequent is not None and subsequent.batch_id != original.batch_id
    assert _batch_value(watcher.repository, subsequent.batch_id) == latest_value
    clock.advance(2)
    await watcher.run_iteration()
    assert delivery.attempts == [original.batch_id, subsequent.batch_id]
    assert len(delivery.accepted) == 2

    clock.advance(12)
    await watcher.run_iteration()
    assert watcher.repository.open_batch_for_session(SESSION) is None
    assert len(delivery.attempts) == 2


async def test_source_outage_does_not_block_acknowledging_a_frozen_delivery(
    tmp_path: Path,
) -> None:
    clock = FakeClock()
    source = ChangingSource()
    delivery = AcceptedThenInterrupted("exception")
    watcher = _watcher(
        tmp_path / "watcher.sqlite3",
        source,
        FakeSessionService(SessionSnapshot(SESSION, {})),
        delivery,
        clock,
    )
    await watcher.subscribe(
        SESSION, SUBJECT, COMMAND_EVENT_KINDS, source_name=source.name, spec=SPEC
    )
    source.value = "B"
    clock.advance(10)
    await watcher.run_iteration()
    clock.advance(2)
    await watcher.run_iteration()
    original_id = delivery.attempts[0]

    source.unavailable = True
    delivery.receipts_visible = True
    clock.advance(12)
    await watcher.run_iteration()
    assert delivery.attempts == [original_id]
    acknowledged = watcher.repository.batch(original_id)
    assert acknowledged is not None and acknowledged.state is BatchState.DELIVERED
    assert watcher.repository.open_batch_for_session(SESSION) is None


def test_late_duplicate_acknowledgement_does_not_restore_an_old_value(tmp_path: Path) -> None:
    repository = WatcherRepository(tmp_path / "watcher.sqlite3")
    subscription, _ = repository.subscribe(
        SESSION,
        SUBJECT,
        COMMAND_EVENT_KINDS,
        command_poll(SUBJECT, fingerprint="A"),
        now=100,
        next_poll_at=110,
        spec=SPEC,
    )
    batch_ids: list[str] = []
    for value, now in (("B", 200), ("C", 300)):
        repository.apply_poll(
            command_poll(SUBJECT, fingerprint=value),
            now=now,
            next_poll_at=now + 10,
            batch_window_seconds=1,
        )
        batch = repository.open_batch_for_session(SESSION)
        assert batch is not None
        batch_ids.append(batch.batch_id)
        repository.prepare_batch(batch.batch_id, now=now + 1)
        repository.deliver_batch(batch.batch_id, now=now + 1)

    assert batch_ids[0] != batch_ids[1]
    repository.deliver_batch(batch_ids[0], now=400)
    with sqlite3.connect(repository.path) as connection:
        handled = connection.execute(
            "SELECT fingerprint FROM subscription_events WHERE subscription_id = ?",
            (subscription.id,),
        ).fetchall()
    assert handled == [("C",)]
    assert (
        repository.apply_poll(
            command_poll(SUBJECT, fingerprint="C"),
            now=500,
            next_poll_at=510,
            batch_window_seconds=1,
        )
        == 0
    )
    assert repository.open_batch_for_session(SESSION) is None
