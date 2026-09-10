"""Stateless MCP intent tools; Omnigent policy binds results to a session."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Literal
from urllib.parse import quote, urlparse

import httpx
from mcp.server.fastmcp import FastMCP
from pydantic import Field

if TYPE_CHECKING:
    from .repository import WatcherRepository

mcp = FastMCP("diff-watch", log_level="ERROR")
# Spelled out rather than derived from EventKind so the published tool schema
# stays a literal; tests pin the two together.
EventName = Literal["review_comment", "ci_failure", "ai_review", "ci_green"]
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
        description="Existing Phabricator diffs to associate with this session.",
    ),
]
_ALL_EVENTS: tuple[EventName, ...] = (
    "review_comment",
    "ci_failure",
    "ai_review",
    "ci_green",
)

# ``None`` outside a native harness (the streamed SDK harnesses get the policy's
# rewritten result for free); otherwise the harness whose bridge layout applies.
_NATIVE_MODE: str | None = None
_NATIVE_HARNESS = {"codex": "codex-native", "claude": "claude-native"}
_NOT_NATIVE = "diff watch requires an Omnigent native {} session"

# Sources the watch_* surface owns. Scoping unsubscribe by source is what keeps
# it from reaching a diff watch, so a new generic source must be added here or
# its watches become impossible to stop from the tool.
GENERIC_SOURCES = frozenset({"command"})


def _codex_bridge_dir() -> Path:
    codex_home = os.environ.get("CODEX_HOME")
    if not codex_home:
        raise RuntimeError(_NOT_NATIVE.format("Codex"))
    path = Path(codex_home).expanduser()
    if path.name != "codex-home" or path.parent.parent.name != "codex-native":
        raise RuntimeError(_NOT_NATIVE.format("Codex"))
    return path.parent


def _claude_bridge_dir() -> Path:
    """Locate this session's Claude bridge directory.

    Unlike Codex -- where ``CODEX_HOME`` points into the bridge directory --
    the Claude bridge passes its path only as ``--bridge-dir`` to Omnigent's
    own MCP server, so a separately-registered server cannot read it from the
    environment. What Claude Code *does* export to every MCP server it spawns
    is ``CLAUDE_CODE_SESSION_ID``, and the bridge records that same id in
    ``state.json``. Match on it rather than guessing: several bridge
    directories coexist, one per concurrent session.

    Identity therefore still comes from the harness and an owner-only (0700)
    directory, never from the model -- the trust boundary is unchanged.
    """
    claude_session = os.environ.get("CLAUDE_CODE_SESSION_ID")
    if not claude_session or not os.environ.get("OMNIGENT_URL"):
        raise RuntimeError(_NOT_NATIVE.format("Claude"))
    # Both roots: Omnigent builds this path from the system temp dir, but the
    # agent process does not necessarily share our TMPDIR, so "/tmp" is kept
    # as a second candidate rather than assumed to be the same directory.
    roots = dict.fromkeys([tempfile.gettempdir(), "/tmp"])
    for root in roots:
        base = Path(root) / f"omnigent-{os.getuid()}" / "claude-native"
        if not base.is_dir():
            continue
        for candidate in sorted(base.iterdir()):
            state = _read_json_object(candidate / "state.json")
            seen = state.get("seen_claude_session_ids")
            if state.get("claude_session_id") == claude_session or (
                isinstance(seen, list) and claude_session in seen
            ):
                return candidate
    raise RuntimeError(
        "no Omnigent Claude bridge directory matches this session "
        f"({claude_session}); the session may predate the bridge"
    )


def _native_bridge_dir() -> Path:
    if _NATIVE_MODE == "claude":
        return _claude_bridge_dir()
    return _codex_bridge_dir()


def _read_json_object(path: Path) -> dict[str, object]:
    """Read a bridge JSON file, returning an empty mapping when unusable."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _loopback_url(value: object) -> str | None:
    """Return *value* when it is an http(s) loopback base URL, else ``None``."""
    if not isinstance(value, str) or not value:
        return None
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"}:
        return None
    if parsed.hostname not in {"127.0.0.1", "localhost"}:
        return None
    return value.rstrip("/")


