"""A generic watch, end to end through the real engine.

This is the test the whole generalization exists for: subscribe to something
that is not a diff, and get woken when it changes -- with no polling in the
subscriber's own context.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from omnigent_diff_watcher.command_source import CommandSource, CommandSpec
from omnigent_diff_watcher.domain import (
    COMMAND_EVENT_KINDS,
    EventKind,
    SessionSnapshot,
    WatcherConfig,
)
from omnigent_diff_watcher.repository import WatcherRepository
from omnigent_diff_watcher.watcher import DiffWatcher, SubscriptionError
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
) -> tuple[WatcherRepository, DiffWatcher]:
    repository = WatcherRepository(tmp_path / "watcher.sqlite3")
    return repository, DiffWatcher(
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
