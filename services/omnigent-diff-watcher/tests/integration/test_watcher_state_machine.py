from __future__ import annotations

import asyncio
from collections import deque
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from omnigent_diff_watcher.domain import (
    DEFAULT_EVENT_TYPES,
    EventDeliveryResult,
    EventDeliveryStatus,
    SessionSnapshot,
    SubscriptionState,
    WatcherConfig,
)
from omnigent_diff_watcher.repository import (
    WatcherRepository,
)
from omnigent_diff_watcher.source_models import (
    CIAggregateState,
    CIFailure,
    CISnapshot,
    CommentsSnapshot,
    DiffSnapshot,
    ReviewComment,
)
from omnigent_diff_watcher.watcher import (
    DiffWatcher,
    SubscriptionError,
)

FIXTURES = Path(__file__).parents[1] / "fixtures"


def load_snapshot(name: str = "green.json") -> DiffSnapshot:
    return DiffSnapshot.model_validate_json((FIXTURES / name).read_text())


class FakeClock:
    def __init__(self, now: datetime) -> None:
        self.current = now

    def now(self) -> datetime:
        return self.current

    async def sleep(self, seconds: float) -> None:
        self.advance(seconds)

    def advance(self, seconds: float) -> None:
        self.current += timedelta(seconds=seconds)


class FakeSource:
    def __init__(self, *snapshots: DiffSnapshot | Exception) -> None:
        self.snapshots = deque(snapshots)
        self.calls: list[tuple[str, object]] = []
        self.block: asyncio.Event | None = None

    async def snapshot(self, diff_id: str, previous: object) -> DiffSnapshot:
        self.calls.append((diff_id, previous))
        if self.block is not None:
            await self.block.wait()
        value = self.snapshots.popleft()
        if isinstance(value, Exception):
            raise value
        return value


class FakeSessions:
    def __init__(self, snapshot: SessionSnapshot) -> None:
        self.snapshot = snapshot
        self.calls = 0

    async def get(self, session_id: str) -> SessionSnapshot:
        self.calls += 1
        return self.snapshot.__class__(**{**self.snapshot.__dict__, "session_id": session_id})


class RecordingDelivery:
    def __init__(self, *outcomes: EventDeliveryStatus | Exception) -> None:
        self.outcomes = deque(outcomes)
        self.calls: list[tuple[str, str, str]] = []

    async def deliver_message(
        self, session_id: str, delivery_id: str, content: str
    ) -> EventDeliveryResult:
        self.calls.append((session_id, delivery_id, content))
        outcome = self.outcomes.popleft()
        if isinstance(outcome, Exception):
            raise outcome
        return EventDeliveryResult(status=outcome)


def event_snapshot(base: DiffSnapshot, suffix: str = "1") -> DiffSnapshot:
    comment = ReviewComment(
        external_id=f"comment-{suffix}",
        version_id=base.latest_version_id or "",
        updated_at=base.observed_at + timedelta(minutes=1),
        content_fingerprint="sha256:" + suffix[-1] * 64,
    )
    failure = CIFailure(
        external_id=f"signal-{suffix}",
        fingerprint="sha256:" + ("a" if suffix[-1] == "1" else "b") * 64,
    )
    return base.model_copy(
        update={
            "observed_at": base.observed_at + timedelta(minutes=1),
            "comments": CommentsSnapshot(
                status="ok", cursor=f"comments-{suffix}", items=(comment,)
            ),
            "ci": CISnapshot(
                status="ok",
                cursor=f"ci-{suffix}",
                aggregate=CIAggregateState.FAILING,
                failures=(failure,),
            ),
        }
    )


def make_watcher(
    tmp_path: Path,
    source: FakeSource,
    sessions: FakeSessions,
    delivery: RecordingDelivery,
    clock: FakeClock,
    *,
    path: Path | None = None,
) -> DiffWatcher:
    config = WatcherConfig(
        batch_window_seconds=10,
        minimum_delivery_interval_seconds=20,
        poll_concurrency=2,
        poll_lease_seconds=30,
        unavailable_suspend_seconds=40,
        liveness_probe_seconds=10,
        suspended_liveness_probe_seconds=10,
        completed_retention_seconds=100,
        delivery_retry_seconds=5,
    )
    return DiffWatcher(
        WatcherRepository(path or (tmp_path / "watcher.db")),
        source,
        sessions,
        delivery,
        clock=clock,
        config=config,
        owner="test-owner",
    )