def _server_endpoint(
    hook: dict[str, object], session_id: object
) -> tuple[str, dict[str, str]] | None:
    """Build the direct ``/policies/evaluate`` endpoint from a hook file."""
    server_url = _loopback_url(hook.get("ap_server_url"))
    if not server_url or not isinstance(session_id, str) or not session_id:
        return None
    headers: dict[str, str] = {}
    raw_headers = hook.get("ap_auth_headers")
    if isinstance(raw_headers, dict):
        headers = {str(k): str(v) for k, v in raw_headers.items()}
    component = quote(session_id, safe="")
    return (f"{server_url}/v1/sessions/{component}/policies/evaluate", headers)


def _claude_policy_endpoints(bridge_dir: Path) -> list[tuple[str, dict[str, str]]]:
    """Policy endpoints for a native Claude session.

    The Claude bridge has no ``tool_relay.json`` -- it advertises the Omnigent
    server directly in ``permission_hook.json`` -- so there is a single
    endpoint and nothing to fall back to. The session id lives in
    ``bridge.json``; ``state.json`` here holds the *Claude* session id, which
    is a different identifier and must not be used as the Omnigent one.
    """
    hook = _read_json_object(bridge_dir / "permission_hook.json")
    bridge = _read_json_object(bridge_dir / "bridge.json")
    session_id = bridge.get("active_session_id") or bridge.get("conversation_id")
    endpoint = _server_endpoint(hook, session_id)
    return [endpoint] if endpoint else []


def _policy_endpoints(bridge_dir: Path) -> list[tuple[str, dict[str, str]]]:
    """Return ``(url, headers)`` policy endpoints in precedence order.

    The runner's loopback relay is preferred because its token does not
    expire, matching ``omnigent.native_policy_hook``. The direct server is
    kept as a fallback: the relay advertisement is per-runner and goes stale
    when a runner restarts, while ``policy_hook.json`` is rewritten each time.
    """
    if _NATIVE_MODE == "claude":
        return _claude_policy_endpoints(bridge_dir)

    relay = _read_json_object(bridge_dir / "tool_relay.json")
    state = _read_json_object(bridge_dir / "state.json")
    hook = _read_json_object(bridge_dir / "policy_hook.json")

    session_id = relay.get("session_id") or state.get("session_id")
    endpoints: list[tuple[str, dict[str, str]]] = []

    relay_url = _loopback_url(relay.get("url"))
    relay_token = relay.get("token")
    if relay_url and isinstance(relay_token, str) and relay_token:
        endpoints.append(
            (f"{relay_url}/policies/evaluate", {"Authorization": f"Bearer {relay_token}"})
        )

    direct = _server_endpoint(hook, session_id)
    if direct:
        endpoints.append(direct)

    return endpoints


def _native_policy_result(
    tool_name: str,
    arguments: dict[str, object],
    intent_result: str,
) -> str:
    if _NATIVE_MODE is None:
        return intent_result

    endpoints = _policy_endpoints(_native_bridge_dir())
    if not endpoints:
        raise RuntimeError("Omnigent policy routing is not advertised for this session")

    request = {
        "event": {
            "type": "PHASE_TOOL_RESULT",
            "target": "",
            "data": {"result": intent_result},
            "context": {"harness": _NATIVE_HARNESS[_NATIVE_MODE]},
            "request_data": {
                "name": f"mcp__diff_watch__{tool_name}",
                "arguments": arguments,
            },
        }
    }

    last_error = "no policy endpoint was reachable"
    for url, headers in endpoints:
        try:
            with httpx.Client(timeout=30.0, trust_env=False) as client:
                response = client.post(url, headers=headers, json=request)
                response.raise_for_status()
                result = response.json()
        except (httpx.HTTPError, json.JSONDecodeError) as exc:
            # Transport-level failure: a stale relay advertisement must not
            # strand the tool while the server itself is reachable.
            last_error = f"{type(exc).__name__}: {exc}"
            continue
        if not isinstance(result, dict):
            raise RuntimeError("Omnigent policy evaluation returned a malformed response")
        if result.get("result") == "POLICY_ACTION_DENY":
            reason = result.get("reason")
            raise RuntimeError(
                reason if isinstance(reason, str) else "Diff watch was denied by policy"
            )
        data = result.get("data")
        return data if isinstance(data, str) and data else intent_result

    raise RuntimeError(f"Omnigent policy evaluation failed for diff watch ({last_error})")


