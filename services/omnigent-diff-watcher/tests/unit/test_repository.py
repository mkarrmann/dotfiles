from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from omnigent_diff_watcher.domain import (
    DEFAULT_EVENT_TYPES,
    BatchState,
    EventKind,
    Subscription,
    SubscriptionState,
)
from omnigent_diff_watcher.logic import (
    normalize_snapshot,
)
from omnigent_diff_watcher.repository import (
    NewerSchemaError,
    SubscriptionConstraintError,
    WatcherRepository,
)
from omnigent_diff_watcher.source_models import (
    CIAggregateState,
    CIFailure,
    CISnapshot,
    DiffLifecycle,
    DiffSnapshot,
    ReviewComment,
)
from tests.support import (
    FakeClock,
    fixture,
)


def _repository(tmp_path: Path) -> WatcherRepository:
    return WatcherRepository(tmp_path / "watcher.sqlite3")


def _subscribe(
    repo: WatcherRepository,
    clock: FakeClock,
    session_id: str = "session-1",
) -> Subscription:
    return repo.subscribe(
        session_id,
        "D90000001",
        DEFAULT_EVENT_TYPES,
        fixture("active"),
        now=clock.now().timestamp(),
        next_poll_at=clock.now().timestamp() + 60,
    )[0]


def _new_comment_snapshot(clock: FakeClock) -> DiffSnapshot:
    base = fixture("active")
    comment = ReviewComment(
        external_id="comment-new",
        version_id=base.latest_version_id or "",
        updated_at=clock.now(),
        content_fingerprint="sha256:" + "a" * 64,
    )
    return base.model_copy(
        update={
            "observed_at": clock.now(),
            "last_activity_at": clock.now(),
            "comments": base.comments.model_copy(
                update={
                    "cursor": "comments-new",
                    "items": (*base.comments.items, comment),
                }
            ),
        }
    )


def test_schema_migration_and_newer_schema_rejection(tmp_path: Path) -> None:
    path = tmp_path / "watcher.sqlite3"
    assert WatcherRepository(path).schema_version() == 1
    assert path.stat().st_mode & 0o777 == 0o600
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA user_version=99")
    with pytest.raises(NewerSchemaError):
        WatcherRepository(path)


def test_subscribe_and_unsubscribe_are_idempotent_and_session_scoped(
    tmp_path: Path,
) -> None:
    repo = _repository(tmp_path)
    clock = FakeClock()
    first, created = repo.subscribe(
        "session-1",
        "D90000001",
        DEFAULT_EVENT_TYPES,
        fixture("active"),
        now=clock.now().timestamp(),
        next_poll_at=clock.now().timestamp() + 60,
    )
    second, created_again = repo.subscribe(
        "session-1",
        "D90000001",
        DEFAULT_EVENT_TYPES,
        fixture("active"),
        now=clock.now().timestamp() + 10,
        next_poll_at=clock.now().timestamp() + 70,
    )
    assert created is True
    assert created_again is False
    assert second.id == first.id
    assert second.baseline_at == first.baseline_at
    assert repo.subscription("another-session") is None
    assert repo.unsubscribe("session-1", now=clock.now().timestamp()) is True
    assert repo.unsubscribe("session-1", now=clock.now().timestamp()) is False


def test_baseline_suppresses_existing_comment_and_ci(tmp_path: Path) -> None:
    repo = _repository(tmp_path)
    clock = FakeClock()
    snapshot = fixture("failing").model_copy(update={"diff_id": "D90000001"})
    repo.subscribe(
        "session-1",
        "D90000001",
        DEFAULT_EVENT_TYPES,
        snapshot,
        now=clock.now().timestamp(),
        next_poll_at=clock.now().timestamp() + 60,
    )
    clock.advance(60)
    assert (
        repo.apply_snapshot(
            snapshot,
            now=clock.now().timestamp(),
            next_poll_at=clock.now().timestamp() + 60,
            batch_window_seconds=300,
        )
        == 0
    )


