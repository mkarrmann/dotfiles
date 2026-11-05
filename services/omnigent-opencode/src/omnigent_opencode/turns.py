"""Omnigent turn tracking: one session stream's events -> turn outputs (pure).

Carried over from `bin/omnigent-acp`, whose comments record why each edge is
treated the way it is.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Literal

from .approvals import Ask, classify

LOG = logging.getLogger(__name__)

IGNORED_EVENTS = frozenset(
    {
        "session.heartbeat",
        "response.heartbeat",
        "session.presence",
        "session.resource.created",
        "session.resource.deleted",
        "session.changed_files.invalidated",
        "session.sandbox_status",
        "session.todos",
        "session.skills",
        "session.mcp_startup",
        "session.superseded",
        "session.agent_changed",
        "session.collaboration_mode",
        "turn.started",
        "turn.completed",
        "turn.failed",
        "turn.cancelled",
        "response.queued",
        "response.retry",
        "response.incomplete",
    }
)


@dataclass(frozen=True)
class TextDelta:
    text: str


@dataclass(frozen=True)
class ThoughtDelta:
    text: str


@dataclass(frozen=True)
class ToolStarted:
    call_id: str
    name: str
    arguments: Any


@dataclass(frozen=True)
class ToolFinished:
    call_id: str
    output: str


@dataclass(frozen=True)
class Usage:
    used: int
    size: int


@dataclass(frozen=True)
class Elicitation:
    ask: Ask


@dataclass(frozen=True)
class ReplyCommitted:
    """An assistant message was committed: text after it is a new message."""


@dataclass(frozen=True)
class TurnEnded:
    outcome: Literal["completed", "cancelled", "failed"]
    error: str | None = None


@dataclass(frozen=True)
class Quiescent:
    """The turn looks finished but is unconfirmed; it ends if ``generation`` survives a quiet period."""

    generation: int


TurnOutput = TextDelta | ThoughtDelta | ToolStarted | ToolFinished | Usage | Elicitation | ReplyCommitted | TurnEnded | Quiescent


def _is_native_ack(response: dict[str, Any]) -> bool:
    model = response.get("model")
    return isinstance(model, str) and model.endswith("-native-ui") and response.get("usage") is None


def _message_text(item: dict[str, Any]) -> str:
    content = item.get("content")
    if not isinstance(content, list):
        return ""
    return "".join(c["text"] for c in content if isinstance(c, dict) and isinstance(c.get("text"), str))


def elicitation_from(data: dict[str, Any]) -> Elicitation:
    return Elicitation(classify(data))


def _is_reply(item: dict[str, Any]) -> bool:
    return item.get("type") == "message" and item.get("role") == "assistant" and not item.get("is_meta")


def _error_text(error: Any) -> str:
    if isinstance(error, dict):
        return str(error.get("message") or error.get("code") or json.dumps(error))
    return str(error) if error else "Omnigent turn failed"


@dataclass
class TurnTracker:
    """Follows one foreground turn on an Omnigent session stream.

    Terminal-backed (``*-native-ui``) agents answer the POST with an empty
    ``response.completed`` acknowledgement; the real turn starts after
    ``session.input.consumed`` and ends at the ``session.status: idle`` that
    carries a ``response_id`` or a ``background_task_count`` — the
    harness-confirmed completion (claude-native's Stop hook). Bare idles come
    from the pane-activity watcher and can fire mid-turn. The confirmed edge is
    not guaranteed to reach the stream, so a turn whose final assistant message
    is committed, with no tool call open and the pane idle, is reported
    ``Quiescent`` and ended by the caller once a quiet period passes without
    any event, terminal activity pulses included. SDK agents end at
    ``response.completed``. Adapted from CodeCompanion's omnigent/events.lua
    reducer.
    """

    native: bool = False
    expected_pending_id: str | None = None
    expected_item_id: str | None = None
    current_response_id: str | None = None
    queue_response: bool = False
    native_consumed: bool = False
    cancelling: bool = False
    message_streamed: bool = False
    emitted_text: str = ""
    replay_text: str | None = None
    late_reply: str | None = None
    """A reply the previous turn streamed but whose item it ended before seeing; its late commit is not this turn's."""
    seen_calls: set[str] = field(default_factory=set)
    seen_outputs: set[str] = field(default_factory=set)
    open_calls: set[str] = field(default_factory=set)
    pane_idle: bool = False
    reply_committed: bool = False
    quiet_generation: int = 0
    ended: bool = False

    @property
    def quiescent(self) -> bool:
        return self.native_consumed and self.reply_committed and self.pane_idle and not self.open_calls

    def _activity(self) -> None:
        self.quiet_generation += 1

    def expect_input(self, pending_id: str | None, item_id: str | None = None) -> None:
        self.expected_pending_id = pending_id
        self.expected_item_id = item_id

    def on_reconnect(self) -> None:
        # The server replays the in-flight assistant text on subscribe.
        self.replay_text = ""

    def request_cancel(self) -> None:
        self.cancelling = True

    def _awaiting_native_input(self) -> bool:
        # Items that arrive before our input is consumed belong to an earlier turn.
        return self.native and not self.native_consumed

    def _end(self, outcome: Literal["completed", "cancelled", "failed"], error: str | None = None) -> list[TurnOutput]:
        if self.ended:
            return []
        self.ended = True
        return [TurnEnded(outcome, error)]

    def _text_delta(self, delta: str) -> list[TurnOutput]:
        self.message_streamed = True
        if self.replay_text is not None:
            self.replay_text += delta
            if len(self.replay_text) <= len(self.emitted_text):
                return []
            delta = self.replay_text[len(self.emitted_text) :]
            self.replay_text = None
        self.emitted_text += delta
        return [TextDelta(delta)] if delta else []

    def _observe(self, outputs: list[TurnOutput], was_quiescent: bool) -> list[TurnOutput]:
        if self.quiescent and not was_quiescent and not self.ended:
            outputs.append(Quiescent(self.quiet_generation))
        return outputs

    def feed(self, event_type: str, data: dict[str, Any]) -> list[TurnOutput]:
        if self.ended or event_type in IGNORED_EVENTS:
            return []
        was_quiescent = self.quiescent
        return self._observe(self._feed(event_type, data), was_quiescent)

    def on_snapshot(self, snapshot: dict[str, Any]) -> list[TurnOutput]:
        """Catch up from a ``GET /v1/sessions/{id}`` snapshot taken after our input was posted."""
        if self.ended:
            return []
        was_quiescent = self.quiescent
        return self._observe(self._snapshot(snapshot), was_quiescent)

    def _snapshot(self, snapshot: dict[str, Any]) -> list[TurnOutput]:
        status = snapshot.get("status")
        items = [i for i in snapshot.get("items") or [] if isinstance(i, dict)]
        if self.native:
            if not self.native_consumed:
                pending = {p.get("pending_id") for p in snapshot.get("pending_inputs") or [] if isinstance(p, dict)}
                if self.expected_pending_id is None or self.expected_pending_id in pending:
                    return []
                self.native_consumed = True
                self.expected_pending_id = None
            if status == "failed":
                return self._end("failed", _error_text(snapshot.get("last_task_error")))
            # The confirmed idle's response_id is not in the snapshot, so a finished turn ends by quiescence.
            self.pane_idle = status == "idle"
            if self.pane_idle and items and _is_reply(items[-1]):
                self.reply_committed = True
                self.open_calls.clear()
            return []
        ours = next((n for n, i in enumerate(items) if self.expected_item_id and i.get("id") == self.expected_item_id), None)
        # A status from before our turn started would be the previous turn's.
        if self.current_response_id is None and (ours is None or ours + 1 == len(items)):
            return []
        if status == "failed":
            return self._end("failed", _error_text(snapshot.get("last_task_error")))
        if status == "idle":
            return self._end("completed")
        return []

    def _feed(self, event_type: str, data: dict[str, Any]) -> list[TurnOutput]:
        if event_type in ("response.created", "response.in_progress"):
            response = data.get("response") or {}
            model = response.get("model")
            self.queue_response = isinstance(model, str) and model.endswith("-native-ui")
            if response.get("id") != self.current_response_id:
                self.current_response_id = response.get("id")
                self.seen_calls.clear()
                self.seen_outputs.clear()
            return []

        if (
            event_type
            in (
                "response.output_text.delta",
                "response.reasoning_text.delta",
                "response.reasoning_summary_text.delta",
                "response.output_item.done",
            )
            and self._awaiting_native_input()
        ):
            return []

        if event_type == "response.output_text.delta":
            self.reply_committed = False
            self._activity()
            return self._text_delta(data.get("delta") or "")

        if event_type in ("response.reasoning_text.delta", "response.reasoning_summary_text.delta"):
            self.reply_committed = False
            self._activity()
            delta = data.get("delta") or ""
            return [ThoughtDelta(delta)] if delta else []

        if event_type == "response.output_item.done":
            self._activity()
            return self._item_done(data.get("item") or {})

        if event_type == "response.completed":
            response = data.get("response") or {}
            if _is_native_ack(response) or self._awaiting_native_input():
                self.queue_response = False
                self.current_response_id = None
                return []
            return self._usage(response.get("usage")) + self._end("completed")

        if event_type == "response.failed":
            return self._end("failed", _error_text((data.get("response") or {}).get("error")))

        if event_type in ("response.cancelled", "session.interrupted"):
            # codex-native reports one Stop twice; the echo can land after the next turn starts.
            if self._awaiting_native_input() and not self.cancelling:
                return []
            return self._end("cancelled")

        if event_type == "session.terminal.activity":
            # Claude redraws its busy line while it thinks, so a pulse means the turn may still be running.
            self._activity()
            return []

        if event_type == "session.status":
            status = data.get("status")
            if status == "failed":
                return self._end("failed", _error_text(data.get("error")))
            self.pane_idle = status == "idle"
            if status != "idle":
                self._activity()
                return []
            if self.cancelling:
                return self._end("cancelled")
            if self.native:
                # Only harness-confirmed edges carry a response id or a background-task count (Stop hook).
                confirmed = data.get("response_id") or data.get("background_task_count") is not None
                if self.native_consumed and confirmed:
                    return self._end("completed")
                return []
            if self.current_response_id and not self.queue_response:
                return self._end("completed")
            return []

        if event_type == "session.input.consumed":
            consumed = data.get("data") or {}
            matches = self.expected_pending_id is None or consumed.get("cleared_pending_id") == self.expected_pending_id
            if self.native and not self.native_consumed and matches:
                self.native_consumed = True
                self.expected_pending_id = None
            return []

        if event_type == "session.usage":
            return self._usage(data)

        if event_type == "response.elicitation_request":
            return [elicitation_from(data)]

        LOG.debug("unhandled event %s", event_type)
        return []

    def _item_done(self, item: dict[str, Any]) -> list[TurnOutput]:
        item_type = item.get("type")
        if _is_reply(item):
            streamed = self.message_streamed
            self.message_streamed = False
            self.emitted_text = ""
            self.replay_text = None
            self.reply_committed = True
            text = _message_text(item)
            if not streamed and self.late_reply is not None and text.strip() == self.late_reply.strip():
                self.late_reply = None
                self.reply_committed = False
                return []
            return [ReplyCommitted()] if streamed or not text else [TextDelta(text), ReplyCommitted()]
        if item_type == "function_call" and isinstance(item.get("call_id"), str):
            if item["call_id"] in self.seen_calls:
                return []
            self.seen_calls.add(item["call_id"])
            self.open_calls.add(item["call_id"])
            self.reply_committed = False
            arguments: Any = item.get("arguments")
            if isinstance(arguments, str):
                try:
                    arguments = json.loads(arguments)
                except json.JSONDecodeError:
                    pass
            return [ToolStarted(item["call_id"], str(item.get("name") or "tool"), arguments)]
        if item_type == "function_call_output" and isinstance(item.get("call_id"), str):
            key = f"{item.get('response_id') or self.current_response_id}:{item['call_id']}"
            if key in self.seen_outputs:
                return []
            self.seen_outputs.add(key)
            self.open_calls.discard(item["call_id"])
            output = item.get("output")
            text = output if isinstance(output, str) else json.dumps(output)
            return [ToolFinished(item["call_id"], text)]
        LOG.debug("unhandled item type %s", item_type)
        return []

    def _usage(self, usage: Any) -> list[TurnOutput]:
        if not isinstance(usage, dict):
            return []
        used = usage.get("context_tokens")
        size = usage.get("context_window")
        if isinstance(used, int) and isinstance(size, int) and size > 0:
            return [Usage(used, size)]
        return []
