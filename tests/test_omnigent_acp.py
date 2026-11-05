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
        self.posted_to = []
        self.created = []
        self.resolved = []
        self.post_response = {"queued": True, "item_id": "item_user"}
        self.snapshot = {"status": "idle", "items": [], "pending_inputs": []}
        self.resolve_error = None
        self.post_error = None
        self.stream_error = None
        self.host_list = [{"host_id": "host_1", "name": "box", "status": "online"}]
        self.home = "/home/far"
        self.updates = []
        self.forks = []
        self.runners = []
        self.runner_error = None
        self.sessions = []
        self.items = []
        self.pages = []
        self.page_requests = []

    async def agents(self):
        self.agents_called.set()
        await self.agents_gate.wait()
        return [{"id": "ag_1", "name": "agent", "harness": self.harness}]

    async def hosts(self):
        return self.host_list

    async def host_home(self, host_id):
        return self.home

    async def model_options(self, host_id, harness):
        return []

    async def create_session(self, body):
        self.created.append(body)
        return {"id": "conv_1"}

    async def update_session(self, session_id, body):
        self.updates.append((session_id, body))

    async def fork_session(self, session_id, body):
        self.forks.append((session_id, body))
        return {"id": "conv_fork"}

    async def launch_runner(self, host_id, session_id, workspace):
        if self.runner_error is not None:
            raise self.runner_error
        self.runners.append((host_id, session_id, workspace))
        return {"status": "launching"}

    async def find_session_by_label(self, agent_id, key, value):
        return next((s for s in self.sessions if (s.get("labels") or {}).get(key) == value), None)

    async def session_items(self, session_id):
        for item in self.items:
            yield item

    async def session_page(self, params):
        self.page_requests.append(params)
        await asyncio.sleep(0)
        return self.pages[min(len(self.page_requests), len(self.pages)) - 1]

    async def post_event(self, session_id, event):
        if self.post_error is not None and event["type"] == "message":
            raise self.post_error
        self.posted.append(event)
        self.posted_to.append(session_id)
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
        if self.stream_error is not None:
            raise self.stream_error
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
    def make_agent(self, harness="claude-sdk", strip=False):
        self.omnigent = FakeOmnigent(harness)
        self.client = FakeClientConnection()
        agent = shim.OmnigentAgent(self.omnigent, "agent", "host_1", strip=strip, t3_tools=False, trace=None)
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

    async def test_terminal_activity_keeps_a_quiet_looking_native_turn_open(self):
        agent = self.make_agent("claude-native")
        sid = await self.open_session(agent)
        self.omnigent.post_response = {"queued": True, "pending_id": "pending_1"}
        quiet = shim.QUIESCENT_END_S
        shim.QUIESCENT_END_S = 0.1
        self.addCleanup(setattr, shim, "QUIESCENT_END_S", quiet)
        self.omnigent.snapshot = {"status": "running", "items": [], "pending_inputs": [{"pending_id": "pending_1"}]}
        task = await self.start_prompt(agent, sid)
        self.omnigent.emit(NATIVE_ACK, CONSUMED, assistant_item("Let me check."), BARE_IDLE)
        for _ in range(10):
            self.omnigent.emit(TERMINAL_ACTIVITY)
            await asyncio.sleep(0.05)
        self.assertFalse(task.done())
        response = await asyncio.wait_for(task, 5)
        self.assertEqual((response.stop_reason, self.client.agent_text()), ("end_turn", "Let me check."))

    async def test_deleted_session_fails_the_running_prompt_and_stops_reconnecting(self):
        agent = self.make_agent()
        sid = await self.open_session(agent)
        task = await self.start_prompt(agent, sid)
        self.omnigent.stream_error = shim.OmnigentError("stream conv_1 -> 404: gone", 404)
        self.omnigent.emit(DROP)
        with self.assertRaises(acp.RequestError) as raised:
            await asyncio.wait_for(task, 5)
        self.assertIn("no longer exists", str(raised.exception.data))
        attempts = self.omnigent.connect_attempts
        await asyncio.sleep(1.5)
        self.assertEqual(self.omnigent.connect_attempts, attempts)

    async def test_prompting_a_deleted_session_fails_without_waiting_for_the_connect_timeout(self):
        agent = self.make_agent()
        sid = await self.open_session(agent)
        self.omnigent.stream_error = shim.OmnigentError("stream conv_1 -> 410: gone", 410)
        with self.assertRaises(shim.OmnigentError) as raised:
            await asyncio.wait_for(agent.prompt(session_id=sid, prompt=text_prompt("hi")), 5)
        self.assertIn("no longer exists", str(raised.exception))
        self.assertEqual(self.omnigent.posted, [])

    async def test_mode_change_is_announced_again_when_its_message_failed_to_post(self):
        agent = self.make_agent(strip=True)
        sid = await self.open_session(agent)
        plan = (
            f"<t3_code_instructions>\n{PLAN_SECTION}\n</t3_code_instructions>\n\n<user_request>\ndesign it\n</user_request>"
        )
        self.omnigent.post_error = shim.OmnigentError("POST -> 503", 503)
        with self.assertRaises(shim.OmnigentError):
            await agent.prompt(session_id=sid, prompt=text_prompt(plan))
        self.omnigent.post_error = None
        for sent in (1, 2):
            task = asyncio.create_task(agent.prompt(session_id=sid, prompt=text_prompt(plan)))
            await until(lambda: len(self.omnigent.messages()) == sent)
            self.omnigent.emit(SDK_STARTED, SDK_COMPLETED)
            await asyncio.wait_for(task, 5)
        self.assertEqual(
            [m["data"]["content"][0]["text"] for m in self.omnigent.messages()],
            [f"{PLAN_SECTION}\n\ndesign it", "design it"],
        )


