"""Native subagents as OpenCode subagent calls and child sessions.

Both native harnesses run subagents as Omnigent child sessions. On the parent's
stream a subagent shows as the harness's spawning tool call (claude-native
`Agent`/`Task`, codex-native `spawn_agent`), then `session.created` naming the
child, whose labels carry the spawning call's id. The child's work streams on
its own session. A Claude subagent in the background returns at launch ("Async
agent launched"), and its result reaches the parent later as a
`<task-notification>` input that starts a turn of its own.

T3 Code's OpenCode driver knows OpenCode's `subagent` tool: a call linked to a
child session by `session.created{parentID}` plus `tool.progress{sessionID}`,
child executions on the child's id, and a background result delivered as a
`synthetic` inbox item before the parent's next execution.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Final, Literal

from .wire import JsonObject

SPAWNING_TOOLS: Final = frozenset({"Agent", "Task", "spawn_agent", "spawnAgent"})
CALL_LABELS: Final = ("omnigent.claude_native.tool_use_id", "omnigent.codex_native.collab_tool_call_id")
DESCRIPTION_LABELS: Final = ("omnigent.claude_native.description", "omnigent.codex_native.agent_nickname")
BACKGROUND_MARKER: Final = "Async agent launched"
NOTIFICATION_TAG: Final = "<task-notification>"

ReportState = Literal["completed", "error", "cancelled"]


def is_spawning(name: str) -> bool:
    return name in SPAWNING_TOOLS


def call_input(arguments: Any) -> JsonObject:
    """The OpenCode subagent tool's input for the harness's spawning call."""
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except json.JSONDecodeError:
            arguments = {"prompt": arguments}
    arguments = arguments if isinstance(arguments, dict) else {}
    prompt = arguments.get("prompt") or arguments.get("message") or arguments.get("task") or ""
    tool_input: JsonObject = {"prompt": str(prompt)}
    description = arguments.get("description") or arguments.get("agent_nickname")
    if description:
        tool_input["description"] = str(description)
    agent = arguments.get("subagent_type") or arguments.get("agent_type") or arguments.get("agent_role")
    if agent:
        tool_input["agent"] = str(agent)
    if arguments.get("run_in_background") is True:
        tool_input["background"] = True
    return tool_input


def output_text(output: str) -> str:
    """A spawning call's output as text; Claude returns it as a JSON list of content blocks."""
    try:
        blocks = json.loads(output)
    except json.JSONDecodeError:
        return output
    if isinstance(blocks, list):
        return "".join(b.get("text", "") for b in blocks if isinstance(b, dict) and isinstance(b.get("text"), str))
    return output


def is_background_launch(output: str) -> bool:
    return output_text(output).lstrip().startswith(BACKGROUND_MARKER)


def spawning_call_id(labels: dict[str, Any]) -> str | None:
    return next((str(labels[k]) for k in CALL_LABELS if labels.get(k)), None)


def description_of(labels: dict[str, Any]) -> str | None:
    return next((str(labels[k]) for k in DESCRIPTION_LABELS if labels.get(k)), None)


@dataclass(frozen=True)
class Notification:
    """A background subagent's result, as Claude delivers it to its parent."""

    call_id: str | None
    state: ReportState
    summary: str
    result: str


def _tag(text: str, name: str) -> str | None:
    match = re.search(rf"<{name}>(.*?)</{name}>", text, re.DOTALL)
    return match.group(1).strip() if match else None


def notification(text: str) -> Notification | None:
    if not text.lstrip().startswith(NOTIFICATION_TAG):
        return None
    status = (_tag(text, "status") or "completed").lower()
    state: ReportState = (
        "completed" if status == "completed" else "cancelled" if status in ("killed", "stopped", "cancelled") else "error"
    )
    summary = _tag(text, "summary") or "Subagent finished"
    return Notification(_tag(text, "tool-use-id"), state, summary, _tag(text, "result") or summary)
