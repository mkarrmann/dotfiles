from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from omnigent_watcher.command_source import CommandSpec
from omnigent_watcher.domain import (
    COMMAND_EVENT_KINDS,
    DIFF_EVENT_KINDS,
    Batch,
    BatchState,
    EventKind,
    PollResult,
)
from omnigent_watcher.phabricator_source import to_poll_result
from omnigent_watcher.repository import WatcherRepository
from omnigent_watcher.source_models import ReviewComment
from tests.support import command_poll, fixture

SESSION = "session-1"
COMMAND = "job:recovery"
SPEC = CommandSpec(["test-probe"]).to_json()


def _apply(repository: WatcherRepository, result: PollResult, now: float) -> int:
    return repository.apply_poll(result, now=now, next_poll_at=now + 10, batch_window_seconds=5)


def _pending(repository: WatcherRepository) -> Batch:
    batch = repository.open_batch_for_session(SESSION)
    assert batch is not None
    return batch


def _phab(subject: str = "D90000001", *, new_comment: bool = False) -> PollResult:
    base = fixture("active").model_copy(update={"subject": subject})
    if new_comment:
        comment = ReviewComment(
            external_id="new-comment",
            version_id=base.latest_version_id or "",
            updated_at=base.observed_at,
            content_fingerprint="sha256:" + "a" * 64,
        )
        base = base.model_copy(
            update={
                "comments": base.comments.model_copy(
                    update={"items": (*base.comments.items, comment)}
                )
            }
        )
    return to_poll_result(base)


def _command_attempt(repository: WatcherRepository) -> Batch:
    repository.subscribe(
        SESSION,
        COMMAND,
        COMMAND_EVENT_KINDS,
        command_poll(COMMAND, fingerprint="A"),
        now=100,
        next_poll_at=110,
        spec=SPEC,
    )
    _apply(repository, command_poll(COMMAND, fingerprint="B"), 110)
    batch = _pending(repository)
    assert repository.prepare_batch(batch.batch_id, now=115) == {EventKind.CHANGED: 1}
    return batch


@pytest.mark.parametrize("latest", ["A", "C"])
def test_new_value_remains_pending_across_delayed_ack_and_restarts(
    tmp_path: Path, latest: str
) -> None:
    path = tmp_path / "watcher.sqlite3"
    repository = WatcherRepository(path)
    attempt = _command_attempt(repository)
    assert _apply(repository, command_poll(COMMAND, fingerprint=latest), 120) == 1
    queued = _pending(repository)
    assert queued.state is BatchState.OPEN and queued.batch_id != attempt.batch_id
    assert not repository.batch_is_current(attempt.batch_id)
    assert repository.prepare_batch(queued.batch_id, now=130) is None
    assert [batch.batch_id for batch in repository.due_batches(130)] == [attempt.batch_id]

    repository = WatcherRepository(path)
    repository.deliver_batch(attempt.batch_id, now=500, delivered_at=115)
    subscription = repository.subscription(SESSION, COMMAND)
    assert subscription is not None and subscription.last_delivery_at == 115
    assert [batch.batch_id for batch in repository.due_batches(500)] == [queued.batch_id]
    assert repository.prepare_batch(queued.batch_id, now=500) == {EventKind.CHANGED: 1}
    repository.deliver_batch(queued.batch_id, now=501, delivered_at=500)
    repository = WatcherRepository(path)
    repository.deliver_batch(attempt.batch_id, now=600)
    assert _apply(repository, command_poll(COMMAND, fingerprint=latest), 610) == 0
    assert repository.open_batch_for_session(SESSION) is None


def test_superseding_ambiguous_attempt_preserves_return_to_baseline(tmp_path: Path) -> None:
    repository = WatcherRepository(tmp_path / "watcher.sqlite3")
    attempt = _command_attempt(repository)
    _apply(repository, command_poll(COMMAND, fingerprint="A"), 120)
    replacement = repository.supersede_batch(attempt.batch_id, now=130)
    assert replacement is not None and replacement.batch_id != attempt.batch_id
    assert replacement.flush_at == attempt.flush_at
    assert repository.prepare_batch(replacement.batch_id, now=130) == {EventKind.CHANGED: 1}
    repository.deliver_batch(replacement.batch_id, now=130)
    assert _apply(repository, command_poll(COMMAND, fingerprint="A"), 140) == 0


