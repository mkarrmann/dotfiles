"""Stopping and listing watches, and the source scoping that separates them.

Both surfaces share this code, so ``sources`` is the only thing keeping a diff
unsubscribe away from a generic watch. Every bug these cover has happened.
"""

from __future__ import annotations

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
from omnigent_watcher.watch_api import GENERIC_SOURCES, cancel_watches, describe_watches
from tests.support import apply_snapshot, command_poll, fixture, subscribe_snapshot

SESSION = "conv_test"
KNOB = "jk:presto/presto_batch:demo_knob"
DIFF_SOURCES = frozenset({PHABRICATOR_SOURCE_NAME})


def _spec(interval: float = 60.0) -> str:
    return CommandSpec(["true"], None, interval).to_json()


def _repository(tmp_path: Path) -> WatcherRepository:
    return WatcherRepository(tmp_path / "watcher.sqlite3")


def _generic_watch(repository: WatcherRepository, *, request: bool = True) -> None:
    if request:
        repository.request_watch(
            SESSION, COMMAND_SOURCE_NAME, KNOB, COMMAND_EVENT_KINDS, spec=_spec(), now=1000.0
        )
    repository.subscribe(
        SESSION,
        KNOB,
        COMMAND_EVENT_KINDS,
        command_poll(KNOB),
        now=1000.0,
        next_poll_at=1060.0,
        max_active_subjects=100,
        spec=_spec(),
    )


def _watch(repository: WatcherRepository) -> str:
    diff = fixture("active")
    repository.request_watch(
        SESSION, PHABRICATOR_SOURCE_NAME, diff.subject, DIFF_EVENT_KINDS, spec=None, now=1000.0
    )
    subscribe_snapshot(
        repository,
        SESSION,
        diff.subject,
        DIFF_EVENT_KINDS,
        diff,
        now=1000.0,
        next_poll_at=1060.0,
    )
    return str(diff.subject)


