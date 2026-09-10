"""Typed domain contracts for the diff watcher state machine."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol

from .source_models import DiffSnapshot, SourceCursor


class EventKind(StrEnum):
    """The closed vocabulary of wake reasons, shared by every source.

    Kinds are shared rather than source-private so ``parse_event_types`` can
    validate a subscription and the MCP surface can stay typed. A source
    declares the subset it emits through ``WatchSource.event_kinds``.
    """

    REVIEW_COMMENT = "review_comment"
    CI_FAILURE = "ci_failure"
    AI_REVIEW = "ai_review"
    CI_GREEN = "ci_green"
    CHANGED = "changed"


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
    subject: str
    kind: EventKind
    external_id: str
    version_id: str
    fingerprint: str
    changed_at: datetime


class Lifecycle(StrEnum):
    """Whether a subject is still worth polling.

    Coarser than any one source's own states: the engine only needs to know
    whether to keep polling, stop, or count another consecutive miss. The
    source's own word for it travels alongside in ``PollResult.state_label``.
    """

    ACTIVE = "active"
    TERMINAL = "terminal"
    MISSING = "missing"


@dataclass(frozen=True)
class PollResult:
    """One source's authoritative reading of a subject.

    This is the contract the engine is written against. It replaced a
    diff-shaped snapshot so that leasing, fingerprint diffing, batching and
    delivery -- none of which ever depended on the subject being a diff -- can
    serve any source.

    ``ok_kinds`` and ``failed_kinds`` partition the kinds this source was asked
    for. A source that reads some kinds and fails others reports both, so the
    engine can persist real progress while still backing off; a poll with no
    ``ok_kinds`` is a total failure.
    """

    subject: str
    source: str
    lifecycle: Lifecycle
    state_label: str
    latest_version_id: str | None
    last_activity_at: datetime
    observed_at: datetime
    cursor: str | None
    status: str
    events: Mapping[EventKind, tuple[NormalizedEvent, ...]]
    ok_kinds: frozenset[EventKind]
    failed_kinds: frozenset[EventKind] = frozenset()
    error_category: str | None = None
    poll_hint_seconds: float | None = None
    # Kinds whose identity is scoped to ``latest_version_id``; the engine stops
    # treating them as actionable once the subject moves to a new revision.
    version_scoped_kinds: frozenset[EventKind] = frozenset()

    @property
    def totally_failed(self) -> bool:
        return not self.ok_kinds and bool(self.failed_kinds)

    @property
    def partially_failed(self) -> bool:
        return bool(self.ok_kinds) and bool(self.failed_kinds)


@dataclass(frozen=True)
class WatchedSubject:
    subject: str
    source: str
    lifecycle: str
    latest_version_id: str | None
    last_activity_at: float
    next_poll_at: float
    cursor: str | None
    spec: str | None
    failure_count: int
    last_success_at: float | None


@dataclass(frozen=True)
class Subscription:
    id: int
    session_id: str
    subject: str
    event_types: frozenset[EventKind]
    state: SubscriptionState
    baseline_at: float
    last_delivery_at: float | None
    unavailable_since: float | None
    retired_reason: str | None


@dataclass(frozen=True)
class Batch:
    """One pending wake for a session, spanning every diff it watches.

    Batches are session-scoped rather than subscription-scoped so a stack whose
    diffs all go red produces a single message instead of one per diff.
    ``subjects`` is derived from the batch's events, so it is empty only for a
    batch that has just been pruned empty.
    """

    batch_id: str
    session_id: str
    subjects: tuple[str, ...]
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
    """Reads a diff's current review and CI state.

    ``snapshot`` raises ``source_models.ReviewSourceError`` when the diff
    cannot be read at all, which callers must distinguish from a snapshot that
    reports per-section failures.
    """

    async def snapshot(
        self,
        subject: str,
        previous: SourceCursor | None,
    ) -> DiffSnapshot: ...


class WatchSource(Protocol):
    """Reads one subject and reports it in the engine's terms.

    ``poll`` raises ``source_models.ReviewSourceError`` when the subject cannot
    be read at all, which callers must distinguish from a ``PollResult`` that
    reports per-kind failures.
    """

    @property
    def name(self) -> str: ...

    @property
    def event_kinds(self) -> frozenset[EventKind]:
        """The kinds this source can emit; bounds what may be subscribed."""
        ...

    def validate_subject(self, subject: str, spec: str | None) -> None:
        """Reject a subject or spec this source cannot poll, before it is stored."""
        ...

    async def poll(
        self,
        subject: str,
        cursor: str | None,
        spec: str | None,
    ) -> PollResult: ...

    def describe(self, counts: Mapping[EventKind, int]) -> str:
        """Render this source's share of a wake message."""
        ...


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
    max_active_subjects: int = 100
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
            self.max_active_subjects,
            self.delivery_retry_seconds,
        )
        if any(value <= 0 for value in numeric):
            raise ValueError("watcher limits and intervals must be positive")
        if (
            self.poll_interval_override_seconds is not None
            and self.poll_interval_override_seconds <= 0
        ):
            raise ValueError("poll interval override must be positive")


# Which kinds each source can emit. A subscription is only meaningful for the
# kinds its source actually produces, so these also bound what the MCP surface
# offers per tool rather than offering the union to everyone.
DIFF_EVENT_KINDS = frozenset(
    {
        EventKind.REVIEW_COMMENT,
        EventKind.CI_FAILURE,
        EventKind.AI_REVIEW,
        EventKind.CI_GREEN,
    }
)
COMMAND_EVENT_KINDS = frozenset({EventKind.CHANGED})

DEFAULT_EVENT_TYPES = DIFF_EVENT_KINDS


def parse_event_types(
    values: Sequence[str] | None,
    default: frozenset[EventKind] = DEFAULT_EVENT_TYPES,
) -> frozenset[EventKind]:
    if values is None:
        return default
    parsed = frozenset(EventKind(value) for value in values)
    if not parsed:
        raise ValueError("at least one event type is required")
    return parsed
