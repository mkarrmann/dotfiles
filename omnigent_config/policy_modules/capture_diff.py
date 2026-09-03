"""Passive label-capture policy for the Omnigent server.

Harness-agnostic metadata capture: every tool call (native Claude/Codex,
generic ``acp:`` agents, goose, qwen, ...) is dispatched through the
server-side policy engine, so a single ``tool_result`` policy here captures
metadata regardless of which harness produced it -- no agent cooperation
required.

The canonical use is stamping a session with the Phabricator diffs it created:
when any tool's output contains ``Differential Revision: <url>/D12345``, the
``D12345`` is appended to the ``omnigent.diff.number`` session label, which is a
comma-separated ordered set so one session can own a whole stack. The diff
watcher also binds its stateless MCP intent tools to the current session here;
subscribe can add validated IDs for a pre-existing stack.

The capture pattern deliberately requires the full Phabricator URL rather than a
bare ``D\\d+``: the label is written from *any* tool's output, so a bare pattern
latches onto example diff numbers in documentation and skill output. Combined
with the old first-match-wins rule that silently bound sessions to placeholder
diffs forever.

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

import json
import re
from collections.abc import Callable
from typing import Any

_LABEL_VALUE_MAX = 256
_WATCH_LABEL = "omnigent.diff.watch"
_DIFF_LABEL = "omnigent.diff.number"
_WATCH_TOOLS = {
    "diff_watch_subscribe",
    "diff_watch_unsubscribe",
    "diff_watch_status",
}
_WATCH_EVENTS = {
    "review_comment",
    "ci_failure",
    "ai_review",
    "ci_green",
}


def _result_text(event: dict) -> str:
    """Best-effort flatten of a tool_result payload to searchable text.

    The ``tool_result`` payload shape varies by harness/tool, so the whole
    ``data`` is stringified when the ``result`` key is absent. A diff
    announcement survives either way.

    The REQUEST is deliberately not searched. It used to be, as a second
    fallback, but a request is the command -- not its output -- and the only
    diff URLs appearing in a command are ones being *written*: a drafted
    ``sl commit -m "...Differential Revision: <url>..."``, documentation, or a
    test fixture. Observed: verifying this very policy with a fixture holding
    ``Differential Revision: https://.../D99999999`` bound that nonexistent
    diff to the live session. Every real announcement -- jf submit, conf
    submit, and ``sl log`` of a landed commit -- arrives as output.

    This matters beyond a stray subscription: the label is a capped set that
    evicts oldest-first, so a false capture can push out a real diff.
    """
    data = event.get("data")
    if isinstance(data, dict) and "result" in data:
        return str(data.get("result"))
    return str(data)


def _merge_accumulated(current: str | None, found: list[str]) -> str | None:
    """Append newly seen values to a comma-separated ordered set.

    Order is preserved so the first diff a session created stays first, which
    keeps the label stable for a stack submitted bottom-up. Oldest entries are
    dropped when the 256-char label cap would be exceeded, because a session
    that has produced 20+ diffs cares about the recent ones.
    """
    existing = [part for part in (current or "").split(",") if part]
    merged = list(existing)
    for value in found:
        if value not in merged:
            merged.append(value)
    if merged == existing:
        return None
    while merged and len(",".join(merged)) > _LABEL_VALUE_MAX:
        merged.pop(0)
    return ",".join(merged) if merged else None


def capture_labels_policy(
    patterns: dict | None = None,
    on_tools: list | None = None,
    accumulate: list | None = None,
) -> Callable[[dict], dict | None]:
    """Build a passive capture evaluator.

    :param patterns: Map of ``label_key -> regex``. On a ``tool_result`` whose
        text matches the regex, ``label_key`` is set to capture group 1 (or the
        whole match if the pattern has no groups). A label already present on the
        session is never overwritten unless the key is in ``accumulate``.
    :param on_tools: Optional allowlist of tool names to inspect. When omitted,
        every tool's result is inspected.
    :param accumulate: Label keys that collect every distinct match as a
        comma-separated ordered set instead of latching the first one. Needed
        for diff numbers: one session routinely submits a whole stack, and a
        single ``jf submit`` prints every diff in it.
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
    accumulating = {str(key) for key in (accumulate or [])}

    def _evaluate(event: dict) -> dict | None:
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
                if key in accumulating:
                    found = [m.group(1) if m.groups() else m.group(0) for m in rx.finditer(text)]
                    merged = _merge_accumulated(current.get(key), [v for v in found if v])
                    if merged is not None:
                        set_labels[key] = merged
                    continue
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
                "reason": "capture_diff: stamped session metadata " + ", ".join(sorted(set_labels)),
            }
        except Exception:
            # Never let a capture failure block or deny a tool call.
            return None

    return _evaluate


