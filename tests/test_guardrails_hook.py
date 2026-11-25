"""Behavior of claude_config/hooks/guardrails.py as Claude Code runs it: JSON in, decision out.

Run with: python3 -m unittest discover -s tests -p test_guardrails_hook.py
"""

import json
import os
from pathlib import Path
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / "claude_config/hooks/guardrails.py"


def decide(tool_name, tool_input, omnigent=False):
    env = {k: v for k, v in os.environ.items() if k != "OMNIGENT"}
    env["DOTFILES_DIR"] = str(ROOT)
    if omnigent:
        env["OMNIGENT"] = "1"
    out = subprocess.run(
        ["python3", str(HOOK)],
        input=json.dumps({"tool_name": tool_name, "tool_input": tool_input}),
        capture_output=True,
        text=True,
        env=env,
        check=True,
    ).stdout.strip()
    return json.loads(out)["hookSpecificOutput"]["permissionDecision"] if out else "allow"


class GuardrailsHookTest(unittest.TestCase):
    def test_denies_catastrophic_commands(self):
        for command in (
            "sl push -f --to main",
            "hg push --delete mybook",
            "git push origin +main",
            "cd repo && git push --force-with-lease",
            "rm -rf ~",
            "sudo rm -r /etc/nginx",
            "git reset --hard origin/main",
        ):
            with self.subTest(command=command):
                self.assertEqual(decide("Bash", {"command": command}), "deny")

    def test_denies_new_environments_and_foreground_waits(self):
        self.assertEqual(decide("Bash", {"command": "sl clone fbsource /tmp/x"}), "deny")
        self.assertEqual(decide("EnterWorktree", {}), "deny")
        self.assertEqual(decide("Agent", {"prompt": "x", "isolation": "worktree"}), "deny")
        self.assertEqual(decide("Bash", {"command": "sleep 120"}), "deny")

    def test_allows_ordinary_work(self):
        for command in ("echo hi", "rm -rf build", "sl push --to main", 'git commit -m "push -f later"', "sleep 5"):
            with self.subTest(command=command):
                self.assertEqual(decide("Bash", {"command": command}), "allow")
        self.assertEqual(decide("Agent", {"prompt": "x"}), "allow")

    def test_abstains_inside_omnigent_where_server_policies_apply(self):
        self.assertEqual(decide("Bash", {"command": "rm -rf ~"}, omnigent=True), "allow")


if __name__ == "__main__":
    unittest.main()
