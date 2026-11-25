#!/usr/bin/env python3
"""PreToolUse hook: Matt's guardrails for Claude Code sessions outside Omnigent.

Omnigent enforces these as server policies (omnigent_config/policy_modules),
but a session Omnigent does not front -- Metamate 3's hosted Claude Code, or a
bare `claude` -- only has its own hooks. Metamate links ~/.claude/settings.json
into each hosted session's config folder, so this hook reaches those sessions
too (a deny blocks the call there; verified 2026-11-24). It refuses:

- a new working copy or build environment (no_new_environments);
- a foreground sleep that blocks the turn (no_foreground_wait);
- a catastrophic, irreversible command (destructive_commands).

Inside an Omnigent session ($OMNIGENT set) the server policies already apply,
so the hook abstains rather than deny twice. Any error abstains: a guardrail
must never break a session.
"""

import json
import os
import sys
from pathlib import Path

POLICY_DIR = Path(os.environ.get("DOTFILES_DIR", Path.home() / "dotfiles")) / "omnigent_config" / "policy_modules"


def deny(reason: str) -> None:
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": reason}}))


def main() -> None:
    if os.environ.get("OMNIGENT"):
        return
    payload = json.load(sys.stdin)
    sys.path.insert(0, str(POLICY_DIR))
    import destructive_commands
    import foreground_wait
    import no_foreground_wait
    import no_new_environments

    name = payload.get("tool_name")
    arguments = payload.get("tool_input") or {}
    event = {"type": "tool_call", "data": {"name": name, "arguments": arguments}}

    for gate in (no_new_environments.gate_new_environments, no_foreground_wait.block_foreground_wait()):
        verdict = gate(event)
        if verdict and verdict.get("result") == "DENY":
            deny(verdict["reason"])
            return
    if foreground_wait.is_shell_tool(name) and destructive_commands.is_catastrophic(foreground_wait.command_text(arguments)):
        deny(destructive_commands.REASON)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
