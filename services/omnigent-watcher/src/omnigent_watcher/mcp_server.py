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
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Literal

import httpx
from mcp.server.fastmcp import FastMCP
from pydantic import Field

from .phabricator_source import SOURCE_NAME as PHABRICATOR_SOURCE

if TYPE_CHECKING:
    from .settings import ServiceSettings

mcp = FastMCP("watch", log_level="ERROR")

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


def _strings(value: object) -> list[str]:
    """Coerce a JSON array from the hub into a list of strings."""
    return [str(item) for item in value] if isinstance(value, list) else []


async def _call(method: str, path: str, **kwargs: object) -> dict[str, object]:
    """Call the watcher API on the hub, and translate its failures honestly.

    Every failure mode here used to be a confusing local one. A client with no
    route to the hub said its database was at the wrong schema; a hub without
    the router mounted said nothing at all. Both now name what is actually
    wrong and what would fix it.
    """
    url = f"{_server_url().rstrip('/')}{path}"
    try:
        async with httpx.AsyncClient(timeout=180.0, trust_env=False) as client:
            response = await client.request(method, url, **kwargs)  # type: ignore[arg-type]
    except httpx.HTTPError as exc:
        raise ValueError(
            f"could not reach the Omnigent hub at {_server_url()} ({exc}). Watching runs on "
            "the hub; this host reaches it through the omnigent-client-proxy forward."
        ) from exc
    if response.status_code == 404:
        raise ValueError(
            "the hub's Omnigent server has no watcher API. It is mounted through the "
            "debug_router_modules key in omnigent_config/server.yaml and loaded at "
            "startup, so the hub needs a config sync and a server restart."
        )
    if response.status_code >= 400:
        detail: object = response.text
        try:
            payload = response.json()
        except ValueError:
            payload = None
        if isinstance(payload, dict) and payload.get("detail"):
            detail = payload["detail"]
        raise ValueError(str(detail))
    result = response.json()
    if not isinstance(result, dict):
        raise ValueError("the watcher API returned a malformed response")
    return result


# -- Diff watches ---------------------------------------------------------


@mcp.tool()
async def diff_subscribe(
    session_id: SessionId,
    diffs: DiffIds,
    events: list[EventName] | None = None,
) -> str:
    """Wake a session when its diffs get review comments, CI results, or AI findings.

    Name the diffs explicitly -- they are not inferred. Every diff is read once
    during the call, so an id that does not resolve is reported now rather than
    failing silently in the background.
    """
    selected = sorted(set(_ALL_EVENTS if events is None else events))
    if not selected:
        raise ValueError("at least one event type is required")

    await _validate_session(session_id)
    result = await _call(
        "POST",
        "/v1/watches",
        json={
            "session_id": session_id,
            "source": PHABRICATOR_SOURCE,
            "subjects": list(dict.fromkeys(diffs)),
            "events": selected,
        },
    )
    bound = _strings(result.get("bound"))
    failures = _strings(result.get("failures"))
    if not bound:
        raise ValueError("could not watch any of those diffs: " + "; ".join(failures))
    answer = f"Watching {', '.join(bound)} for {', '.join(selected)}."
    if failures:
        answer += " Could not watch: " + "; ".join(failures) + "."
    return answer


@mcp.tool()
async def diff_unsubscribe(session_id: SessionId, diff: DiffId | None = None) -> str:
    """Stop watching one diff, or every diff, for a session."""
    result = await _call(
        "POST",
        "/v1/watches/cancel",
        json={"session_id": session_id, "sources": [PHABRICATOR_SOURCE], "subject": diff},
    )
    return str(result.get("detail", ""))


@mcp.tool()
async def diff_status(session_id: SessionId) -> str:
    """List the diffs a session is watching, and for which events."""
    result = await _call(
        "GET",
        "/v1/watches",
        params={"session_id": session_id, "sources": PHABRICATOR_SOURCE},
    )
    return str(result.get("detail", ""))


# -- Generic watches ------------------------------------------------------
#
# Deliberately a separate tool set from the diff ones. Those stay opinionated
# about diffs -- no source, no argv, events fixed to the four diff kinds --
# because that is the interface worth having for the common case. These make no
# assumption about what is being watched, at the cost of the caller having to
# say how to read it. The route is identical for both.