@mcp.tool()
def diff_watch_subscribe(
    events: list[EventName] | None = None,
    diffs: DiffIds | None = None,
) -> str:
    """Subscribe this session to review and CI updates for associated diffs.

    Pass ``diffs`` when watching existing diffs. Diffs created or updated by
    this session are associated automatically and do not need to be repeated.
    """
    selected = sorted(set(_ALL_EVENTS if events is None else events))
    if not selected:
        raise ValueError("at least one event type is required")
    if diffs is not None and not diffs:
        raise ValueError("at least one diff ID is required when diffs is provided")
    arguments: dict[str, object] = {"events": selected}
    if diffs is not None:
        arguments["diffs"] = list(dict.fromkeys(diffs))
    return _native_policy_result(
        "diff_watch_subscribe",
        arguments,
        "Diff-watch preference requested for: " + ",".join(selected),
    )


@mcp.tool()
def diff_watch_unsubscribe() -> str:
    """Stop diff notifications for the current Omnigent session."""
    return _native_policy_result(
        "diff_watch_unsubscribe",
        {},
        "Diff-watch unsubscribe requested.",
    )


@mcp.tool()
def diff_watch_status() -> str:
    """Read the current session's diff-watch preference."""
    return _native_policy_result(
        "diff_watch_status",
        {},
        "Diff-watch status is supplied by the Omnigent session policy.",
    )


# -- Generic watch surface ------------------------------------------------
#
# Deliberately a separate tool set from diff_watch_*. Those stay opinionated
# about diffs -- no source, no argv, events fixed to the four diff kinds --
# because that is the interface worth having for the common case. These make no
# assumption about what is being watched, at the cost of the caller having to
# say how to read it.
#
# They also take a different route. A diff watch is a session *label*, capped at
# 256 characters, which an arbitrary argv overruns; a generic watch is written
# straight to the watcher database, which the sidecar reconciles.

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


def _database_path() -> Path:
    """Locate the watcher database the sidecar reconciles from.

    The repo checkout is the normal case, but this server can also be launched
    from an installed package where ``config.toml`` is not alongside the code.
    Falling back to the documented default beats refusing to subscribe --
    settings.py uses the same default when the key is absent.
    """
    from .settings import ServiceSettings

    config = Path(__file__).resolve().parents[2] / "config.toml"
    if config.is_file():
        try:
            return ServiceSettings.load(config).database_path
        except (OSError, ValueError):
            pass
    return Path(DEFAULT_DATABASE_PATH).expanduser()


def _watch_repository() -> tuple[WatcherRepository, str]:
    """Open the watcher database and resolve this session's Omnigent id."""
    from .repository import WatcherRepository

    bridge_dir = _native_bridge_dir()
    if _NATIVE_MODE == "claude":
        bridge = _read_json_object(bridge_dir / "bridge.json")
        session_id = bridge.get("active_session_id") or bridge.get("conversation_id")
    else:
        relay = _read_json_object(bridge_dir / "tool_relay.json")
        state = _read_json_object(bridge_dir / "state.json")
        session_id = relay.get("session_id") or state.get("session_id")
    if not isinstance(session_id, str) or not session_id:
        raise RuntimeError("could not resolve this Omnigent session")
    # migrate=False: the sidecar owns the schema. See WatcherRepository.__init__.
    return WatcherRepository(_database_path(), migrate=False), session_id