def test_definitely_unsent_attempt_can_coalesce_back_to_baseline(tmp_path: Path) -> None:
    repository = WatcherRepository(tmp_path / "watcher.sqlite3")
    attempt = _command_attempt(repository)
    _apply(repository, command_poll(COMMAND, fingerprint="A"), 120)
    assert repository.reset_unsent_batch(attempt.batch_id, now=130) is None
    assert repository.delivering_batch_for_session(SESSION) is None
    assert repository.open_batch_for_session(SESSION) is None


@pytest.mark.parametrize("reopen_before_ack", [False, True])
def test_resolution_and_reopening_survive_old_phabricator_acknowledgement(
    tmp_path: Path, reopen_before_ack: bool
) -> None:
    path = tmp_path / "watcher.sqlite3"
    repository = WatcherRepository(path)
    baseline = _phab()
    repository.subscribe(
        SESSION, baseline.subject, DIFF_EVENT_KINDS, baseline, now=100, next_poll_at=110
    )
    reopened = _phab(new_comment=True)
    _apply(repository, reopened, 110)
    attempt = _pending(repository)
    repository.prepare_batch(attempt.batch_id, now=115)
    _apply(repository, baseline, 120)
    if reopen_before_ack:
        assert _apply(repository, reopened, 125) == 1
    repository = WatcherRepository(path)
    repository.deliver_batch(attempt.batch_id, now=130)
    repository = WatcherRepository(path)
    if not reopen_before_ack:
        assert _apply(repository, reopened, 140) == 1
    next_batch = _pending(repository)
    assert next_batch.batch_id != attempt.batch_id
    assert repository.prepare_batch(next_batch.batch_id, now=150) == {EventKind.REVIEW_COMMENT: 1}
    repository.deliver_batch(next_batch.batch_id, now=150)
    assert _apply(repository, reopened, 160) == 0


def test_retirement_preserves_attempt_until_receipt_check_then_requeues_live_sibling(
    tmp_path: Path,
) -> None:
    repository = WatcherRepository(tmp_path / "watcher.sqlite3")
    subscriptions = []
    for subject in ("D90000001", "D90000002"):
        subscriptions.append(
            repository.subscribe(
                SESSION, subject, DIFF_EVENT_KINDS, _phab(subject), now=100, next_poll_at=110
            )[0]
        )
        _apply(repository, _phab(subject, new_comment=True), 110)
    attempt = _pending(repository)
    repository.prepare_batch(attempt.batch_id, now=115)
    frozen = repository.batch(attempt.batch_id)
    assert frozen is not None

    repository.retire_subscription(subscriptions[0].id, "committed", now=120)
    retired = repository.batch(attempt.batch_id)
    assert retired is not None and retired.state is BatchState.DELIVERING
    assert retired.summary == frozen.summary and retired.subjects == frozen.subjects
    assert not repository.batch_is_current(attempt.batch_id)
    replacement = repository.supersede_batch(attempt.batch_id, now=120)
    assert replacement is not None
    assert replacement.subjects == ("D90000002",)
    assert replacement.flush_at == attempt.flush_at
    repository.prepare_batch(replacement.batch_id, now=120)
    current = repository.batch(replacement.batch_id)
    assert current is not None and current.summary is not None
    assert "D90000001" not in current.summary and "D90000002" in current.summary
    repository.deliver_batch(attempt.batch_id, now=125)
    assert repository.delivering_batch_for_session(SESSION) is not None


def test_stale_pending_members_do_not_make_an_unchanged_attempt_obsolete(tmp_path: Path) -> None:
    repository = WatcherRepository(tmp_path / "watcher.sqlite3")
    attempt = _command_attempt(repository)
    sibling = "job:other"
    repository.subscribe(
        SESSION,
        sibling,
        COMMAND_EVENT_KINDS,
        command_poll(sibling, fingerprint="A"),
        now=115,
        next_poll_at=125,
        spec=SPEC,
    )
    _apply(repository, command_poll(sibling, fingerprint="C"), 120)
    assert not repository.batch_is_current(attempt.batch_id)
    _apply(repository, command_poll(sibling, fingerprint="A"), 130)
    assert repository.batch_is_current(attempt.batch_id)


