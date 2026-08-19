"""Stateless MCP intent tools; Omnigent policy binds results to a session."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Literal
from urllib.parse import quote, urlparse

import httpx
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("diff-watch", log_level="ERROR")
EventName = Literal["review_comment", "ci_failure"]
_NATIVE_CODEX_MODE = False


def _native_bridge_dir() -> Path:
    codex_home = os.environ.get("CODEX_HOME")
    if not codex_home:
        raise RuntimeError("diff watch requires an Omnigent native Codex session")
    path = Path(codex_home).expanduser()
    if path.name != "codex-home" or path.parent.parent.name != "codex-native":
        raise RuntimeError("diff watch requires an Omnigent native Codex session")
    return path.parent


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


def _policy_endpoints(bridge_dir: Path) -> list[tuple[str, dict[str, str]]]:
    """Return ``(url, headers)`` policy endpoints in precedence order.

    The runner's loopback relay is preferred because its token does not
    expire, matching ``omnigent.native_policy_hook``. The direct server is
    kept as a fallback: the relay advertisement is per-runner and goes stale
    when a runner restarts, while ``policy_hook.json`` is rewritten each time.
    """
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

    server_url = _loopback_url(hook.get("ap_server_url"))
    if server_url and isinstance(session_id, str) and session_id:
        headers: dict[str, str] = {}
        raw_headers = hook.get("ap_auth_headers")
        if isinstance(raw_headers, dict):
            headers = {str(k): str(v) for k, v in raw_headers.items()}
        component = quote(session_id, safe="")
        endpoints.append((f"{server_url}/v1/sessions/{component}/policies/evaluate", headers))

    return endpoints


def _native_policy_result(
    tool_name: str,
    arguments: dict[str, object],
    intent_result: str,
) -> str:
    if not _NATIVE_CODEX_MODE:
        return intent_result

    endpoints = _policy_endpoints(_native_bridge_dir())
    if not endpoints:
        raise RuntimeError("Omnigent policy routing is not advertised for this session")

    request = {
        "event": {
            "type": "PHASE_TOOL_RESULT",
            "target": "",
            "data": {"result": intent_result},
            "context": {"harness": "codex-native"},
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
    parser.add_argument("--native-codex", action="store_true")
    args = parser.parse_args()
    global _NATIVE_CODEX_MODE
    _NATIVE_CODEX_MODE = args.native_codex
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
