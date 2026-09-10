"""Session-scoped read and stop operations, shared by both watch surfaces.

Both surfaces do the same thing to different sources, so the wording lives
here once rather than twice in the MCP server. ``sources`` is what keeps them
apart: it is the only reason ``diff_watch_unsubscribe`` cannot reach a generic
watch, and vice versa, so a new source must be added to :data:`GENERIC_SOURCES`
or its watches become impossible to stop from the tool.
"""

from __future__ import annotations

import shlex
import time
from collections.abc import Iterable

from .command_source import SOURCE_NAME as COMMAND_SOURCE_NAME
from .command_source import CommandSpec
from .domain import SubscriptionState
from .repository import WatcherRepository

__all__ = ["GENERIC_SOURCES", "cancel_watches", "describe_watches"]

GENERIC_SOURCES = frozenset({COMMAND_SOURCE_NAME})


def cancel_watches(
    repository: WatcherRepository,
    session_id: str,
    subject: str | None = None,
    *,
    sources: Iterable[str],
    now: float | None = None,
) -> str:
    """Stop one watch, or all of a session's watches within *sources*."""
    moment = time.time() if now is None else now
    scoped = frozenset(sources)

    # Retiring the subscription is what actually stops the watch: the poller
    # only claims subjects that still have an active subscriber. Cancelling the
    # request alone would merely stop it being re-bound, leaving it polling and
    # waking this session indefinitely.
    #
    # Targets come from the subscriptions themselves rather than from the stored
    # requests, which also reaches an orphan -- a subscription whose request was
    # already cancelled without it, the exact state an older build of this tool
    # used to leave behind.
    targets = [
        row
        for row in repository.subscriptions_for_session(
            session_id,
            states=(SubscriptionState.ACTIVE, SubscriptionState.SUSPENDED),
            sources=scoped,
        )
        if subject is None or row.subject == subject
    ]
    cancelled = repository.cancel_watch_requests(
        session_id, now=moment, subject=subject, sources=scoped
    )
    for row in targets:
        repository.retire_subscription(row.id, "unsubscribed", now=moment)

    scope = subject if subject is not None else "all subjects"
    return f"Cancelled {cancelled} and stopped {len(targets)} watch(es) for {scope}."


def describe_watches(
    repository: WatcherRepository,
    session_id: str,
    *,
    sources: Iterable[str],
) -> str:
    """List a session's watches within *sources*, and how each one reads.

    A command watch shows its argv: it is stored and re-run on an interval long
    after the turn that registered it, so it has to be auditable from the tool
    rather than only by reading the database.
    """
    scoped = frozenset(sources)
    rows = [row for row in repository.active_watch_requests(session_id) if row[1] in scoped]
    if not rows:
        return "This session has no watches of that kind."
    lines = []
    for _session, source, subject, spec_json, kinds in rows:
        detail = ""
        if source == COMMAND_SOURCE_NAME:
            try:
                spec = CommandSpec.from_json(spec_json)
            except ValueError:
                detail = " — unreadable spec; the watch will fail to poll"
            else:
                detail = (
                    f" — {shlex.join(spec.argv)}"
                    f" every {spec.interval_seconds:g}s"
                    f", timeout {spec.timeout_seconds:g}s"
                )
                if spec.extract is not None:
                    # Quoted but not repr'd: repr escapes the backslashes, so a
                    # pattern reads back as something the caller never typed.
                    detail += f", matching '{spec.extract}'"
        else:
            detail = " — " + ", ".join(sorted(kind.value for kind in kinds))
        lines.append(f"{subject} via {source}{detail}")
    return "Active watches:\n" + "\n".join(lines)