@mcp.tool()
def watch_subscribe(
    subject: WatchSubject,
    command: WatchArgv,
    extract: str | None = None,
    interval_seconds: float = 60.0,
    timeout_seconds: float = 30.0,
) -> str:
    """Wake this session when the output of a command changes.

    Use for anything that has no purpose-built watcher: a JustKnob rollout, a
    config value, a job's status. Prefer ``diff_watch_subscribe`` for diffs.

    The command runs on an interval in a background service, not in this
    session, so waiting costs no model turns. Its output is hashed; the session
    is woken only when the hash changes. Pass ``extract`` -- a regular
    expression, optionally with one capture group -- when the output carries a
    timestamp or request id that would otherwise change on every poll.

    The first reading is the baseline and never wakes anyone. Subscribe *before*
    the change you are waiting for can happen, or you will baseline the value
    you were watching for.

    ``timeout_seconds`` (1..120, and never more than ``interval_seconds``)
    bounds each run. Raise it for a slow probe -- a `meta`/`jf` round trip, or a
    command that asks a model to judge whether a condition has been met.
    """
    import time

    from .command_source import SOURCE_NAME, CommandSpec
    from .domain import COMMAND_EVENT_KINDS

    spec = CommandSpec(command, extract, interval_seconds, timeout_seconds)
    repository, session_id = _watch_repository()
    repository.request_watch(
        session_id,
        SOURCE_NAME,
        subject,
        COMMAND_EVENT_KINDS,
        spec=spec.to_json(),
        now=time.time(),
    )
    return (
        f"Watching {subject}: {' '.join(spec.argv)} every {spec.interval_seconds:g}s. "
        "This session will be woken when its output changes."
    )


@mcp.tool()
def watch_unsubscribe(subject: WatchSubject | None = None) -> str:
    """Stop one generic watch, or all of them, for the current session."""
    import time

    from .domain import SubscriptionState

    repository, session_id = _watch_repository()
    now = time.time()

    # Retiring the subscription is what actually stops the watch: the poller
    # only claims subjects that still have an active subscriber. Cancelling the
    # request alone would merely stop it being re-bound, leaving it polling and
    # waking this session indefinitely.
    #
    # Targets come from the subscriptions themselves, scoped by source, rather
    # than from the stored requests. Scoping is what stops a bare unsubscribe
    # reaching a diff watch, and reading subscriptions directly also reaches an
    # orphan -- a subscription whose request was already cancelled without it,
    # which is the exact state an older build of this tool used to leave behind.
    targets = [
        row
        for row in repository.subscriptions_for_session(
            session_id,
            states=(SubscriptionState.ACTIVE, SubscriptionState.SUSPENDED),
            sources=GENERIC_SOURCES,
        )
        if subject is None or row.subject == subject
    ]
    cancelled = repository.cancel_watch_requests(session_id, now=now, subject=subject)

    for row in targets:
        repository.retire_subscription(row.id, "unsubscribed", now=now)
    retired = len(targets)

    scope = subject if subject is not None else "all subjects"
    return f"Cancelled {cancelled} and stopped {retired} watch(es) for {scope}."


@mcp.tool()
def watch_status() -> str:
    """List the generic watches this session has registered, and how each reads.

    The command is shown, not just the subject: it is stored and re-run on an
    interval long after the turn that registered it, so it has to be auditable
    from here rather than only by reading the database.
    """
    import shlex

    from .command_source import SOURCE_NAME as COMMAND_SOURCE_NAME
    from .command_source import CommandSpec

    repository, session_id = _watch_repository()
    rows = repository.active_watch_requests(session_id)
    if not rows:
        return "This session has no generic watches."
    lines = []
    for _session, source, subject, spec_json, _kinds in rows:
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
        lines.append(f"{subject} via {source}{detail}")
    return "Active watches:\n" + "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser()
    # A native harness runs the vendor TUI, which does not hand the policy's
    # rewritten tool result back to the model, so the server must make the
    # policy round trip itself and return the policy's own answer. The two
    # flags are kept separate rather than folded into one --native because the
    # bridge layouts differ; they are mutually exclusive.
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--native-codex", action="store_true")
    mode.add_argument("--native-claude", action="store_true")
    args = parser.parse_args()
    global _NATIVE_MODE
    if args.native_codex:
        _NATIVE_MODE = "codex"
    elif args.native_claude:
        _NATIVE_MODE = "claude"
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