@mcp.tool()
async def subscribe(
    session_id: SessionId,
    subject: WatchSubject,
    command: WatchArgv,
    extract: str | None = None,
    interval_seconds: float = 60.0,
    timeout_seconds: float = 30.0,
) -> str:
    """Wake a session when the output of a command changes.

    Use for anything that has no purpose-built watcher: a JustKnob rollout, a
    config value, a job's status. Prefer ``diff_subscribe`` for diffs.

    The command runs on an interval in a background service on the hub, not in
    this session, so waiting costs no model turns. Its output is hashed; the
    session is woken only when the hash changes. Pass ``extract`` -- a regular
    expression, optionally with one capture group -- when the output carries a
    timestamp or request id that would otherwise change on every poll.

    The command is run once during the call to establish that baseline, so a
    command that cannot run is reported now rather than failing silently in the
    background. Subscribe *before* the change you are waiting for can happen,
    or you will baseline the value you were watching for.

    ``timeout_seconds`` (1..120, and never more than ``interval_seconds``)
    bounds each run. Raise it for a slow probe -- a `meta`/`jf` round trip, or a
    command that asks a model to judge whether a condition has been met.

    The command runs on the hub, not on this machine. A path or binary that
    only exists on your devserver will not resolve there.
    """
    import shlex

    from .command_source import SOURCE_NAME as COMMAND_SOURCE
    from .command_source import CommandSpec
    from .domain import COMMAND_EVENT_KINDS

    # Validated here too so a malformed argv fails immediately and precisely
    # rather than after a round trip; the hub validates it again.
    spec = CommandSpec(command, extract, interval_seconds, timeout_seconds)
    await _validate_session(session_id)
    result = await _call(
        "POST",
        "/v1/watches",
        json={
            "session_id": session_id,
            "source": COMMAND_SOURCE,
            "subjects": [subject],
            "events": sorted(kind.value for kind in COMMAND_EVENT_KINDS),
            "spec": spec.to_json(),
        },
    )
    if not _strings(result.get("bound")):
        raise ValueError(f"could not start watching {'; '.join(_strings(result.get('failures')))}")
    return (
        f"Watching {subject}: {shlex.join(spec.argv)} every {spec.interval_seconds:g}s "
        f"on the hub. Session {session_id} will be woken when its output changes."
    )


@mcp.tool()
async def unsubscribe(session_id: SessionId, subject: WatchSubject | None = None) -> str:
    """Stop one generic watch, or all of them, for a session."""
    from .watch_api import GENERIC_SOURCES

    result = await _call(
        "POST",
        "/v1/watches/cancel",
        json={
            "session_id": session_id,
            "sources": sorted(GENERIC_SOURCES),
            "subject": subject,
        },
    )
    return str(result.get("detail", ""))


@mcp.tool()
async def status(session_id: SessionId) -> str:
    """List a session's generic watches, and the exact command each will run.

    The command is shown, not just the subject: it is stored and re-run on an
    interval long after the turn that registered it, so it has to be auditable
    from here rather than only by reading the hub's database.
    """
    from .watch_api import GENERIC_SOURCES

    result = await _call(
        "GET",
        "/v1/watches",
        params={"session_id": session_id, "sources": ",".join(sorted(GENERIC_SOURCES))},
    )
    return str(result.get("detail", ""))


def main() -> None:
    # Identity is a tool argument now, so nothing here is harness-specific and
    # the flags that used to select a bridge layout do nothing.
    #
    # They are still ACCEPTED, because removing them from the source config
    # does not remove them from an installed one: the Codex config is built by
    # a recursive dict merge that never deletes keys (agent_config/codex_config.py),
    # so `args = ["--native-codex"]` survives in ~/.codex/config.toml, and on
    # every other machine this repo cannot re-sync remotely. Rejecting the flag
    # made argparse exit before serving, which does not read as "stale config"
    # from inside a session -- the tools are simply absent. Ignoring it costs
    # nothing and fails open.
    parser = argparse.ArgumentParser()
    parser.add_argument("--native-codex", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--native-claude", action="store_true", help=argparse.SUPPRESS)
    parser.parse_args()
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
