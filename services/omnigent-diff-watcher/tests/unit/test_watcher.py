from __future__ import annotations

from pathlib import Path

import pytest

from omnigent_diff_watcher.domain import (
    DEFAULT_EVENT_TYPES,
    BatchState,
    EventDeliveryStatus,
    SessionSnapshot,
    SubscriptionState,
    WatcherConfig,
)
from omnigent_diff_watcher.repository import (
    WatcherRepository,
)
from omnigent_diff_watcher.source_models import (
    DiffSnapshot,
    ReviewComment,
)
from omnigent_diff_watcher.watcher import (
    DiffWatcher,
    SubscriptionError,
)
from tests.support import (
    FakeClock,
    FakeReviewSource,
    FakeSessionService,
    RecordingDeliveryService,
    fixture,
)


def _new_snapshot(clock: FakeClock, *, include_ci: bool = True) -> DiffSnapshot:
    base = fixture("active")
    comment = ReviewComment(
        external_id="comment-new",
        version_id=base.latest_version_id or "",
        updated_at=clock.now(),
        content_fingerprint="sha256:" + "a" * 64,
    )
    update = {
        "observed_at": clock.now(),
        "last_activity_at": clock.now(),
        "comments": base.comments.model_copy(
            update={"cursor": "comments-new", "items": (*base.comments.items, comment)}
        ),
    }
    if include_ci:
        failing = fixture("failing")
        update["ci"] = failing.ci.model_copy(update={"cursor": "ci-new"})
    return base.model_copy(update=update)


def _config() -> WatcherConfig:
    return WatcherConfig(
        batch_window_seconds=5,
        minimum_delivery_interval_seconds=10,
        poll_lease_seconds=30,
        unavailable_suspend_seconds=24,
        liveness_probe_seconds=6,
        suspended_liveness_probe_seconds=6,
        delivery_retry_seconds=2,
    )


def _watcher(
    tmp_path: Path,
    source: FakeReviewSource,
    sessions: FakeSessionService,
    delivery: RecordingDeliveryService,
    clock: FakeClock,
) -> DiffWatcher:
    return DiffWatcher(
        WatcherRepository(tmp_path / "watcher.sqlite3"),
        source,
        sessions,
        delivery,
        clock=clock,
        config=_config(),
        owner="test-scheduler",
    )


@pytest.mark.asyncio
async def test_subscribe_baselines_and_repeated_call_does_not_reset(
    tmp_path: Path,
) -> None:
    clock = FakeClock()
    source = FakeReviewSource(fixture("active"), fixture("active"))
    sessions = FakeSessionService(SessionSnapshot("session-1", {}))
    watcher = _watcher(tmp_path, source, sessions, RecordingDeliveryService(), clock)

    first, created = await watcher.subscribe("session-1", "D90000001", DEFAULT_EVENT_TYPES)
    clock.advance(10)
    second, created_again = await watcher.subscribe("session-1", "D90000001", DEFAULT_EVENT_TYPES)

    assert created is True
    assert created_again is False
    assert first.id == second.id
    assert first.baseline_at == second.baseline_at
    assert watcher.repository.open_batch_for(first.id) is None


@pytest.mark.asyncio
async def test_subscribe_rejects_terminal_and_failed_requested_baseline(
    tmp_path: Path,
) -> None:
    clock = FakeClock()
    sessions = FakeSessionService(SessionSnapshot("session-1", {}))
    watcher = _watcher(
        tmp_path,
        FakeReviewSource(fixture("committed")),
        sessions,
        RecordingDeliveryService(),
        clock,
    )
    with pytest.raises(SubscriptionError, match="terminal"):
        await watcher.subscribe("session-1", "D90000004", DEFAULT_EVENT_TYPES)

    watcher = _watcher(
        tmp_path,
        FakeReviewSource(fixture("partial_failure")),
        sessions,
        RecordingDeliveryService(),
        clock,
    )
    with pytest.raises(SubscriptionError, match="CI baseline"):
        await watcher.subscribe("session-1", "D90000006", DEFAULT_EVENT_TYPES)


