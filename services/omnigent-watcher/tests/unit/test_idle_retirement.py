"""Ageing out a watch that has stopped saying anything.

Terminal retirement covers a diff that lands, is abandoned, or is reverted.
Nothing covered the common case: a diff that simply sits in review while the
session that asked about it is long gone. Omnigent rarely closes a session, so
the liveness sweep never fires either, and the watch polls forever. Production
had 26 of 29 watches older than a week, the oldest 24 days, before this existed.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from omnigent_watcher.command_source import SOURCE_NAME as COMMAND_SOURCE_NAME
from omnigent_watcher.command_source import CommandSpec
from omnigent_watcher.domain import (
    COMMAND_EVENT_KINDS,
    DIFF_EVENT_KINDS,
    SubscriptionState,
)
from omnigent_watcher.phabricator_source import SOURCE_NAME as PHABRICATOR_SOURCE_NAME
from omnigent_watcher.repository import WatcherRepository
from tests.support import command_poll, fixture, subscribe_snapshot

SESSION = "conv_test"
KNOB = "jk:presto/presto_batch:demo_knob"
DAY = 24 * 60 * 60
WEEK = 7 * DAY


def _repository(tmp_path: Path) -> WatcherRepository:
    return WatcherRepository(tmp_path / "watcher.sqlite3")


def _watch(repository: WatcherRepository, *, now: float) -> str:
    diff = fixture("active")
    repository.request_watch(
        SESSION, PHABRICATOR_SOURCE_NAME, diff.subject, DIFF_EVENT_KINDS, spec=None, now=now
    )
    subscribe_snapshot(
        repository,
        SESSION,
        diff.subject,
        DIFF_EVENT_KINDS,
        diff,
        now=now,
        next_poll_at=now + 60.0,
    )
    return str(diff.subject)


def _command_watch(repository: WatcherRepository, *, now: float) -> str:
    spec = CommandSpec(["true"], None, 60.0).to_json()
    repository.request_watch(
        SESSION, COMMAND_SOURCE_NAME, KNOB, COMMAND_EVENT_KINDS, spec=spec, now=now
    )
    repository.subscribe(
        SESSION,
        KNOB,
        COMMAND_EVENT_KINDS,
        command_poll(KNOB),
        now=now,
        next_poll_at=now + 60.0,
        max_active_subjects=100,
        spec=spec,
    )
    return KNOB


def _active_requests(repository: WatcherRepository) -> set[str]:
    return {subject for _s, _src, subject, _spec, _k in repository.active_watch_requests(SESSION)}


def _record_delivery(path: Path, subscription_id: int, at: float) -> None:
    """Stamp ``last_delivery_at`` without staging a whole batch flush.

    The column is only ever written by ``deliver_batch``; driving a real
    delivery here would test the batching machinery rather than the age-out.
    """
    with sqlite3.connect(path) as connection:
        connection.execute(
            "UPDATE subscriptions SET last_delivery_at = ? WHERE id = ?",
            (at, subscription_id),
        )


def _claim(repository: WatcherRepository, now: float) -> list[str]:
    claimed = repository.claim_due_watches(now=now, owner="test", lease_seconds=30.0, limit=10)
    return [watch.subject for watch in claimed]


def test_a_watch_quiet_for_longer_than_the_limit_is_retired(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    subject = _watch(repository, now=1000.0)

    retired = repository.retire_idle_watches(now=1000.0 + WEEK + 1, max_idle_seconds=WEEK)

    assert retired == [(SESSION, subject)]
    row = repository.subscription(SESSION, subject)
    assert row is not None
    assert row.state is SubscriptionState.RETIRED
    assert row.retired_reason == "idle"


def test_retiring_also_cancels_the_request_so_reconcile_cannot_rebind(tmp_path: Path) -> None:
    """The load-bearing half.

    Retiring only the subscription leaves an active request, which the
    reconcile loop re-binds within seconds. The rebound subscription is created
    fresh, so it would age out and come straight back, forever.
    """
    repository = _repository(tmp_path)
    subject = _watch(repository, now=1000.0)
    assert _active_requests(repository) == {subject}

    repository.retire_idle_watches(now=1000.0 + WEEK + 1, max_idle_seconds=WEEK)

    assert _active_requests(repository) == set()


def test_a_recent_delivery_keeps_the_watch_alive(tmp_path: Path) -> None:
    """Idleness is measured from the last delivery, not from creation.

    A month-old diff still under active review must keep its watch; a hard TTL
    on creation would retire exactly the watches that are working.
    """
    repository = _repository(tmp_path)
    subject = _watch(repository, now=1000.0)
    row = repository.subscription(SESSION, subject)
    assert row is not None
    _record_delivery(tmp_path / "watcher.sqlite3", row.id, 1000.0 + 3 * WEEK)

    retired = repository.retire_idle_watches(now=1000.0 + 3 * WEEK + DAY, max_idle_seconds=WEEK)

    assert retired == []
    survivor = repository.subscription(SESSION, subject)
    assert survivor is not None
    assert survivor.state is SubscriptionState.ACTIVE
    assert _active_requests(repository) == {subject}


def test_a_fresh_watch_is_left_alone(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    subject = _watch(repository, now=1000.0)

    assert repository.retire_idle_watches(now=1000.0 + DAY, max_idle_seconds=WEEK) == []
    survivor = repository.subscription(SESSION, subject)
    assert survivor is not None
    assert survivor.state is SubscriptionState.ACTIVE


def test_a_retired_watch_stops_being_polled(tmp_path: Path) -> None:
    """Retirement has to stop the work, not just relabel a row."""
    repository = _repository(tmp_path)
    subject = _watch(repository, now=1000.0)
    due = 1000.0 + WEEK + 1
    assert _claim(repository, due) == [subject]

    repository.retire_idle_watches(now=due, max_idle_seconds=WEEK)

    assert _claim(repository, due + 1) == []


def test_resubscribing_after_an_age_out_restarts_the_clock(tmp_path: Path) -> None:
    """Re-subscribing must survive the next sweep.

    Resurrecting a retired subscription resets baseline_at and clears
    last_delivery_at, but leaves created_at at the original value -- so
    measuring idleness from created_at retired the watch again on the very next
    tick, seconds after the agent was told it was watching. baseline_at is the
    right fallback precisely because it is reset on resurrection.
    """
    repository = _repository(tmp_path)
    subject = _watch(repository, now=1000.0)
    aged_out = 1000.0 + WEEK + 1
    repository.retire_idle_watches(now=aged_out, max_idle_seconds=WEEK)

    _watch(repository, now=aged_out)

    assert repository.retire_idle_watches(now=aged_out + DAY, max_idle_seconds=WEEK) == []
    survivor = repository.subscription(SESSION, subject)
    assert survivor is not None
    assert survivor.state is SubscriptionState.ACTIVE
    assert _active_requests(repository) == {subject}


def test_the_age_out_is_source_agnostic(tmp_path: Path) -> None:
    """A command watch on a rollout that shipped weeks ago is just as stale.

    The rule is about silence, not about what is being watched, so scoping it
    to diffs would leave the generic surface with no reaping at all.
    """
    repository = _repository(tmp_path)
    diff_subject = _watch(repository, now=1000.0)
    knob = _command_watch(repository, now=1000.0)

    retired = repository.retire_idle_watches(now=1000.0 + WEEK + 1, max_idle_seconds=WEEK)

    assert sorted(retired) == sorted([(SESSION, diff_subject), (SESSION, knob)])
    assert _active_requests(repository) == set()
