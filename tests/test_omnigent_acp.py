"""Checks for bin/omnigent-acp's turn tracking and prompt rewriting. No network.

Run with: uv run --no-project --with agent-client-protocol==0.12.1 --with httpx \
    python -m unittest discover -s tests -p test_omnigent_acp.py
"""

import asyncio
import json
from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader
from pathlib import Path
import sys
import unittest

import acp
from acp.schema import AllowedOutcome, EnvVariable, McpServerStdio, RequestPermissionResponse
import httpx

ROOT = Path(__file__).resolve().parents[1]
_loader = SourceFileLoader("omnigent_acp", str(ROOT / "bin/omnigent-acp"))
_spec = spec_from_loader("omnigent_acp", _loader)
shim = module_from_spec(_spec)
sys.modules["omnigent_acp"] = shim
_loader.exec_module(shim)

NATIVE_ACK = ("response.completed", {"response": {"id": "resp_q", "model": "claude-native-ui"}})
CONSUMED = ("session.input.consumed", {"data": {"cleared_pending_id": "pending_1"}})
BARE_IDLE = ("session.status", {"status": "idle", "response_id": None})
CONFIRMED_IDLE = ("session.status", {"status": "idle", "response_id": "resp_claude_1"})


def assistant_item(text):
    return (
        "response.output_item.done",
        {"item": {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": text}]}},
    )


def feed(tracker, events, keep_quiescent=False):
    out = []
    for event_type, data in events:
        out.extend(tracker.feed(event_type, data))
    return out if keep_quiescent else [o for o in out if not isinstance(o, shim.Quiescent)]


class NativeTurnTest(unittest.TestCase):
    def native_tracker(self):
        tracker = shim.TurnTracker(native=True)
        tracker.expect_input("pending_1")
        return tracker

    def test_bare_idle_mid_turn_does_not_end_turn(self):
        tracker = self.native_tracker()
        out = feed(
            tracker,
            [
                ("response.in_progress", {"response": {"id": "resp_q", "model": "claude-native-ui"}}),
                NATIVE_ACK,
                CONSUMED,
                ("response.output_text.delta", {"delta": "pong"}),
                BARE_IDLE,
                assistant_item("pong"),
                CONFIRMED_IDLE,
            ],
        )
        self.assertEqual(out, [shim.TextDelta("pong"), shim.TurnEnded("completed")])

    def test_earlier_turn_items_before_consumption_are_dropped(self):
        tracker = self.native_tracker()
        out = feed(tracker, [assistant_item("stale"), CONFIRMED_IDLE, NATIVE_ACK, CONSUMED, assistant_item("fresh")])
        self.assertEqual(out, [shim.TextDelta("fresh")])

    def test_earlier_turn_output_before_post_returns_is_dropped(self):
        tracker = shim.TurnTracker(native=True)
        out = feed(
            tracker,
            [("response.output_text.delta", {"delta": "essay"}), CONFIRMED_IDLE, NATIVE_ACK, CONSUMED, assistant_item("alive")],
        )
        self.assertEqual(out, [shim.TextDelta("alive")])

    def test_earlier_turn_interrupt_does_not_end_the_next_turn(self):
        tracker = self.native_tracker()
        stale = ("session.interrupted", {"data": {"response_id": "codex_previous"}})
        out = feed(tracker, [stale, NATIVE_ACK, CONSUMED, assistant_item("alive"), CONFIRMED_IDLE])
        self.assertEqual(out, [shim.TextDelta("alive"), shim.TurnEnded("completed")])

    def test_committed_message_without_deltas_is_forwarded(self):
        tracker = self.native_tracker()
        out = feed(tracker, [NATIVE_ACK, CONSUMED, assistant_item("only committed"), CONFIRMED_IDLE])
        self.assertEqual(out, [shim.TextDelta("only committed"), shim.TurnEnded("completed")])

    def test_idle_after_cancel_ends_as_cancelled(self):
        tracker = self.native_tracker()
        feed(tracker, [NATIVE_ACK, CONSUMED, ("response.output_text.delta", {"delta": "1\n2\n"})])
        tracker.request_cancel()
        self.assertEqual(feed(tracker, [BARE_IDLE]), [shim.TurnEnded("cancelled")])

    def test_unconfirmed_finished_turn_becomes_quiescent(self):
        tracker = self.native_tracker()
        out = feed(
            tracker,
            [NATIVE_ACK, CONSUMED, BARE_IDLE, ("response.output_text.delta", {"delta": "ready"}), assistant_item("ready")],
            keep_quiescent=True,
        )
        self.assertEqual(out, [shim.TextDelta("ready"), shim.Quiescent(tracker.quiet_generation)])

    def test_open_tool_call_or_running_pane_is_not_quiescent(self):
        tracker = self.native_tracker()
        call = {"type": "function_call", "call_id": "toolu_1", "name": "Bash", "arguments": "{}"}
        feed(tracker, [NATIVE_ACK, CONSUMED, assistant_item("checking"), ("response.output_item.done", {"item": call})])
        self.assertFalse(feed(tracker, [BARE_IDLE], keep_quiescent=True))
        feed(tracker, [("response.output_item.done", {"item": {"type": "function_call_output", "call_id": "toolu_1", "output": "ok"}})])
        feed(tracker, [assistant_item("done")])
        self.assertTrue(tracker.quiescent)
        generation = tracker.quiet_generation
        feed(tracker, [("session.status", {"status": "running"})])
        self.assertFalse(tracker.quiescent)
        self.assertNotEqual(tracker.quiet_generation, generation)

    def test_reconnect_replay_emits_only_new_suffix(self):
        tracker = self.native_tracker()
        feed(tracker, [NATIVE_ACK, CONSUMED, ("response.output_text.delta", {"delta": "abc"})])
        tracker.on_reconnect()
        out = feed(
            tracker,
            [("response.output_text.delta", {"delta": "ab"}), ("response.output_text.delta", {"delta": "cde"})],
        )
        self.assertEqual(out, [shim.TextDelta("de")])


class SdkTurnTest(unittest.TestCase):
    def test_tools_dedupe_and_turn_ends_at_completed(self):
        call = {"type": "function_call", "call_id": "toolu_1", "name": "Bash", "arguments": '{"command": "ls"}'}
        result = {"type": "function_call_output", "call_id": "toolu_1", "output": "a\nb"}
        out = feed(
            shim.TurnTracker(),
            [
                ("response.created", {"response": {"id": "resp_1", "model": "claude-haiku-5-5"}}),
                ("response.output_text.delta", {"delta": "Listing"}),
                ("response.output_item.done", {"item": call}),
                ("response.output_item.done", {"item": {**call, "status": "completed"}}),
                ("response.output_item.done", {"item": result}),
                ("response.output_item.done", {"item": result}),
                (
                    "response.completed",
                    {"response": {"id": "resp_1", "usage": {"context_tokens": 10, "context_window": 100}}},
                ),
            ],
        )
        self.assertEqual(
            out,
            [
                shim.TextDelta("Listing"),
                shim.ToolStarted("toolu_1", "Bash", {"command": "ls"}),
                shim.ToolFinished("toolu_1", "a\nb"),
                shim.Usage(10, 100),
                shim.TurnEnded("completed"),
            ],
        )

    def test_elicitation_shows_the_command_of_the_serialized_call(self):
        preview = '{"name": "Bash", "arguments": {"command": "echo hi", "description": "Echo"}}'
        out = feed(
            shim.TurnTracker(),
            [("response.elicitation_request", {"elicitation_id": "e1", "params": {"message": "Run?", "content_preview": preview}})],
        )
        self.assertEqual(out, [shim.Elicitation("e1", "Run?", None, "echo hi")])

    def test_failed_response_reports_error(self):
        out = feed(shim.TurnTracker(), [("response.failed", {"response": {"error": {"message": "boom"}}})])
        self.assertEqual(out, [shim.TurnEnded("failed", "boom")])


DEFAULT_SECTION = "## T3 Code interaction mode: Default\n\nPrefer assumptions."
PLAN_SECTION = "## T3 Code interaction mode: Plan\n\nDo not edit files."
TOOLS_SECTION = "## T3 Code browser\n\nTools."


class StripT3InstructionsTest(unittest.TestCase):
    def wrapped(self, mode_section, request):
        return (
            f"<t3_code_instructions>\n{mode_section}\n\n{TOOLS_SECTION}\n</t3_code_instructions>\n\n"
            f"<user_request>\n{request}\n</user_request>"
        )

    def test_tool_sections_and_runtime_info_are_dropped(self):
        blocks = [self.wrapped(DEFAULT_SECTION, "fix the bug"), "<runtime_info>In case you're asked</runtime_info>"]
        self.assertEqual(
            shim.strip_t3_instructions(blocks, agent_mode="default"),
            shim.RewrittenPrompt("fix the bug", "default"),
        )

    def test_mode_section_is_forwarded_only_when_the_mode_changes(self):
        entering = shim.strip_t3_instructions([self.wrapped(PLAN_SECTION, "design it")], agent_mode="default")
        self.assertEqual(entering, shim.RewrittenPrompt(f"{PLAN_SECTION}\n\ndesign it", "plan"))
        leaving = shim.strip_t3_instructions([self.wrapped(DEFAULT_SECTION, "build it")], agent_mode="plan")
        self.assertEqual(leaving.text, f"{DEFAULT_SECTION}\n\nbuild it")

    def test_natively_applied_mode_sections_are_dropped(self):
        rewritten = shim.strip_t3_instructions([self.wrapped(PLAN_SECTION, "design it")], agent_mode=None)
        self.assertEqual(rewritten, shim.RewrittenPrompt("design it", "plan"))

    def test_unwrapped_prompts_pass_through(self):
        self.assertEqual(shim.strip_t3_instructions(["/compact"], agent_mode="default").text, "/compact")


class TargetAndToolsTest(unittest.TestCase):
    def test_effort_ladder_is_the_ordered_union_of_model_levels(self):
        models = [
            {"id": "a", "supportedReasoningEfforts": [{"reasoningEffort": "high"}, {"reasoningEffort": "low"}]},
            {"id": "b", "supportedReasoningEfforts": [{"reasoningEffort": "xhigh"}]},
        ]
        self.assertEqual(shim.Target("ag", "h", "codex-native", models).efforts, ("low", "high", "xhigh"))
        self.assertEqual(
            shim.Target("ag", "h", "claude-native", [{"id": "opus"}]).efforts, ("low", "medium", "high", "xhigh", "max")
        )

    def test_mcp_launch_args_carry_stdio_servers_with_env(self):
        server = McpServerStdio(
            name="t3-code",
            command="/opt/t3",
            args=["acp-mcp-bridge"],
            env=[EnvVariable(name="T3_ACP_MCP_ENDPOINT", value="http://127.0.0.1:1/mcp")],
        )
        flag, config = shim.mcp_launch_args([server])
        self.assertEqual(flag, "--mcp-config")
        self.assertEqual(
            json.loads(config),
            {
                "mcpServers": {
                    "t3-code": {
                        "type": "stdio",
                        "command": "/opt/t3",
                        "args": ["acp-mcp-bridge"],
                        "env": {"T3_ACP_MCP_ENDPOINT": "http://127.0.0.1:1/mcp"},
                    }
                }
            },
        )
        self.assertEqual(shim.mcp_launch_args([]), [])



class EditDiffsTest(unittest.TestCase):
    def diffs(self, name, arguments):
        return [(d.path, d.old_text, d.new_text) for d in shim.edit_diffs(name, arguments, "/repo")]

    def test_claude_edits_carry_snippets_at_absolute_paths(self):
        self.assertEqual(
            self.diffs("Edit", {"file_path": "src/a.py", "old_string": "x = 1", "new_string": "x = 2"}),
            [("/repo/src/a.py", "x = 1", "x = 2")],
        )
        multi = {"file_path": "/abs/b.py", "edits": [{"old_string": "a", "new_string": "b"}, {"old_string": "c", "new_string": "d"}]}
        self.assertEqual(self.diffs("MultiEdit", multi), [("/abs/b.py", "a", "b"), ("/abs/b.py", "c", "d")])
        self.assertEqual(self.diffs("Write", {"file_path": "./new.txt", "content": "hi\n"}), [("/repo/new.txt", None, "hi\n")])

    def test_codex_file_changes_become_one_diff_per_hunk(self):
        changes = [
            {"path": "/repo/new.py", "kind": {"type": "add"}, "diff": "print(1)\n"},
            {"path": "/repo/old.py", "kind": {"type": "delete"}, "diff": "gone\n"},
            {
                "path": "/repo/mod.py",
                "kind": {"type": "update", "move_path": None},
                "diff": "--- a/mod.py\n+++ b/mod.py\n@@ -1,3 +1,3 @@\n a\n-b\n+B\n c\n@@ -9,2 +9,3 @@\n y\n+z\n w\n",
            },
        ]
        self.assertEqual(
            self.diffs("apply_patch", {"changes": changes}),
            [
                ("/repo/new.py", None, "print(1)\n"),
                ("/repo/old.py", "gone\n", ""),
                ("/repo/mod.py", "a\nb\nc\n", "a\nB\nc\n"),
                ("/repo/mod.py", "y\nw\n", "y\nz\nw\n"),
            ],
        )

    def test_other_tools_have_no_diff(self):
        self.assertEqual(self.diffs("Bash", {"command": "ls", "file_path": "x"}), [])
        self.assertEqual(self.diffs("Edit", "not json"), [])


DROP = object()


class FakeOmnigent:
    """An Omnigent server whose stream the test drives event by event."""

    def __init__(self, harness):
        self.harness = harness
        self.agents_gate = asyncio.Event()
        self.agents_gate.set()
        self.connect_gate = asyncio.Event()
        self.connect_gate.set()
        self.agents_called = asyncio.Event()
        self.connect_attempts = 0
        self.connections = []
        self.posted = []
        self.created = []
        self.resolved = []
        self.post_response = {"queued": True, "item_id": "item_user"}
        self.snapshot = {"status": "idle", "items": [], "pending_inputs": []}
        self.resolve_error = None

    async def agents(self):
        self.agents_called.set()
        await self.agents_gate.wait()
        return [{"id": "ag_1", "name": "agent", "harness": self.harness}]

    async def hosts(self):
        return [{"host_id": "host_1", "name": "box", "status": "online"}]

    async def model_options(self, host_id, harness):
        return []

    async def create_session(self, body):
        self.created.append(body)
        return {"id": "conv_1"}

    async def update_session(self, session_id, body):
        pass

    async def post_event(self, session_id, event):
        self.posted.append(event)
        return self.post_response if event["type"] == "message" else {"queued": True}

    async def session_snapshot(self, session_id, *, items):
        return self.snapshot

    async def resolve_elicitation(self, session_id, elicitation_id, action):
        if self.resolve_error is not None:
            raise self.resolve_error
        self.resolved.append((session_id, elicitation_id, action))

    async def stream(self, session_id, connected):
        self.connect_attempts += 1
        await self.connect_gate.wait()
        events = asyncio.Queue()
        self.connections.append(events)
        connected.set()
        yield shim.STREAM_OPENED, {}
        while True:
            event = await events.get()
            if event is DROP:
                raise httpx.ReadError("dropped")
            yield event

    def emit(self, *events):
        for event in events:
            self.connections[-1].put_nowait(event)

    def messages(self):
        return [e for e in self.posted if e["type"] == "message"]

    def interrupts(self):
        return [e for e in self.posted if e["type"] == "interrupt"]


class FakeClientConnection:
    def __init__(self):
        self.updates = []
        self.permission_requests = []

    async def session_update(self, session_id, update):
        self.updates.append(update)

    async def request_permission(self, session_id, tool_call, options):
        self.permission_requests.append(tool_call)
        return RequestPermissionResponse(outcome=AllowedOutcome(outcome="selected", option_id="accept"))

    def agent_text(self):
        return "".join(u.content.text for u in self.updates if getattr(u, "session_update", None) == "agent_message_chunk")


async def until(predicate, timeout=5.0):
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.005)


def text_prompt(text):
    return [acp.text_block(text)]


SDK_STARTED = ("response.created", {"response": {"id": "resp_1", "model": "claude-haiku"}})
SDK_COMPLETED = ("response.completed", {"response": {"id": "resp_1"}})


class AgentTest(unittest.IsolatedAsyncioTestCase):
    def make_agent(self, harness="claude-sdk"):
        self.omnigent = FakeOmnigent(harness)
        self.client = FakeClientConnection()
        agent = shim.OmnigentAgent(self.omnigent, "agent", "host_1", strip=False, t3_tools=False, trace=None)
        agent.on_connect(self.client)
        return agent

    async def open_session(self, agent):
        return (await agent.new_session(cwd="/repo")).session_id

    async def start_prompt(self, agent, sid, text="hi"):
        task = asyncio.create_task(agent.prompt(session_id=sid, prompt=text_prompt(text)))
        await until(lambda: self.omnigent.messages() or task.done())
        return task

    async def test_policy_denied_message_ends_the_turn_as_a_refusal_with_its_reason(self):
        agent = self.make_agent("claude-native")
        sid = await self.open_session(agent)
        self.omnigent.post_response = {"queued": False, "denied": True, "reason": "Blocked by the secrets policy"}
        response = await asyncio.wait_for(agent.prompt(session_id=sid, prompt=text_prompt("cat .env")), 5)
        self.assertEqual(response.stop_reason, "refusal")
        self.assertIn("Blocked by the secrets policy", self.client.agent_text())

    async def test_turn_that_finished_while_the_stream_was_down_ends_from_the_snapshot(self):
        agent = self.make_agent()
        sid = await self.open_session(agent)
        task = await self.start_prompt(agent, sid)
        self.omnigent.snapshot = {
            "status": "idle",
            "items": [
                {"id": "item_user", "type": "message", "role": "user"},
                {"id": "item_reply", "type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "done"}]},
            ],
        }
        self.omnigent.emit(DROP)
        response = await asyncio.wait_for(task, 5)
        self.assertEqual(response.stop_reason, "end_turn")

    async def test_snapshot_from_before_the_turn_started_does_not_end_it(self):
        agent = self.make_agent()
        sid = await self.open_session(agent)
        task = await self.start_prompt(agent, sid)
        self.omnigent.snapshot = {"status": "idle", "items": [{"id": "item_user", "type": "message", "role": "user"}]}
        self.omnigent.emit(DROP)
        await until(lambda: len(self.omnigent.connections) == 2)
        await asyncio.sleep(0.05)
        self.assertFalse(task.done())
        self.omnigent.emit(SDK_STARTED, ("response.output_text.delta", {"delta": "late"}), SDK_COMPLETED)
        response = await asyncio.wait_for(task, 5)
        self.assertEqual((response.stop_reason, self.client.agent_text()), ("end_turn", "late"))

    async def test_native_turn_that_finished_while_the_stream_was_down_ends_after_a_quiet_period(self):
        agent = self.make_agent("claude-native")
        sid = await self.open_session(agent)
        self.omnigent.post_response = {"queued": True, "pending_id": "pending_1"}
        quiet = shim.QUIESCENT_END_S
        shim.QUIESCENT_END_S = 0.05
        self.addCleanup(setattr, shim, "QUIESCENT_END_S", quiet)
        task = await self.start_prompt(agent, sid)
        self.omnigent.snapshot = {
            "status": "idle",
            "pending_inputs": [],
            "items": [{"id": "i2", "type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "ok"}]}],
        }
        self.omnigent.emit(DROP)
        response = await asyncio.wait_for(task, 5)
        self.assertEqual(response.stop_reason, "end_turn")

    async def test_cancel_while_the_stream_connects_does_not_send_the_message(self):
        agent = self.make_agent()
        sid = await self.open_session(agent)
        self.omnigent.connect_gate.clear()
        task = asyncio.create_task(agent.prompt(session_id=sid, prompt=text_prompt("hi")))
        await until(lambda: self.omnigent.connect_attempts > 0)
        await agent.cancel(session_id=sid)
        response = await asyncio.wait_for(task, 5)
        self.assertEqual(response.stop_reason, "cancelled")
        self.omnigent.connect_gate.set()
        await asyncio.sleep(0.05)
        self.assertEqual(self.omnigent.posted, [])

    async def test_cancel_before_the_turn_is_set_up_cancels_the_prompt(self):
        agent = self.make_agent()
        sid = await self.open_session(agent)
        agent._target = None
        self.omnigent.agents_called.clear()
        self.omnigent.agents_gate.clear()
        task = asyncio.create_task(agent.prompt(session_id=sid, prompt=text_prompt("hi")))
        await asyncio.wait_for(self.omnigent.agents_called.wait(), 5)
        await agent.cancel(session_id=sid)
        self.omnigent.agents_gate.set()
        response = await asyncio.wait_for(task, 5)
        self.assertEqual(response.stop_reason, "cancelled")
        self.assertEqual((self.omnigent.created, self.omnigent.posted), ([], []))

    async def test_cancel_after_posting_interrupts_and_ends_cancelled(self):
        agent = self.make_agent()
        sid = await self.open_session(agent)
        task = await self.start_prompt(agent, sid)
        await agent.cancel(session_id=sid)
        self.omnigent.emit(("response.cancelled", {}))
        response = await asyncio.wait_for(task, 5)
        self.assertEqual((response.stop_reason, len(self.omnigent.interrupts())), ("cancelled", 1))

    async def test_closing_the_session_ends_its_running_prompt_and_interrupts_omnigent(self):
        agent = self.make_agent()
        sid = await self.open_session(agent)
        task = await self.start_prompt(agent, sid)
        await agent.close_session(session_id=sid)
        response = await asyncio.wait_for(task, 5)
        self.assertEqual((response.stop_reason, len(self.omnigent.interrupts())), ("cancelled", 1))

    async def test_approval_requested_between_prompts_is_asked_and_resolved(self):
        agent = self.make_agent()
        sid = await self.open_session(agent)
        task = await self.start_prompt(agent, sid)
        self.omnigent.emit(SDK_STARTED, SDK_COMPLETED)
        await asyncio.wait_for(task, 5)
        self.omnigent.emit(("response.elicitation_request", {"elicitation_id": "el_1", "params": {"message": "Run rm?"}}))
        await until(lambda: self.omnigent.resolved)
        self.assertEqual([r.title for r in self.client.permission_requests], ["Run rm?"])
        self.assertEqual(self.omnigent.resolved, [("conv_1", "el_1", "accept")])

    async def test_approval_pending_in_the_snapshot_is_asked_once(self):
        agent = self.make_agent()
        sid = await self.open_session(agent)
        request = {"elicitation_id": "el_2", "params": {"message": "Write file?"}}
        self.omnigent.snapshot = {"status": "running", "pending_elicitations": [request]}
        task = await self.start_prompt(agent, sid)
        self.omnigent.emit(SDK_STARTED, ("response.elicitation_request", request), SDK_COMPLETED)
        await asyncio.wait_for(task, 5)
        await until(lambda: self.omnigent.resolved)
        self.assertEqual(len(self.client.permission_requests), 1)

    async def test_approval_that_cannot_be_resolved_does_not_fail_the_prompt(self):
        agent = self.make_agent()
        sid = await self.open_session(agent)
        self.omnigent.resolve_error = shim.OmnigentError("409: already resolved")
        task = await self.start_prompt(agent, sid)
        self.omnigent.emit(
            SDK_STARTED,
            ("response.elicitation_request", {"elicitation_id": "el_3", "params": {"message": "Run?"}}),
            SDK_COMPLETED,
        )
        response = await asyncio.wait_for(task, 5)
        self.assertEqual((response.stop_reason, len(self.client.permission_requests)), ("end_turn", 1))

    async def test_edit_tool_call_reports_its_diff_through_completion(self):
        agent = self.make_agent()
        sid = await self.open_session(agent)
        task = await self.start_prompt(agent, sid)
        call = {
            "type": "function_call",
            "call_id": "toolu_1",
            "name": "Edit",
            "arguments": json.dumps({"file_path": "a.py", "old_string": "x", "new_string": "y"}),
        }
        self.omnigent.emit(
            SDK_STARTED,
            ("response.output_item.done", {"item": call}),
            ("response.output_item.done", {"item": {"type": "function_call_output", "call_id": "toolu_1", "output": "ok"}}),
            SDK_COMPLETED,
        )
        await asyncio.wait_for(task, 5)
        started, finished = [u for u in self.client.updates if getattr(u, "tool_call_id", None) == "toolu_1"]
        diff = ("/repo/a.py", "x", "y")
        self.assertEqual(started.kind, "edit")
        self.assertEqual([loc.path for loc in started.locations], ["/repo/a.py"])
        self.assertEqual([(c.path, c.old_text, c.new_text) for c in started.content], [diff])
        self.assertEqual(
            [(c.path, c.old_text, c.new_text) if c.type == "diff" else c.content.text for c in finished.content],
            [diff, "ok"],
        )


if __name__ == "__main__":
    unittest.main()
