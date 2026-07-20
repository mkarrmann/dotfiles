"""Pure scheduling, normalization, and message-rendering logic."""

from __future__ import annotations

import hashlib
from datetime import datetime

from .domain import EventKind, NormalizedEvent
from .source_models import CIAggregateState, DiffSnapshot

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
        result[EventKind.CI_FAILURE] = (
            tuple(
                NormalizedEvent(
                    diff_id=snapshot.diff_id,
                    kind=EventKind.CI_FAILURE,
                    external_id=item.external_id,
                    version_id=snapshot.latest_version_id or "",
                    fingerprint=item.fingerprint,
                    changed_at=snapshot.observed_at,
                )
                for item in snapshot.ci.failures
            )
            if snapshot.ci.aggregate is CIAggregateState.FAILING
            else ()
        )
    return result


def render_batch_summary(
    batch_id: str,
    diff_id: str,
    comment_count: int,
    ci_count: int,
) -> str:
    """Render one concise wake without raw comments, URLs, or CI logs."""
    parts: list[str] = []
    if comment_count:
        noun = "review comment" if comment_count == 1 else "review comments"
        parts.append(f"{comment_count} unresolved {noun}")
    if ci_count:
        noun = "CI failure" if ci_count == 1 else "CI failures"
        parts.append(f"{ci_count} current-version {noun}")
    if not parts:
        raise ValueError("cannot render an empty watcher batch")
    joined = parts[0] if len(parts) == 1 else f"{parts[0]} and {parts[1]}"
    return (
        f"[Diff watcher {batch_id}] {diff_id} has {joined}. "
        "Load the current diff review and CI "
        "state, address actionable findings, and update the diff as needed."
    )
