"""Detection of a blocking foreground wait in a shell command.

Split out from :mod:`no_foreground_wait` so the rule — what counts as waiting —
is testable on its own, without standing up a policy factory.

Deliberately stdlib-only and side-effect-free. It is imported at Omnigent server
startup, where a failure fails *closed* to DENY and would block every tool call.
"""

from __future__ import annotations

import re

# A fixed sleep outside a loop is a guess at how long the work takes: it either
# overshoots, wasting wall time after the work is done, or undershoots and costs
# another check-and-sleep turn. Below this it is a retry or settle pause, and
# denying it would be noise.
GUESS_THRESHOLD_SECONDS = 30

# A sleep inside a while/until/for body is a poll interval: the loop exits once
# the condition holds, so overshoot is bounded by the interval. Only a long
# interval is worth refusing.
POLL_INTERVAL_THRESHOLD_SECONDS = 60

# Every harness's shell tool. Mirrors
# ``omnigent.policies.builtins._shell.SHELL_TOOLS``, which is the authoritative
# list — it is derived from observed per-harness tool naming. Copied rather than
# imported: that module is private, so an upstream rename would break policy
# import and take the gate down, whereas a stale copy here merely leaves a new
# harness un-gated. Keep in sync when Omnigent adds one.
SHELL_TOOLS: frozenset[str] = frozenset(
    {
        "sys_os_shell",  # Omnigent built-in (SDK harnesses)
        "Bash",  # Claude Code / Codex native
        "bash",  # pi / opencode native
        "Shell",  # Cursor
        "terminal",  # Hermes
        "developer__shell",  # Goose
        "shell",  # codex in-process harness
    }
)

# A heredoc body is data, not commands. ``cat > watch.sh <<'EOF' ... sleep 300
# ... EOF`` writes a script; it does not wait. Stripping these first is what
# stops the gate from blocking you from *writing* the backgrounded watcher it is
# telling you to write, and from firing on a snippet that merely quotes a sleep.
_HEREDOC = re.compile(r"<<-?\s*(['\"]?)(\w+)\1.*?^\s*\2\s*$", re.S | re.M)

# A sleep this agent is actually waiting on, rather than one mentioned in data.
# Two accepted positions:
#   command position  -- start of string, after a separator, or after a
#                        loop/conditional keyword: ``foo && sleep 300``
#   interpreter form  -- ``sh -c 'sleep 300'``, which is how Codex presents a
#                        command: ``/bin/zsh -lc 'sleep 600'``
# Naming the interpreter matters: a bare ``-c`` would also match ``grep -c
# 'sleep 300'``. Requiring whitespace before the number excludes
# ``sleep_seconds=300``, and neither position matches ``--sleep 300`` or
# ``mysleep 300``.
_CMD_POSITION = r"(?:\A|[;&|(\n]|\b(?:do|then|else)\b)\s*"
_INTERPRETER = r"(?:\A|[\s/])(?:ba|z|k|da)?sh\s+-[A-Za-z]*c[A-Za-z]*\s+['\"]?\s*"
_SLEEP = re.compile(
    rf"(?:{_CMD_POSITION}|{_INTERPRETER})sleep\s+(\d+(?:\.\d+)?)([smhd]?)\b"
)

_UNITS = {"": 1, "s": 1, "m": 60, "h": 3600, "d": 86400}

# The body of a shell loop. Approximate (nesting and a stray ``for``/``do`` in
# quoted text can misplace a boundary), but a misclassified sleep only moves
# between the two thresholds; it never escapes the gate.
_LOOP_BODY = re.compile(r"\b(?:while|until|for)\b.*?\bdo\b(.*?)\bdone\b", re.S)

# Already-detached work does not hold the turn open, so it is not our business.
_DETACHED = re.compile(r"\b(?:setsid|nohup|disown)\b|&\s*$")

# Always-true instruction, so an unrecognised harness still gets a correct
# answer rather than one borrowed from a harness it is not running.
_GENERIC = (
    "Wait on the condition itself so you resume the moment it holds: "
    "`wait <pid>`, or `until <check>; do sleep 5; done`. If you have other "
    "work, detach that wait and have it signal you, then continue."
)

_HINTS = {
    "Bash": (
        "In Claude Code: with nothing else to do, run that loop in the "
        "foreground; with other work, run it with run_in_background=true and "
        "carry on — the harness re-invokes you when it exits."
    ),
    "shell": (
        "In Codex: `setsid nohup ... &` that calls "
        "`codex queue --thread <thread> --message ...` on the condition, or "
        "hold the process in a unified_exec session."
    ),
}


def remedy_for(tool_name: str) -> str:
    """Return the fix to suggest for a shell tool, harness hint included."""
    hint = _HINTS.get(tool_name)
    return f"{_GENERIC} {hint}" if hint else _GENERIC


def is_shell_tool(tool_name: object) -> bool:
    return isinstance(tool_name, str) and tool_name in SHELL_TOOLS


def command_text(arguments: object) -> str:
    """Flatten a shell tool's command argument into text.

    Most harnesses pass ``command`` as a string; Codex may pass either a string
    (``/bin/zsh -lc 'pwd'``) or an argv list.
    """
    if not isinstance(arguments, dict):
        return ""
    command = arguments.get("command", "")
    if isinstance(command, (list, tuple)):
        return " ".join(str(part) for part in command)
    return str(command)


def longest_gated_sleep(command: str) -> float:
    """Return the longest sleep in ``command`` over its threshold, in seconds.

    Each sleep is held to :data:`POLL_INTERVAL_THRESHOLD_SECONDS` inside a loop
    body and :data:`GUESS_THRESHOLD_SECONDS` elsewhere. Returns 0.0 when none
    qualifies, or when the command detaches, since a detached wait does not hold
    the turn open.
    """
    if _DETACHED.search(command):
        return 0.0
    command = _HEREDOC.sub("", command)
    loop_bodies = [m.span(1) for m in _LOOP_BODY.finditer(command)]
    gated = []
    for match in _SLEEP.finditer(command):
        seconds = float(match.group(1)) * _UNITS[match.group(2)]
        # The match itself can begin at the ``do`` that opens the body, so
        # locate the sleep by its duration.
        in_loop = any(start <= match.start(1) < end for start, end in loop_bodies)
        threshold = POLL_INTERVAL_THRESHOLD_SECONDS if in_loop else GUESS_THRESHOLD_SECONDS
        if seconds >= threshold:
            gated.append(seconds)
    return max(gated, default=0.0)


def blocking_wait_seconds(tool_name: object, arguments: object) -> float:
    """Seconds this call would block the turn, or 0.0 if it would not.

    The one entry point the policy calls, so the whole rule is reachable from a
    single function in tests.
    """
    if not is_shell_tool(tool_name):
        return 0.0
    if isinstance(arguments, dict) and arguments.get("run_in_background"):
        # Claude Code's own backgrounding flag. Other harnesses never set it.
        return 0.0
    return longest_gated_sleep(command_text(arguments))


def denial_reason(seconds: float, tool_name: str) -> str:
    """The message an agent sees when a blocking wait is refused.

    It must say that nothing ran: agents otherwise re-issue only the wait and
    lose the rest of the command, such as files the same call was writing.
    """
    return (
        f"Refused, and nothing in this command ran — re-run every part of it, "
        f"not just the wait. A {seconds:.0f}s sleep is a guess: it "
        f"overshoots and wastes wall time, or undershoots and costs another "
        f"turn. {remedy_for(tool_name)}"
    )
