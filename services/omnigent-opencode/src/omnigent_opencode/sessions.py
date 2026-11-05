"""One OpenCode session over one Omnigent session: the execution state machine.

T3 Code's view of a session is a series of executions, strictly one at a time,
each `session.execution.started` ... exactly one end. While a T3 Code turn is
active every event of the session belongs to it, and the first end finishes
it, so this module is the only place that starts or ends one:

- A prompt on an idle session posts the message and starts an execution that
  ends when Omnigent confirms the turn is over (`turns.TurnTracker`).
- A prompt on a busy session is a steer. Omnigent delivers it to the running
  harness at once (codex-native `turn/steer`, claude-native's own input queue);
  `session.inbox.delivered` follows when the harness records it. A steer still
  undelivered when the execution ends keeps T3 Code's turn open, so its
  delivery starts a new execution, and one never delivered is cancelled.
- An Omnigent turn this server did not start (a watcher wakeup, a wake notice,
  input from another client) starts an execution with no T3 Code turn, which
  T3 Code shows in the thread as a turn the agent started.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import logging
import mimetypes
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Final, Literal

import httpx

from . import approvals, history, subagents
from .approvals import Ask
from .catalog import DEFAULT_MODEL, AgentTarget
from .omnigent import STREAM_CONNECT_S, STREAM_OPENED, OmnigentClient, OmnigentError
from .store import Outcome, Store
from .turns import (
    Elicitation,
    Quiescent,
    ReplyCommitted,
    TextDelta,
    ThoughtDelta,
    ToolFinished,
    ToolStarted,
    TurnEnded,
    TurnOutput,
    TurnTracker,
    Usage,
    _message_text,
    elicitation_from,
)
from .wire import VERSION, Json, JsonObject, event, form_id, location, now_ms, permission_id, zero_tokens
from .wire import message_id as new_message_id

LOG = logging.getLogger(__name__)

SESSION_PREFIX: Final = "ses_"
SESSION_LABEL: Final = "t3.opencode_session_id"
PROVIDER_LABEL: Final = "t3.opencode_provider"
CANCEL_GRACE_S: Final = 8.0
QUIESCENT_END_S: Final = 10.0
# How long after its execution ends an undelivered steer waits for the harness
# to take it up as a turn of its own before it is cancelled.
STEER_DELIVERY_S: Final = 60.0
# Omnigent caps a request body at 10 MiB; an image is sent inline as a data URI.
IMAGE_LIMIT: Final = 7 * 1024 * 1024
COMPACTION_TIMEOUT_S: Final = 600.0
RECONNECT_MIN_S: Final = 0.5
RECONNECT_MAX_S: Final = 15.0
RULE_DENIED: Final = "This thread's permission rules in T3 Code do not allow it."
PLAN_EXIT_DENIED: Final = (
    "The user is in T3 Code's plan mode, which has no ExitPlanMode step. Do not call ExitPlanMode again: "
    "reply with the complete plan as your final message, and the user will switch to build mode to carry it out."
)
# How long a subagent call's result waits for its child session to be named before it is reported unlinked.
CHILD_LINK_WAIT_S: Final = 3.0
FORM_DISMISSED: Final = "The user dismissed the question without answering."

Publish = Callable[[JsonObject], None]
Adopt = Callable[[str, "Session"], "Session"]
BlockKind = Literal["text", "reasoning"]


def session_id_for(conversation_id: str) -> str:
    return f"{SESSION_PREFIX}{conversation_id}"


def conversation_named_by(session_id: str) -> str | None:
    return session_id[len(SESSION_PREFIX) :] if session_id.startswith(SESSION_PREFIX) else None


def native_id(prefix: str, conversation_id: str, native: str) -> str:
    """A stable id unique across sessions: T3 Code keys messages and tool items by id alone, and forks copy items."""
    digest = hashlib.sha256(f"{conversation_id}\0{native}".encode()).hexdigest()[:24]
    return f"{prefix}{digest}"


def tool_name(name: str) -> str:
    """The tool name as OpenCode's would read: T3 Code picks a tool's item kind by name (toolItems.ts)."""
    lowered = name.lower()
    if lowered in ("bash", "shell", "exec_command", "local_shell"):
        return "bash"
    if lowered in ("multiedit", "apply_patch"):
        return "edit" if lowered == "multiedit" else "patch"
    return lowered


@dataclass
class Block:
    kind: BlockKind
    ordinal: int
    text: str = ""


@dataclass
class Execution:
    kind: Literal["prompt", "steer", "wake", "compact"]
    tracker: TurnTracker
    assistant_id: str
    message_id: str
    stepped: bool = False
    block: Block | None = None
    ordinals: dict[BlockKind, int] = field(default_factory=lambda: {"text": 0, "reasoning": 0})
    tools: dict[str, str] = field(default_factory=dict)
    tool_messages: dict[str, str] = field(default_factory=dict)
    """The assistant message each open tool call belongs to; a steer starts a new one mid-execution."""
    usage: Usage | None = None
    interrupted: bool = False
    compacting: bool = False
    ended: bool = False
    quiet: asyncio.TimerHandle | None = None
    grace: asyncio.TimerHandle | None = None


@dataclass
class Steer:
    inbox_id: str
    pending_id: str | None
    timer: asyncio.TimerHandle | None = None


@dataclass
class SubagentCall:
    """A harness's spawning tool call, shown as OpenCode's subagent tool."""

    call_id: str
    tool_id: str
    assistant_id: str
    prompt: str
    child: Session | None = None
    output: str | None = None
    timer: asyncio.TimerHandle | None = None


@dataclass
class Pending:
    """An Omnigent elicitation T3 Code shows as a permission request or a form."""

    ask: Ask
    shown: JsonObject


class Session:
    def __init__(
        self,
        *,
        session_id: str,
        conversation_id: str,
        target: AgentTarget,
        directory: str,
        model: JsonObject,
        client: OmnigentClient,
        store: Store,
        publish: Publish,
        title: str | None = None,
        created: int | None = None,
        parent: Session | None = None,
        adopt: Adopt | None = None,
    ) -> None:
        self.id = session_id
        self.conversation_id = conversation_id
        self.target = target
        self.directory = directory
        self.model = model
        self.agent = "build"
        self.title = title
        self.permissions: list[Json] = []
        self.instructions: Json = None
        self.created = created if created is not None else now_ms()
        self.updated = self.created
        self.outcome: Outcome | None = None
        self.staged_revert: str | None = None
        self._client = client
        self._store = store
        self._publish = publish
        self.execution: Execution | None = None
        self.steers: dict[str, Steer] = {}
        self.approvals: dict[str, Pending] = {}
        self.forms: dict[str, Pending] = {}
        self._settled: set[str] = set()
        self._decline_note: str | None = None
        self._policy: tuple[str, bool] | None = None
        """This server's session policy in Omnigent: its id and whether it is enabled."""
        self._posted: set[str] = set()
        self._posting: str | None = None
        self._draining = False
        self._connected = asyncio.Event()
        self._stream: asyncio.Task[None] | None = None
        self._background: set[asyncio.Task[Any]] = set()
        self.gone: str | None = None
        self.parent = parent
        self._adopt = adopt
        self._late_reply: str | None = None
        self.calls: dict[str, SubagentCall] = {}
        self.children: dict[str, Session] = {}

    # -- OpenCode shapes ------------------------------------------------

    def info(self) -> JsonObject:
        info: JsonObject = {
            "id": self.id,
            "projectID": "global",
            "agent": self.agent,
            "model": self.model,
            "cost": 0,
            "tokens": zero_tokens(),
            "time": {"created": self.created, "updated": self.updated},
            "location": location(self.directory),
            "permissions": self.permissions,
        }
        if self.title:
            info["title"] = self.title
        if self.parent is not None:
            info["parentID"] = self.parent.id
        if self.outcome is not None:
            info["outcome"] = self.outcome
        return info

    @property
    def running(self) -> bool:
        return self.execution is not None

    def _emit(self, event_type: str, data: JsonObject) -> None:
        self._publish(event(event_type, {"sessionID": self.id, **data}))

    # -- Omnigent stream ------------------------------------------------

    async def connect(self) -> None:
        if self._stream is None or self._stream.done():
            self.gone = None
            self._stream = asyncio.create_task(self._pump())
        connected = asyncio.ensure_future(self._connected.wait())
        try:
            await asyncio.wait((connected, self._stream), timeout=STREAM_CONNECT_S, return_when=asyncio.FIRST_COMPLETED)
        finally:
            connected.cancel()
        if self._connected.is_set():
            return
        raise OmnigentError(self.gone or f"Omnigent stream for {self.conversation_id} did not connect in {STREAM_CONNECT_S:.0f}s")

    async def close(self) -> None:
        for task in [self._stream, *self._background]:
            if task is not None:
                task.cancel()

    async def _pump(self) -> None:
        backoff = RECONNECT_MIN_S
        while True:
            try:
                async for event_type, payload in self._client.stream(self.conversation_id, self._connected):
                    if event_type == STREAM_OPENED:
                        backoff = RECONNECT_MIN_S
                        await self._resync()
                        continue
                    self.on_event(event_type, payload)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - reconnect on any transport failure
                if isinstance(exc, OmnigentError) and exc.gone:
                    self.gone = f"Omnigent session {self.conversation_id} no longer exists (HTTP {exc.status})"
                    LOG.warning("%s; not reconnecting", self.gone)
                    if self.execution is not None:
                        self._end(self.execution, TurnEnded("failed", self.gone))
                    return
                LOG.warning("stream %s dropped: %s", self.conversation_id, exc)
            self._connected.clear()
            if self.execution is not None:
                self.execution.tracker.on_reconnect()
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, RECONNECT_MAX_S)

    async def _resync(self) -> None:
        execution = self.execution
        try:
            snapshot = await self._client.session_snapshot(self.conversation_id, items=execution is not None)
        except (OmnigentError, httpx.HTTPError) as exc:
            LOG.warning("snapshot of %s failed: %s", self.conversation_id, exc)
            return
        if execution is not None and self.execution is execution:
            self._apply(execution, execution.tracker.on_snapshot(snapshot))
        for pending in snapshot.get("pending_elicitations") or []:
            if isinstance(pending, dict):
                if self.execution is None:
                    self._start("wake", self.user_id(f"approval:{pending.get('elicitation_id')}"), native_consumed=True)
                self._ask(elicitation_from(pending).ask)

    def _in_background(self, work: Any) -> None:
        task = asyncio.ensure_future(work)
        self._background.add(task)

        def settled(done: asyncio.Task[Any]) -> None:
            self._background.discard(done)
            if not done.cancelled() and done.exception() is not None:
                LOG.warning("background work on %s failed: %s", self.id, done.exception())

        task.add_done_callback(settled)

    def on_event(self, event_type: str, payload: dict[str, Any]) -> None:
        if event_type == "session.input.consumed":
            if self._consumed(payload):
                return
        if event_type == "session.status" and payload.get("status") == "idle":
            self._draining = False
        if event_type == "session.created" and payload.get("child_session_id"):
            self._in_background(self._adopt_child(str(payload["child_session_id"])))
            return
        if self.parent is not None and event_type == "session.status":
            self._child_status(payload)
        if event_type == "response.elicitation_resolved":
            self._resolved_elsewhere(payload)
            return
        if event_type == "response.elicitation_request" and self.execution is None:
            # An approval blocks a turn T3 Code did not start; T3 Code shows approvals only inside an execution.
            self._start("wake", self.user_id(f"approval:{payload.get('elicitation_id')}"), native_consumed=True)
        execution = self.execution
        if event_type.startswith("response.compaction."):
            self._compaction(execution, event_type, payload)
            return
        if execution is not None and execution.kind != "compact":
            self._apply(execution, execution.tracker.feed(event_type, payload))

    def _compaction(self, execution: Execution | None, event_type: str, payload: dict[str, Any]) -> None:
        """A compaction, asked for by `/compact` or done by the harness on its own (`reason: auto`)."""
        if execution is None:
            return
        reason = "manual" if execution.kind == "compact" else "auto"
        status = event_type.removeprefix("response.compaction.")
        if status == "in_progress":
            if not execution.compacting:
                execution.compacting = True
                self._emit("session.compaction.started", {"reason": reason, "recent": ""})
            return
        if not execution.compacting:
            self._emit("session.compaction.started", {"reason": reason, "recent": ""})
        execution.compacting = False
        if status == "completed":
            summary = payload.get("summary")
            text = summary if isinstance(summary, str) and summary else "Conversation compacted."
            self._emit("session.compaction.ended", {"reason": reason, "text": text, "recent": ""})
            if execution.kind == "compact":
                self._end(execution, TurnEnded("completed"))
        elif status == "failed":
            self._emit("session.compaction.failed", {"reason": reason, "error": _session_error("Compaction failed")})
            if execution.kind == "compact":
                self._end(execution, TurnEnded("failed", "Compaction failed"))

    async def compact(self, message_id: str) -> None:
        await self.connect()
        if self.execution is not None:
            raise ValueError("the session is busy; compact it once the turn ends")
        execution = self._start("compact", message_id)
        execution.compacting = True
        self._emit("session.compaction.started", {"reason": "manual", "recent": ""})

        def give_up() -> None:
            if self.execution is execution:
                self._emit("session.compaction.failed", {"reason": "manual", "error": _session_error("Compaction timed out")})
                self._end(execution, TurnEnded("failed", "Compaction timed out"))

        execution.grace = asyncio.get_running_loop().call_later(COMPACTION_TIMEOUT_S, give_up)
        try:
            await self._client.post_event(self.conversation_id, {"type": "compact", "data": {}})
        except (OmnigentError, httpx.HTTPError) as exc:
            self._emit("session.compaction.failed", {"reason": "manual", "error": _session_error(str(exc))})
            self._end(execution, TurnEnded("failed", f"Omnigent did not compact: {exc}"))

    async def rebind(self, conversation_id: str) -> None:
        """Point this session at another Omnigent session (a rollback's fork); nothing may be running."""
        await self.close()
        self._stream = None
        self._connected.clear()
        self._posted.clear()
        self.conversation_id = conversation_id
        self._store.set_conversation(self.id, conversation_id)

    def _consumed(self, payload: dict[str, Any]) -> bool:
        """Settle a steer or start a turn T3 Code did not; whether the input is handled."""
        consumed = payload.get("data") or {}
        pending_id = consumed.get("cleared_pending_id")
        steer = next((s for s in self.steers.values() if pending_id is not None and s.pending_id == pending_id), None)
        if steer is not None:
            if consumed.get("item_id"):
                self._store.bind_consumed(self.id, str(pending_id), str(consumed["item_id"]))
            self._deliver(steer)
            return False
        if pending_id in self._posted:
            if consumed.get("item_id"):
                self._store.bind_consumed(self.id, str(pending_id), str(consumed["item_id"]))
            return False
        if consumed.get("type") != "message" or not consumed.get("item_id"):
            return False
        text = _message_text(consumed.get("data") or {})
        if self._posting is not None and text == self._posting:
            return False
        report = subagents.notification(text)
        if report is not None:
            self._report(report)
        if self.execution is None and not self._draining and self.target.native:
            LOG.info("turn started outside T3 Code on %s", self.conversation_id)
            self._start("wake", self.user_id(str(consumed["item_id"])), native_consumed=True)
            return True
        return False

    # -- executions -----------------------------------------------------

    def assistant_id(self, user_message_id: str) -> str:
        """The reply to a user message, keyed as `history.build` keys it."""
        return native_id("msg_", self.conversation_id, f"assistant:{user_message_id}")

    def user_id(self, item_id: str) -> str:
        """A user message T3 Code did not send: one from outside T3 Code."""
        return native_id("msg_", self.conversation_id, f"user:{item_id}")

    def _start(
        self, kind: Literal["prompt", "steer", "wake", "compact"], message_id: str, *, native_consumed: bool = False
    ) -> Execution:
        assert self.execution is None, "executions are serial"
        tracker = TurnTracker(native=self.target.native, native_consumed=native_consumed and self.target.native)
        tracker.late_reply, self._late_reply = self._late_reply, None
        execution = Execution(kind=kind, tracker=tracker, assistant_id=self.assistant_id(message_id), message_id=message_id)
        self.execution = execution
        self._emit("session.execution.started", {})
        return execution

    def _step(self, execution: Execution) -> None:
        if execution.stepped:
            return
        execution.stepped = True
        self._emit(
            "session.step.started",
            {"assistantMessageID": execution.assistant_id, "agent": self.agent, "model": self.model, "started": now_ms()},
        )

    def _close_block(self, execution: Execution) -> None:
        block = execution.block
        if block is None:
            return
        execution.block = None
        self._emit(
            f"session.{block.kind}.ended",
            {"assistantMessageID": execution.assistant_id, "ordinal": block.ordinal, "text": block.text},
        )

    def _delta(self, execution: Execution, kind: BlockKind, text: str) -> None:
        if not text:
            return
        self._step(execution)
        if execution.block is not None and execution.block.kind != kind:
            self._close_block(execution)
        if execution.block is None:
            execution.block = Block(kind, execution.ordinals[kind])
            execution.ordinals[kind] += 1
            self._emit(
                f"session.{kind}.started", {"assistantMessageID": execution.assistant_id, "ordinal": execution.block.ordinal}
            )
        execution.block.text += text
        self._emit(
            f"session.{kind}.delta",
            {"assistantMessageID": execution.assistant_id, "ordinal": execution.block.ordinal, "delta": text},
        )

    def _apply(self, execution: Execution, outputs: list[TurnOutput]) -> None:
        for output in outputs:
            if execution.ended:
                return
            match output:
                case TextDelta(text):
                    self._delta(execution, "text", text)
                case ThoughtDelta(text):
                    self._delta(execution, "reasoning", text)
                case ToolStarted(call_id, name, arguments):
                    self._tool_started(execution, call_id, name, arguments)
                case ToolFinished(call_id, text):
                    self._tool_finished(execution, call_id, text)
                case Usage():
                    execution.usage = output
                case ReplyCommitted():
                    if execution.block is not None and execution.block.kind == "text":
                        self._close_block(execution)
                case Elicitation(ask):
                    self._ask(ask)
                case Quiescent(generation):
                    self._schedule_quiet_end(execution, generation)
                case TurnEnded():
                    self._end(execution, output)

    def _tool_started(self, execution: Execution, call_id: str, name: str, arguments: Any) -> None:
        self._step(execution)
        self._close_block(execution)
        tool_id = native_id("call_", self.conversation_id, call_id)
        execution.tools[call_id] = tool_id
        execution.tool_messages[call_id] = execution.assistant_id
        base: JsonObject = {"assistantMessageID": execution.assistant_id, "id": tool_id}
        if subagents.is_spawning(name):
            call_input = subagents.call_input(arguments)
            self.calls[call_id] = SubagentCall(call_id, tool_id, execution.assistant_id, str(call_input["prompt"]))
            self._emit("session.tool.input.started", {**base, "name": "subagent"})
            self._emit("session.tool.called", {**base, "input": call_input, "executed": True})
            return
        self._emit("session.tool.input.started", {**base, "name": tool_name(name)})
        tool_input: Json = arguments if isinstance(arguments, dict) else {"input": arguments}
        self._emit("session.tool.called", {**base, "input": tool_input, "executed": True})

    def _tool_finished(self, execution: Execution, call_id: str, text: str) -> None:
        call = self.calls.get(call_id)
        if call is not None and call_id in execution.tools:
            call.output = text
            if call.child is not None:
                self._finish_call(call)
            elif call.timer is None:
                call.timer = asyncio.get_running_loop().call_later(CHILD_LINK_WAIT_S, self._finish_call, call)
            return
        tool_id = execution.tools.pop(call_id, None)
        if tool_id is None:
            return
        self._emit(
            "session.tool.success",
            {
                "assistantMessageID": execution.tool_messages.pop(call_id, execution.assistant_id),
                "id": tool_id,
                "content": [{"type": "text", "text": text or "(no output)"}],
                "executed": True,
            },
        )

    def _schedule_quiet_end(self, execution: Execution, generation: int) -> None:
        if execution.quiet is not None:
            execution.quiet.cancel()

        def check() -> None:
            tracker = execution.tracker
            if self.execution is execution and tracker.quiescent and tracker.quiet_generation == generation:
                LOG.info("execution on %s ended by quiescence; no confirmed idle arrived", self.conversation_id)
                self._end(execution, TurnEnded("completed"))

        execution.quiet = asyncio.get_running_loop().call_later(QUIESCENT_END_S, check)

    def _end(self, execution: Execution, ended: TurnEnded) -> None:
        if execution.ended:
            return
        execution.ended = True
        execution.tracker.ended = True
        tracker = execution.tracker
        self._late_reply = tracker.emitted_text if tracker.message_streamed and tracker.emitted_text else None
        for timer in (execution.quiet, execution.grace):
            if timer is not None:
                timer.cancel()
        self._close_block(execution)
        for call_id, tool_id in list(execution.tools.items()):
            self._emit(
                "session.tool.failed",
                {
                    "assistantMessageID": execution.tool_messages.get(call_id, execution.assistant_id),
                    "id": tool_id,
                    "error": {"type": "aborted", "message": "The turn ended before the tool finished."},
                    "executed": True,
                },
            )
            execution.tools.pop(call_id)
        outcome: Outcome = "succeeded"
        if execution.interrupted or ended.outcome == "cancelled":
            outcome = "interrupted"
        elif ended.outcome == "failed":
            outcome = "failed"
        if execution.stepped:
            tokens = zero_tokens()
            if execution.usage is not None:
                tokens["input"] = execution.usage.used
            step: JsonObject = {"assistantMessageID": execution.assistant_id}
            if outcome == "failed":
                self._emit("session.step.failed", {**step, "error": _session_error(ended.error), "tokens": tokens})
            else:
                self._emit("session.step.ended", {**step, "finish": "stop", "cost": 0, "tokens": tokens})
        self.execution = None
        self.approvals.clear()
        self.forms.clear()
        self.outcome = outcome
        self.updated = now_ms()
        self._store.add_execution(self.id, self.updated, outcome, execution.message_id)
        if outcome == "succeeded":
            self._emit("session.execution.succeeded", {})
        elif outcome == "interrupted":
            self._emit("session.execution.interrupted", {"reason": "user"})
        else:
            self._emit("session.execution.failed", {"error": _session_error(ended.error)})
        for steer in self.steers.values():
            if steer.timer is None:
                self._await_delivery(steer)

    # -- subagents --------------------------------------------------------

    def _finish_call(self, call: SubagentCall) -> None:
        """Report a spawning call's result: a background launch keeps the call running until its report."""
        if call.timer is not None:
            call.timer.cancel()
            call.timer = None
        execution = self.execution
        if call.output is None or execution is None or execution.tools.pop(call.call_id, None) is None:
            return
        metadata: JsonObject = {} if call.child is None else {"sessionID": call.child.id}
        text = subagents.output_text(call.output)
        if subagents.is_background_launch(call.output):
            metadata["status"] = "running"
        self._emit(
            "session.tool.success",
            {
                "assistantMessageID": call.assistant_id,
                "id": call.tool_id,
                "content": [{"type": "text", "text": text or "(no output)"}],
                "metadata": metadata,
                "executed": True,
            },
        )

    async def _adopt_child(self, conversation_id: str) -> None:
        """A subagent's Omnigent session: announce it as a child session and link it to its spawning call."""
        if conversation_id in self.children or self._adopt is None:
            return
        try:
            snapshot = await self._client.session_snapshot(conversation_id, items=False)
        except (OmnigentError, httpx.HTTPError) as exc:
            LOG.warning("could not read subagent session %s: %s", conversation_id, exc)
            snapshot = {}
        labels = snapshot.get("labels") or {}
        call_id = subagents.spawning_call_id(labels)
        call = self.calls.get(call_id or "") or next((c for c in self.calls.values() if c.child is None), None)
        if call is not None and call.child is not None:
            call = None
        child = self._adopt(conversation_id, self)
        self.children[conversation_id] = child
        child.title = subagents.description_of(labels) or snapshot.get("title") or None
        announced: JsonObject = {
            "sessionID": child.id,
            "projectID": "global",
            "location": location(child.directory),
            "parentID": self.id,
            "slug": child.id,
            "version": VERSION,
            "agent": child.agent,
            "model": child.model,
            "permissions": list(self.permissions),
        }
        if child.title:
            announced["title"] = child.title
        self._publish(event("session.created", announced))
        if call is not None:
            call.child = child
            self._emit(
                "session.tool.progress",
                {"assistantMessageID": call.assistant_id, "id": call.tool_id, "metadata": {"sessionID": child.id}},
            )
            if call.output is not None:
                self._finish_call(call)
        child.permissions = list(self.permissions)
        child.begin(call.prompt if call is not None else None)
        try:
            await child.connect()
        except OmnigentError as exc:
            LOG.warning("could not follow subagent session %s: %s", conversation_id, exc)

    def begin(self, prompt: str | None) -> None:
        """A subagent session's first run, which its parent started."""
        if self.execution is not None:
            return
        if prompt:
            self._emit(
                "session.inbox.enqueued",
                {"inboxID": new_message_id(), "item": {"type": "user", "payload": {"text": prompt}, "delivery": "queue"}},
            )
        self._start("wake", self.user_id("subagent"), native_consumed=True)

    def _child_status(self, payload: dict[str, Any]) -> None:
        """A subagent session's status. Its idle is a transcript lull, not a confirmed turn end, but nothing better comes."""
        status = payload.get("status")
        execution = self.execution
        if status == "running" and execution is None:
            self._start("wake", self.user_id(f"resumed:{now_ms()}"), native_consumed=True)
        elif status == "idle" and execution is not None and execution.kind != "compact":
            self._end(execution, TurnEnded("completed"))
        elif status == "failed" and execution is not None:
            self._end(execution, TurnEnded("failed", _error_message(payload.get("error"))))

    def _report(self, report: subagents.Notification) -> None:
        """A background subagent finished: settle its call in T3 Code before the turn its report starts."""
        call = self.calls.get(report.call_id or "")
        child = call.child if call is not None else None
        if child is None:
            return
        if child.execution is not None:
            outcome = TurnEnded("completed") if report.state == "completed" else TurnEnded("failed", report.summary)
            child._end(child.execution, outcome)
        metadata: JsonObject = {"source": "subagent", "childID": child.id, "state": report.state, "description": report.summary}
        self._emit(
            "session.inbox.enqueued",
            {
                "inboxID": new_message_id(),
                "item": {
                    "type": "synthetic",
                    "payload": {"text": report.result, "description": report.summary, "metadata": metadata},
                    "delivery": "queue",
                },
            },
        )

    # -- requests from T3 Code ------------------------------------------

    async def prompt(
        self, message_id: str, text: str, delivery: Literal["queue", "steer"], files: list[str] | None = None
    ) -> None:
        """Post a prompt; a prompt on a busy session is a steer."""
        await self.connect()
        self._store.add_prompt(self.id, message_id, text, now_ms())
        content = [*_attachments(files or []), {"type": "input_text", "text": text}]
        if self.execution is not None:
            await self._steer(message_id, text, content)
            return
        await self.sync_policy()
        execution = self._start("prompt", message_id)
        try:
            posted = await self._post(text, content)
        except (OmnigentError, httpx.HTTPError) as exc:
            self._end(execution, TurnEnded("failed", f"Omnigent did not take the message: {exc}"))
            raise
        if posted.get("denied"):
            reason = str(posted.get("reason") or "Denied by policy")
            self._delta(execution, "text", reason)
            self._end(execution, TurnEnded("failed", reason))
            return
        pending_id = posted.get("pending_id")
        self._store.bind_prompt(self.id, message_id, pending_id, posted.get("item_id"))
        execution.tracker.expect_input(pending_id, posted.get("item_id"))
        if execution.interrupted:
            await self._interrupt(execution)

    async def _post(self, text: str, content: list[dict[str, Any]]) -> dict[str, Any]:
        self._posting = text
        try:
            posted = await self._client.post_event(
                self.conversation_id, {"type": "message", "data": {"role": "user", "content": content}}
            )
        finally:
            self._posting = None
        if posted.get("pending_id"):
            self._posted.add(str(posted["pending_id"]))
        return posted

    async def _steer(self, inbox_id: str, text: str, content: list[dict[str, Any]]) -> None:
        if inbox_id in self.steers:
            return
        steer = Steer(inbox_id, None)
        self.steers[inbox_id] = steer
        self._emit(
            "session.inbox.enqueued",
            {"inboxID": inbox_id, "item": {"type": "user", "payload": {"text": text}, "delivery": "steer"}},
        )
        try:
            posted = await self._post(text, content)
        except (OmnigentError, httpx.HTTPError):
            self.steers.pop(inbox_id, None)
            self._emit("session.inbox.cancelled", {"inboxID": inbox_id})
            raise
        pending_id = posted.get("pending_id")
        self._store.bind_prompt(self.id, inbox_id, pending_id, posted.get("item_id"))
        if not pending_id:
            # Delivered with the post: SDK harnesses take input into the running turn directly.
            self._deliver(steer)
            return
        steer.pending_id = str(pending_id)

    def _deliver(self, steer: Steer) -> None:
        self.steers.pop(steer.inbox_id, None)
        if steer.timer is not None:
            steer.timer.cancel()
        if self.execution is None:
            # The harness took the steer up after its turn ended: that is a turn of its own.
            self._start("steer", steer.inbox_id, native_consumed=True)
        else:
            self._next_message(self.execution, steer.inbox_id)
        self._emit("session.inbox.delivered", {"inboxID": steer.inbox_id})

    def _next_message(self, execution: Execution, user_message_id: str) -> None:
        """What follows a steer is the reply to it, a new assistant message, as `history.build` has it."""
        self._close_block(execution)
        if execution.stepped:
            tokens = zero_tokens()
            self._emit(
                "session.step.ended",
                {"assistantMessageID": execution.assistant_id, "finish": "stop", "cost": 0, "tokens": tokens},
            )
        execution.stepped = False
        execution.assistant_id = self.assistant_id(user_message_id)
        execution.ordinals = {"text": 0, "reasoning": 0}

    def _await_delivery(self, steer: Steer) -> None:
        def expire() -> None:
            if self.steers.get(steer.inbox_id) is steer and self.execution is None:
                LOG.info("steer %s on %s was never delivered; cancelling it", steer.inbox_id, self.id)
                self.cancel_steer(steer.inbox_id)

        steer.timer = asyncio.get_running_loop().call_later(STEER_DELIVERY_S, expire)

    def cancel_steer(self, inbox_id: str) -> None:
        """Forget a steer. Omnigent cannot withdraw a posted message; if the harness takes it up later, that is a turn T3 Code did not start."""
        steer = self.steers.pop(inbox_id, None)
        if steer is None:
            return
        if steer.timer is not None:
            steer.timer.cancel()
        if steer.pending_id is not None:
            self._posted.discard(steer.pending_id)
        self._emit("session.inbox.cancelled", {"inboxID": inbox_id})

    async def interrupt(self) -> bool:
        execution = self.execution
        if execution is None:
            return False
        execution.interrupted = True
        if execution.tracker.expected_pending_id is not None or execution.kind != "prompt" or execution.message_id:
            await self._interrupt(execution)
        return True

    async def _interrupt(self, execution: Execution) -> None:
        execution.tracker.request_cancel()

        def give_up() -> None:
            if self.execution is execution:
                LOG.warning("Omnigent did not confirm the stop on %s; ending the execution", self.conversation_id)
                self._draining = True
                self._end(execution, TurnEnded("cancelled"))

        execution.grace = asyncio.get_running_loop().call_later(CANCEL_GRACE_S, give_up)
        try:
            await self._client.post_event(self.conversation_id, {"type": "interrupt", "data": {}})
        except (OmnigentError, httpx.HTTPError) as exc:
            LOG.warning("interrupt on %s failed: %s", self.conversation_id, exc)

    async def messages(self) -> list[JsonObject]:
        return await self.messages_of([item async for item in self._client.session_items(self.conversation_id)])

    async def messages_of(self, items: list[dict[str, Any]]) -> list[JsonObject]:
        present = {str(item.get("id")) for item in items}
        prompts = self._store.prompts(self.id)
        user_ids = {item_id: message_id for message_id, _, _, item_id in prompts if item_id}
        # A prompt whose item this server never saw (consumed while it was down, or copied by a fork
        # under a new id) is matched by text, in order.
        unmatched = [(message_id, text) for message_id, text, _, item_id in prompts if not item_id or item_id not in present]

        def user_id_for(item: dict[str, Any]) -> str:
            text = history._text(item)
            for n, (message_id, prompt_text) in enumerate(unmatched):
                if prompt_text == text:
                    del unmatched[n]
                    return message_id
            return self.user_id(str(item.get("id")))

        executions = self._store.executions(self.id)
        outcomes = {message_id: outcome for _, outcome, message_id in executions}
        running = self.execution.message_id if self.execution is not None else None
        # Steers delivered into a running execution: their reply continues it, with no idle marker before them.
        continuing = {message_id for message_id, _, _, _ in prompts if message_id not in outcomes and message_id != running}
        return history.build(
            items=items,
            user_ids=user_ids,
            user_id_for=user_id_for,
            assistant_id_for=self.assistant_id,
            tool_id_for=lambda call_id: native_id("call_", self.conversation_id, call_id),
            tool_name_for=tool_name,
            outcomes=outcomes,
            continuing=continuing,
            agent=self.agent,
            model=self.model,
            running=self.running,
        )

    async def fork_point(self, before: str | None) -> str | None:
        """The `up_to_response_id` that keeps this session's history strictly before user message `before`.

        `None` keeps all of it; an empty string means nothing comes before it.
        """
        if before is None:
            return None
        items = [item async for item in self._client.session_items(self.conversation_id)]
        messages = await self.messages_of(items)
        users = [m for m in messages if m.get("type") == "user"]
        index = next((n for n, m in enumerate(users) if m.get("id") == before), None)
        if index is None:
            raise LookupError(before)
        user_items = [i for i in items if i.get("type") == "message" and i.get("role") == "user"]
        boundary = user_items[index]
        earlier = items[: items.index(boundary)]
        response = next(
            (i.get("response_id") for i in reversed(earlier) if i.get("response_id") and i.get("type") != "resource_event"), None
        )
        return str(response) if response else ""

    # -- approvals -------------------------------------------------------

    def _ask(self, ask: Ask) -> None:
        child = self.children.get(ask.target_session_id or "")
        if child is not None:
            # A child's request, mirrored to its parent: T3 Code shows it in the child's thread.
            child._ask(ask)
            return
        shown = [p.ask.elicitation_id for p in (*self.approvals.values(), *self.forms.values())]
        if ask.elicitation_id in shown or ask.elicitation_id in self._settled:
            return
        if ask.questions:
            form = approvals.form(ask, form_id(), self.id)
            self.forms[str(form["id"])] = Pending(ask, form)
            self._publish(event("form.created", {"form": form}))
            return
        if ask.plan_exit and self.agent == "plan":
            self._settle(ask, approvals.resolution(ask, "deny", PLAN_EXIT_DENIED), "plan mode has no ExitPlanMode")
            return
        verdict = self._verdict(ask) if ask.governed else "ask"
        if verdict != "ask":
            self._settle(ask, approvals.resolution(ask, verdict, RULE_DENIED), f"the thread's rules say {verdict}")
            return
        request: JsonObject = {
            "id": permission_id(),
            "sessionID": self.id,
            "action": ask.action,
            "resources": list(ask.resources),
            "message": ask.message,
        }
        if ask.save:
            request["save"] = list(ask.save)
        execution = self.execution
        if ask.call_id and execution is not None and ask.call_id in execution.tools:
            request["source"] = {"type": "tool", "messageID": execution.assistant_id, "id": execution.tools[ask.call_id]}
        self.approvals[str(request["id"])] = Pending(ask, request)
        self._publish(event("permission.asked", request))

    def _settle(self, ask: Ask, resolution: approvals.Resolution, why: str) -> None:
        """Answer an elicitation without asking the user."""
        LOG.info("answering %s %s on %s with %s: %s", ask.action, ask.resources, self.id, resolution.body["action"], why)
        self._settled.add(ask.elicitation_id)
        self._in_background(self._resolve(ask, resolution))

    async def _resolve(self, ask: Ask, resolution: approvals.Resolution) -> None:
        self._settled.add(ask.elicitation_id)
        try:
            await self._client.resolve_elicitation(
                ask.target_session_id or self.conversation_id, ask.elicitation_id, resolution.body
            )
        except (OmnigentError, httpx.HTTPError) as exc:
            # Resolved elsewhere or timed out; the agent has moved on either way.
            LOG.warning("resolving approval %s on %s failed: %s", ask.elicitation_id, self.id, exc)
        if resolution.ends_turn and self.execution is not None:
            # Omnigent stops the turn itself; interrupting too gives the execution its usual end.
            await self.interrupt()

    def _resolved_elsewhere(self, payload: dict[str, Any]) -> None:
        elicitation_id = payload.get("elicitation_id") or (payload.get("data") or {}).get("elicitation_id")
        accepted = payload.get("action") == "accept"
        for request_id, pending in list(self.approvals.items()):
            if pending.ask.elicitation_id == elicitation_id:
                del self.approvals[request_id]
                reply = "once" if accepted else "reject"
                self._publish(event("permission.replied", {"sessionID": self.id, "requestID": request_id, "reply": reply}))
        for form_key, pending in list(self.forms.items()):
            if pending.ask.elicitation_id == elicitation_id:
                del self.forms[form_key]
                self._publish(event("form.cancelled", {"id": form_key, "sessionID": self.id}))

    def note_decline(self, text: str) -> None:
        """T3 Code's explanation of the decline that follows, which it sends as a synthetic message."""
        self._decline_note = text

    async def reply(self, request_id: str, decision: str, message: str | None) -> bool:
        """Answer an approval. A reject without a message is T3 Code's Cancel, which stops the execution."""
        pending = self.approvals.pop(request_id, None)
        if pending is None:
            return False
        note, self._decline_note = self._decline_note, None
        if decision == "always":
            grants: list[Json] = [{"action": pending.ask.action, "resource": p, "effect": "allow"} for p in pending.ask.save]
            self.permissions = [*self.permissions, *grants]
        answer: approvals.Answer = "allow" if decision in ("once", "always") else "stop" if message is None else "deny"
        self._publish(event("permission.replied", {"sessionID": self.id, "requestID": request_id, "reply": decision}))
        await self._resolve(pending.ask, approvals.resolution(pending.ask, answer, note or message))
        return True

    async def answer_form(self, form_key: str, answer: dict[str, Any]) -> bool:
        pending = self.forms.pop(form_key, None)
        if pending is None:
            return False
        resolution = approvals.Resolution({"action": "accept", "content": approvals.form_answer(pending.ask, answer)}, False)
        await self._resolve(pending.ask, resolution)
        self._publish(event("form.replied", {"id": form_key, "sessionID": self.id, "answer": answer}))
        return True

    async def cancel_form(self, form_key: str) -> bool:
        pending = self.forms.pop(form_key, None)
        if pending is None:
            return False
        # An unanswered question: Claude is told so and goes on; Codex gets no answers (a decline would interrupt it).
        body: JsonObject = (
            {"action": "cancel"}
            if pending.ask.source == "codex"
            else {"action": "decline", "content": {"feedback": FORM_DISMISSED}}
        )
        await self._resolve(pending.ask, approvals.Resolution(body, False))
        self._publish(event("form.cancelled", {"id": form_key, "sessionID": self.id}))
        return True

    def _verdict(self, ask: Ask) -> approvals.Effect:
        verdicts = [approvals.evaluate(self.permissions, ask.action, ask.resources)]
        outside = [p for p in ask.paths if os.path.isabs(p) and not _within(p, self.directory)]
        if outside:
            verdicts.append(approvals.evaluate(self.permissions, "external_directory", outside))
        return "deny" if "deny" in verdicts else "ask" if "ask" in verdicts else "allow"

    # -- the session policy ---------------------------------------------------

    async def sync_policy(self) -> None:
        """Enable this server's session policy while the thread is not in full access or is planning; disable it otherwise.

        Best effort: without it the harness asks only what its own mode asks.
        """
        wanted = approvals.wants_policy(self.permissions, self.agent == "plan")
        try:
            if self._policy is None:
                existing = next(
                    (
                        p
                        for p in await self._client.session_policies(self.conversation_id)
                        if p.get("name") == approvals.T3_POLICY
                    ),
                    None,
                )
                if existing is not None and existing.get("id"):
                    self._policy = (str(existing["id"]), existing.get("enabled") is not False)
            if self._policy is None:
                if not wanted:
                    return
                created = await self._client.add_session_policy(
                    self.conversation_id, {"name": approvals.T3_POLICY, "type": "python", "handler": approvals.T3_POLICY_HANDLER}
                )
                self._policy = (str(created["id"]), True)
                LOG.info("added the T3 Code approval policy to %s", self.conversation_id)
                return
            policy_id, enabled = self._policy
            if enabled != wanted:
                await self._client.update_session_policy(self.conversation_id, policy_id, {"enabled": wanted})
                self._policy = (policy_id, wanted)
                LOG.info("%s the T3 Code approval policy on %s", "enabled" if wanted else "disabled", self.conversation_id)
        except (OmnigentError, httpx.HTTPError, KeyError) as exc:
            LOG.warning("could not update the T3 Code approval policy on %s: %s", self.conversation_id, exc)

    def rules_changed(self) -> None:
        """T3 Code sent new rules (a mode switch or a session grant); a switch mid-turn applies at once."""
        self._in_background(self.sync_policy())

    async def set_model(self, model: JsonObject) -> None:
        if model.get("providerID") != self.target.name:
            raise ValueError(
                f"this thread runs on Omnigent agent {self.target.name}; start a new thread for {model.get('providerID')}"
            )
        update: dict[str, Any] = {}
        if model.get("id") != self.model.get("id"):
            update["model_override"] = None if model.get("id") == DEFAULT_MODEL else model.get("id")
        if model.get("variant") != self.model.get("variant"):
            update["reasoning_effort"] = model.get("variant") or None
        if update:
            await self._client.update_session(self.conversation_id, update)
        previous = self.model
        self.model = {k: v for k, v in model.items() if v is not None}
        self._emit("session.model.selected", {"model": self.model, "previous": previous})

    async def set_agent(self, agent: str) -> None:
        if agent == self.agent:
            return
        if self.target.native_plan:
            await self._client.update_session(
                self.conversation_id, {"collaboration_mode": "plan" if agent == "plan" else "default"}
            )
        self.agent = agent


