from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from omnigent_diff_watcher.domain import (
    DEFAULT_EVENT_TYPES,
    BatchState,
    SessionSnapshot,
)
from omnigent_diff_watcher.repository import (
    WatcherRepository,
)
from omnigent_diff_watcher.source_models import (
    DiffSnapshot,
    SourceCursor,
)
from omnigent_diff_watcher.watcher import DiffWatcher
from tests.support import (
    FakeClock,
    FakeSessionService,
    RecordingDeliveryService,
    fixture,
)
from tests.unit.test_repository import (
    _new_comment_snapshot,
)


def test_restart_recovers_open_delivering_and_delivered_batches(tmp_path: Path) -> None:
    path = tmp_path / "watcher.sqlite3"
    clock = FakeClock()
    repository = WatcherRepository(path)
    subscription, _ = repository.subscribe(
        "session-1",
        "D90000001",
        DEFAULT_EVENT_TYPES,
        fixture("active"),
        now=clock.now().timestamp(),
        next_poll_at=clock.now().timestamp() + 60,
    )
    clock.advance(60)
    repository.apply_snapshot(
        _new_comment_snapshot(clock),
        now=clock.now().timestamp(),
        next_poll_at=clock.now().timestamp() + 60,
        batch_window_seconds=5,
    )
    open_batch = repository.open_batch_for(subscription.id)
    assert open_batch is not None

    restarted = WatcherRepository(path)
    assert restarted.open_batch_for(subscription.id) == open_batch
    clock.advance(6)
    assert restarted.prepare_batch(open_batch.batch_id, now=clock.now().timestamp()) == (1, 0)

    restarted_again = WatcherRepository(path)
    delivering = restarted_again.batch(open_batch.batch_id)
    assert delivering is not None
    assert delivering.state is BatchState.DELIVERING
    assert delivering.summary is not None
    restarted_again.deliver_batch(open_batch.batch_id, now=clock.now().timestamp())

    final = WatcherRepository(path).batch(open_batch.batch_id)
    assert final is not None
    assert final.state is BatchState.DELIVERED
    assert WatcherRepository(path).open_batch_for(subscription.id) is None


class _BlockingSource:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.calls = 0

    async def snapshot(
        self,
        diff_id: str,
        previous: SourceCursor | None,
    ) -> DiffSnapshot:
        del diff_id, previous
        self.calls += 1
        self.started.set()
        await self.release.wait()
        return fixture("active")


@pytest.mark.asyncio
async def test_two_scheduler_instances_do_not_overlap_one_diff(tmp_path: Path) -> None:
    path = tmp_path / "watcher.sqlite3"
    clock = FakeClock()
    repository = WatcherRepository(path)
    repository.subscribe(
        "session-1",
        "D90000001",
        DEFAULT_EVENT_TYPES,
        fixture("active"),
        now=clock.now().timestamp(),
        next_poll_at=clock.now().timestamp() + 1,
    )
    source = _BlockingSource()
    sessions = FakeSessionService(SessionSnapshot("session-1", {}))
    first = DiffWatcher(
        WatcherRepository(path),
        source,
        sessions,
        RecordingDeliveryService(),
        clock=clock,
        owner="scheduler-1",
    )
    second = DiffWatcher(
        WatcherRepository(path),
        source,
        sessions,
        RecordingDeliveryService(),
        clock=clock,
        owner="scheduler-2",
    )
    clock.advance(2)
    first_task = asyncio.create_task(first.run_iteration())
    await source.started.wait()
    await second.run_iteration()
    assert source.calls == 1
    source.release.set()
    await first_task


@pytest.mark.asyncio
async def test_poll_cancellation_releases_lease_for_restart(tmp_path: Path) -> None:
    path = tmp_path / "watcher.sqlite3"
    clock = FakeClock()
    repository = WatcherRepository(path)
    repository.subscribe(
        "session-1",
        "D90000001",
        DEFAULT_EVENT_TYPES,
        fixture("active"),
        now=clock.now().timestamp(),
        next_poll_at=clock.now().timestamp() + 1,
    )
    source = _BlockingSource()
    watcher = DiffWatcher(
        repository,
        source,
        FakeSessionService(SessionSnapshot("session-1", {})),
        RecordingDeliveryService(),
        clock=clock,
        owner="cancelled-scheduler",
    )
    clock.advance(2)
    task = asyncio.create_task(watcher.run_iteration())
    await source.started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    reclaimed = WatcherRepository(path).claim_due_watches(
        now=clock.now().timestamp(),
        owner="restarted-scheduler",
        lease_seconds=30,
        limit=1,
    )
    assert [watch.diff_id for watch in reclaimed] == ["D90000001"]
