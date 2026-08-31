"""Pure scheduling, normalization, and message-rendering logic."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from datetime import datetime

from .domain import EventKind, NormalizedEvent
from .source_models import CIAggregateState, DiffSnapshot, fingerprint

_FAILURE_DELAYS = (60.0, 120.0, 300.0, 900.0, 1800.0)


def successful_poll_delay(snapshot: DiffSnapshot, now: datetime) -> float:
    """Return the unjittered adaptive interval for a successful snapshot."""
    if snapshot.ci.aggregate is CIAggregateState.PENDING:
        return 60.0
    idle_seconds = max(0.0, (now - snapshot.last_activity_at).total_seconds())
    if idle_seconds < 60 * 60:
        return 60.0
    if idle_seconds < 6 * 60 * 60:
        return 5 * 60.0
    if idle_seconds < 24 * 60 * 60:
        return 15 * 60.0
    if idle_seconds < 3 * 24 * 60 * 60:
        return 60 * 60.0
    if idle_seconds < 14 * 24 * 60 * 60:
        return 6 * 60 * 60.0
    return 24 * 60 * 60.0


def deterministic_jitter(delay: float, diff_id: str, cycle: int) -> float:
    """Apply stable +/-10 percent jitter without global random state."""
    digest = hashlib.sha256(f"{diff_id}:{cycle}".encode()).digest()
    fraction = int.from_bytes(digest[:8], "big") / ((1 << 64) - 1)
    return delay * (0.9 + 0.2 * fraction)


def failure_poll_delay(failure_count: int, diff_id: str) -> float:
    index = min(max(failure_count, 1), len(_FAILURE_DELAYS)) - 1
    return deterministic_jitter(_FAILURE_DELAYS[index], diff_id, failure_count)


def normalize_snapshot(
    snapshot: DiffSnapshot,
) -> dict[EventKind, tuple[NormalizedEvent, ...]]:
    """Normalize only source components that succeeded in this poll."""
    result: dict[EventKind, tuple[NormalizedEvent, ...]] = {}
    if snapshot.comments.status == "ok":
        result[EventKind.REVIEW_COMMENT] = tuple(
            NormalizedEvent(
                diff_id=snapshot.diff_id,
                kind=EventKind.REVIEW_COMMENT,
                external_id=item.external_id,
                version_id=item.version_id,
                fingerprint=item.content_fingerprint,
                changed_at=item.updated_at,
            )
            for item in snapshot.comments.items
            if item.version_id == snapshot.latest_version_id
        )
    if snapshot.ci.status == "ok":
        version_id = snapshot.latest_version_id or ""
        result[EventKind.CI_FAILURE] = tuple(
            NormalizedEvent(
                diff_id=snapshot.diff_id,
                kind=EventKind.CI_FAILURE,
                external_id=item.external_id,
                version_id=version_id,
                fingerprint=item.fingerprint,
                changed_at=snapshot.observed_at,
            )
            for item in snapshot.ci.failures
        )
        # One event per version rather than per poll: the external ID and the
        # fingerprint are both version-scoped, so re-observing a green run
        # deduplicates instead of waking the session again.
        result[EventKind.CI_GREEN] = (
            (
                NormalizedEvent(
                    diff_id=snapshot.diff_id,
                    kind=EventKind.CI_GREEN,
                    external_id=f"green:{version_id}",
                    version_id=version_id,
                    fingerprint=fingerprint(f"{version_id}:{snapshot.ci.aggregate.value}"),
                    changed_at=snapshot.observed_at,
                ),
            )
            if snapshot.ci.green
            else ()
        )
    if snapshot.ai_reviews.status == "ok":
        result[EventKind.AI_REVIEW] = tuple(
            NormalizedEvent(
                diff_id=snapshot.diff_id,
                kind=EventKind.AI_REVIEW,
                external_id=item.external_id,
                version_id=snapshot.latest_version_id or "",
                fingerprint=item.fingerprint,
                changed_at=snapshot.observed_at,
            )
            for item in snapshot.ai_reviews.items
        )
    return result


# Rendered in this order so a wake leads with what needs action. Each entry is
# (singular, plural); CI_GREEN is a state report rather than a count, so it
# renders through a separate branch below.
_KIND_NOUNS: dict[EventKind, tuple[str, str]] = {
    EventKind.REVIEW_COMMENT: ("unresolved review comment", "unresolved review comments"),
    EventKind.CI_FAILURE: ("current-version CI failure", "current-version CI failures"),
    EventKind.AI_REVIEW: (
        "unresolved automated-review finding",
        "unresolved automated-review findings",
    ),
}


def _join(parts: Sequence[str]) -> str:
    if len(parts) == 1:
        return parts[0]
    return f"{', '.join(parts[:-1])} and {parts[-1]}"


def _describe_counts(counts: Mapping[EventKind, int]) -> str:
    parts = [
        f"{count} {nouns[0] if count == 1 else nouns[1]}"
        for kind, nouns in _KIND_NOUNS.items()
        if (count := counts.get(kind, 0))
    ]
    if counts.get(EventKind.CI_GREEN, 0):
        parts.append("CI green")
    if not parts:
        raise ValueError("cannot render an empty watcher batch")
    return _join(parts)


def render_batch_summary(
    batch_id: str,
    counts_by_diff: Sequence[tuple[str, Mapping[EventKind, int]]],
) -> str:
    """Render one concise wake without raw comments, URLs, or CI logs.

    ``counts_by_diff`` is ``(diff_id, counts_by_kind)`` per diff, in a stable
    caller-chosen order. A session watching a stack gets one message covering
    every affected diff rather than one wake per diff.
    """
    described = [
        (diff_id, _describe_counts(counts))
        for diff_id, counts in counts_by_diff
        if any(counts.values())
    ]
    if not described:
        raise ValueError("cannot render an empty watcher batch")
    if len(described) == 1:
        diff_id, joined = described[0]
        body = f"{diff_id} has {joined}"
        tail = "the diff"
    else:
        body = "; ".join(f"{diff_id} has {joined}" for diff_id, joined in described)
        tail = "each diff"
    return (
        f"[Diff watcher {batch_id}] {body}. "
        "Load the current diff review and CI "
        f"state, address actionable findings, and update {tail} as needed."
    )