def test_cancelling_generic_watches_never_reaches_a_diff(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    subject = _watch(repository)
    _generic_watch(repository)

    cancel_watches(repository, SESSION, sources=GENERIC_SOURCES, now=2000.0)

    survivor = repository.subscription(SESSION, subject)
    assert survivor is not None and survivor.state is SubscriptionState.ACTIVE
    stopped = repository.subscription(SESSION, KNOB)
    assert stopped is not None and stopped.state is SubscriptionState.RETIRED


def test_cancelling_watches_never_reaches_a_generic_one(tmp_path: Path) -> None:
    """The mirror case, which only became reachable when both surfaces
    started writing to the same table."""
    repository = _repository(tmp_path)
    subject = _watch(repository)
    _generic_watch(repository)

    cancel_watches(repository, SESSION, sources=DIFF_SOURCES, now=2000.0)

    stopped = repository.subscription(SESSION, subject)
    assert stopped is not None and stopped.state is SubscriptionState.RETIRED
    survivor = repository.subscription(SESSION, KNOB)
    assert survivor is not None and survivor.state is SubscriptionState.ACTIVE
    # And its request survives, or the sidecar would quietly stop re-binding it.
    assert [row[2] for row in repository.active_watch_requests(SESSION)] == [KNOB]


def test_cancelling_reaches_a_watch_whose_request_was_already_cancelled(
    tmp_path: Path,
) -> None:
    """An orphan -- cancelled request, surviving subscription -- must be reachable.

    That is the exact state an older build of the tool left behind, and it was
    hit for real in production. Deriving targets from the stored requests would
    find nothing here and leave the watch polling with no way to stop it.
    """
    repository = _repository(tmp_path)
    _generic_watch(repository)
    assert repository.cancel_watch_requests(SESSION, now=1100.0) == 1
    orphan = repository.subscription(SESSION, KNOB)
    assert orphan is not None and orphan.state is SubscriptionState.ACTIVE

    cancel_watches(repository, SESSION, sources=GENERIC_SOURCES, now=2000.0)

    stopped = repository.subscription(SESSION, KNOB)
    assert stopped is not None and stopped.state is SubscriptionState.RETIRED


def test_status_shows_the_command_a_watch_will_keep_running(tmp_path: Path) -> None:
    """The argv outlives the turn that registered it, so it has to be legible
    from the status tool rather than only by reading the database."""
    repository = _repository(tmp_path)
    repository.request_watch(
        SESSION,
        COMMAND_SOURCE_NAME,
        KNOB,
        COMMAND_EVENT_KINDS,
        spec=CommandSpec(["cat", "/tmp/knob"], r"(\d+)", 120.0).to_json(),
        now=1000.0,
    )

    status = describe_watches(repository, SESSION, sources=GENERIC_SOURCES)

    assert KNOB in status
    assert "cat /tmp/knob" in status
    assert "every 120s" in status
    assert "state: pending (not bound)" in status
    assert "Active watches:" not in status
    # Quoted, not repr'd: repr escapes the backslash and shows a pattern the
    # caller never typed.
    assert r"(\d+)" in status


def test_status_is_scoped_to_its_own_surface(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    subject = _watch(repository)
    _generic_watch(repository)

    generic = describe_watches(repository, SESSION, sources=GENERIC_SOURCES)
    assert KNOB in generic and subject not in generic

    diffs = describe_watches(repository, SESSION, sources=DIFF_SOURCES)
    assert subject in diffs and KNOB not in diffs


def test_status_shows_bound_state_and_recorded_progress(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    _generic_watch(repository)

    status = describe_watches(repository, SESSION, sources=GENERIC_SOURCES)

    assert "state: active" in status
    assert "last result: 1970-01-01T00:16:40Z" in status
    assert "consecutive failures: 0" in status
    assert "next poll scheduled: 1970-01-01T00:17:40Z" in status
    assert "last session delivery: none recorded" in status
    assert "not a worker health check" in status
    assert "Last result includes baseline and partial reads" in status


def test_status_shows_failure_streak_and_retry_without_inventing_error_details(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    _generic_watch(repository)
    for now in (1100.0, 1200.0):
        assert repository.claim_watch(KNOB, now=now, owner="test", lease_seconds=60) is not None
        repository.poll_failed(KNOB, "test", next_poll_at=now + 60)

    status = describe_watches(repository, SESSION, sources=GENERIC_SOURCES)

    assert "state: active" in status
    assert "consecutive failures: 2" in status
    assert "next retry scheduled: 1970-01-01T00:21:00Z" in status
    assert "last result: 1970-01-01T00:16:40Z" in status
    assert "Last poll attempt and error category are not persisted" in status


def test_status_does_not_label_a_partial_result_as_a_successful_poll(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    subject = _watch(repository)
    apply_snapshot(
        repository,
        fixture("partial_failure").model_copy(update={"subject": subject}),
        now=2100.0,
        next_poll_at=2160.0,
        batch_window_seconds=30.0,
    )
    repository.partial_poll_failed(subject, next_poll_at=2160.0)

    status = describe_watches(repository, SESSION, sources=DIFF_SOURCES)

    assert "last result: 1970-01-01T00:35:00Z" in status
    assert "consecutive failures: 1" in status
    assert "Last result includes baseline and partial reads" in status
    assert "successful poll" not in status


def test_status_shows_suspension_and_why_a_session_is_unavailable(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    _generic_watch(repository)
    for now in (1100.0, 1200.0):
        repository.suspend_or_retire_session(
            SESSION, now=now, terminal_reason=None, suspend_after=60.0
        )

    status = describe_watches(repository, SESSION, sources=GENERIC_SOURCES)

    assert "state: suspended" in status
    assert "session unavailable since: 1970-01-01T00:18:20Z" in status
    assert "next poll scheduled" not in status


def test_status_keeps_retired_history_visible_after_cancellation(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    _generic_watch(repository)
    cancel_watches(repository, SESSION, sources=GENERIC_SOURCES, now=2000.0)

    status = describe_watches(repository, SESSION, sources=GENERIC_SOURCES)

    assert KNOB in status
    assert "state: retired (unsubscribed)" in status
    assert "true every 60s" in status
    assert "next poll scheduled" not in status
    assert "request pending" not in status


def test_status_shows_an_orphaned_subscription_without_a_durable_request(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    _generic_watch(repository, request=False)

    status = describe_watches(repository, SESSION, sources=GENERIC_SOURCES)

    assert "state: active; no durable request" in status
    assert "true every 60s" in status


def test_status_audits_the_bound_command_when_a_request_differs(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    _generic_watch(repository)
    repository.request_watch(
        SESSION,
        COMMAND_SOURCE_NAME,
        KNOB,
        COMMAND_EVENT_KINDS,
        spec=CommandSpec(["false"], None, 120.0).to_json(),
        now=1100.0,
    )

    status = describe_watches(repository, SESSION, sources=GENERIC_SOURCES)

    assert "requested settings differ from bound watch" in status
    assert "true every 60s" in status
    assert "false every 120s" not in status


def test_status_accepts_equivalent_legacy_command_settings(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    _generic_watch(repository)
    repository.request_watch(
        SESSION,
        COMMAND_SOURCE_NAME,
        KNOB,
        COMMAND_EVENT_KINDS,
        spec='{"argv": ["true"], "interval_seconds": 60}',
        now=1100.0,
    )

    status = describe_watches(repository, SESSION, sources=GENERIC_SOURCES)

    assert "requested settings differ" not in status
    assert "true every 60s, timeout 30s" in status


def test_status_reports_pending_notification_and_session_delivery(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    _generic_watch(repository)
    repository.apply_poll(
        command_poll(KNOB, fingerprint="changed"),
        now=2000.0,
        next_poll_at=2060.0,
        batch_window_seconds=5.0,
    )
    batch = repository.open_batch_for_session(SESSION)
    assert batch is not None
    repository.defer_batch(batch.batch_id, now=2005.0, retry_at=2300.0)

    pending = describe_watches(repository, SESSION, sources=GENERIC_SOURCES)

    assert "Pending session notification: open; deferrals: 1" in pending
    assert "next attempt scheduled: 1970-01-01T00:38:20Z" in pending

    repository.deliver_batch(batch.batch_id, now=2310.0)
    delivered = describe_watches(repository, SESSION, sources=GENERIC_SOURCES)

    assert "last session delivery: 1970-01-01T00:38:30Z" in delivered
    assert "Pending session notification" not in delivered


def test_status_does_not_reveal_another_sessions_watch(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    _generic_watch(repository)

    status = describe_watches(repository, "conv_other", sources=GENERIC_SOURCES)

    assert status == "This session has no watches of that kind."


def test_status_lists_an_attempted_notification_only_once(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    _generic_watch(repository)
    repository.apply_poll(
        command_poll(KNOB, fingerprint="changed"),
        now=2000.0,
        next_poll_at=2060.0,
        batch_window_seconds=5.0,
    )
    batch = repository.open_batch_for_session(SESSION)
    assert batch is not None
    assert repository.prepare_batch(batch.batch_id, now=2005.0) is not None

    status = describe_watches(repository, SESSION, sources=GENERIC_SOURCES)

    assert "Pending session notification: delivering" in status
    assert status.count(batch.batch_id) == 1
    assert status.count("Pending session notification:") == 1


def test_status_shows_attempted_and_queued_notifications_scoped_by_source(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    _generic_watch(repository)
    _watch(repository)
    repository.apply_poll(
        command_poll(KNOB, fingerprint="changed"),
        now=2000.0,
        next_poll_at=2060.0,
        batch_window_seconds=5.0,
    )
    attempted = repository.open_batch_for_session(SESSION)
    assert attempted is not None
    assert repository.prepare_batch(attempted.batch_id, now=2005.0) is not None
    repository.defer_batch(attempted.batch_id, now=2005.0, retry_at=2300.0)
    repository.apply_poll(
        command_poll(KNOB, fingerprint="changed-again"),
        now=2100.0,
        next_poll_at=2160.0,
        batch_window_seconds=5.0,
    )
    queued = repository.open_batch_for_session(SESSION)
    assert queued is not None and queued.batch_id != attempted.batch_id

    status = describe_watches(repository, SESSION, sources=GENERIC_SOURCES)

    assert "Pending session notification: delivering; deferrals: 1" in status
    assert "next attempt scheduled: 1970-01-01T00:38:20Z" in status
    assert "Pending session notification: open; deferrals: 0" in status
    assert "next attempt scheduled: 1970-01-01T00:35:05Z" in status
    assert status.count(attempted.batch_id) == 1
    assert status.count(queued.batch_id) == 1
    assert status.count("Pending session notification:") == 2

    diff_status = describe_watches(repository, SESSION, sources=DIFF_SOURCES)
    assert "Pending session notification:" not in diff_status
    assert attempted.batch_id not in diff_status
    assert queued.batch_id not in diff_status
