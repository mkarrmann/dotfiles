"""Session-scoped read and stop operations, shared by both watch surfaces.

Both surfaces do the same thing to different sources, so the wording lives
here once rather than twice in the MCP server. ``sources`` is what keeps them
apart: it is the only reason ``diff_unsubscribe`` cannot reach a generic
watch, and vice versa, so a new source must be added to :data:`GENERIC_SOURCES`
or its watches become impossible to stop from the tool.
"""

from __future__ import annotations

import shlex
import time
from collections.abc import Iterable
from datetime import UTC, datetime

from .command_source import SOURCE_NAME as COMMAND_SOURCE_NAME
from .command_source import CommandSpec
from .domain import EventKind, SubscriptionState
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
    """Describe recorded requests, subscriptions, and source progress.

    A command watch shows its argv: it is stored and re-run on an interval long
    after the turn that registered it, so it has to be auditable from the tool
    rather than only by reading the database.
    """
    scoped = frozenset(sources)
    requests = {
        subject: (source, spec, kinds)
        for _session, source, subject, spec, kinds in repository.active_watch_requests(session_id)
        if source in scoped
    }
    subscriptions = {
        row.subject: row for row in repository.subscriptions_for_session(session_id, sources=scoped)
    }
    subjects = dict.fromkeys((*requests, *subscriptions))
    if not subjects:
        return "This session has no watches of that kind."

    lines = ["Recorded watches (not a worker health check):"]
    for subject in subjects:
        request = requests.get(subject)
        subscription = subscriptions.get(subject)
        watch = repository.watch(subject)
        if request is not None:
            source, spec_json, kinds = request
            if watch is None or watch.source != source:
                subscription = None
        else:
            if subscription is None or watch is None:
                continue
            source, spec_json, kinds = watch.source, watch.spec, subscription.event_types

        if subscription is None:
            state = "pending (not bound)"
        else:
            state = subscription.state.value
            if subscription.retired_reason is not None:
                state += f" ({subscription.retired_reason})"
            if subscription.state is SubscriptionState.RETIRED:
                if request is not None:
                    state += "; request pending"
            elif request is None:
                state += "; no durable request"
            if watch is not None:
                if request is not None and (
                    not _same_spec(source, spec_json, watch.spec)
                    or kinds != subscription.event_types
                ):
                    state += "; requested settings differ from bound watch"
                spec_json, kinds = watch.spec, subscription.event_types

        detail = _source_detail(source, spec_json, kinds)
        lines.append(f"{subject} via {source} — state: {state}{detail}")
        if subscription is None or watch is None:
            continue
        progress = [
            f"last result: {_timestamp(watch.last_success_at)}",
            f"consecutive failures: {watch.failure_count}",
        ]
        if subscription.state is SubscriptionState.ACTIVE:
            next_action = "retry" if watch.failure_count else "poll"
            progress.append(f"next {next_action} scheduled: {_timestamp(watch.next_poll_at)}")
        if subscription.unavailable_since is not None:
            progress.append(
                f"session unavailable since: {_timestamp(subscription.unavailable_since)}"
            )
        progress.append(f"last session delivery: {_timestamp(subscription.last_delivery_at)}")
        lines.append("  " + "; ".join(progress))

    batches = {
        batch.batch_id: batch
        for batch in (
            repository.delivering_batch_for_session(session_id),
            repository.open_batch_for_session(session_id),
        )
        if batch is not None and subjects.keys() & set(batch.subjects)
    }
    for batch in batches.values():
        lines.append(
            f"Pending session notification: {batch.state.value}; deferrals: {batch.retry_count}; "
            f"next attempt scheduled: {_timestamp(batch.next_attempt_at)}; batch: {batch.batch_id}"
        )
    lines.append(
        "Last result includes baseline and partial reads. "
        "Last poll attempt and error category are not persisted."
    )
    return "\n".join(lines)


def _timestamp(moment: float | None) -> str:
    if moment is None:
        return "none recorded"
    return datetime.fromtimestamp(moment, UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _same_spec(source: str, requested: str | None, bound: str | None) -> bool:
    if requested == bound:
        return True
    if source != COMMAND_SOURCE_NAME:
        return False
    try:
        return CommandSpec.from_json(requested).to_json() == CommandSpec.from_json(bound).to_json()
    except ValueError:
        return False


def _source_detail(source: str, spec_json: str | None, kinds: frozenset[EventKind]) -> str:
    if source != COMMAND_SOURCE_NAME:
        return " — " + ", ".join(sorted(kind.value for kind in kinds))
    try:
        spec = CommandSpec.from_json(spec_json)
    except ValueError:
        return " — unreadable command spec"
    detail = (
        f" — {shlex.join(spec.argv)}"
        f" every {spec.interval_seconds:g}s"
        f", timeout {spec.timeout_seconds:g}s"
    )
    if spec.extract is not None:
        detail += f", matching '{spec.extract}'"
    return detail
