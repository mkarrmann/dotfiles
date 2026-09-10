"""MCP tools that register watches against the watcher's own database.

Every tool takes the Omnigent ``session_id`` it should wake, because that is
the only thing a watch needs from its caller that the caller cannot state
directly. Agents get it from Omnigent's own ``sys_session_get_info``.

Asking for it, rather than discovering it, is what makes this work in every
harness. The MCP protocol carries no session context in any transport --
stdio ``env`` and HTTP ``headers`` are static, there is no ``_meta``
plumbing, and Omnigent's pool shares one server process across sessions -- so
a tool can only learn its own session by scraping the harness's private bridge
directory, which exists for exactly two harnesses. An explicit address costs
one cheap tool call and works everywhere.

It is also more capable: ``sys_session_get_info`` returns ``parent_session_id``
alongside the session's own, so a subagent can register a watch that wakes its
*parent*. Self-discovery could only ever bind the wake to the subagent, which
is usually gone by the time it fires.

The trade is that the address comes from the model, so a session could name
another of its own. Every id is validated against the server before anything
is written, which catches a typo or a dead session; a deliberate wrong-but-live
id would wake a different session of the same user, on the same machine, from
an agent that already has that user's shell. Nuisance, not escalation.
"""

from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Literal

import httpx
from mcp.server.fastmcp import FastMCP
from pydantic import Field

from .domain import EventDeliveryResult, SessionSnapshot

if TYPE_CHECKING:
    from .repository import WatcherRepository
    from .settings import ServiceSettings
    from .watcher import DiffWatcher

mcp = FastMCP("diff-watch", log_level="ERROR")

# Spelled out rather than derived from EventKind so the published tool schema
# stays a literal; tests pin the two together.
EventName = Literal["review_comment", "ci_failure", "ai_review", "ci_green"]
_ALL_EVENTS: tuple[EventName, ...] = (
    "review_comment",
    "ci_failure",
    "ai_review",
    "ci_green",
)

SessionId = Annotated[
    str,
    Field(
        pattern=r"^[A-Za-z0-9_-]{8,128}$",
        description=(
            "The Omnigent session to wake. Call sys_session_get_info and pass "
            "its session_id -- or its parent_session_id if you are a subagent "
            "that will not outlive the watch."
        ),
    ),
]
DiffId = Annotated[
    str,
    Field(
        pattern=r"^D[1-9][0-9]*$",
        description="Phabricator diff ID, for example D111179041.",
    ),
]
DiffIds = Annotated[
    list[DiffId],
    Field(
        min_length=1,
        max_length=20,
        description="The Phabricator diffs to watch.",
    ),
]
WatchSubject = Annotated[
    str,
    Field(
        pattern=r"^[a-z][a-z0-9_-]{0,31}:[\x20-\x7e]{1,200}$",
        description=(
            "Namespaced identifier for the thing being watched, "
            "for example jk:presto/presto_batch:my_knob."
        ),
    ),
]
WatchArgv = Annotated[
    list[Annotated[str, Field(min_length=1, max_length=512)]],
    Field(
        min_length=1,
        max_length=32,
        description=(
            "Command to run, as an argv list. Executed directly, never through "
            "a shell, so pipes and redirection are not available -- wrap those "
            "in a script and name the script here."
        ),
    ),
]

DEFAULT_DATABASE_PATH = "~/.omnigent/diff-watcher.sqlite3"
DEFAULT_SERVER_URL = "http://127.0.0.1:6767"
# Binding a stack means one Phabricator read per diff, and they are slow. Bound
# the fan-out rather than issuing twenty at once; the sidecar polls at 2.
_BIND_CONCURRENCY = 4


def _service_settings() -> ServiceSettings | None:
    """Load the sidecar's own settings, or ``None`` when they are not readable.

    The repo checkout is the normal case, but this server can also be launched
    from an installed package where ``config.toml`` is not alongside the code.
    """
    from .settings import ServiceSettings

    config = Path(__file__).resolve().parents[2] / "config.toml"
    if config.is_file():
        try:
            return ServiceSettings.load(config)
        except (OSError, ValueError):
            return None
    return None