@pytest.mark.asyncio
async def test_comment_and_ci_burst_delivers_one_message_once(tmp_path: Path) -> None:
    clock = FakeClock()
    new = _new_snapshot(clock)
    source = FakeReviewSource(fixture("active"), new, new, new)
    sessions = FakeSessionService(SessionSnapshot("session-1", {}))
    delivery = RecordingDeliveryService(EventDeliveryStatus.ACCEPTED)
    watcher = _watcher(tmp_path, source, sessions, delivery, clock)
    subscription, _ = await watcher.subscribe("session-1", "D90000001", DEFAULT_EVENT_TYPES)

    clock.advance(70)
    await watcher.run_iteration()
    batch = watcher.repository.open_batch_for(subscription.id)
    assert batch is not None
    fixed_flush = batch.flush_at
    clock.advance(6)
    await watcher.run_iteration()

    assert len(delivery.calls) == 1
    assert delivery.calls[0][1] == batch.batch_id
    assert "review comment" in delivery.calls[0][2]
    assert "CI failures" in delivery.calls[0][2]
    assert watcher.repository.batch(batch.batch_id).state is BatchState.DELIVERED  # type: ignore[union-attr]
    assert fixed_flush == batch.first_event_at + 5

    clock.advance(70)
    await watcher.run_iteration()
    assert len(delivery.calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("outcome", "batch_state", "subscription_state"),
    [
        (
            EventDeliveryStatus.ALREADY_ACCEPTED,
            BatchState.DELIVERED,
            SubscriptionState.ACTIVE,
        ),
        (EventDeliveryStatus.DEFERRED, BatchState.OPEN, SubscriptionState.ACTIVE),
        (EventDeliveryStatus.TERMINAL, None, SubscriptionState.RETIRED),
        (RuntimeError("transport"), BatchState.OPEN, SubscriptionState.ACTIVE),
    ],
)
async def test_delivery_outcomes_make_correct_durable_transition(
    tmp_path: Path,
    outcome: EventDeliveryStatus | Exception,
    batch_state: BatchState | None,
    subscription_state: SubscriptionState,
) -> None:
    clock = FakeClock()
    new = _new_snapshot(clock, include_ci=False)
    source = FakeReviewSource(fixture("active"), new, new)
    sessions = FakeSessionService(SessionSnapshot("session-1", {}))
    delivery = RecordingDeliveryService(outcome)
    watcher = _watcher(tmp_path, source, sessions, delivery, clock)
    subscription, _ = await watcher.subscribe("session-1", "D90000001", DEFAULT_EVENT_TYPES)
    clock.advance(70)
    await watcher.run_iteration()
    batch = watcher.repository.open_batch_for(subscription.id)
    assert batch is not None
    clock.advance(6)
    await watcher.run_iteration()

    current_subscription = watcher.repository.subscription("session-1", "D90000001")
    assert current_subscription is not None
    assert current_subscription.state is subscription_state
    current_batch = watcher.repository.batch(batch.batch_id)
    if batch_state is None:
        assert current_batch is not None and current_batch.state is BatchState.CANCELLED
    else:
        assert current_batch is not None and current_batch.state is batch_state


@pytest.mark.asyncio
async def test_busy_session_defers_without_delivery_and_keeps_batch(
    tmp_path: Path,
) -> None:
    clock = FakeClock()
    new = _new_snapshot(clock, include_ci=False)
    source = FakeReviewSource(fixture("active"), new, new)
    sessions = FakeSessionService(SessionSnapshot("session-1", {}, can_accept_input=False))
    delivery = RecordingDeliveryService()
    watcher = _watcher(tmp_path, source, sessions, delivery, clock)
    subscription, _ = await watcher.subscribe("session-1", "D90000001", DEFAULT_EVENT_TYPES)
    clock.advance(70)
    await watcher.run_iteration()
    clock.advance(6)
    await watcher.run_iteration()

    assert delivery.calls == []
    assert watcher.repository.open_batch_for(subscription.id) is not None
    assert len(source.calls) == 2


