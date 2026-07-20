from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from omnigent_diff_watcher.domain import (
    DEFAULT_EVENT_TYPES,
    SubscriptionState,
)
from omnigent_diff_watcher.logic import (
    deterministic_jitter,
    failure_poll_delay,
    successful_poll_delay,
)
from omnigent_diff_watcher.repository import (
    NewerSchemaError,
    WatcherRepository,
)
from omnigent_diff_watcher.source_models import (
    CIAggregateState,
    CIFailure,
    CISnapshot,
    CommentsSnapshot,
    DiffLifecycle,
    DiffSnapshot,
    ReviewComment,
)

FIXTURES = Path(__file__).parents[1] / "fixtures"
UTC = UTC


def load_snapshot(name: str = "active.json") -> DiffSnapshot:
    return DiffSnapshot.model_validate_json((FIXTURES / name).read_text())


def comments_snapshot(*comments: ReviewComment, cursor: str = "comments-next") -> CommentsSnapshot:
    return CommentsSnapshot(status="ok", cursor=cursor, items=comments)


def ci_snapshot(
    state: CIAggregateState,
    *failures: CIFailure,
    cursor: str = "ci-next",
) -> CISnapshot:
    return CISnapshot(status="ok", cursor=cursor, aggregate=state, failures=failures)


def updated(
    base: DiffSnapshot,
    *,
    comments: CommentsSnapshot | None = None,
    ci: CISnapshot | None = None,
    lifecycle: DiffLifecycle | None = None,
    latest_version_id: str | None = None,
    observed_at: datetime | None = None,
) -> DiffSnapshot:
    values: dict[str, object] = {
        "observed_at": observed_at or base.observed_at + timedelta(minutes=1),
    }
    if comments is not None:
        values["comments"] = comments
    if ci is not None:
        values["ci"] = ci
    if lifecycle is not None:
        values["lifecycle"] = lifecycle
    if latest_version_id is not None:
        values["latest_version_id"] = latest_version_id
    return base.model_copy(update=values)


def subscribe(
    repository: WatcherRepository,
    snapshot: DiffSnapshot,
    *,
    session_id: str = "session-1",
    now: float = 1000,
) -> int:
    subscription, _created = repository.subscribe(
        session_id,
        snapshot.diff_id,
        DEFAULT_EVENT_TYPES,
        snapshot,
        now=now,
        next_poll_at=now + 60,
    )
    return subscription.id


@pytest.mark.parametrize(
    ("age", "expected"),
    [
        (timedelta(minutes=59), 60),
        (timedelta(hours=1), 300),
        (timedelta(hours=6), 900),
        (timedelta(days=1), 3600),
        (timedelta(days=3), 21600),
        (timedelta(days=14), 86400),
    ],
)
def test_adaptive_interval_boundaries(age: timedelta, expected: float) -> None:
    snapshot = load_snapshot("green.json")
    now = snapshot.last_activity_at + age
    assert successful_poll_delay(snapshot, now) == expected


def test_running_ci_and_deterministic_failure_jitter() -> None:
    snapshot = load_snapshot()
    assert successful_poll_delay(snapshot, snapshot.last_activity_at + timedelta(days=20)) == 60
    assert deterministic_jitter(100, snapshot.diff_id, 3) == deterministic_jitter(
        100, snapshot.diff_id, 3
    )
    assert 90 <= deterministic_jitter(100, snapshot.diff_id, 3) <= 110
    delays = [failure_poll_delay(index, snapshot.diff_id) for index in range(1, 7)]
    bases = (60, 120, 300, 900, 1800, 1800)
    assert all(base * 0.9 <= value <= base * 1.1 for base, value in zip(bases, delays, strict=True))


def test_baseline_suppresses_existing_events_and_subscribe_is_idempotent(
    tmp_path: Path,
) -> None:
    repository = WatcherRepository(tmp_path / "watcher.db")
    snapshot = load_snapshot("failing.json")
    first, created = repository.subscribe(
        "session-1",
        snapshot.diff_id,
        DEFAULT_EVENT_TYPES,
        snapshot,
        now=1000,
        next_poll_at=1060,
    )
    second, created_again = repository.subscribe(
        "session-1",
        snapshot.diff_id,
        DEFAULT_EVENT_TYPES,
        snapshot,
        now=1001,
        next_poll_at=1061,
    )

    assert created is True
    assert created_again is False
    assert second.id == first.id
    assert repository.open_batch_for(first.id) is None
    assert repository.subscription("another-session") is None
    assert repository.unsubscribe("session-1", now=1002) is True
    assert repository.unsubscribe("session-1", now=1003) is False
    assert repository.subscription("session-1").state is SubscriptionState.RETIRED  # type: ignore[union-attr]


