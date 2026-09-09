"""Run with python3 -m unittest discover -s tests -p test_no_foreground_wait.py.

Covers the foreground-wait gate: the detection rule and the Omnigent policy that
enforces it. Imports the modules the way the server does — by name, off the
policy-module directory that ``systemd/omnigent-server.service`` puts on
PYTHONPATH — so a break in that path shows up here rather than at server start.
"""

from __future__ import annotations

import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
POLICIES = ROOT / "omnigent_config" / "policy_modules"
sys.path.insert(0, str(POLICIES))

import foreground_wait as detection  # noqa: E402
import no_foreground_wait as policy_mod  # noqa: E402


def tool_call(tool_name: str, **arguments: object) -> dict:
    return {"type": "tool_call", "data": {"name": tool_name, "arguments": arguments}}


class TestDetection(unittest.TestCase):
    def test_every_harness_shell_tool_is_gated(self) -> None:
        # A name missing from SHELL_TOOLS is a silently un-gated harness.
        for tool in detection.SHELL_TOOLS:
            with self.subTest(tool=tool):
                self.assertEqual(
                    detection.blocking_wait_seconds(tool, {"command": "sleep 300"}),
                    300.0,
                )

    def test_matches_omnigent_canonical_list(self) -> None:
        # Mirrors omnigent.policies.builtins._shell.SHELL_TOOLS.
        self.assertEqual(
            detection.SHELL_TOOLS,
            frozenset(
                {
                    "sys_os_shell",
                    "Bash",
                    "bash",
                    "Shell",
                    "terminal",
                    "developer__shell",
                    "shell",
                }
            ),
        )

    def test_unit_suffixes(self) -> None:
        for command, want in [
            ("sleep 5m", 300.0),
            ("sleep 2h", 7200.0),
            ("sleep 90s", 90.0),
            ("sleep 90", 90.0),
        ]:
            with self.subTest(command=command):
                self.assertEqual(
                    detection.blocking_wait_seconds("Bash", {"command": command}), want
                )

    def test_longest_sleep_wins(self) -> None:
        self.assertEqual(
            detection.blocking_wait_seconds("Bash", {"command": "sleep 30 && sleep 900"}),
            900.0,
        )

    def test_codex_argv_list(self) -> None:
        self.assertEqual(
            detection.blocking_wait_seconds(
                "shell", {"command": ["/bin/zsh", "-lc", "sleep 600"]}
            ),
            600.0,
        )

    def test_real_invocations_are_caught(self) -> None:
        cases = {
            "bare": ("sleep 420; date", 420.0),
            "chained": ("date && sleep 900", 900.0),
            "in a foreground loop": ("while :; do sleep 300; done", 300.0),
            "codex interpreter form": ("/bin/zsh -lc 'sleep 600'", 600.0),
            "bash -c": ('bash -c "sleep 300"', 300.0),
            "after a pipe": ("foo | sleep 120", 120.0),
        }
        for label, (command, want) in cases.items():
            with self.subTest(case=label):
                self.assertEqual(
                    detection.blocking_wait_seconds("Bash", {"command": command}), want
                )

    def test_mentioning_a_sleep_is_not_waiting_on_one(self) -> None:
        """Regression: the gate fired on commands that only *quote* a sleep.

        Caught in production. The first case is a python heredoc whose test data
        contained the literal string; the second is writing the very
        backgrounded watcher script the gate asks for, which it must not block.
        """
        cases = {
            "heredoc test data": (
                "python3 - <<'EOF'\n"
                'ev = {"data": {"arguments": {"command": "sleep 600"}}}\n'
                "print(policy(ev))\n"
                "EOF"
            ),
            "writing a watcher script": (
                "cat > watch.sh <<'EOF'\n"
                "while :; do\n"
                "  check && break\n"
                "  sleep 300\n"
                "done\n"
                "EOF"
            ),
            "unquoted heredoc": "cat > w.sh <<EOF\nsleep 900\nEOF",
            "grep -c is not an interpreter": "grep -c 'sleep 300' notes.txt",
            "echoing it": 'echo "sleep 300" >> notes.txt',
            "python time.sleep": "python3 -c 'import time; time.sleep(300)'",
        }
        for label, command in cases.items():
            with self.subTest(case=label):
                self.assertEqual(
                    detection.blocking_wait_seconds("Bash", {"command": command}), 0.0
                )

    def test_allowed(self) -> None:
        cases = {
            "non-shell tool": ("Edit", {"command": "sleep 300"}),
            "backgrounded": ("Bash", {"command": "sleep 300", "run_in_background": True}),
            "below threshold": ("Bash", {"command": "sleep 59"}),
            "retry pause": ("Bash", {"command": "sleep 5; retry"}),
            "flag not a wait": ("Bash", {"command": "mytool --sleep 300 --run"}),
            "identifier": ("Bash", {"command": "X=1 sleep_seconds=300 run"}),
            "longer word": ("Bash", {"command": "mysleep 300"}),
            "setsid detached": ("Bash", {"command": "setsid nohup sh -c 'sleep 300' &"}),
            "trailing ampersand": ("Bash", {"command": "sleep 300 &"}),
            "no command": ("Bash", {}),
            "arguments not a dict": ("Bash", None),
        }
        for label, (tool, args) in cases.items():
            with self.subTest(case=label):
                self.assertEqual(detection.blocking_wait_seconds(tool, args), 0.0)

    def test_remedy_is_harness_specific(self) -> None:
        self.assertIn("run_in_background", detection.remedy_for("Bash"))
        self.assertIn("codex queue", detection.remedy_for("shell"))
        # An unrecognised harness still gets a correct, generic instruction.
        self.assertIn("Detach the wait", detection.remedy_for("developer__shell"))
        self.assertNotIn("run_in_background", detection.remedy_for("developer__shell"))


