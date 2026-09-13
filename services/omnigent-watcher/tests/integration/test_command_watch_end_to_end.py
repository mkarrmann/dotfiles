"""A generic watch, end to end through the real engine.

This is the test the whole generalization exists for: subscribe to something
that is not a diff, and get woken when it changes -- with no polling in the
subscriber's own context.
"""

from __future__ import annotations

import sqlite3
from collections import Counter
from pathlib import Path

import pytest

from omnigent_watcher.command_source import CommandSource, CommandSpec
from omnigent_watcher.domain import (
    COMMAND_EVENT_KINDS,
    EventKind,
    SessionSnapshot,
    WatcherConfig,
)
from omnigent_watcher.repository import WatcherRepository
from omnigent_watcher.source_models import fingerprint
from omnigent_watcher.watcher import SubscriptionError, Watcher
from tests.support import (
    FakeClock,
    FakeReviewSource,
    FakeSessionService,
    RecordingDeliveryService,
)

SUBJECT = "jk:presto/presto_batch:demo_knob"
ENV = {"PATH": "/usr/bin:/bin"}


def _spec(value_file: Path, interval: float = 60.0) -> str:
    return CommandSpec(["cat", str(value_file)], interval_seconds=interval).to_json()


def _watcher(
    tmp_path: Path, clock: FakeClock, delivery: RecordingDeliveryService
) -> tuple[WatcherRepository, Watcher]:
    repository = WatcherRepository(tmp_path / "watcher.sqlite3")
    return repository, Watcher(
        repository,
        (FakeReviewSource(), CommandSource(env=ENV)),
        FakeSessionService(SessionSnapshot(session_id="session-1", labels={})),
        delivery,
        clock=clock,
        config=WatcherConfig(
            batch_window_seconds=0.01,
            minimum_delivery_interval_seconds=0.01,
        ),
    )


@pytest.mark.asyncio
async def test_a_command_watch_baselines_quietly_then_wakes_on_change(
    tmp_path: Path,
) -> None:
    value = tmp_path / "knob"
    value.write_text("false\n")
    clock = FakeClock()
    delivery = RecordingDeliveryService()
    repository, watcher = _watcher(tmp_path, clock, delivery)

    subscription, created = await watcher.subscribe(
        "session-1",
        SUBJECT,
        COMMAND_EVENT_KINDS,
        source_name="command",
        spec=_spec(value),
    )
    assert created is True
    assert subscription.subject == SUBJECT

    # The value the subscriber already knows about must not wake it. This is
    # the trap that has actually bitten a hand-rolled watcher: baseline first.
    clock.advance(120)
    await watcher.run_iteration()
    assert delivery.calls == []

    value.write_text("1/10\n")
    clock.advance(120)
    await watcher.run_iteration()
    # The poll opens the batch; the batch flushes once its window elapses.
    clock.advance(60)
    await watcher.run_iteration()

    assert len(delivery.calls) == 1
    _session, _delivery_id, content = delivery.calls[0]
    assert SUBJECT in content
    # The wake must not carry diff wording for a knob.
    assert "diff review and CI" not in content
    assert "act on what changed" in content


@pytest.mark.asyncio
async def test_a_steady_value_never_wakes_however_often_it_is_polled(
    tmp_path: Path,
) -> None:
    value = tmp_path / "knob"
    value.write_text("false\n")
    clock = FakeClock()
    delivery = RecordingDeliveryService()
    _repository, watcher = _watcher(tmp_path, clock, delivery)

    await watcher.subscribe(
        "session-1",
        SUBJECT,
        COMMAND_EVENT_KINDS,
        source_name="command",
        spec=_spec(value),
    )
    for _ in range(10):
        clock.advance(120)
        await watcher.run_iteration()

    assert delivery.calls == []


@pytest.mark.asyncio
async def test_one_change_wakes_once_not_once_per_poll(tmp_path: Path) -> None:
    value = tmp_path / "knob"
    value.write_text("false\n")
    clock = FakeClock()
    delivery = RecordingDeliveryService()
    _repository, watcher = _watcher(tmp_path, clock, delivery)

    await watcher.subscribe(
        "session-1",
        SUBJECT,
        COMMAND_EVENT_KINDS,
        source_name="command",
        spec=_spec(value),
    )
    value.write_text("1/10\n")
    for _ in range(6):
        clock.advance(120)
        await watcher.run_iteration()
        clock.advance(60)
        await watcher.run_iteration()

    assert len(delivery.calls) == 1