def _within(path: str, directory: str) -> bool:
    return os.path.commonpath([os.path.realpath(path), os.path.realpath(directory)]) == os.path.realpath(directory)


def _error_message(error: Any) -> str | None:
    if isinstance(error, dict):
        return str(error.get("message") or error.get("code") or "") or None
    return str(error) if error else None


def _session_error(message: str | None) -> JsonObject:
    return {"type": "unknown", "message": message or "The Omnigent turn failed"}


def _attachments(paths: list[str]) -> list[dict[str, Any]]:
    """T3 Code's image attachments, inline: they are files on this machine (T3 Code also names them in the text)."""
    blocks: list[dict[str, Any]] = []
    for path in paths:
        mime = mimetypes.guess_type(path)[0] or "application/octet-stream"
        try:
            if os.path.getsize(path) > IMAGE_LIMIT:
                LOG.warning("not attaching %s: larger than %d bytes", path, IMAGE_LIMIT)
                continue
            with open(path, "rb") as handle:
                data = base64.b64encode(handle.read()).decode()
        except OSError as exc:
            LOG.warning("not attaching %s: %s", path, exc)
            continue
        kind = "input_image" if mime.startswith("image/") else "input_file"
        url_key = "image_url" if kind == "input_image" else "file_data"
        blocks.append({"type": kind, url_key: f"data:{mime};base64,{data}", "filename": os.path.basename(path)})
    return blocks
