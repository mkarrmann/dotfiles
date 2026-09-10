from __future__ import annotations

import json
from collections import deque
from datetime import UTC, datetime, timedelta
from pathlib import Path

from omnigent_diff_watcher.domain import (
    DIFF_EVENT_KINDS,
    EventDeliveryResult,
    EventDeliveryStatus,
    EventKind,
    PollResult,
    SessionSnapshot,
    Subscription,
)
from omnigent_diff_watcher.logic import PHABRICATOR_SOURCE
from omnigent_diff_watcher.phabricator_source import to_poll_result
from omnigent_diff_watcher.repository import WatcherRepository
from omnigent_diff_watcher.source_models import (
    DiffSnapshot,
    SourceCursor,
)

FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name: str) -> DiffSnapshot:
    return DiffSnapshot.model_validate(json.loads((FIXTURES / f"{name}.json").read_text()))


class FakeClock:
    def __init__(self, now: datetime | None = None) -> None:
        self.current = now or datetime(2026, 1, 15, 12, 5, tzinfo=UTC)

    def now(self) -> datetime:
        return self.current

    async def sleep(self, seconds: float) -> None:
        self.advance(seconds)

    def advance(self, seconds: float) -> None:
        self.current += timedelta(seconds=seconds)


def subscribe_snapshot(
    repository: WatcherRepository,
    session_id: str,
    subject: str,
    event_types: frozenset[EventKind],
    snapshot: DiffSnapshot,
    *,
    now: float,
    next_poll_at: float,
    max_active_subjects: int | None = None,
    spec: str | None = None,
) -> tuple[Subscription, bool]:
    """Baseline a subscription from a diff snapshot. See ``apply_snapshot``."""
    return repository.subscribe(
        session_id,
        subject,
        event_types,
        to_poll_result(snapshot),
        now=now,
        next_poll_at=next_poll_at,
        max_active_subjects=max_active_subjects,
        spec=spec,
    )


def apply_snapshot(
    repository: WatcherRepository,
    snapshot: DiffSnapshot,
    *,
    now: float,
    next_poll_at: float,
    batch_window_seconds: float,
) -> int:
    """Apply a diff snapshot through the source-neutral engine entry point.

    The engine takes a ``PollResult``; these tests are about diff behaviour and
    are clearest written in diff snapshots, so they adapt at the call rather
    than restating every fixture.

    The stored cursor is read back first, exactly as the real poll path does.
    Without it a snapshot whose CI section failed would report no CI cursor at
    all rather than the one it last succeeded with.
    """
    watch = repository.watch(snapshot.subject)
    previous = _as_cursor(watch.cursor) if watch is not None else None
    return repository.apply_poll(
        to_poll_result(snapshot, previous),
        now=now,
        next_poll_at=next_poll_at,
        batch_window_seconds=batch_window_seconds,
    )


class DiffSourceMixin:
    """Give a snapshot-only test fake the generic ``WatchSource`` surface.

    The tests' fakes are written in diff snapshots because that is what makes
    their assertions readable. The engine takes a ``PollResult``, so this
    adapts through the real Phabricator adapter rather than a second,
    divergent one.
    """

    async def snapshot(self, subject: str, previous: SourceCursor | None) -> DiffSnapshot:
        raise NotImplementedError

    @property
    def name(self) -> str:
        return PHABRICATOR_SOURCE

    @property
    def event_kinds(self) -> frozenset[EventKind]:
        return DIFF_EVENT_KINDS

    def validate_subject(self, subject: str, spec: str | None) -> None:
        del subject, spec

    async def poll(
        self,
        subject: str,
        cursor: str | None = None,
        spec: str | None = None,
    ) -> PollResult:
        del spec
        previous = _as_cursor(cursor)
        return to_poll_result(await self.snapshot(subject, previous), previous)


class FakeReviewSource(DiffSourceMixin):
    """A diff source in the tests' terms, exposed as a generic WatchSource."""

    def __init__(self, *outcomes: DiffSnapshot | Exception) -> None:
        self.outcomes = deque(outcomes)
        self.calls: list[tuple[str, SourceCursor | None]] = []
        self.active = 0
        self.max_active = 0

    async def snapshot(
        self,
        subject: str,
        previous: SourceCursor | None,
    ) -> DiffSnapshot:
        self.calls.append((subject, previous))
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            if not self.outcomes:
                raise AssertionError(f"no fake source outcome left for {subject}")
            outcome = self.outcomes.popleft()
            if isinstance(outcome, Exception):
                raise outcome
            return outcome
        finally:
            self.active -= 1


def _as_cursor(cursor: str | None) -> SourceCursor | None:
    if not cursor:
        return None
    try:
        return SourceCursor.model_validate_json(cursor)
    except ValueError:
        return None


class FakeSessionService:
    def __init__(self, *snapshots: SessionSnapshot) -> None:
        self.snapshots = {snapshot.session_id: snapshot for snapshot in snapshots}
        self.calls: list[str] = []

    async def get(self, session_id: str) -> SessionSnapshot:
        self.calls.append(session_id)
        return self.snapshots.get(
            session_id,
            SessionSnapshot(session_id=session_id, labels={}, exists=False),
        )


class RecordingDeliveryService:
    def __init__(self, *outcomes: EventDeliveryStatus | Exception) -> None:
        self.outcomes = deque(outcomes or (EventDeliveryStatus.ACCEPTED,))
        self.calls: list[tuple[str, str, str]] = []

    async def deliver_message(
        self,
        session_id: str,
        delivery_id: str,
        content: str,
    ) -> EventDeliveryResult:
        self.calls.append((session_id, delivery_id, content))
        outcome = self.outcomes.popleft() if self.outcomes else EventDeliveryStatus.ACCEPTED
        if isinstance(outcome, Exception):
            raise outcome
        return EventDeliveryResult(status=outcome)
