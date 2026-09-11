"""Pure scheduling and message-rendering logic, shared by every source."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from datetime import datetime

from .domain import EventKind

_FAILURE_DELAYS = (60.0, 120.0, 300.0, 900.0, 1800.0)

PHABRICATOR_SOURCE = "phabricator"


def successful_poll_delay(
    last_activity_at: datetime,
    now: datetime,
    poll_hint_seconds: float | None = None,
) -> float:
    """Return the unjittered adaptive interval for a successful poll.

    A source may override the idle-based ladder with ``poll_hint_seconds`` when
    it knows something the clock does not -- a CI run still in flight, say.
    """
    if poll_hint_seconds is not None:
        return poll_hint_seconds
    idle_seconds = max(0.0, (now - last_activity_at).total_seconds())
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


def deterministic_jitter(delay: float, subject: str, cycle: int) -> float:
    """Apply stable +/-10 percent jitter without global random state."""
    digest = hashlib.sha256(f"{subject}:{cycle}".encode()).digest()
    fraction = int.from_bytes(digest[:8], "big") / ((1 << 64) - 1)
    return delay * (0.9 + 0.2 * fraction)


def failure_poll_delay(failure_count: int, subject: str) -> float:
    index = min(max(failure_count, 1), len(_FAILURE_DELAYS)) - 1
    return deterministic_jitter(_FAILURE_DELAYS[index], subject, failure_count)


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
    EventKind.CHANGED: ("change", "changes"),
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
    counts_by_subject: Sequence[tuple[str, str, Mapping[EventKind, int]]],
) -> str:
    """Render one concise wake without raw comments, URLs, or CI logs.

    ``counts_by_subject`` is ``(source, subject, counts_by_kind)`` per subject,
    in a stable caller-chosen order. A session watching a stack gets one message
    covering every affected diff rather than one wake per diff.

    The closing instruction stays diff-specific only while every subject in the
    batch is a diff. A session may watch a diff and a JustKnob at once, and
    telling it to go read the CI state of a knob would be nonsense.
    """
    described = [
        (source, subject, _describe_counts(counts))
        for source, subject, counts in counts_by_subject
        if any(counts.values())
    ]
    if not described:
        raise ValueError("cannot render an empty watcher batch")
    body = "; ".join(f"{subject} has {joined}" for _, subject, joined in described)
    if all(source == PHABRICATOR_SOURCE for source, _, _ in described):
        tail = "the diff" if len(described) == 1 else "each diff"
        instruction = (
            "Load the current diff review and CI "
            f"state, address actionable findings, and update {tail} as needed."
        )
    else:
        instruction = "Load the current state of each subject and act on what changed."
    return f"[Watcher {batch_id}] {body}. {instruction}"