def test_first_event_fixes_flush_and_comment_ci_share_batch(tmp_path: Path) -> None:
    repo = _repository(tmp_path)
    clock = FakeClock()
    subscription = _subscribe(repo, clock)
    clock.advance(60)
    comment_snapshot = _new_comment_snapshot(clock)
    assert (
        repo.apply_snapshot(
            comment_snapshot,
            now=clock.now().timestamp(),
            next_poll_at=clock.now().timestamp() + 60,
            batch_window_seconds=300,
        )
        == 1
    )
    batch = repo.open_batch_for(subscription.id)
    assert batch is not None
    original_flush = batch.flush_at

    clock.advance(60)
    ci = CISnapshot(
        status="ok",
        cursor="ci-failing-new",
        aggregate=CIAggregateState.FAILING,
        failures=(
            CIFailure(
                external_id="signal-new",
                fingerprint="sha256:" + "b" * 64,
            ),
        ),
    )
    combined = comment_snapshot.model_copy(update={"observed_at": clock.now(), "ci": ci})
    assert (
        repo.apply_snapshot(
            combined,
            now=clock.now().timestamp(),
            next_poll_at=clock.now().timestamp() + 60,
            batch_window_seconds=300,
        )
        == 1
    )
    assert repo.open_batch_for(subscription.id).flush_at == original_flush  # type: ignore[union-attr]
    clock.advance(300)
    assert repo.prepare_batch(batch.batch_id, now=clock.now().timestamp()) == (1, 1)
    prepared = repo.batch(batch.batch_id)
    assert prepared is not None
    assert "review comment" in (prepared.summary or "")
    assert "CI failure" in (prepared.summary or "")


def test_resolution_before_flush_drops_empty_batch(tmp_path: Path) -> None:
    repo = _repository(tmp_path)
    clock = FakeClock()
    subscription = _subscribe(repo, clock)
    clock.advance(60)
    snapshot = _new_comment_snapshot(clock)
    repo.apply_snapshot(
        snapshot,
        now=clock.now().timestamp(),
        next_poll_at=clock.now().timestamp() + 60,
        batch_window_seconds=300,
    )
    batch = repo.open_batch_for(subscription.id)
    assert batch is not None
    clock.advance(300)
    resolved = snapshot.model_copy(
        update={
            "observed_at": clock.now(),
            "comments": snapshot.comments.model_copy(
                update={
                    "cursor": "comments-resolved",
                    "items": fixture("active").comments.items,
                }
            ),
        }
    )
    repo.apply_snapshot(
        resolved,
        now=clock.now().timestamp(),
        next_poll_at=clock.now().timestamp() + 60,
        batch_window_seconds=300,
    )
    assert repo.prepare_batch(batch.batch_id, now=clock.now().timestamp()) is None
    assert repo.batch(batch.batch_id).state is BatchState.CANCELLED  # type: ignore[union-attr]


def test_material_edit_qualifies_once(tmp_path: Path) -> None:
    repo = _repository(tmp_path)
    clock = FakeClock()
    subscription = _subscribe(repo, clock)
    clock.advance(60)
    base = fixture("active")
    edited = base.comments.items[0].model_copy(
        update={
            "content_fingerprint": "sha256:" + "e" * 64,
        }
    )
    snapshot = base.model_copy(
        update={
            "observed_at": clock.now(),
            "comments": base.comments.model_copy(
                update={"cursor": "comments-edit", "items": (edited,)}
            ),
        }
    )
    assert (
        repo.apply_snapshot(
            snapshot,
            now=clock.now().timestamp(),
            next_poll_at=clock.now().timestamp() + 60,
            batch_window_seconds=300,
        )
        == 1
    )
    assert (
        repo.apply_snapshot(
            snapshot,
            now=clock.now().timestamp() + 1,
            next_poll_at=clock.now().timestamp() + 61,
            batch_window_seconds=300,
        )
        == 0
    )
    assert repo.open_batch_for(subscription.id) is not None


@pytest.mark.parametrize("lifecycle", ["committed", "abandoned", "reverted"])
def test_terminal_diff_retires_subscription(tmp_path: Path, lifecycle: str) -> None:
    repo = _repository(tmp_path)
    clock = FakeClock()
    subscription = _subscribe(repo, clock)
    terminal = fixture("committed").model_copy(
        update={
            "diff_id": "D90000001",
            "lifecycle": DiffLifecycle(lifecycle),
            "latest_version_id": fixture("active").latest_version_id,
        }
    )
    repo.apply_snapshot(
        terminal,
        now=clock.now().timestamp(),
        next_poll_at=clock.now().timestamp() + 60,
        batch_window_seconds=300,
    )
    assert repo.subscription("session-1", "D90000001").state is SubscriptionState.RETIRED  # type: ignore[union-attr]
    assert repo.open_batch_for(subscription.id) is None


