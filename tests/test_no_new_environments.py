"""Run with python3 -m unittest discover -s tests -p test_no_new_environments.py.

Covers the new-environment reminder: which commands and tools it refuses, the
override assignment that lets an approved command through, and the Omnigent
policy wrapper. Imports the modules the way the server does — by name, off the
policy-module directory that ``systemd/omnigent-server.service`` and
``systemd/desktop/omnigent-host.service`` put on PYTHONPATH.
"""

from __future__ import annotations

import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
POLICIES = ROOT / "omnigent_config" / "policy_modules"
sys.path.insert(0, str(POLICIES))

import foreground_wait  # noqa: E402
import no_new_environments as policy_mod  # noqa: E402


def tool_call(tool_name: str, **arguments: object) -> dict:
    return {"type": "tool_call", "data": {"name": tool_name, "arguments": arguments}}


class TestCreatesEnvironment(unittest.TestCase):
    def test_creating_commands(self) -> None:
        for command in [
            "sl worktree add /data/users/me/wt -r abc123 --label x",
            "sl wt add ../wt",
            "hg worktree add ../wt",
            "git worktree add .worktrees/t -b polly/t",
            "git -C ~/repo worktree add ../wt",
            "sl --reason 'isolate' worktree add ../wt",
            "sl --cwd ~/checkout1/fbsource worktree add ../wt",
            "wt add rabitq",
            "~/dotfiles/bin/wt add rabitq --at abc",
            "eden clone fbsource ~/fb2",
            "fbclone fbsource",
            "git clone https://github.com/x/y",
            "sl clone ssh://repo",
            "buck2 --isolation-dir mine build //x:y",
            "buck2 build --isolation-dir=mine //x:y",
            "cd ~/checkout1/fbsource && time sl worktree add ../wt",
            "timeout 600 sl worktree add ../wt",
            "echo start; (sl worktree add ../wt)",
            "x=$(sl worktree add ../wt)",
            "/bin/zsh -lc 'sl worktree add ../wt'",
            "bash -c \"cd /tmp && git worktree add w\"",
            "true\nsl worktree add ../wt",
        ]:
            with self.subTest(command=command):
                self.assertTrue(policy_mod.creates_environment(command))

    def test_non_creating_commands(self) -> None:
        for command in [
            "sl worktree list",
            "sl worktree remove /data/users/me/wt -y",
            "wt list",
            "wt rm rabitq",
            "git worktree prune",
            "sl status",
            "sl help worktree",
            "echo 'use sl worktree add to isolate'",
            "grep -rn 'git worktree add' .",
            "buck2 --isolation-dir mine kill",
            "buck2 --isolation-dir .autodeps2 clean",
            "buck2 build //x:y",
            "eden list",
            "cat > note.md <<'EOF'\nsl worktree add ../wt\nEOF",
            "",
        ]:
            with self.subTest(command=command):
                self.assertFalse(policy_mod.creates_environment(command))

    def test_override_assignment_lets_the_marked_command_run(self) -> None:
        for command in [
            "OMNIGENT_NEW_ENV_APPROVED=1 sl worktree add ../wt",
            "cd x && OMNIGENT_NEW_ENV_APPROVED=1 wt add rabitq",
            "FOO=1 OMNIGENT_NEW_ENV_APPROVED=1 git worktree add w",
        ]:
            with self.subTest(command=command):
                self.assertFalse(policy_mod.creates_environment(command))

    def test_override_covers_only_its_own_segment(self) -> None:
        self.assertTrue(
            policy_mod.creates_environment(
                "OMNIGENT_NEW_ENV_APPROVED=1 wt add a && eden clone fbsource ~/fb2"
            )
        )

    def test_override_must_be_exactly_one(self) -> None:
        self.assertTrue(
            policy_mod.creates_environment("OMNIGENT_NEW_ENV_APPROVED=0 sl worktree add w")
        )

    def test_unbalanced_quotes_fall_back_to_word_split(self) -> None:
        self.assertTrue(policy_mod.creates_environment("sl worktree add ../wt 'oops"))


class TestPolicy(unittest.TestCase):
    def test_every_harness_shell_tool_is_gated(self) -> None:
        for tool in foreground_wait.SHELL_TOOLS:
            with self.subTest(tool=tool):
                result = policy_mod.gate_new_environments(
                    tool_call(tool, command="sl worktree add ../wt")
                )
                self.assertEqual(result["result"], "DENY")

    def test_codex_argv_list_keeps_its_shell_script(self) -> None:
        result = policy_mod.gate_new_environments(
            tool_call("Bash", command=["/bin/zsh", "-lc", "sl worktree add ../wt"])
        )
        self.assertEqual(result["result"], "DENY")

    def test_reason_says_nothing_ran_and_names_the_override(self) -> None:
        reason = policy_mod.gate_new_environments(
            tool_call("Bash", command="wt add x")
        )["reason"]
        self.assertIn("nothing in this command ran", reason)
        self.assertIn(policy_mod.OVERRIDE_ASSIGNMENT, reason)

    def test_worktree_tools_are_refused(self) -> None:
        for event in [
            tool_call("EnterWorktree", name="x"),
            tool_call("Agent", prompt="p", isolation="worktree"),
            tool_call("Task", prompt="p", isolation="worktree"),
        ]:
            with self.subTest(tool=event["data"]["name"]):
                result = policy_mod.gate_new_environments(event)
                self.assertEqual(result["result"], "DENY")
                self.assertIn(policy_mod.OVERRIDE_ASSIGNMENT, result["reason"])

    def test_abstains_on_everything_else(self) -> None:
        for event in [
            tool_call("Bash", command="sl status"),
            tool_call("Agent", prompt="p"),
            tool_call("Read", file_path="/x"),
            {"type": "request", "data": {"text": "sl worktree add"}},
            {"type": "tool_call", "data": "garbage"},
            {"type": "tool_call", "data": {"name": "Bash", "arguments": None}},
        ]:
            with self.subTest(event=event):
                self.assertIsNone(policy_mod.gate_new_environments(event))

    def test_exceptions_abstain_instead_of_failing_closed(self) -> None:
        class Exploding(dict):
            def get(self, *args: object, **kwargs: object) -> object:
                raise RuntimeError("boom")

        self.assertIsNone(policy_mod.gate_new_environments(Exploding()))

    def test_registry_entry_names_this_callable(self) -> None:
        (entry,) = policy_mod.POLICY_REGISTRY
        self.assertEqual(entry["kind"], "callable")
        module, _, attribute = entry["handler"].rpartition(".")
        self.assertEqual(module, policy_mod.__name__)
        self.assertIs(getattr(policy_mod, attribute), policy_mod.gate_new_environments)


if __name__ == "__main__":
    unittest.main()
