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
    DIFF_EVENT_KINDS,
    EventKind,
    SessionSnapshot,
    SubscriptionState,
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
        FakeReviewSource(),
        FakeSessionService(SessionSnapshot(session_id="session-1", labels={})),
        delivery,
        clock=clock,
        config=WatcherConfig(
            batch_window_seconds=0.01,
            minimum_delivery_interval_seconds=0.01,
        ),
        sources={"command": CommandSource(env=ENV)},
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


@pytest.mark.asyncio
async def test_unsubscribing_actually_stops_the_watch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cancelling the stored request is not enough to stop a watch.

    The poller claims any subject that still has an active subscriber, so a
    watch whose request was cancelled but whose subscription survived would go
    on polling and waking this session forever -- from a tool named
    unsubscribe. The proof is the silence after the value moves.
    """
    from omnigent_diff_watcher import mcp_server

    value = tmp_path / "knob"
    value.write_text("false\n")
    clock = FakeClock()
    delivery = RecordingDeliveryService()
    repository, watcher = _watcher(tmp_path, clock, delivery)

    spec = _spec(value)
    repository.request_watch(
        "session-1", "command", SUBJECT, COMMAND_EVENT_KINDS, spec=spec, now=1000.0
    )
    await watcher.subscribe(
        "session-1", SUBJECT, COMMAND_EVENT_KINDS, source_name="command", spec=spec
    )

    monkeypatch.setattr(mcp_server, "_watch_repository", lambda: (repository, "session-1"))
    mcp_server.watch_unsubscribe()

    subscription = repository.subscription("session-1", SUBJECT)
    assert subscription is not None
    assert subscription.state is SubscriptionState.RETIRED
    assert repository.active_watch_requests("session-1") == []

    # The value moves; nobody is woken, because nobody is subscribed.
    value.write_text("1/10\n")
    for _ in range(3):
        clock.advance(120)
        await watcher.run_iteration()
        clock.advance(60)
        await watcher.run_iteration()
    assert delivery.calls == []


@pytest.mark.asyncio
async def test_status_shows_the_command_a_watch_will_keep_running(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The argv outlives the turn that registered it, so it has to be legible
    from the status tool rather than only by reading the database."""
    from omnigent_diff_watcher import mcp_server

    value = tmp_path / "knob"
    value.write_text("false\n")
    repository, _watcher_unused = _watcher(tmp_path, FakeClock(), RecordingDeliveryService())
    repository.request_watch(
        "session-1",
        "command",
        SUBJECT,
        COMMAND_EVENT_KINDS,
        spec=CommandSpec(["cat", str(value)], r"(\d+)", 120.0).to_json(),
        now=1000.0,
    )

    monkeypatch.setattr(mcp_server, "_watch_repository", lambda: (repository, "session-1"))
    status = mcp_server.watch_status()

    assert SUBJECT in status
    assert f"cat {value}" in status
    assert "every 120s" in status
    assert r"(\d+)" in status


@pytest.mark.asyncio
async def test_unsubscribe_reaches_a_watch_whose_request_was_already_cancelled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An orphan -- cancelled request, surviving subscription -- must be reachable.

    That is the exact state an older build of the tool left behind, and it was
    hit for real in production. Deriving targets from the stored requests would
    find nothing here and leave the watch polling with no way to stop it from
    the tool; deriving them from the subscriptions, scoped by source, reaches
    it.
    """
    from omnigent_diff_watcher import mcp_server

    value = tmp_path / "knob"
    value.write_text("false\n")
    clock = FakeClock()
    delivery = RecordingDeliveryService()
    repository, watcher = _watcher(tmp_path, clock, delivery)

    spec = _spec(value)
    repository.request_watch(
        "session-1", "command", SUBJECT, COMMAND_EVENT_KINDS, spec=spec, now=1000.0
    )
    await watcher.subscribe(
        "session-1", SUBJECT, COMMAND_EVENT_KINDS, source_name="command", spec=spec
    )
    # Orphan it: the request goes, the subscription stays.
    assert repository.cancel_watch_requests("session-1", now=1100.0) == 1
    orphan = repository.subscription("session-1", SUBJECT)
    assert orphan is not None and orphan.state is SubscriptionState.ACTIVE

    monkeypatch.setattr(mcp_server, "_watch_repository", lambda: (repository, "session-1"))
    mcp_server.watch_unsubscribe()

    stopped = repository.subscription("session-1", SUBJECT)
    assert stopped is not None
    assert stopped.state is SubscriptionState.RETIRED


@pytest.mark.asyncio
async def test_unsubscribe_never_reaches_a_diff_watch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Scoping by source is the only thing keeping these surfaces apart."""
    from omnigent_diff_watcher import mcp_server
    from tests.support import fixture, subscribe_snapshot

    clock = FakeClock()
    delivery = RecordingDeliveryService()
    repository, _watcher_unused = _watcher(tmp_path, clock, delivery)

    diff = fixture("active")
    subscribe_snapshot(
        repository,
        "session-1",
        diff.subject,
        DIFF_EVENT_KINDS,
        diff,
        now=1000.0,
        next_poll_at=1060.0,
    )

    monkeypatch.setattr(mcp_server, "_watch_repository", lambda: (repository, "session-1"))
    mcp_server.watch_unsubscribe()

    survivor = repository.subscription("session-1", diff.subject)
    assert survivor is not None
    assert survivor.state is SubscriptionState.ACTIVE