class QuiescenceTest(unittest.TestCase):
    def test_stop_hook_idle_without_a_response_id_ends_the_turn(self):
        tracker = shim.TurnTracker(native=True)
        tracker.expect_input("pending_1")
        stop_hook = ("session.status", {"status": "idle", "background_task_count": 0})
        out = feed(tracker, [NATIVE_ACK, CONSUMED, assistant_item("done"), stop_hook])
        self.assertEqual(out, [shim.TextDelta("done"), shim.TurnEnded("completed")])


TERMINAL_ACTIVITY = ("session.terminal.activity", {"session_id": "conv_1", "terminal_id": "claude"})


class LoadSessionTest(unittest.IsolatedAsyncioTestCase):
    def make_agent(self):
        self.omnigent = FakeOmnigent("claude-native")
        self.client = FakeClientConnection()
        agent = shim.OmnigentAgent(self.omnigent, "agent", "host_1", strip=False, t3_tools=False, trace=None)
        agent.on_connect(self.client)
        return agent

    async def test_loading_a_thread_omnigent_does_not_have_fails_and_creates_nothing(self):
        agent = self.make_agent()
        with self.assertRaises(acp.RequestError) as raised:
            await agent.load_session(cwd="/repo", session_id="acp_unknown")
        self.assertEqual(raised.exception.code, -32002)
        with self.assertRaises(acp.RequestError):
            await agent.prompt(session_id="acp_unknown", prompt=text_prompt("hi"))
        self.assertEqual((self.omnigent.created, self.omnigent.posted), ([], []))

    async def test_loading_replays_messages_and_tool_calls_before_answering(self):
        agent = self.make_agent()
        self.omnigent.sessions = [{"id": "conv_9", "labels": {shim.SESSION_LABEL: "acp_1"}}]
        self.omnigent.items = [
            {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "list files"}]},
            {"type": "message", "role": "user", "is_meta": True, "content": [{"type": "input_text", "text": "wake"}]},
            {"type": "function_call", "call_id": "toolu_1", "name": "Bash", "arguments": '{"command": "ls"}'},
            {"type": "function_call_output", "call_id": "toolu_1", "output": "a.py"},
            {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "One file."}]},
        ]
        await agent.load_session(cwd="/repo", session_id="acp_1")
        replay = [
            (u.session_update, getattr(getattr(u, "content", None), "text", None) or getattr(u, "tool_call_id", None))
            for u in self.client.updates
            if u.session_update != "available_commands_update"
        ]
        self.assertEqual(
            replay,
            [("user_message_chunk", "list files"), ("tool_call", "toolu_1"), ("agent_message_chunk", "One file.")],
        )
        tool = next(u for u in self.client.updates if u.session_update == "tool_call")
        self.assertEqual((tool.status, tool.raw_input, tool.content[0].content.text), ("completed", {"command": "ls"}, "a.py"))

    async def test_a_loaded_thread_prompts_its_omnigent_session(self):
        agent = self.make_agent()
        self.omnigent.sessions = [{"id": "conv_9", "labels": {shim.SESSION_LABEL: "acp_1"}}]
        await agent.load_session(cwd="/repo", session_id="acp_1")
        task = asyncio.create_task(agent.prompt(session_id="acp_1", prompt=text_prompt("hi")))
        await until(lambda: self.omnigent.messages())
        task.cancel()
        self.assertEqual((self.omnigent.created, self.omnigent.posted_to), ([], ["conv_9"]))