@pytest.mark.asyncio
async def test_partial_refresh_defers_batch_until_all_sources_are_authoritative(
    tmp_path: Path,
) -> None:
    clock = FakeClock()
    changed = _new_snapshot(clock, include_ci=False)
    partial_raw = fixture("partial_failure")
    partial = partial_raw.model_copy(
        update={
            "diff_id": changed.diff_id,
            "latest_version_id": changed.latest_version_id,
            "comments": changed.comments,
        }
    )
    source = FakeReviewSource(fixture("active"), changed, partial, changed)
    sessions = FakeSessionService(SessionSnapshot("session-1", {}))
    delivery = RecordingDeliveryService()
    watcher = _watcher(tmp_path, source, sessions, delivery, clock)
    subscription, _ = await watcher.subscribe("session-1", "D90000001", DEFAULT_EVENT_TYPES)

    clock.advance(70)
    await watcher.run_iteration()
    batch = watcher.repository.open_batch_for(subscription.id)
    assert batch is not None
    clock.advance(6)
    await watcher.run_iteration()
    assert delivery.calls == []
    assert watcher.repository.open_batch_for(subscription.id) is not None
    assert watcher.repository.watch("D90000001").failure_count == 1  # type: ignore[union-attr]
    assert watcher.last_source_error_category == "unavailable"

    clock.advance(70)
    await watcher.run_iteration()
    assert len(delivery.calls) == 1
    assert watcher.last_source_error_category is None


@pytest.mark.asyncio
async def test_repeated_partial_failures_increase_backoff_streak(
    tmp_path: Path,
) -> None:
    clock = FakeClock()
    partial = fixture("partial_failure").model_copy(update={"diff_id": "D90000001"})
    source = FakeReviewSource(fixture("active"), partial, partial)
    watcher = _watcher(
        tmp_path,
        source,
        FakeSessionService(SessionSnapshot("session-1", {})),
        RecordingDeliveryService(),
        clock,
    )
    await watcher.subscribe("session-1", "D90000001", DEFAULT_EVENT_TYPES)

    clock.advance(70)
    await watcher.run_iteration()
    first = watcher.repository.watch("D90000001")
    assert first is not None and first.failure_count == 1

    clock.advance(130)
    await watcher.run_iteration()
    second = watcher.repository.watch("D90000001")
    assert second is not None and second.failure_count == 2
    assert second.next_poll_at - clock.now().timestamp() > 100


@pytest.mark.asyncio
async def test_authoritative_terminal_and_two_missing_polls_retire(
    tmp_path: Path,
) -> None:
    clock = FakeClock()
    failed_components = fixture("missing")
    committed = fixture("committed").model_copy(
        update={
            "diff_id": "D90000001",
            "comments": failed_components.comments,
            "ci": failed_components.ci,
        }
    )
    sessions = FakeSessionService(SessionSnapshot("session-1", {}))
    terminal_watcher = _watcher(
        tmp_path / "terminal",
        FakeReviewSource(fixture("active"), committed),
        sessions,
        RecordingDeliveryService(),
        clock,
    )
    await terminal_watcher.subscribe("session-1", "D90000001", DEFAULT_EVENT_TYPES)
    clock.advance(70)
    await terminal_watcher.run_iteration()
    terminal_subscription = terminal_watcher.repository.subscription("session-1", "D90000001")
    assert terminal_subscription is not None
    assert terminal_subscription.state is SubscriptionState.RETIRED

    missing = failed_components.model_copy(update={"diff_id": "D90000001"})
    missing_watcher = _watcher(
        tmp_path / "missing",
        FakeReviewSource(fixture("active"), missing, missing),
        sessions,
        RecordingDeliveryService(),
        clock,
    )
    await missing_watcher.subscribe("session-1", "D90000001", DEFAULT_EVENT_TYPES)
    clock.advance(70)
    await missing_watcher.run_iteration()
    first_missing = missing_watcher.repository.subscription("session-1", "D90000001")
    assert first_missing is not None
    assert first_missing.state is SubscriptionState.ACTIVE

    clock.advance(130)
    await missing_watcher.run_iteration()
    second_missing = missing_watcher.repository.subscription("session-1", "D90000001")
    assert second_missing is not None
    assert second_missing.state is SubscriptionState.RETIRED
    assert second_missing.retired_reason == "missing"