@pytest.mark.parametrize("legacy_history", [False, True])
async def test_values_can_recur_after_delivery_and_worker_restart(
    tmp_path: Path, legacy_history: bool
) -> None:
    value = tmp_path / "state"
    value.write_text("A\n")
    clock = FakeClock()
    delivery = RecordingDeliveryService()
    repository, watcher = _watcher(tmp_path, clock, delivery)
    subscription, _ = await watcher.subscribe(
        "session-1", SUBJECT, COMMAND_EVENT_KINDS, source_name="command", spec=_spec(value)
    )

    for observed, expected in (("B", 1), ("A", 2), ("B", 3), ("B", 3), ("C", 4), ("C", 4)):
        value.write_text(observed + "\n")
        _repository, watcher = _watcher(tmp_path, clock, delivery)
        clock.advance(120)
        await watcher.run_iteration()
        clock.advance(60)
        await watcher.run_iteration()
        assert len(delivery.calls) == expected
        if legacy_history and expected == 1:
            with sqlite3.connect(repository.path) as connection:
                connection.execute(
                    "INSERT INTO subscription_events "
                    "(subscription_id, kind, external_id, fingerprint, handled_at) "
                    "VALUES (?, 'changed', 'value', ?, ?)",
                    (subscription.id, fingerprint("A"), subscription.baseline_at),
                )


async def test_return_to_acknowledged_value_before_flush_cancels_notification(
    tmp_path: Path,
) -> None:
    value = tmp_path / "state"
    value.write_text("A\n")
    clock = FakeClock()
    delivery = RecordingDeliveryService()
    repository, watcher = _watcher(tmp_path, clock, delivery)
    await watcher.subscribe(
        "session-1", SUBJECT, COMMAND_EVENT_KINDS, source_name="command", spec=_spec(value)
    )
    value.write_text("B\n")
    clock.advance(120)
    await watcher.run_iteration()
    assert repository.open_batch_for_session("session-1") is not None

    value.write_text("A\n")
    clock.advance(60)
    await watcher.run_iteration()
    assert delivery.calls == []
    assert repository.open_batch_for_session("session-1") is None

    value.write_text("B\n")
    clock.advance(120)
    await watcher.run_iteration()
    clock.advance(60)
    await watcher.run_iteration()
    assert len(delivery.calls) == 1


async def test_subscribers_keep_independent_acknowledged_values(tmp_path: Path) -> None:
    value = tmp_path / "state"
    value.write_text("A\n")
    clock = FakeClock()
    delivery = RecordingDeliveryService()
    _repository, watcher = _watcher(tmp_path, clock, delivery)
    watcher.sessions = FakeSessionService(
        SessionSnapshot("session-1", {}), SessionSnapshot("session-2", {})
    )
    await watcher.subscribe(
        "session-1", SUBJECT, COMMAND_EVENT_KINDS, source_name="command", spec=_spec(value)
    )
    value.write_text("B\n")
    clock.advance(120)
    await watcher.subscribe(
        "session-2", SUBJECT, COMMAND_EVENT_KINDS, source_name="command", spec=_spec(value)
    )
    clock.advance(60)
    await watcher.run_iteration()
    assert Counter(call[0] for call in delivery.calls) == {"session-1": 1}

    for observed, expected in (
        ("A", {"session-1": 2, "session-2": 1}),
        ("B", {"session-1": 3, "session-2": 2}),
    ):
        value.write_text(observed + "\n")
        clock.advance(120)
        await watcher.run_iteration()
        clock.advance(60)
        await watcher.run_iteration()
        assert Counter(call[0] for call in delivery.calls) == expected


@pytest.mark.asyncio
async def test_a_broken_command_backs_off_without_waking(tmp_path: Path) -> None:
    clock = FakeClock()
    delivery = RecordingDeliveryService()
    repository, watcher = _watcher(tmp_path, clock, delivery)
    missing = tmp_path / "does-not-exist"

    with pytest.raises(SubscriptionError, match="could not read the subject"):
        await watcher.subscribe(
            "session-1",
            SUBJECT,
            COMMAND_EVENT_KINDS,
            source_name="command",
            spec=_spec(missing),
        )
    assert repository.watch(SUBJECT) is None
    assert delivery.calls == []


@pytest.mark.asyncio
async def test_a_command_subject_cannot_be_subscribed_through_the_diff_source(
    tmp_path: Path,
) -> None:
    """The two surfaces stay separate: the diff source rejects a namespaced
    subject rather than trying to read D-less input as a diff."""
    clock = FakeClock()
    delivery = RecordingDeliveryService()
    _repository, watcher = _watcher(tmp_path, clock, delivery)

    with pytest.raises(SubscriptionError):
        await watcher.subscribe(
            "session-1",
            SUBJECT,
            frozenset({EventKind.CI_FAILURE}),
            source_name="command",
        )
