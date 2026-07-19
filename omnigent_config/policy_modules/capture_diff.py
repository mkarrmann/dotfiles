"""Passive label-capture policy for the Omnigent server.

Harness-agnostic metadata capture: every tool call (native Claude/Codex,
generic ``acp:`` agents, goose, qwen, ...) is dispatched through the
server-side policy engine, so a single ``tool_result`` policy here captures
metadata regardless of which harness produced it -- no agent cooperation
required.

The canonical use is stamping a session with the Phabricator diff it created:
when any tool's output contains ``Differential Revision: .../D12345``, the
``D12345`` is written to the ``omnigent.diff.number`` session label. Orchest
already polls session labels, so the diff surfaces on the associated task with
no further wiring.

Contract (verified against omnigent 0.5.1):
- Declared as a *factory* in ``POLICY_REGISTRY`` (kind ``factory``); the server
  calls it once at build time with the ``arguments`` from server config and the
  returned closure is the per-event evaluator.
- The evaluator returns ``None`` to abstain (engine treats as ALLOW) or a dict
  ``{"result": "ALLOW", "set_labels": {...}}`` to capture. ``set_labels`` on a
  plain ALLOW is applied by ``engine.apply_label_writes``.
- HACK: policy exceptions fail *closed* to DENY and would block the tool, so the
  evaluator catches everything and abstains -- a capture policy must never be
  able to break a session.
- Labels are upsert-merged and each value is capped at 256 chars.
"""

from __future__ import annotations

import re
from typing import Any, Callable, Optional

_LABEL_VALUE_MAX = 256


def _result_text(event: dict) -> str:
    """Best-effort flatten of a tool_result payload to searchable text.

    The ``tool_result`` payload shape varies by harness/tool, so rather than
    assume ``data["result"]`` we stringify the whole ``data`` plus the original
    request. The ``Differential Revision:`` line survives either way.
    """
    parts = []
    data = event.get("data")
    if isinstance(data, dict) and "result" in data:
        parts.append(str(data.get("result")))
    else:
        parts.append(str(data))
    req = event.get("request_data")
    if req is not None:
        parts.append(str(req))
    return "\n".join(parts)


def capture_labels_policy(
    patterns: Optional[dict] = None,
    on_tools: Optional[list] = None,
) -> Callable[[dict], Optional[dict]]:
    """Build a passive capture evaluator.

    :param patterns: Map of ``label_key -> regex``. On a ``tool_result`` whose
        text matches the regex, ``label_key`` is set to capture group 1 (or the
        whole match if the pattern has no groups). A label already present on the
        session is never overwritten.
    :param on_tools: Optional allowlist of tool names to inspect. When omitted,
        every tool's result is inspected.
    :returns: An arity-1 evaluator ``(event) -> dict | None``.
    """
    compiled = []
    for key, pat in (patterns or {}).items():
        try:
            compiled.append((str(key), re.compile(pat)))
        except re.error:
            # A bad pattern must not take down policy loading; skip it.
            continue
    tool_filter = set(on_tools) if on_tools else None

    def _evaluate(event: dict) -> Optional[dict]:
        try:
            if event.get("type") != "tool_result":
                return None
            if tool_filter is not None and event.get("target") not in tool_filter:
                return None
            if not compiled:
                return None

            current = event.get("context", {}).get("labels", {}) or {}
            text = _result_text(event)

            set_labels: dict[str, str] = {}
            for key, rx in compiled:
                if key in current:
                    continue
                m = rx.search(text)
                if not m:
                    continue
                value = m.group(1) if m.groups() else m.group(0)
                if value:
                    set_labels[key] = value[:_LABEL_VALUE_MAX]

            if not set_labels:
                return None
            return {
                "result": "ALLOW",
                "set_labels": set_labels,
                "reason": "capture_diff: stamped session metadata "
                + ", ".join(sorted(set_labels)),
            }
        except Exception:
            # Never let a capture failure block or deny a tool call.
            return None

    return _evaluate


POLICY_REGISTRY: list[dict[str, Any]] = [
    {
        "handler": "capture_diff.capture_labels_policy",
        "kind": "factory",
        "name": "Capture labels from tool output",
        "description": (
            "Passively extract metadata (e.g. Phabricator diff numbers) from "
            "tool_result output via regex and write it to session labels."
        ),
        "params_schema": {
            "type": "object",
            "properties": {
                "patterns": {
                    "type": "object",
                    "description": "Map of label key -> regex applied to tool output.",
                    "additionalProperties": {"type": "string"},
                },
                "on_tools": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional allowlist of tool names to inspect.",
                },
            },
        },
    },
]