@pytest.mark.asyncio
async def test_subscribe_validates_baseline_and_terminal_session(
    tmp_path: Path,
) -> None:
    base = load_snapshot()
    clock = FakeClock(base.observed_at)
    closed = FakeSessions(SessionSnapshot("session-1", {}, closed=True))
    watcher = make_watcher(
        tmp_path,
        FakeSource(base),
        closed,
        RecordingDelivery(EventDeliveryStatus.ACCEPTED),
        clock,
    )
    with pytest.raises(SubscriptionError, match="closed"):
        await watcher.subscribe("session-1", base.diff_id, DEFAULT_EVENT_TYPES)

    partial = load_snapshot("partial_failure.json")
    watcher = make_watcher(
        tmp_path,
        FakeSource(partial),
        FakeSessions(SessionSnapshot("session-1", {})),
        RecordingDelivery(EventDeliveryStatus.ACCEPTED),
        clock,
        path=tmp_path / "partial.db",
    )
    with pytest.raises(SubscriptionError, match="CI baseline"):
        await watcher.subscribe("session-1", partial.diff_id, DEFAULT_EVENT_TYPES)


@pytest.mark.asyncio
async def test_two_subscribers_share_one_poll_and_get_separate_batches(
    tmp_path: Path,
) -> None:
    base = load_snapshot()
    changed = event_snapshot(base)
    clock = FakeClock(base.observed_at)
    source = FakeSource(base, base, changed)
    watcher = make_watcher(
        tmp_path,
        source,
        FakeSessions(SessionSnapshot("session-1", {})),
        RecordingDelivery(EventDeliveryStatus.ACCEPTED),
        clock,
    )
    first, _ = await watcher.subscribe("session-1", base.diff_id, DEFAULT_EVENT_TYPES)
    second, _ = await watcher.subscribe("session-2", base.diff_id, DEFAULT_EVENT_TYPES)
    clock.advance(400)
    await watcher.run_iteration()

    assert len(source.calls) == 3
    assert watcher.repository.open_batch_for(first.id) is not None
    assert watcher.repository.open_batch_for(second.id) is not None


def test_two_repository_instances_cannot_overlap_one_poll(tmp_path: Path) -> None:
    path = tmp_path / "watcher.db"
    first = WatcherRepository(path)
    base = load_snapshot()
    first.subscribe(
        "session-1",
        base.diff_id,
        DEFAULT_EVENT_TYPES,
        base,
        now=1000,
        next_poll_at=1000,
    )
    second = WatcherRepository(path)
    claimed_a = first.claim_due_watches(now=1000, owner="a", lease_seconds=30, limit=2)
    claimed_b = second.claim_due_watches(now=1000, owner="b", lease_seconds=30, limit=2)
    assert [watch.diff_id for watch in claimed_a + claimed_b] == [base.diff_id]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("outcome", "expected_state", "expected_subscription"),
    [
        (EventDeliveryStatus.ACCEPTED, "delivered", SubscriptionState.ACTIVE),
        (EventDeliveryStatus.ALREADY_ACCEPTED, "delivered", SubscriptionState.ACTIVE),
        (EventDeliveryStatus.DEFERRED, "open", SubscriptionState.ACTIVE),
        (EventDeliveryStatus.TERMINAL, "cancelled", SubscriptionState.RETIRED),
        (RuntimeError("transport"), "open", SubscriptionState.ACTIVE),
    ],
)
async def test_delivery_outcomes_are_durable(
    tmp_path: Path,
    outcome: EventDeliveryStatus | Exception,
    expected_state: str,
    expected_subscription: SubscriptionState,
) -> None:
    base = load_snapshot()
    clock = FakeClock(base.observed_at)
    delivery = RecordingDelivery(outcome)
    watcher = make_watcher(
        tmp_path,
        FakeSource(base),
        FakeSessions(SessionSnapshot("session-1", {})),
        delivery,
        clock,
        path=tmp_path / f"{expected_state}-{type(outcome).__name__}.db",
    )
    subscription, _ = await watcher.subscribe("session-1", base.diff_id, DEFAULT_EVENT_TYPES)
    watcher.repository.apply_snapshot(
        event_snapshot(base),
        now=clock.now().timestamp() + 1,
        next_poll_at=clock.now().timestamp() + 60,
        batch_window_seconds=10,
    )
    batch = watcher.repository.open_batch_for(subscription.id)
    assert batch is not None
    clock.advance(11)
    await watcher._flush_batch(batch)

    persisted = watcher.repository.batch(batch.batch_id)
    assert persisted is not None
    assert persisted.state.value == expected_state
    assert watcher.repository.subscription("session-1").state is expected_subscription  # type: ignore[union-attr]
    assert len(delivery.calls) == 1