class TestPolicy(unittest.TestCase):
    def setUp(self) -> None:
        self.policy = policy_mod.block_foreground_wait()

    def test_denies_tool_call(self) -> None:
        out = self.policy(tool_call("shell", command="sleep 600"))
        assert out is not None
        self.assertEqual(out["result"], "DENY")
        self.assertIn("600s", out["reason"])
        self.assertIn("codex queue", out["reason"])

    def test_ask_action(self) -> None:
        policy = policy_mod.block_foreground_wait(action="ask")
        out = policy(tool_call("Bash", command="sleep 300"))
        assert out is not None
        self.assertEqual(out["result"], "ASK")

    def test_invalid_action_is_loud_at_build_time(self) -> None:
        with self.assertRaises(ValueError):
            policy_mod.block_foreground_wait(action="warn")

    def test_abstains(self) -> None:
        cases = {
            "not a tool_call": {"type": "tool_result", "data": {}},
            "data not a dict": {"type": "tool_call", "data": "nope"},
            "no data": {"type": "tool_call"},
            "non-shell tool": tool_call("Edit", command="sleep 300"),
            "short sleep": tool_call("Bash", command="sleep 5"),
            "backgrounded": tool_call("Bash", command="sleep 300", run_in_background=True),
            "empty event": {},
        }
        for label, event in cases.items():
            with self.subTest(case=label):
                self.assertIsNone(self.policy(event))

    def test_evaluator_never_raises(self) -> None:
        # Policy exceptions fail CLOSED to DENY and would block every tool call,
        # so the evaluator must swallow anything a malformed event throws at it.
        for event in [None, [], "string", {"type": "tool_call", "data": {"name": 5}}]:
            with self.subTest(event=event):
                try:
                    self.policy(event)  # type: ignore[arg-type]
                except Exception as exc:  # pragma: no cover
                    self.fail(f"evaluator raised {exc!r} on {event!r}")

    def test_registry_shape(self) -> None:
        (entry,) = policy_mod.POLICY_REGISTRY
        self.assertEqual(entry["kind"], "factory")
        self.assertEqual(entry["handler"], "no_foreground_wait.block_foreground_wait")
        self.assertIn("action", entry["params_schema"]["properties"])


class TestServerWiring(unittest.TestCase):
    """server.yaml must actually reference what the module exports."""

    def setUp(self) -> None:
        self.yaml = (ROOT / "omnigent_config" / "server.yaml").read_text()

    def test_module_is_registered(self) -> None:
        self.assertRegex(self.yaml, r"(?m)^\s*-\s*no_foreground_wait\s*$")

    def test_handler_path_matches_registry(self) -> None:
        self.assertIn(policy_mod.POLICY_REGISTRY[0]["handler"], self.yaml)


if __name__ == "__main__":
    unittest.main(verbosity=2)