@pytest.mark.asyncio
async def test_two_subscribers_share_poll_and_get_separate_batches(
    tmp_path: Path,
) -> None:
    clock = FakeClock()
    new = _new_snapshot(clock, include_ci=False)
    source = FakeReviewSource(fixture("active"), fixture("active"), new)
    sessions = FakeSessionService(
        SessionSnapshot("session-1", {}),
        SessionSnapshot("session-2", {}),
    )
    watcher = _watcher(tmp_path, source, sessions, RecordingDeliveryService(), clock)
    first, _ = await watcher.subscribe("session-1", "D90000001", DEFAULT_EVENT_TYPES)
    second, _ = await watcher.subscribe("session-2", "D90000001", DEFAULT_EVENT_TYPES)
    clock.advance(70)
    await watcher.run_iteration()

    assert len(source.calls) == 3
    assert watcher.repository.open_batch_for(first.id) is not None
    assert watcher.repository.open_batch_for(second.id) is not None


@pytest.mark.asyncio
async def test_unavailable_session_suspends_then_recovers(tmp_path: Path) -> None:
    clock = FakeClock()
    source = FakeReviewSource(fixture("active"))
    unavailable = SessionSnapshot("session-1", {}, reachable=False)
    sessions = FakeSessionService(unavailable)
    watcher = _watcher(tmp_path, source, sessions, RecordingDeliveryService(), clock)
    await watcher.subscribe("session-1", "D90000001", DEFAULT_EVENT_TYPES)

    clock.advance(7)
    await watcher.run_iteration()
    clock.advance(25)
    await watcher.run_iteration()
    assert (
        watcher.repository.subscription("session-1", "D90000001").state  # type: ignore[union-attr]
        is SubscriptionState.SUSPENDED
    )
    assert (
        watcher.repository.claim_due_watches(
            now=clock.now().timestamp() + 1000,
            owner="other",
            lease_seconds=10,
            limit=2,
        )
        == []
    )

    sessions.snapshots["session-1"] = SessionSnapshot("session-1", {}, reachable=True)
    clock.advance(7)
    await watcher.run_iteration()
    assert (
        watcher.repository.subscription("session-1", "D90000001").state  # type: ignore[union-attr]
        is SubscriptionState.ACTIVE
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "snapshot",
    [
        SessionSnapshot("session-1", {}, exists=False),
        SessionSnapshot("session-1", {}, archived=True),
        SessionSnapshot("session-1", {}, closed=True),
    ],
)
async def test_terminal_sessions_retire(snapshot: SessionSnapshot, tmp_path: Path) -> None:
    clock = FakeClock()
    sessions = FakeSessionService(SessionSnapshot("session-1", {}))
    source = FakeReviewSource(fixture("active"))
    watcher = _watcher(
        tmp_path,
        source,
        sessions,
        RecordingDeliveryService(),
        clock,
    )
    await watcher.subscribe("session-1", "D90000001", DEFAULT_EVENT_TYPES)
    sessions.snapshots["session-1"] = snapshot
    clock.advance(70)
    await watcher.run_iteration()
    assert (
        watcher.repository.subscription("session-1", "D90000001").state  # type: ignore[union-attr]
        is SubscriptionState.RETIRED
    )
    assert len(source.calls) == 1