def test_two_schedulers_cannot_claim_same_diff(tmp_path: Path) -> None:
    repo = _repository(tmp_path)
    clock = FakeClock()
    _subscribe(repo, clock)
    now = clock.now().timestamp() + 61
    first = repo.claim_due_watches(now=now, owner="one", lease_seconds=120, limit=2)
    second = repo.claim_due_watches(now=now, owner="two", lease_seconds=120, limit=2)
    assert [watch.diff_id for watch in first] == ["D90000001"]
    assert second == []


def test_next_wake_tracks_deadlines_and_stop_releases_owned_lease(
    tmp_path: Path,
) -> None:
    repo = _repository(tmp_path)
    clock = FakeClock()
    _subscribe(repo, clock)
    now = clock.now().timestamp()
    assert (
        repo.next_wake_at(
            active_probe_seconds=3600,
            suspended_probe_seconds=7200,
        )
        == now + 60
    )

    claimed = repo.claim_due_watches(
        now=now + 61,
        owner="stopping-scheduler",
        lease_seconds=120,
        limit=1,
    )
    assert len(claimed) == 1
    repo.release_owner_leases("stopping-scheduler")
    assert (
        len(
            repo.claim_due_watches(
                now=now + 61,
                owner="replacement-scheduler",
                lease_seconds=120,
                limit=1,
            )
        )
        == 1
    )


def test_retention_prunes_retired_subscription_and_orphaned_diff(
    tmp_path: Path,
) -> None:
    repo = _repository(tmp_path)
    clock = FakeClock()
    _subscribe(repo, clock)
    retired_at = clock.now().timestamp()
    assert repo.unsubscribe("session-1", now=retired_at) is True

    repo.prune(now=retired_at + 101, retention_seconds=100)

    assert repo.subscription("session-1", "D90000001") is None
    assert repo.watch("D90000001") is None
    assert repo.counts()["subscriptions_retired"] == 0


def test_delivered_fingerprint_survives_batch_retention(tmp_path: Path) -> None:
    repo = _repository(tmp_path)
    clock = FakeClock()
    subscription = _subscribe(repo, clock)
    snapshot = _new_comment_snapshot(clock)
    observed = clock.now().timestamp() + 60
    repo.apply_snapshot(
        snapshot,
        now=observed,
        next_poll_at=observed + 60,
        batch_window_seconds=5,
    )
    batch = repo.open_batch_for(subscription.id)
    assert batch is not None
    repo.prepare_batch(batch.batch_id, now=observed + 6)
    repo.deliver_batch(batch.batch_id, now=observed + 6)
    repo.prune(now=observed + 107, retention_seconds=100)

    repo.apply_snapshot(
        snapshot,
        now=observed + 108,
        next_poll_at=observed + 168,
        batch_window_seconds=5,
    )

    assert repo.batch(batch.batch_id) is None
    assert repo.open_batch_for(subscription.id) is None


def test_non_actionable_interval_allows_one_recurrence(tmp_path: Path) -> None:
    repo = _repository(tmp_path)
    clock = FakeClock()
    subscription = _subscribe(repo, clock)
    failing = fixture("failing").model_copy(update={"diff_id": "D90000001"})
    observed = clock.now().timestamp() + 60
    repo.apply_snapshot(
        failing,
        now=observed,
        next_poll_at=observed + 60,
        batch_window_seconds=5,
    )
    first = repo.open_batch_for(subscription.id)
    assert first is not None
    repo.prepare_batch(first.batch_id, now=observed + 6)
    repo.deliver_batch(first.batch_id, now=observed + 6)

    green = fixture("green").model_copy(
        update={
            "diff_id": "D90000001",
            "latest_version_id": failing.latest_version_id,
        }
    )
    repo.apply_snapshot(
        green,
        now=observed + 7,
        next_poll_at=observed + 67,
        batch_window_seconds=5,
    )
    repo.apply_snapshot(
        failing,
        now=observed + 8,
        next_poll_at=observed + 68,
        batch_window_seconds=5,
    )

    recurrence = repo.open_batch_for(subscription.id)
    assert recurrence is not None
    assert recurrence.batch_id != first.batch_id


