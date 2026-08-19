#!/usr/bin/env python3
"""Feed Claude tool output to Omnigent's tool_result policy phase.

Works around an upstream bug that makes diff-watch unusable in native Claude
sessions. Omnigent injects its own catch-all ``PostToolUse`` hook
(``claude_native_hook evaluate-policy``), which builds the ``PHASE_TOOL_RESULT``
event in ``omnigent/native_policy_hook.py``::

    if hook_event == _POST_TOOL_USE:
        tool_output = payload.get("tool_output", "")

Claude Code names that field ``tool_response``; ``tool_output`` is a Codex-ism,
and the module docstring's claim that "PreToolUse / PostToolUse payloads use the
same field names" holds only for the fields they share. So every result event
from a native Claude session carries ``data.result == ""``.

Nothing errors -- it just silently defeats every tool_result policy. The one
that matters here is ``capture_diff`` (omnigent_config/policy_modules), which
regex-matches submit output to stamp ``omnigent.diff.number`` on the session.
With empty text it never matches, the label is never written, and
``diff_watch_subscribe`` answers "this session has no associated Phabricator
diff" forever -- no CI or review notifications, no error anyone can see.

This hook re-sends the same event with the output actually populated. The
server-side pattern in ``omnigent_config/server.yaml`` stays authoritative; this
only supplies the text it was always meant to see. Delete this file once
upstream reads ``tool_response``: a duplicate capture is harmless (the label is
an idempotent ordered set), so the two can overlap safely during a transition.
"""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import urllib.request
from pathlib import Path
from urllib.parse import quote, urlparse

# Strict superset of the server's capture pattern, which requires
# `https?://<...>/D<digits>`. Gating on the *shape* rather than on the server's
# leading marker allowlist ("Differential Revision:", "created:", ...) keeps the
# two from drifting: a marker added to server.yaml alone would otherwise
# silently stop being captured here.
_LOOKS_LIKE_DIFF = re.compile(r"/D[1-9][0-9]*\b")

_TIMEOUT_SECONDS = 5


def _output_text(payload: dict) -> str:
    """Flatten the tool result to text, tolerating either field name.

    ``tool_response`` is what Claude Code sends and may be a str or a dict
    (``{"stdout": ..., "stderr": ...}`` for Bash). ``tool_output`` is read too
    so this keeps working unchanged if upstream renames rather than fixes.
    """
    for key in ("tool_response", "tool_output"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value
        if isinstance(value, dict):
            return json.dumps(value)
        if value:
            return str(value)
    return ""


def _bridge_dir(claude_session: str) -> Path | None:
    """Find the Omnigent bridge directory owning this Claude session.

    Mirrors ``omnigent_diff_watcher.mcp_server._claude_bridge_dir``: several
    bridge directories coexist (one per concurrent session), and a session that
    was forked on compaction appears in ``seen_claude_session_ids`` rather than
    ``claude_session_id``.
    """
    for root in dict.fromkeys([tempfile.gettempdir(), "/tmp"]):
        base = Path(root) / f"omnigent-{os.getuid()}" / "claude-native"
        if not base.is_dir():
            continue
        for candidate in sorted(base.iterdir()):
            try:
                state = json.loads((candidate / "state.json").read_text("utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(state, dict):
                continue
            seen = state.get("seen_claude_session_ids")
            if state.get("claude_session_id") == claude_session or (
                isinstance(seen, list) and claude_session in seen
            ):
                return candidate
    return None


def _read_json(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text("utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _endpoint(bridge: Path) -> tuple[str, dict[str, str]] | None:
    hook = _read_json(bridge / "permission_hook.json")
    server_url = hook.get("ap_server_url")
    if not isinstance(server_url, str):
        return None
    parsed = urlparse(server_url)
    # Loopback only: this posts session-scoped data with the bridge's own
    # credentials, so a non-local advertisement is treated as unusable.
    if parsed.scheme not in {"http", "https"} or parsed.hostname not in {
        "127.0.0.1",
        "localhost",
    }:
        return None
    session_id = _read_json(bridge / "bridge.json").get("active_session_id")
    if not isinstance(session_id, str) or not session_id:
        return None
    raw_headers = hook.get("ap_auth_headers")
    headers = (
        {str(k): str(v) for k, v in raw_headers.items()}
        if isinstance(raw_headers, dict)
        else {}
    )
    url = (
        f"{server_url.rstrip('/')}/v1/sessions/"
        f"{quote(session_id, safe='')}/policies/evaluate"
    )
    return url, headers


def main() -> None:
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError:
        return
    if not isinstance(payload, dict):
        return

    text = _output_text(payload)
    if not text or not _LOOKS_LIKE_DIFF.search(text):
        return

    claude_session = payload.get("session_id") or os.environ.get("CLAUDE_CODE_SESSION_ID")
    if not isinstance(claude_session, str) or not claude_session:
        return
    bridge = _bridge_dir(claude_session)
    if bridge is None:
        return
    endpoint = _endpoint(bridge)
    if endpoint is None:
        return
    url, headers = endpoint

    body = {
        "event": {
            "type": "PHASE_TOOL_RESULT",
            "target": "",
            "data": {"result": text},
            "context": {"harness": "claude-native"},
            "request_data": {
                "name": payload.get("tool_name", ""),
                "arguments": payload.get("tool_input") or {},
            },
        }
    }
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", **headers},
    )
    # Loopback must not be routed through the corp proxy, and the ambient
    # http_proxy env would otherwise capture it.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    opener.open(request, timeout=_TIMEOUT_SECONDS).read()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # A capture must never fail a tool call. PostToolUse cannot block, but a
        # traceback on stderr still surfaces as hook noise on every submit.
        pass