class FakeT3:
    """T3 Code's orchestrator tools: the thread they name and the messages sent to it."""

    def __init__(self):
        self.sent = []

    async def call(self, tool, arguments):
        assert tool == "orchestrator_capabilities", tool
        return {"parentThreadId": "thread_7"}

    async def send(self, thread_id, message):
        self.sent.append({"threadId": thread_id, "message": message})

    async def aclose(self):
        pass


def outside_input(item_id, text, pending_id="pending_elsewhere"):
    return (
        "session.input.consumed",
        {
            "data": {
                "item_id": item_id,
                "type": "message",
                "data": {"role": "user", "content": [{"type": "input_text", "text": text}]},
                "cleared_pending_id": pending_id,
            }
        },
    )


class OutsideTurnTest(unittest.IsolatedAsyncioTestCase):
    """Turns started outside T3 Code reach the thread through an echo, and only once."""

    async def asyncSetUp(self):
        self.omnigent = FakeOmnigent("claude-native")
        self.client = FakeClientConnection()
        self.agent = shim.OmnigentAgent(self.omnigent, "agent", "host_1", strip=True, t3_tools=False, trace=None)
        self.agent.on_connect(self.client)
        self.t3 = FakeT3()
        self.omnigent.sessions = [{"id": "conv_9", "labels": {shim.SESSION_LABEL: "acp_1"}}]
        await self.agent.load_session(cwd="/repo", session_id="acp_1")
        self.agent._sessions["acp_1"].t3 = self.t3
        await until(lambda: self.omnigent.connections)

    def echoes(self):
        return self.t3.sent

    async def prompt(self, text):
        return await asyncio.wait_for(self.agent.prompt(session_id="acp_1", prompt=text_prompt(text)), 5)

    async def test_a_loaded_thread_echoes_a_turn_started_elsewhere_into_itself(self):
        self.omnigent.emit(outside_input("item_w", "[Watcher dwb_1] D1 has 1 CI failure. Fix it.\nMore detail"))
        await until(lambda: self.echoes())
        (echo,) = self.echoes()
        self.assertEqual(echo["threadId"], "thread_7")
        self.assertEqual(echo["message"], "\u21aa [Watcher dwb_1] D1 has 1 CI failure. Fix it.\n\n\u27e6omnigent-echo:item_w\u27e7")
        self.assertEqual(self.omnigent.messages(), [])

    async def test_the_echo_prompt_shows_the_outside_turn_and_never_reaches_the_agent(self):
        self.omnigent.emit(outside_input("item_w", "wake up"), ("response.output_text.delta", {"delta": "Look"}))
        await until(lambda: self.echoes())
        echo = self.echoes()[0]["message"]
        # T3 Code wraps the echo like any prompt it sends.
        task = asyncio.create_task(self.agent.prompt(session_id="acp_1", prompt=text_prompt(
            f"<t3_code_instructions>\ntools\n</t3_code_instructions>\n\n<user_request>\n{echo}\n</user_request>")))
        await asyncio.sleep(0.05)
        self.omnigent.emit(("response.output_text.delta", {"delta": "ing now."}), assistant_item("Looking now."), CONFIRMED_IDLE)
        response = await asyncio.wait_for(task, 5)
        self.assertEqual((response.stop_reason, self.client.agent_text()), ("end_turn", "Looking now."))
        self.assertEqual(self.omnigent.messages(), [])

    async def test_a_background_task_notification_is_echoed_by_its_summary(self):
        self.omnigent.emit(outside_input("item_n", "<task-notification>\n<task-id>b1</task-id>\n<summary>Build finished</summary>\n</task-notification>"))
        await until(lambda: self.echoes())
        self.assertTrue(self.echoes()[0]["message"].startswith("\u21aa Background task: Build finished\n"))

    async def test_the_threads_own_prompts_are_not_echoed(self):
        self.omnigent.post_response = {"queued": True, "pending_id": "pending_mine"}
        task = asyncio.create_task(self.agent.prompt(session_id="acp_1", prompt=text_prompt("hi")))
        await until(lambda: self.omnigent.messages())
        self.omnigent.emit(outside_input("item_mine", "hi", pending_id="pending_mine"), assistant_item("hello"), CONFIRMED_IDLE)
        await asyncio.wait_for(task, 5)
        await asyncio.sleep(0.05)
        self.assertEqual(self.echoes(), [])

    async def test_an_outside_input_ends_the_turn_before_it_without_lending_it_output(self):
        self.omnigent.post_response = {"queued": True, "pending_id": "pending_mine"}
        quiet = shim.QUIESCENT_END_S
        shim.QUIESCENT_END_S = 5
        self.addCleanup(setattr, shim, "QUIESCENT_END_S", quiet)
        task = asyncio.create_task(self.agent.prompt(session_id="acp_1", prompt=text_prompt("hi")))
        await until(lambda: self.omnigent.messages())
        # The thread's turn looks finished but its confirmed idle never comes; then a wakeup arrives.
        self.omnigent.emit(outside_input("item_mine", "hi", pending_id="pending_mine"), assistant_item("hello"), BARE_IDLE)
        self.omnigent.emit(outside_input("item_w", "wake"), assistant_item("woken"))
        response = await asyncio.wait_for(task, 2)
        self.assertEqual((response.stop_reason, self.client.agent_text()), ("end_turn", "hello"))

    async def test_an_echo_for_a_turn_this_process_did_not_follow_replays_it(self):
        self.omnigent.items = [
            {"id": "item_old", "type": "message", "role": "user", "content": [{"type": "input_text", "text": "earlier"}]},
            {"id": "item_w", "type": "message", "role": "user", "content": [{"type": "input_text", "text": "wake"}]},
            {"id": "c1", "type": "function_call", "call_id": "toolu_1", "name": "Bash", "arguments": '{"command": "ls"}'},
            {"id": "c2", "type": "function_call_output", "call_id": "toolu_1", "output": "a.py"},
            {"id": "r1", "type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "Done."}]},
            {"id": "item_next", "type": "message", "role": "user", "content": [{"type": "input_text", "text": "later"}]},
        ]
        self.client.updates.clear()
        response = await self.prompt("\u21aa wake\n\n\u27e6omnigent-echo:item_w\u27e7")
        self.assertEqual(response.stop_reason, "end_turn")
        self.assertEqual(
            [u.session_update for u in self.client.updates if u.session_update != "available_commands_update"],
            ["tool_call", "agent_message_chunk"],
        )
        self.assertEqual(self.client.agent_text(), "Done.")
        self.assertEqual(self.omnigent.messages(), [])

    async def echo_prompt(self, item_id):
        return asyncio.create_task(
            self.agent.prompt(session_id="acp_1", prompt=text_prompt(f"\u21aa x\n\n\u27e6omnigent-echo:{item_id}\u27e7"))
        )

    async def test_a_turn_of_the_thread_after_an_unconfirmed_outside_turn_is_not_shown_twice(self):
        # The wakeup's turn ends without a confirmed idle; the user prompts before its echo is answered.
        self.omnigent.emit(outside_input("item_w", "wake"), assistant_item("woken"), BARE_IDLE)
        await until(lambda: self.echoes())
        self.omnigent.post_response = {"queued": True, "pending_id": "pending_mine"}
        task = asyncio.create_task(self.agent.prompt(session_id="acp_1", prompt=text_prompt("hi")))
        await until(lambda: self.omnigent.messages())
        self.omnigent.emit(outside_input("item_mine", "hi", pending_id="pending_mine"), assistant_item("hello"), CONFIRMED_IDLE)
        await asyncio.wait_for(task, 5)
        self.client.updates.clear()
        response = await asyncio.wait_for(await self.echo_prompt("item_w"), 5)
        self.assertEqual((response.stop_reason, self.client.agent_text()), ("end_turn", "woken"))

    async def test_an_outside_input_while_the_threads_message_posts_is_not_taken_for_it(self):
        gate = asyncio.Event()
        post = self.omnigent.post_event

        async def slow_post(session_id, event):
            await gate.wait()
            return await post(session_id, event)

        self.omnigent.post_event = slow_post
        self.omnigent.post_response = {"queued": True, "pending_id": "pending_mine"}
        task = asyncio.create_task(self.agent.prompt(session_id="acp_1", prompt=text_prompt("hi")))
        await asyncio.sleep(0.05)
        self.omnigent.emit(outside_input("item_w", "wake"), assistant_item("woken"), CONFIRMED_IDLE)
        await until(lambda: self.echoes())
        gate.set()
        await until(lambda: self.omnigent.messages())
        self.omnigent.emit(outside_input("item_mine", "hi", pending_id="pending_mine"), assistant_item("hello"), CONFIRMED_IDLE)
        await asyncio.wait_for(task, 5)
        self.assertEqual(self.client.agent_text(), "hello")

    async def test_an_echo_answered_after_its_turn_ended_shows_all_of_it(self):
        self.omnigent.emit(
            outside_input("item_w", "wake"), ("response.output_text.delta", {"delta": "all "}),
            ("response.output_text.delta", {"delta": "done"}), assistant_item("all done"), CONFIRMED_IDLE,
        )
        await until(lambda: self.echoes())
        await asyncio.sleep(0.05)
        response = await asyncio.wait_for(await self.echo_prompt("item_w"), 5)
        self.assertEqual((response.stop_reason, self.client.agent_text()), ("end_turn", "all done"))

    async def test_stopping_the_echo_of_a_finished_turn_does_not_interrupt_the_agent(self):
        self.omnigent.emit(outside_input("item_w", "wake"), assistant_item("woken"), CONFIRMED_IDLE)
        await until(lambda: self.echoes())
        await asyncio.sleep(0.05)
        session = self.agent._sessions["acp_1"]
        turn = session.outside["item_w"]
        turn.outputs = asyncio.Queue()  # its output already shown; the row is still open
        task = await self.echo_prompt("item_w")
        await until(lambda: session.turn is turn)
        await self.agent.cancel(session_id="acp_1")
        response = await asyncio.wait_for(task, 5)
        self.assertEqual((response.stop_reason, self.omnigent.interrupts()), ("cancelled", []))

    async def test_an_idle_thread_sends_heartbeats(self):
        beat = shim.HEARTBEAT_S
        shim.HEARTBEAT_S = 0.05
        self.addCleanup(setattr, shim, "HEARTBEAT_S", beat)
        agent = shim.OmnigentAgent(self.omnigent, "agent", "host_1", strip=True, t3_tools=False, trace=None)
        client = FakeClientConnection()
        agent.on_connect(client)
        await agent.load_session(cwd="/repo", session_id="acp_1")
        await until(lambda: [u for u in client.updates if u.session_update == shim.HEARTBEAT_KIND])
        await agent.close_session(session_id="acp_1")


class SlashCommandTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.omnigent = FakeOmnigent("claude-native")
        self.omnigent.host_list = [
            {"host_id": "host_1", "name": "box", "status": "online"},
            {"host_id": "host_2", "name": "devvm7.example.com", "status": "online"},
            {"host_id": "host_3", "name": "devvm8.example.com", "status": "offline"},
        ]
        self.client = FakeClientConnection()
        self.agent = shim.OmnigentAgent(self.omnigent, "agent", "host_1", strip=True, t3_tools=False, trace=None)
        self.agent.on_connect(self.client)
        self.omnigent.sessions = [{"id": "conv_1", "labels": {shim.SESSION_LABEL: "acp_1"}}]
        self.sid = "acp_1"
        await self.agent.load_session(cwd="/home/me/checkout3", session_id=self.sid)

    async def fork(self, text):
        self.client.updates.clear()
        response = await asyncio.wait_for(self.agent.prompt(session_id=self.sid, prompt=text_prompt(text)), 5)
        self.assertEqual(response.stop_reason, "end_turn")
        return self.client.agent_text()

    async def test_sessions_advertise_fork_to(self):
        await until(lambda: any(u.session_update == "available_commands_update" for u in self.client.updates))
        update = next(u for u in self.client.updates if u.session_update == "available_commands_update")
        self.assertEqual(
            [(c.name, c.input.root.hint) for c in update.available_commands], [("fork-to", "HOST [CHECKOUT]")]
        )

    async def test_fork_to_starts_the_fork_on_the_host_in_the_same_checkout(self):
        reply = await self.fork("/fork-to devvm7")
        self.assertEqual(self.omnigent.forks, [("conv_1", {})])
        self.assertEqual(self.omnigent.runners, [("host_2", "conv_fork", "/home/far/checkout3")])
        self.assertIn(("conv_fork", {"labels": {shim.SESSION_LABEL: "omnigent:conv_fork"}}), self.omnigent.updates)
        for detail in ("conv_fork", "devvm7.example.com", "/home/far/checkout3", "omnigent:conv_fork"):
            self.assertIn(detail, reply)
        self.assertEqual(self.omnigent.messages(), [])

    async def test_fork_to_takes_a_checkout_and_falls_back_to_this_layout(self):
        self.omnigent.home = None
        await self.fork("/fork-to devvm7.example.com checkout9")
        self.assertEqual(self.omnigent.runners, [("host_2", "conv_fork", "/home/me/checkout9")])

    async def test_fork_to_an_unknown_or_offline_host_forks_nothing_and_names_the_hosts(self):
        reply = await self.fork("/fork-to devvm8")
        self.assertIn("devvm7.example.com", reply)
        self.assertEqual((self.omnigent.forks, self.omnigent.messages()), ([], []))

    async def test_fork_to_without_a_host_shows_usage(self):
        self.assertIn("Usage: /fork-to HOST [CHECKOUT]", await self.fork("/fork-to"))
        self.assertEqual(self.omnigent.forks, [])

    async def test_a_fork_that_cannot_start_names_it(self):
        self.omnigent.runner_error = shim.OmnigentError("400: workspace does not exist", 400)
        reply = await self.fork("/fork-to devvm7")
        self.assertIn("conv_fork", reply)
        self.assertIn("workspace does not exist", reply)

    async def test_other_slash_prompts_reach_the_agent(self):
        task = asyncio.create_task(self.agent.prompt(session_id=self.sid, prompt=text_prompt("/fork-tomorrow")))
        await until(lambda: self.omnigent.messages())
        task.cancel()
        self.assertEqual(self.omnigent.forks, [])

    async def test_a_thread_without_an_omnigent_session_has_nothing_to_fork(self):
        sid = (await self.agent.new_session(cwd="/home/me/checkout3")).session_id
        await asyncio.wait_for(self.agent.prompt(session_id=sid, prompt=text_prompt("/fork-to devvm7")), 5)
        self.assertEqual((self.omnigent.forks, self.omnigent.created), ([], []))


class ListSessionsTest(unittest.IsolatedAsyncioTestCase):
    def make_agent(self):
        self.omnigent = FakeOmnigent("claude-native")
        agent = shim.OmnigentAgent(self.omnigent, "agent", "host_1", strip=False, t3_tools=False, trace=None)
        agent.on_connect(FakeClientConnection())
        return agent

    async def test_listing_ends_when_the_server_gives_no_cursor_to_continue_from(self):
        agent = self.make_agent()
        elsewhere = {"id": "conv_x", "host_id": "host_other", "workspace": "/repo"}
        self.omnigent.pages = [{"data": [elsewhere], "has_more": True, "last_id": None}]
        listed = await asyncio.wait_for(agent.list_sessions(cwd="/repo"), 5)
        self.assertEqual((listed.sessions, listed.next_cursor), ([], None))
        self.omnigent.pages = [{"data": [elsewhere], "has_more": True, "last_id": "conv_x"}]
        listed = await asyncio.wait_for(agent.list_sessions(cwd="/repo", cursor="conv_x"), 5)
        self.assertEqual((listed.sessions, listed.next_cursor), ([], None))

    async def test_cwd_matches_regardless_of_trailing_slash(self):
        agent = self.make_agent()
        row = {"id": "conv_1", "host_id": "host_1", "workspace": "/repo/", "labels": {shim.SESSION_LABEL: "acp_1"}}
        self.omnigent.pages = [{"data": [row], "has_more": False}]
        listed = await agent.list_sessions(cwd="/repo")
        self.assertEqual([s.session_id for s in listed.sessions], ["acp_1"])


class OmnigentClientTest(unittest.IsolatedAsyncioTestCase):
    def client(self, handler):
        self.requests = []

        def record(request):
            self.requests.append(request)
            return handler(request)

        return shim.OmnigentClient("http://omnigent", transport=httpx.MockTransport(record))

    async def test_label_lookup_scans_a_bounded_number_of_sessions(self):
        def many(request):
            n = len(self.requests)
            rows = [{"id": f"conv_{n}_{i}", "labels": {}} for i in range(int(request.url.params["limit"]))]
            more = n * len(rows) < 10 * shim.LABEL_SCAN_LIMIT
            return httpx.Response(200, json={"data": rows, "has_more": more, "last_id": rows[-1]["id"]})

        client = self.client(many)
        self.assertIsNone(await client.find_session_by_label("ag_1", "k", "v"))
        self.assertLessEqual(sum(int(r.url.params["limit"]) for r in self.requests), shim.LABEL_SCAN_LIMIT)

    async def test_label_lookup_skips_forks_that_copied_the_label(self):
        rows = [
            {"id": "conv_fork", "labels": {"k": "v", shim.FORK_SOURCE_LABEL: "conv_src"}},
            {"id": "conv_src", "labels": {"k": "v"}},
        ]
        client = self.client(lambda request: httpx.Response(200, json={"data": rows, "has_more": False}))
        self.assertEqual((await client.find_session_by_label("ag_1", "k", "v"))["id"], "conv_src")

    async def test_pagination_stops_when_the_cursor_does_not_advance(self):
        page = {"data": [{"id": "a"}], "has_more": True, "last_id": None}
        client = self.client(lambda request: httpx.Response(200, json=page))
        self.assertEqual([a["id"] for a in await asyncio.wait_for(client.agents(), 5)], ["a"])

    async def test_deleted_sessions_are_reported_as_gone(self):
        client = self.client(lambda request: httpx.Response(404, json={"detail": "not found"}))
        with self.assertRaises(shim.OmnigentError) as raised:
            await client.session("conv_gone")
        self.assertTrue(raised.exception.gone)


class TraceFileTest(unittest.TestCase):
    def test_trace_is_readable_by_its_owner_only(self):
        import os
        import stat
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            old = os.umask(0o022)
            try:
                fresh = Path(tmp, "fresh.jsonl")
                shim.open_trace(str(fresh)).close()
                existing = Path(tmp, "existing.jsonl")
                existing.write_text("old\n")
                existing.chmod(0o644)
                with shim.open_trace(str(existing)) as trace:
                    trace.write("new\n")
            finally:
                os.umask(old)
            self.assertEqual(stat.S_IMODE(fresh.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(existing.stat().st_mode), 0o600)
            self.assertEqual(existing.read_text(), "old\nnew\n")


if __name__ == "__main__":
    unittest.main()
