from __future__ import annotations

import json
from collections import deque
from datetime import UTC, datetime, timedelta
from pathlib import Path

from omnigent_diff_watcher.domain import (
    EventDeliveryResult,
    EventDeliveryStatus,
    SessionSnapshot,
)
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


class FakeReviewSource:
    def __init__(self, *outcomes: DiffSnapshot | Exception) -> None:
        self.outcomes = deque(outcomes)
        self.calls: list[tuple[str, SourceCursor | None]] = []
        self.active = 0
        self.max_active = 0

    async def snapshot(
        self,
        diff_id: str,
        previous: SourceCursor | None,
    ) -> DiffSnapshot:
        self.calls.append((diff_id, previous))
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            if not self.outcomes:
                raise AssertionError(f"no fake source outcome left for {diff_id}")
            outcome = self.outcomes.popleft()
            if isinstance(outcome, Exception):
                raise outcome
            return outcome
        finally:
            self.active -= 1


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