def _database_path() -> Path:
    """The database this session's watches are written to.

    Two processes must agree on this file -- this server writes it and the
    sidecar polls it -- so the override is an environment variable rather than
    an argument: a stdio MCP server's argv is fixed by whoever registered it,
    while the environment can be set alongside the sidecar's own.
    """
    override = os.environ.get("OMNIGENT_DIFF_WATCHER_DATABASE")
    if override:
        return Path(override).expanduser()
    settings = _service_settings()
    if settings is not None:
        return settings.database_path
    return Path(DEFAULT_DATABASE_PATH).expanduser()


def _server_url() -> str:
    settings = _service_settings()
    return settings.server_url if settings is not None else DEFAULT_SERVER_URL


async def _validate_session(session_id: str) -> None:
    """Confirm *session_id* is a live Omnigent session before writing anything.

    The address is supplied by the caller, so a typo or a session that has since
    closed would otherwise become a watch that polls forever and wakes nobody.

    ``trust_env=False``: an ambient ``http_proxy`` sends loopback traffic to a
    corporate proxy, which answers 403 and makes a perfectly good session look
    invalid.

    :raises ValueError: If the session is unknown, closed, or archived.
    """
    url = f"{_server_url().rstrip('/')}/v1/sessions/{session_id}"
    try:
        async with httpx.AsyncClient(timeout=15.0, trust_env=False) as client:
            response = await client.get(url)
    except httpx.HTTPError as exc:
        raise ValueError(
            f"could not reach Omnigent to validate session {session_id}: {exc}"
        ) from exc
    if response.status_code == 404:
        raise ValueError(
            f"no Omnigent session {session_id}. Call sys_session_get_info and "
            "pass the session_id it reports."
        )
    if response.status_code >= 400:
        raise ValueError(f"Omnigent rejected a lookup of session {session_id}")
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError(f"Omnigent returned no usable record for session {session_id}")
    if payload.get("archived") or payload.get("status") in {"closed", "deleted"}:
        raise ValueError(f"session {session_id} is closed or archived; nothing could be woken")


class _AddressedSession:
    """A ``SessionService`` for an address already validated by the tool.

    ``DiffWatcher.subscribe`` re-checks that the session is not terminal, which
    normally costs a round trip. :func:`_validate_session` has just made it, so
    this answers from that result instead of making a second identical call.
    """

    async def get(self, session_id: str) -> SessionSnapshot:
        return SessionSnapshot(session_id=session_id, labels={})


class _UnusedDelivery:
    """``DiffWatcher`` requires a delivery service; ``subscribe`` never uses one.

    Waking a session is the sidecar's job and happens in the sidecar's process.
    Raising rather than silently no-op'ing means a future code path that starts
    delivering from here fails loudly instead of dropping wakes on the floor.
    """

    async def deliver_message(
        self,
        session_id: str,
        delivery_id: str,
        content: str,
    ) -> EventDeliveryResult:
        raise RuntimeError("the MCP server does not deliver watch notifications")


def _repository() -> WatcherRepository:
    # migrate=False: the sidecar owns the schema. See WatcherRepository.__init__.
    from .repository import WatcherRepository

    return WatcherRepository(_database_path(), migrate=False)


def _watch_engine(repository: WatcherRepository) -> DiffWatcher:
    """Build the same engine the sidecar uses, for one synchronous subscribe.

    Sharing ``DiffWatcher.subscribe`` rather than re-implementing it is the
    point: binding reads the subject for a baseline and applies the
    active-subject limit, and a second implementation of that would drift from
    the one the sidecar enforces.
    """
    from .command_source import SOURCE_NAME as COMMAND_SOURCE_NAME
    from .command_source import CommandSource
    from .phabricator_source import PhabricatorReviewSource, bounded_source_environment
    from .watcher import DiffWatcher

    settings = _service_settings()
    environment = bounded_source_environment()
    return DiffWatcher(
        repository,
        PhabricatorReviewSource(),
        _AddressedSession(),
        _UnusedDelivery(),
        config=settings.watcher if settings is not None else None,
        sources={COMMAND_SOURCE_NAME: CommandSource(env=environment)},
    )


