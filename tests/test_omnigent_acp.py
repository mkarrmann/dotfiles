"""Checks for bin/omnigent-acp's turn tracking and prompt rewriting. No network.

Run with: uv run --no-project --with agent-client-protocol==0.12.1 --with httpx \
    python -m unittest discover -s tests -p test_omnigent_acp.py
"""

import json
from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader
from pathlib import Path
import sys
import unittest

from acp.schema import EnvVariable, McpServerStdio

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


if __name__ == "__main__":
    unittest.main()