def test_expanding_event_preferences_baselines_new_kind(tmp_path: Path) -> None:
    repo = _repository(tmp_path)
    clock = FakeClock()
    comments_only = frozenset({EventKind.REVIEW_COMMENT})
    partial = fixture("partial_failure").model_copy(update={"diff_id": "D90000001"})
    repo.subscribe(
        "session-1",
        "D90000001",
        comments_only,
        partial,
        now=clock.now().timestamp(),
        next_poll_at=clock.now().timestamp() + 60,
    )
    current_failure = partial.model_copy(update={"ci": fixture("failing").ci})
    subscription, _ = repo.subscribe(
        "session-1",
        "D90000001",
        DEFAULT_EVENT_TYPES,
        current_failure,
        now=clock.now().timestamp() + 1,
        next_poll_at=clock.now().timestamp() + 61,
    )

    repo.apply_snapshot(
        current_failure,
        now=clock.now().timestamp() + 62,
        next_poll_at=clock.now().timestamp() + 122,
        batch_window_seconds=5,
    )

    assert repo.open_batch_for(subscription.id) is None


def test_partial_failure_advances_only_successful_cursor(tmp_path: Path) -> None:
    repo = _repository(tmp_path)
    clock = FakeClock()
    _subscribe(repo, clock)
    prior = repo.watch("D90000001")
    assert prior is not None
    partial = fixture("partial_failure").model_copy(
        update={"diff_id": "D90000001", "latest_version_id": prior.latest_version_id}
    )
    repo.apply_snapshot(
        partial,
        now=clock.now().timestamp() + 60,
        next_poll_at=clock.now().timestamp() + 120,
        batch_window_seconds=300,
    )
    after = repo.watch("D90000001")
    assert after is not None
    assert after.cursor.comments == "comments-partial-2"
    assert after.cursor.ci == prior.cursor.ci


def test_normalizer_omits_pending_green_and_skipped_ci() -> None:
    for state in (
        CIAggregateState.PENDING,
        CIAggregateState.PASSED,
        CIAggregateState.SKIPPED,
    ):
        base = fixture("green")
        snapshot = base.model_copy(update={"ci": base.ci.model_copy(update={"aggregate": state})})
        assert normalize_snapshot(snapshot)[EventKind.CI_FAILURE] == ()


def test_subscription_resource_constraints_are_transactional(tmp_path: Path) -> None:
    repo = _repository(tmp_path)
    clock = FakeClock()
    _subscribe(repo, clock)
    second = fixture("active").model_copy(update={"diff_id": "D90000002"})

    with pytest.raises(SubscriptionConstraintError, match="active-diff limit"):
        repo.subscribe(
            "session-2",
            "D90000002",
            DEFAULT_EVENT_TYPES,
            second,
            now=clock.now().timestamp(),
            next_poll_at=clock.now().timestamp() + 60,
            max_active_diffs=1,
        )
    with pytest.raises(SubscriptionConstraintError, match="different diff"):
        repo.subscribe(
            "session-1",
            "D90000002",
            DEFAULT_EVENT_TYPES,
            second,
            now=clock.now().timestamp(),
            next_poll_at=clock.now().timestamp() + 60,
            max_active_diffs=10,
        )

    assert repo.watch("D90000002") is None
    assert repo.active_diff_count() == 1


def test_recovered_subscription_forces_one_current_poll(tmp_path: Path) -> None:
    repo = _repository(tmp_path)
    clock = FakeClock()
    _subscribe(repo, clock)
    initial = clock.now().timestamp()
    repo.suspend_or_retire_session(
        "session-1",
        now=initial + 1,
        terminal_reason=None,
        suspend_after=10,
    )
    repo.suspend_or_retire_session(
        "session-1",
        now=initial + 12,
        terminal_reason=None,
        suspend_after=10,
    )

    assert repo.mark_session_usable("session-1", now=initial + 13)
    watch = repo.watch("D90000001")
    assert watch is not None
    assert watch.next_poll_at == initial + 13
