"""Typed domain contracts for the diff watcher state machine."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol

from .source_models import DiffSnapshot, SourceCursor


class EventKind(StrEnum):
    REVIEW_COMMENT = "review_comment"
    CI_FAILURE = "ci_failure"


class SubscriptionState(StrEnum):
    ACTIVE = "active"
    SUSPENDED = "suspended"
    RETIRED = "retired"


class BatchState(StrEnum):
    OPEN = "open"
    DELIVERING = "delivering"
    DELIVERED = "delivered"
    CANCELLED = "cancelled"


class EventDeliveryStatus(StrEnum):
    ACCEPTED = "accepted"
    ALREADY_ACCEPTED = "already_accepted"
    DEFERRED = "deferred"
    TERMINAL = "terminal"


@dataclass(frozen=True)
class EventDeliveryResult:
    status: EventDeliveryStatus


class Clock(Protocol):
    def now(self) -> datetime: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


@dataclass(frozen=True)
class NormalizedEvent:
    diff_id: str
    kind: EventKind
    external_id: str
    version_id: str
    fingerprint: str
    changed_at: datetime


@dataclass(frozen=True)
class WatchedDiff:
    diff_id: str
    lifecycle: str
    latest_version_id: str | None
    last_activity_at: float
    next_poll_at: float
    cursor: SourceCursor
    failure_count: int
    last_success_at: float | None


@dataclass(frozen=True)
class Subscription:
    id: int
    session_id: str
    diff_id: str
    event_types: frozenset[EventKind]
    state: SubscriptionState
    baseline_at: float
    last_delivery_at: float | None
    unavailable_since: float | None
    retired_reason: str | None


@dataclass(frozen=True)
class Batch:
    batch_id: str
    subscription_id: int
    session_id: str
    diff_id: str
    state: BatchState
    first_event_at: float
    flush_at: float
    retry_count: int
    next_attempt_at: float
    summary: str | None


@dataclass(frozen=True)
class SessionSnapshot:
    session_id: str
    labels: dict[str, str]
    exists: bool = True
    archived: bool = False
    closed: bool = False
    reachable: bool = True
    can_accept_input: bool = True

    @property
    def terminal(self) -> bool:
        return not self.exists or self.archived or self.closed


class ReviewSource(Protocol):
    async def snapshot(
        self,
        diff_id: str,
        previous: SourceCursor | None,
    ) -> DiffSnapshot: ...


class SessionService(Protocol):
    async def get(self, session_id: str) -> SessionSnapshot: ...


class DeliveryService(Protocol):
    async def deliver_message(
        self,
        session_id: str,
        delivery_id: str,
        content: str,
    ) -> EventDeliveryResult: ...


@dataclass(frozen=True)
class WatcherConfig:
    batch_window_seconds: float = 5 * 60
    minimum_delivery_interval_seconds: float = 10 * 60
    poll_concurrency: int = 2
    poll_lease_seconds: float = 2 * 60
    unavailable_suspend_seconds: float = 24 * 60 * 60
    liveness_probe_seconds: float = 5 * 60
    suspended_liveness_probe_seconds: float = 6 * 60 * 60
    completed_retention_seconds: float = 30 * 24 * 60 * 60
    max_active_diffs: int = 100
    delivery_retry_seconds: float = 5 * 60
    poll_interval_override_seconds: float | None = None

    def __post_init__(self) -> None:
        numeric = (
            self.batch_window_seconds,
            self.minimum_delivery_interval_seconds,
            self.poll_concurrency,
            self.poll_lease_seconds,
            self.unavailable_suspend_seconds,
            self.liveness_probe_seconds,
            self.suspended_liveness_probe_seconds,
            self.completed_retention_seconds,
            self.max_active_diffs,
            self.delivery_retry_seconds,
        )
        if any(value <= 0 for value in numeric):
            raise ValueError("watcher limits and intervals must be positive")
        if (
            self.poll_interval_override_seconds is not None
            and self.poll_interval_override_seconds <= 0
        ):
            raise ValueError("poll interval override must be positive")


DEFAULT_EVENT_TYPES = frozenset({EventKind.REVIEW_COMMENT, EventKind.CI_FAILURE})


def parse_event_types(values: Sequence[str] | None) -> frozenset[EventKind]:
    if values is None:
        return DEFAULT_EVENT_TYPES
    parsed = frozenset(EventKind(value) for value in values)
    if not parsed:
        raise ValueError("at least one event type is required")
    return parsed
