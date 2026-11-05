"""A session's history as OpenCode messages, built from its Omnigent items.

T3 Code reads history for thread snapshots, fork and rollback boundaries, and
to catch a running turn up after a reconnect. A user message carries the id T3
Code chose for its prompt (kept in the store), and the assistant message after
it is keyed by that id, as the live events of its execution are, so the two
agree. Each assistant message that finished gets an `idle` marker.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any

from .store import Outcome
from .wire import Json, JsonObject

TERMINAL_OUTPUT = "(no output)"


def _text(item: dict[str, Any]) -> str:
    content = item.get("content")
    if isinstance(content, str):
        try:
            content = json.loads(content)
        except json.JSONDecodeError:
            return content
    if not isinstance(content, list):
        return ""
    return "".join(c["text"] for c in content if isinstance(c, dict) and isinstance(c.get("text"), str))


def _ms(item: dict[str, Any], fallback: int) -> int:
    created = item.get("created_at")
    return int(created * 1000) if isinstance(created, (int, float)) else fallback


def _arguments(item: dict[str, Any]) -> JsonObject:
    arguments = item.get("arguments")
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except json.JSONDecodeError:
            return {"input": arguments}
    return arguments if isinstance(arguments, dict) else {"input": arguments}


def build(
    *,
    items: Iterable[dict[str, Any]],
    user_ids: dict[str, str],
    user_id_for: Any,
    assistant_id_for: Any,
    tool_id_for: Any,
    tool_name_for: Any,
    outcomes: dict[str, Outcome],
    continuing: set[str],
    agent: str,
    model: JsonObject,
    running: bool,
) -> list[JsonObject]:
    """OpenCode messages, oldest first.

    `user_ids` maps an Omnigent user item id to T3 Code's id for it;
    `user_id_for(item)` names the others. `assistant_id_for(user_id)` keys the
    reply to a user message. A user message in `continuing` was a steer into a
    running execution, so no idle marker comes before it.
    """
    messages: list[JsonObject] = []
    assistant: JsonObject | None = None
    tools: dict[str, JsonObject] = {}
    last_user: str | None = None
    owner: str | None = None
    """The user message that started the running execution; steers into it do not."""
    last_time = 0

    def finish(idle: bool) -> None:
        nonlocal assistant
        if assistant is None:
            return
        time = assistant["time"]
        assert isinstance(time, dict)
        time["completed"] = last_time
        messages.append(assistant)
        if idle:
            user = owner or last_user or str(assistant["id"])
            messages.append(
                {
                    "id": f"{assistant['id']}_idle",
                    "type": "idle",
                    "outcome": outcomes.get(user, "succeeded"),
                    "time": {"created": last_time},
                }
            )
        assistant = None
        tools.clear()

    def reply(created: int) -> JsonObject:
        nonlocal assistant
        if assistant is None:
            assistant = {
                "id": assistant_id_for(last_user or ""),
                "type": "assistant",
                "agent": agent,
                "model": model,
                "content": [],
                "time": {"created": created},
            }
        return assistant

    for item in items:
        kind = item.get("type")
        created = _ms(item, last_time)
        if kind == "message" and item.get("role") == "user":
            item_id = str(item.get("id"))
            user = user_ids.get(item_id) or user_id_for(item)
            finish(idle=user not in continuing)
            last_user = user
            if user not in continuing:
                owner = user
            messages.append({"id": last_user, "type": "user", "text": _text(item), "time": {"created": created}})
        elif kind == "message" and item.get("role") == "assistant" and not item.get("is_meta"):
            content = reply(created)["content"]
            assert isinstance(content, list)
            content.append({"type": "text", "text": _text(item)})
        elif kind == "reasoning":
            text = _text(item) or "".join(s.get("text", "") for s in item.get("summary") or [] if isinstance(s, dict))
            if text:
                content = reply(created)["content"]
                assert isinstance(content, list)
                content.append({"type": "reasoning", "text": text})
        elif kind == "function_call" and isinstance(item.get("call_id"), str):
            content = reply(created)["content"]
            assert isinstance(content, list)
            part: JsonObject = {
                "type": "tool",
                "id": tool_id_for(item["call_id"]),
                "name": tool_name_for(str(item.get("name") or "tool")),
                "state": {"status": "running", "input": _arguments(item), "metadata": {}},
                "time": {"created": created},
            }
            tools[item["call_id"]] = part
            content.append(part)
        elif kind == "function_call_output" and isinstance(item.get("call_id"), str):
            part_or_none = tools.pop(item["call_id"], None)
            if part_or_none is not None:
                output = item.get("output")
                text = output if isinstance(output, str) else json.dumps(output)
                state = part_or_none["state"]
                assert isinstance(state, dict)
                part_or_none["state"] = {
                    "status": "completed",
                    "input": state["input"],
                    "content": [{"type": "text", "text": text or TERMINAL_OUTPUT}],
                }
                time = part_or_none["time"]
                assert isinstance(time, dict)
                time["completed"] = created
        else:
            continue
        last_time = max(last_time, created)
    finish(idle=not running)
    return messages


def page(messages: list[JsonObject], *, order: str | None, cursor: str | None, limit: int) -> JsonObject:
    """One page of `message.list`; the cursor is an opaque position that remembers the direction."""
    if cursor:
        direction, _, offset_text = cursor.partition(":")
        offset = int(offset_text or 0)
    else:
        direction, offset = ("desc" if order == "desc" else "asc"), 0
    ordered = list(reversed(messages)) if direction == "desc" else messages
    chunk: list[Json] = list(ordered[offset : offset + limit])
    result: JsonObject = {"data": chunk, "cursor": {}}
    if offset + limit < len(ordered):
        result["cursor"] = {"next": f"{direction}:{offset + limit}"}
    return result