def test_new_comment_and_material_edit_each_qualify_once(tmp_path: Path) -> None:
    repository = WatcherRepository(tmp_path / "watcher.db")
    base = load_snapshot()
    subscription_id = subscribe(repository, base)
    new_comment = ReviewComment(
        external_id="comment-new",
        version_id=base.latest_version_id or "",
        updated_at=base.observed_at + timedelta(minutes=1),
        content_fingerprint="sha256:" + "a" * 64,
    )
    first = updated(base, comments=comments_snapshot(*base.comments.items, new_comment))
    assert (
        repository.apply_snapshot(
            first,
            now=1100,
            next_poll_at=1160,
            batch_window_seconds=300,
        )
        == 1
    )
    batch = repository.open_batch_for(subscription_id)
    assert batch is not None
    original_flush = batch.flush_at

    assert (
        repository.apply_snapshot(
            first,
            now=1110,
            next_poll_at=1170,
            batch_window_seconds=300,
        )
        == 0
    )
    edited = new_comment.model_copy(
        update={
            "updated_at": new_comment.updated_at + timedelta(minutes=1),
            "content_fingerprint": "sha256:" + "b" * 64,
        }
    )
    second = updated(base, comments=comments_snapshot(*base.comments.items, edited))
    assert (
        repository.apply_snapshot(
            second,
            now=1120,
            next_poll_at=1180,
            batch_window_seconds=300,
        )
        == 1
    )
    assert repository.open_batch_for(subscription_id).flush_at == original_flush  # type: ignore[union-attr]
    assert repository.prepare_batch(batch.batch_id, now=1300) == (1, 0)


def test_resolution_before_flush_drops_empty_batch(tmp_path: Path) -> None:
    repository = WatcherRepository(tmp_path / "watcher.db")
    base = load_snapshot("green.json")
    subscription_id = subscribe(repository, base)
    comment = ReviewComment(
        external_id="comment-new",
        version_id=base.latest_version_id or "",
        updated_at=base.observed_at + timedelta(minutes=1),
        content_fingerprint="sha256:" + "c" * 64,
    )
    repository.apply_snapshot(
        updated(base, comments=comments_snapshot(comment)),
        now=1100,
        next_poll_at=1160,
        batch_window_seconds=300,
    )
    batch = repository.open_batch_for(subscription_id)
    assert batch is not None
    repository.apply_snapshot(
        updated(base, comments=comments_snapshot()),
        now=1200,
        next_poll_at=1260,
        batch_window_seconds=300,
    )
    assert repository.prepare_batch(batch.batch_id, now=1400) is None
    assert repository.open_batch_for(subscription_id) is None


@pytest.mark.parametrize(
    "state",
    [
        CIAggregateState.UNKNOWN,
        CIAggregateState.PENDING,
        CIAggregateState.PASSED,
        CIAggregateState.SKIPPED,
    ],
)
def test_nonfailing_ci_does_not_qualify(tmp_path: Path, state: CIAggregateState) -> None:
    repository = WatcherRepository(tmp_path / f"{state}.db")
    base = load_snapshot("green.json")
    subscription_id = subscribe(repository, base)
    repository.apply_snapshot(
        updated(base, ci=ci_snapshot(state)),
        now=1100,
        next_poll_at=1160,
        batch_window_seconds=300,
    )
    assert repository.open_batch_for(subscription_id) is None