@pytest.mark.asyncio
async def test_restart_from_delivering_reuses_batch_id(tmp_path: Path) -> None:
    path = tmp_path / "watcher.db"
    base = load_snapshot()
    clock = FakeClock(base.observed_at)
    first = make_watcher(
        tmp_path,
        FakeSource(base),
        FakeSessions(SessionSnapshot("session-1", {})),
        RecordingDelivery(EventDeliveryStatus.ACCEPTED),
        clock,
        path=path,
    )
    subscription, _ = await first.subscribe("session-1", base.diff_id, DEFAULT_EVENT_TYPES)
    first.repository.apply_snapshot(
        event_snapshot(base),
        now=clock.now().timestamp() + 1,
        next_poll_at=clock.now().timestamp() + 60,
        batch_window_seconds=10,
    )
    batch = first.repository.open_batch_for(subscription.id)
    assert batch is not None
    first.repository.prepare_batch(batch.batch_id, now=clock.now().timestamp() + 11)

    delivery = RecordingDelivery(EventDeliveryStatus.ALREADY_ACCEPTED)
    restarted = make_watcher(
        tmp_path,
        FakeSource(base),
        FakeSessions(SessionSnapshot("session-1", {})),
        delivery,
        clock,
        path=path,
    )
    current = restarted.repository.batch(batch.batch_id)
    assert current is not None
    await restarted._flush_batch(current)
    assert delivery.calls[0][1] == batch.batch_id
    assert restarted.repository.batch(batch.batch_id).state.value == "delivered"  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_busy_session_defers_and_ten_minimum_interval_coalesces(
    tmp_path: Path,
) -> None:
    base = load_snapshot()
    clock = FakeClock(base.observed_at)
    sessions = FakeSessions(SessionSnapshot("session-1", {}, can_accept_input=False))
    delivery = RecordingDelivery(
        EventDeliveryStatus.ACCEPTED,
        EventDeliveryStatus.ACCEPTED,
    )
    watcher = make_watcher(tmp_path, FakeSource(base), sessions, delivery, clock)
    subscription, _ = await watcher.subscribe("session-1", base.diff_id, DEFAULT_EVENT_TYPES)
    watcher.repository.apply_snapshot(
        event_snapshot(base),
        now=clock.now().timestamp() + 1,
        next_poll_at=clock.now().timestamp() + 60,
        batch_window_seconds=10,
    )
    batch = watcher.repository.open_batch_for(subscription.id)
    assert batch is not None
    clock.advance(11)
    await watcher._flush_batch(batch)
    assert delivery.calls == []
    assert watcher.repository.open_batch_for(subscription.id) is not None

    sessions.snapshot = SessionSnapshot("session-1", {}, can_accept_input=True)
    clock.advance(5)
    current = watcher.repository.open_batch_for(subscription.id)
    assert current is not None
    await watcher._flush_batch(current)
    assert len(delivery.calls) == 1

    watcher.repository.apply_snapshot(
        event_snapshot(base, "2"),
        now=clock.now().timestamp() + 1,
        next_poll_at=clock.now().timestamp() + 60,
        batch_window_seconds=10,
    )
    second = watcher.repository.open_batch_for(subscription.id)
    assert second is not None
    clock.advance(11)
    await watcher._flush_batch(second)
    assert len(delivery.calls) == 1
    assert watcher.repository.open_batch_for(subscription.id) is not None


@pytest.mark.asyncio
async def test_unavailable_session_suspends_then_recovers(tmp_path: Path) -> None:
    base = load_snapshot()
    clock = FakeClock(base.observed_at)
    sessions = FakeSessions(SessionSnapshot("session-1", {}, reachable=False))
    watcher = make_watcher(
        tmp_path,
        FakeSource(base),
        sessions,
        RecordingDelivery(EventDeliveryStatus.ACCEPTED),
        clock,
    )
    await watcher.subscribe("session-1", base.diff_id, DEFAULT_EVENT_TYPES)
    await watcher._check_liveness("session-1")
    clock.advance(41)
    await watcher._check_liveness("session-1")
    assert watcher.repository.subscription("session-1").state is SubscriptionState.SUSPENDED  # type: ignore[union-attr]
    assert (
        watcher.repository.claim_due_watches(
            now=clock.now().timestamp() + 100,
            owner="another",
            lease_seconds=30,
            limit=2,
        )
        == []
    )

    sessions.snapshot = SessionSnapshot("session-1", {}, reachable=True)
    await watcher._check_liveness("session-1")
    assert watcher.repository.subscription("session-1").state is SubscriptionState.ACTIVE  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_poll_cancellation_releases_lease(tmp_path: Path) -> None:
    base = load_snapshot()
    clock = FakeClock(base.observed_at)
    source = FakeSource(base)
    watcher = make_watcher(
        tmp_path,
        source,
        FakeSessions(SessionSnapshot("session-1", {})),
        RecordingDelivery(EventDeliveryStatus.ACCEPTED),
        clock,
    )
    await watcher.subscribe("session-1", base.diff_id, DEFAULT_EVENT_TYPES)
    source.snapshots.append(base)
    source.block = asyncio.Event()
    watch = watcher.repository.claim_watch(
        base.diff_id,
        now=clock.now().timestamp(),
        owner=watcher.owner,
        lease_seconds=30,
    )
    assert watch is not None
    task = asyncio.create_task(watcher._poll_watch(watch))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert (
        watcher.repository.claim_watch(
            base.diff_id,
            now=clock.now().timestamp(),
            owner="other",
            lease_seconds=30,
        )
        is not None
    )