async def _bind(
    repository: WatcherRepository,
    session_id: str,
    subjects: list[str],
    event_types: frozenset[str],
    *,
    source_name: str,
    spec: str | None,
) -> tuple[list[str], list[str]]:
    """Subscribe *subjects*, returning ``(bound, failures)``.

    One unreadable subject does not sink the rest: a stack routinely contains a
    diff that has just landed, and refusing the whole call over it would be
    worse than reporting it.
    """
    from .domain import EventKind
    from .watcher import SubscriptionError

    kinds = frozenset(EventKind(value) for value in event_types)
    engine = _watch_engine(repository)
    limit = asyncio.Semaphore(_BIND_CONCURRENCY)

    async def bind_one(subject: str) -> tuple[str, str | None]:
        async with limit:
            try:
                await engine.subscribe(
                    session_id, subject, kinds, source_name=source_name, spec=spec
                )
            except SubscriptionError as exc:
                return subject, str(exc)
            return subject, None

    bound: list[str] = []
    failures: list[str] = []
    for subject, error in await asyncio.gather(*(bind_one(s) for s in subjects)):
        if error is None:
            bound.append(subject)
        else:
            failures.append(f"{subject} ({error})")
    return bound, failures


def _record(
    repository: WatcherRepository,
    session_id: str,
    subjects: list[str],
    event_types: frozenset[str],
    *,
    source_name: str,
    spec: str | None,
) -> None:
    """Persist the request rows that re-bind these watches after a restart."""
    import time

    from .domain import EventKind

    kinds = frozenset(EventKind(value) for value in event_types)
    now = time.time()
    for subject in subjects:
        repository.request_watch(session_id, source_name, subject, kinds, spec=spec, now=now)


# -- Diff watches ---------------------------------------------------------


@mcp.tool()
async def diff_watch_subscribe(
    session_id: SessionId,
    diffs: DiffIds,
    events: list[EventName] | None = None,
) -> str:
    """Wake a session when its diffs get review comments, CI results, or AI findings.

    Name the diffs explicitly -- they are not inferred. Every diff is read once,
    here, so an id that does not resolve is reported now rather than failing
    silently in the background.
    """
    from .phabricator_source import SOURCE_NAME as PHABRICATOR_SOURCE_NAME

    selected = sorted(set(_ALL_EVENTS if events is None else events))
    if not selected:
        raise ValueError("at least one event type is required")
    subjects = list(dict.fromkeys(diffs))

    await _validate_session(session_id)
    repository = _repository()
    kinds = frozenset(selected)
    bound, failures = await _bind(
        repository,
        session_id,
        subjects,
        kinds,
        source_name=PHABRICATOR_SOURCE_NAME,
        spec=None,
    )
    if not bound:
        raise ValueError("could not watch any of those diffs: " + "; ".join(failures))
    _record(
        repository,
        session_id,
        bound,
        kinds,
        source_name=PHABRICATOR_SOURCE_NAME,
        spec=None,
    )
    answer = f"Watching {', '.join(bound)} for {', '.join(selected)}."
    if failures:
        answer += " Could not watch: " + "; ".join(failures) + "."
    return answer


@mcp.tool()
async def diff_watch_unsubscribe(session_id: SessionId, diff: DiffId | None = None) -> str:
    """Stop watching one diff, or every diff, for a session."""
    from .phabricator_source import SOURCE_NAME as PHABRICATOR_SOURCE_NAME
    from .watch_api import cancel_watches

    return cancel_watches(
        _repository(), session_id, diff, sources=frozenset({PHABRICATOR_SOURCE_NAME})
    )


