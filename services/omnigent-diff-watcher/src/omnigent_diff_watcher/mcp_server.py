"""Stateless MCP intent tools; Omnigent policy binds results to a session."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path
from typing import Literal
from urllib.parse import quote, urlparse

import httpx
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("diff-watch", log_level="ERROR")
EventName = Literal["review_comment", "ci_failure"]

# ``None`` outside a native harness (the streamed SDK harnesses get the policy's
# rewritten result for free); otherwise the harness whose bridge layout applies.
_NATIVE_MODE: str | None = None
_NATIVE_HARNESS = {"codex": "codex-native", "claude": "claude-native"}
_NOT_NATIVE = "diff watch requires an Omnigent native {} session"


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
) -> str:
    """Opt the current Omnigent session into diff review and CI notifications."""
    selected = sorted(set(["review_comment", "ci_failure"] if events is None else events))
    if not selected:
        raise ValueError("at least one event type is required")
    arguments: dict[str, object] = {} if events is None else {"events": events}
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