def diff_watch_preference_policy(event: dict) -> dict | None:
    """Bind stateless MCP intent tools to the authenticated session labels."""

    try:
        if event.get("type") != "tool_result":
            return None
        tool = _watch_tool_name(event.get("target"))
        if tool is None:
            return None
        context = event.get("context")
        labels = context.get("labels", {}) if isinstance(context, dict) else {}
        if not isinstance(labels, dict):
            labels = {}
        diff_ids = _diff_ids(labels.get(_DIFF_LABEL))

        if tool == "diff_watch_status":
            preference = labels.get(_WATCH_LABEL, "off")
            listed = ", ".join(diff_ids) if diff_ids else "none"
            noun = "diffs" if len(diff_ids) != 1 else "diff"
            return {
                "result": "ALLOW",
                "data": f"Diff watch: {preference}; associated {noun}: {listed}.",
            }
        if tool == "diff_watch_unsubscribe":
            return {
                "result": "ALLOW",
                "set_labels": {_WATCH_LABEL: "off"},
                "data": "Diff-watch notifications are disabled for this session.",
            }
        request = event.get("request_data")
        arguments = request.get("arguments", {}) if isinstance(request, dict) else {}
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except json.JSONDecodeError:
                arguments = {}
        raw_diffs = arguments.get("diffs") if isinstance(arguments, dict) else None
        if raw_diffs is not None:
            if not isinstance(raw_diffs, list) or not raw_diffs:
                return {
                    "result": "ALLOW",
                    "data": (
                        "Cannot subscribe: diffs must contain at least one Phabricator diff ID."
                    ),
                }
            if any(
                not isinstance(value, str) or not re.fullmatch(r"D[1-9][0-9]*", value)
                for value in raw_diffs
            ):
                return {
                    "result": "ALLOW",
                    "data": "Cannot subscribe: every diff must look like D12345.",
                }
            requested_diff_ids = _validated_diff_ids(raw_diffs)
            merged = _merge_accumulated(labels.get(_DIFF_LABEL), requested_diff_ids)
            if merged is not None:
                diff_ids = _diff_ids(merged)

        if not diff_ids:
            return {
                "result": "ALLOW",
                "data": (
                    "Cannot subscribe: this session has no associated Phabricator diff. "
                    'Pass existing diff IDs with diffs: ["D12345"].'
                ),
            }
        raw_events = arguments.get("events") if isinstance(arguments, dict) else None
        if raw_events is None:
            events = sorted(_WATCH_EVENTS)
        elif (
            isinstance(raw_events, list)
            and raw_events
            and all(item in _WATCH_EVENTS for item in raw_events)
        ):
            events = sorted(set(raw_events))
        else:
            return {
                "result": "ALLOW",
                "data": (
                    "Cannot subscribe: events must select review_comment, ci_failure, "
                    "ai_review, or ci_green."
                ),
            }
        preference = ",".join(events)
        listed = ", ".join(diff_ids)
        set_labels = {_WATCH_LABEL: preference}
        if raw_diffs is not None:
            set_labels[_DIFF_LABEL] = ",".join(diff_ids)
        return {
            "result": "ALLOW",
            "set_labels": set_labels,
            "data": f"Diff-watch notifications requested for {listed}: {preference}.",
        }
    except Exception:
        return None


def _watch_tool_name(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    return next((name for name in _WATCH_TOOLS if value.endswith(name)), None)


def _diff_ids(value: object) -> list[str]:
    """Parse the comma-separated diff label into validated, deduped diff ids."""
    if not isinstance(value, str):
        return []
    seen: list[str] = []
    for part in value.split(","):
        candidate = part.strip()
        if re.fullmatch(r"D[1-9][0-9]*", candidate) and candidate not in seen:
            seen.append(candidate)
    return seen


def _validated_diff_ids(values: list[object]) -> list[str]:
    """Return validated, deduplicated Phabricator diff IDs in input order."""
    seen: list[str] = []
    for value in values:
        if not isinstance(value, str) or not re.fullmatch(r"D[1-9][0-9]*", value):
            continue
        if value not in seen:
            seen.append(value)
    return seen


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
                "accumulate": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Label keys that collect every match as a comma-separated "
                        "ordered set instead of latching the first one."
                    ),
                },
            },
        },
    },
]
