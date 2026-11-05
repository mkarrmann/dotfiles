"""OpenCode 2 wire shapes: ids, times, event envelopes and response bodies.

Everything here is decoded by T3 Code's Effect schemas (`@opencode/schema` and
`@opencode/protocol` 2.0.28): ids carry fixed prefixes, times are epoch
milliseconds, and an optional key is omitted rather than sent as null.
"""

from __future__ import annotations

import itertools
import os
import secrets
import time
from collections import defaultdict
from typing import Any, Final, Literal

type Json = None | bool | int | float | str | list[Json] | dict[str, Json]
type JsonObject = dict[str, Json]

VERSION: Final = "2.0.28"
PROJECT_ID: Final = "global"

# The durable event types and their schema versions (`@opencode/schema` session-event.js).
# A durable event must carry `durable.version` equal to its type's version or it fails to decode.
DURABLE_VERSIONS: Final[dict[str, int]] = {
    **dict.fromkeys(
        [
            "session.created",
            "session.agent.selected",
            "session.model.selected",
            "session.moved",
            "session.renamed",
            "session.permissions",
            "session.inbox.delivered",
            "session.inbox.enqueued",
            "session.inbox.cancelled",
            "session.execution.started",
            "session.execution.succeeded",
            "session.execution.failed",
            "session.execution.interrupted",
            "session.synthetic",
            "session.step.started",
            "session.step.ended",
            "session.step.failed",
            "session.text.started",
            "session.text.ended",
            "session.reasoning.started",
            "session.reasoning.ended",
            "session.tool.input.started",
            "session.tool.input.ended",
            "session.tool.called",
            "session.retry.scheduled",
            "session.compaction.started",
            "session.compaction.ended",
            "session.compaction.failed",
        ],
        1,
    ),
    "session.tool.success": 2,
    "session.tool.failed": 2,
}

_counter = itertools.count()
_seqs: defaultdict[str, itertools.count[int]] = defaultdict(itertools.count)


def now_ms() -> int:
    return int(time.time() * 1000)


def _unique() -> str:
    """Sortable and unique across this server's lifetime and its restarts."""
    return f"{time.time_ns():x}{next(_counter):04x}{secrets.token_hex(3)}"


def event_id() -> str:
    return f"evt_{_unique()}"


def message_id() -> str:
    return f"msg_{_unique()}"


def permission_id() -> str:
    return f"per_{_unique()}"


def form_id() -> str:
    return f"frm_{_unique()}"


def tool_call_id(native: str) -> str:
    """A tool call id unique across sessions: T3 Code keys tool items by this id alone."""
    return f"call_{_unique()}_{native}"


SessionEventType = str


def event(
    event_type: SessionEventType,
    data: JsonObject,
    *,
    location: JsonObject | None = None,
) -> JsonObject:
    """An `OpenCodeEvent` frame; durable session events get the envelope their schema requires."""
    frame: JsonObject = {"id": event_id(), "created": now_ms(), "type": event_type}
    version = DURABLE_VERSIONS.get(event_type)
    if version is not None:
        aggregate = data.get("sessionID")
        if not isinstance(aggregate, str):
            raise ValueError(f"durable event {event_type} needs data.sessionID")
        frame["durable"] = {"aggregateID": aggregate, "seq": next(_seqs[aggregate]), "version": version}
    if location is not None:
        frame["location"] = location
    frame["data"] = data
    return frame


def location(directory: str) -> JsonObject:
    return {"directory": directory}


def server_info(urls: list[str]) -> JsonObject:
    return {"version": VERSION, "pid": os.getpid(), "urls": list[Json](urls), "paths": {"tmp": "/tmp"}}


ErrorTag = Literal[
    "SessionNotFoundError",
    "PermissionNotFoundError",
    "FormNotFoundError",
    "FormAlreadySettledError",
    "UnauthorizedError",
    "InvalidRequestError",
    "ConflictError",
    "CommandNotFoundError",
    "MessageNotFoundError",
]

ERROR_STATUS: Final[dict[ErrorTag, int]] = {
    "SessionNotFoundError": 404,
    "PermissionNotFoundError": 404,
    "FormNotFoundError": 404,
    "FormAlreadySettledError": 409,
    "UnauthorizedError": 401,
    "InvalidRequestError": 400,
    "ConflictError": 409,
    "CommandNotFoundError": 404,
    "MessageNotFoundError": 404,
}


def error_body(tag: ErrorTag, message: str, **fields: Any) -> JsonObject:
    return {"_tag": tag, "message": message, **fields}


def zero_tokens() -> JsonObject:
    return {"input": 0, "output": 0, "reasoning": 0, "cache": {"read": 0, "write": 0}}