def test_current_failure_qualifies_once_and_new_version_invalidates_old(
    tmp_path: Path,
) -> None:
    repository = WatcherRepository(tmp_path / "watcher.db")
    base = load_snapshot("green.json")
    subscription_id = subscribe(repository, base)
    failure = CIFailure(external_id="signal-a", fingerprint="sha256:" + "d" * 64)
    failing = updated(base, ci=ci_snapshot(CIAggregateState.FAILING, failure))
    assert (
        repository.apply_snapshot(
            failing,
            now=1100,
            next_poll_at=1160,
            batch_window_seconds=300,
        )
        == 1
    )
    assert (
        repository.apply_snapshot(
            failing,
            now=1110,
            next_poll_at=1170,
            batch_window_seconds=300,
        )
        == 0
    )
    batch = repository.open_batch_for(subscription_id)
    assert batch is not None

    new_version = updated(
        base,
        latest_version_id="version-green-10",
        ci=ci_snapshot(CIAggregateState.PASSED),
    )
    repository.apply_snapshot(
        new_version,
        now=1120,
        next_poll_at=1180,
        batch_window_seconds=300,
    )
    assert repository.prepare_batch(batch.batch_id, now=1400) is None


def test_comment_and_ci_correlate_into_one_batch(tmp_path: Path) -> None:
    repository = WatcherRepository(tmp_path / "watcher.db")
    base = load_snapshot("green.json")
    subscription_id = subscribe(repository, base)
    comment = ReviewComment(
        external_id="comment-new",
        version_id=base.latest_version_id or "",
        updated_at=base.observed_at + timedelta(minutes=1),
        content_fingerprint="sha256:" + "e" * 64,
    )
    failure = CIFailure(external_id="signal-a", fingerprint="sha256:" + "f" * 64)
    snapshot = updated(
        base,
        comments=comments_snapshot(comment),
        ci=ci_snapshot(CIAggregateState.FAILING, failure),
    )
    assert (
        repository.apply_snapshot(
            snapshot,
            now=1100,
            next_poll_at=1160,
            batch_window_seconds=300,
        )
        == 2
    )
    batch = repository.open_batch_for(subscription_id)
    assert batch is not None
    assert repository.prepare_batch(batch.batch_id, now=1400) == (1, 1)
    assert "1 unresolved review comment and 1 current-version CI failure" in (
        repository.batch(batch.batch_id).summary or ""  # type: ignore[union-attr]
    )


def test_partial_failure_advances_only_successful_cursor(tmp_path: Path) -> None:
    repository = WatcherRepository(tmp_path / "watcher.db")
    base = load_snapshot("green.json")
    subscribe(repository, base)
    partial = load_snapshot("partial_failure.json").model_copy(
        update={
            "diff_id": base.diff_id,
            "latest_version_id": base.latest_version_id,
        }
    )
    repository.apply_snapshot(
        partial,
        now=1100,
        next_poll_at=1160,
        batch_window_seconds=300,
    )
    watch = repository.watch(base.diff_id)
    assert watch is not None
    assert watch.cursor.comments == "comments-partial-2"
    assert watch.cursor.ci == "ci-green-1"


def test_terminal_lifecycle_and_consecutive_missing_retire(tmp_path: Path) -> None:
    for lifecycle in (
        DiffLifecycle.COMMITTED,
        DiffLifecycle.ABANDONED,
        DiffLifecycle.REVERTED,
    ):
        repository = WatcherRepository(tmp_path / f"{lifecycle}.db")
        base = load_snapshot()
        subscribe(repository, base)
        repository.apply_snapshot(
            updated(base, lifecycle=lifecycle),
            now=1100,
            next_poll_at=1160,
            batch_window_seconds=300,
        )
        assert repository.subscription("session-1").state is SubscriptionState.RETIRED  # type: ignore[union-attr]

    repository = WatcherRepository(tmp_path / "missing.db")
    base = load_snapshot()
    subscribe(repository, base)
    missing = load_snapshot("missing.json").model_copy(update={"diff_id": base.diff_id})
    repository.apply_snapshot(
        missing,
        now=1100,
        next_poll_at=1160,
        batch_window_seconds=300,
    )
    assert repository.subscription("session-1").state is SubscriptionState.ACTIVE  # type: ignore[union-attr]
    repository.apply_snapshot(
        missing,
        now=1200,
        next_poll_at=1260,
        batch_window_seconds=300,
    )
    assert repository.subscription("session-1").state is SubscriptionState.RETIRED  # type: ignore[union-attr]


def test_newer_schema_is_rejected_and_database_uses_wal(tmp_path: Path) -> None:
    path = tmp_path / "watcher.db"
    repository = WatcherRepository(path)
    assert repository.schema_version() == 1
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        connection.execute("PRAGMA user_version=99")
    with pytest.raises(NewerSchemaError):
        WatcherRepository(path)