@mcp.tool()
async def diff_watch_status(session_id: SessionId) -> str:
    """List the diffs a session is watching, and for which events."""
    from .phabricator_source import SOURCE_NAME as PHABRICATOR_SOURCE_NAME
    from .watch_api import describe_watches

    return describe_watches(_repository(), session_id, sources=frozenset({PHABRICATOR_SOURCE_NAME}))


# -- Generic watches ------------------------------------------------------
#
# Deliberately a separate tool set from diff_watch_*. Those stay opinionated
# about diffs -- no source, no argv, events fixed to the four diff kinds --
# because that is the interface worth having for the common case. These make no
# assumption about what is being watched, at the cost of the caller having to
# say how to read it. The route is now identical for both.


@mcp.tool()
async def watch_subscribe(
    session_id: SessionId,
    subject: WatchSubject,
    command: WatchArgv,
    extract: str | None = None,
    interval_seconds: float = 60.0,
    timeout_seconds: float = 30.0,
) -> str:
    """Wake a session when the output of a command changes.

    Use for anything that has no purpose-built watcher: a JustKnob rollout, a
    config value, a job's status. Prefer ``diff_watch_subscribe`` for diffs.

    The command runs on an interval in a background service, not in this
    session, so waiting costs no model turns. Its output is hashed; the session
    is woken only when the hash changes. Pass ``extract`` -- a regular
    expression, optionally with one capture group -- when the output carries a
    timestamp or request id that would otherwise change on every poll.

    The command is run once, here, to establish that baseline -- so a command
    that cannot run is reported now rather than failing silently in the
    background. Subscribe *before* the change you are waiting for can happen,
    or you will baseline the value you were watching for.

    ``timeout_seconds`` (1..120, and never more than ``interval_seconds``)
    bounds each run. Raise it for a slow probe -- a `meta`/`jf` round trip, or a
    command that asks a model to judge whether a condition has been met.
    """
    import shlex

    from .command_source import SOURCE_NAME as COMMAND_SOURCE_NAME
    from .command_source import CommandSpec
    from .domain import COMMAND_EVENT_KINDS

    spec = CommandSpec(command, extract, interval_seconds, timeout_seconds)
    await _validate_session(session_id)
    repository = _repository()
    kinds = frozenset(kind.value for kind in COMMAND_EVENT_KINDS)

    # Bind first, and only record the request once binding succeeded. Writing
    # the request first would report success for a watch that the sidecar then
    # fails to bind -- the failure is logged in another process and backs off,
    # so the session that asked would never learn of it.
    bound, failures = await _bind(
        repository,
        session_id,
        [subject],
        kinds,
        source_name=COMMAND_SOURCE_NAME,
        spec=spec.to_json(),
    )
    if not bound:
        raise ValueError(f"could not start watching {'; '.join(failures)}")
    _record(
        repository,
        session_id,
        bound,
        kinds,
        source_name=COMMAND_SOURCE_NAME,
        spec=spec.to_json(),
    )
    return (
        f"Watching {subject}: {shlex.join(spec.argv)} every {spec.interval_seconds:g}s. "
        f"Session {session_id} will be woken when its output changes."
    )


@mcp.tool()
async def watch_unsubscribe(session_id: SessionId, subject: WatchSubject | None = None) -> str:
    """Stop one generic watch, or all of them, for a session."""
    from .watch_api import GENERIC_SOURCES, cancel_watches

    return cancel_watches(_repository(), session_id, subject, sources=GENERIC_SOURCES)


@mcp.tool()
async def watch_status(session_id: SessionId) -> str:
    """List a session's generic watches, and the exact command each will run.

    The command is shown, not just the subject: it is stored and re-run on an
    interval long after the turn that registered it, so it has to be auditable
    from here rather than only by reading the database.
    """
    from .watch_api import GENERIC_SOURCES, describe_watches

    return describe_watches(_repository(), session_id, sources=GENERIC_SOURCES)


def main() -> None:
    # No --native flags any more: identity is an argument, so there is nothing
    # harness-specific left in this process.
    argparse.ArgumentParser().parse_args()
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