def _v5_schema(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        for table in ("source_events", "batch_events", "subscription_events"):
            connection.execute(f"ALTER TABLE {table} DROP COLUMN generation")
        connection.execute("DROP INDEX one_open_batch_per_session")
        connection.execute("DROP INDEX one_delivery_per_session")
        connection.execute("UPDATE batches SET state = 'open' WHERE summary IS NOT NULL")
        connection.execute(
            "CREATE UNIQUE INDEX one_open_batch_per_session ON batches(session_id) "
            "WHERE state IN ('open', 'delivering')"
        )
        connection.execute("PRAGMA user_version=5")


def test_v5_migration_preserves_baseline_requests_and_legacy_attempt(tmp_path: Path) -> None:
    path = tmp_path / "watcher.sqlite3"
    repository = WatcherRepository(path)
    attempt = _command_attempt(repository)
    repository.request_watch(SESSION, "command", COMMAND, COMMAND_EVENT_KINDS, now=100, spec=SPEC)
    baseline = repository.subscription(SESSION, COMMAND)
    _v5_schema(path)

    repository = WatcherRepository(path)
    assert repository.schema_version() == 6
    assert repository.subscription(SESSION, COMMAND) == baseline
    assert len(repository.active_watch_requests(SESSION)) == 1
    restored = repository.delivering_batch_for_session(SESSION)
    assert restored is not None and restored.batch_id == attempt.batch_id
    assert _apply(repository, command_poll(COMMAND, fingerprint="A"), 120) == 1
    repository = WatcherRepository(path)
    repository.deliver_batch(attempt.batch_id, now=130)
    assert repository.prepare_batch(_pending(repository).batch_id, now=140) == {
        EventKind.CHANGED: 1
    }


def test_legacy_attempt_cannot_acknowledge_an_unknown_reopened_occurrence(tmp_path: Path) -> None:
    path = tmp_path / "watcher.sqlite3"
    repository = WatcherRepository(path)
    baseline = _phab()
    changed = _phab(new_comment=True)
    repository.subscribe(
        SESSION, baseline.subject, DIFF_EVENT_KINDS, baseline, now=100, next_poll_at=110
    )
    _apply(repository, changed, 110)
    attempt = _pending(repository)
    repository.prepare_batch(attempt.batch_id, now=115)
    _apply(repository, baseline, 120)
    _apply(repository, changed, 125)
    with sqlite3.connect(path) as connection:
        connection.execute(
            "DELETE FROM batch_events WHERE batch_id IN "
            "(SELECT batch_id FROM batches WHERE state = 'open')"
        )
        connection.execute("DELETE FROM batches WHERE state = 'open'")
    _v5_schema(path)

    repository = WatcherRepository(path)
    repository.deliver_batch(attempt.batch_id, now=130)
    assert _apply(repository, changed, 140) == 1
    assert repository.prepare_batch(_pending(repository).batch_id, now=145) == {
        EventKind.REVIEW_COMMENT: 1
    }


def test_receipt_timestamps_are_bounded_and_never_rewind_delivery_throttle(tmp_path: Path) -> None:
    repository = WatcherRepository(tmp_path / "watcher.sqlite3")
    attempt = _command_attempt(repository)
    repository.deliver_batch(attempt.batch_id, now=120, delivered_at=9999)
    subscription = repository.subscription(SESSION, COMMAND)
    assert subscription is not None and subscription.last_delivery_at == 120

    _apply(repository, command_poll(COMMAND, fingerprint="C"), 130)
    pending = _pending(repository)
    repository.prepare_batch(pending.batch_id, now=135)
    repository.deliver_batch(pending.batch_id, now=140, delivered_at=115)
    subscription = repository.subscription(SESSION, COMMAND)
    assert subscription is not None and subscription.last_delivery_at == 120
