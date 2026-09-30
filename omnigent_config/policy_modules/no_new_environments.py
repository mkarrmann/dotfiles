"""Refuse an agent's first attempt to create a new working copy or build environment.

A new worktree, clone, or Buck isolation dir starts cold: a fresh daemon, a full
rebuild, and on Matt's devservers a fresh Maven build root and local repository.
Agents have created them unasked to "isolate" work, when all work belongs in the
session's own checkout. The rule is written in the agent instructions
(``agent_config/global-development-preferences.md`` and
``agent_config/meta-workspace-preferences.md``); this policy catches the attempt
when an agent misses it.

It is a reminder, not a lock. The refusal tells the agent it needed Matt's
approval, and a command carrying the ``OMNIGENT_NEW_ENV_APPROVED=1`` assignment
runs, so an agent Matt has approved is not blocked twice. Claude Code's
``EnterWorktree`` tool and ``Agent`` / ``Task`` with ``isolation: "worktree"``
carry no command to mark, so they are always refused with a pointer to the shell
form.

Enforced as an Omnigent policy rather than per-harness hooks for the reason given
in :mod:`no_foreground_wait`: one policy covers every harness Omnigent fronts.
A session started outside Omnigent is not gated; the written rules still reach it.

Contract (verified against omnigent 0.15.0):
- Registered in ``POLICY_REGISTRY`` as a direct callable; server config names it
  with ``function.path`` and no ``arguments``, so the server calls it once per
  event.
- Returns ``None`` to abstain (ALLOW) or ``{"result": "DENY", "reason": ...}``.
- HACK: policy exceptions fail *closed* to DENY and would block the tool, so the
  evaluator catches everything and abstains. A reminder must never be able to
  break a session.
"""

from __future__ import annotations

import os
import re
import shlex
from typing import Any

# Sibling module; PYTHONPATH points at this directory (see
# systemd/omnigent-server.service).
import foreground_wait

OVERRIDE_ASSIGNMENT = "OMNIGENT_NEW_ENV_APPROVED=1"

_PUNCTUATION = ";&|()`\n"
_SHELLS = frozenset({"sh", "bash", "zsh", "dash", "ksh"})
# Leading words that run the rest of the segment as the real command.
_WRAPPERS = frozenset({"sudo", "time", "nohup", "command", "exec", "nice", "env"})
# Source-control global options that consume the following token.
_SCM_OPTIONS_WITH_VALUE = frozenset(
    {
        "-C",
        "-R",
        "-c",
        "--cwd",
        "--repository",
        "--repo",
        "--config",
        "--git-dir",
        "--reason",
    }
)
_SCM_TOOLS = frozenset({"sl", "hg", "git"})
_BUCK_TOOLS = frozenset({"buck", "buck2"})
# Buck subcommands that act on an existing isolation dir instead of creating one.
_BUCK_NON_CREATING = frozenset({"kill", "killall", "clean", "status", "log", "help"})
_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_HEREDOC = re.compile(r"<<-?\s*(['\"]?)(\w+)\1[^\n]*\n.*?^\s*\2\s*$", re.S | re.M)

_REASON = (
    "Refused, and nothing in this command ran. It creates a new working copy or "
    "build environment, which needs Matt's explicit approval first: each one "
    "starts cold, with its own build daemon, a full rebuild, and tens to hundreds "
    "of GB of disk. Do the work in this session's own checkout instead. If a new "
    "environment is genuinely needed, stop and ask Matt, saying why and how you "
    "will clean it up. Only if he has explicitly approved this environment in "
    f"this conversation, re-run the whole command with {OVERRIDE_ASSIGNMENT} in "
    "front of the command that creates it."
)
_TOOL_REASON = (
    "Refused: this starts the work in a new worktree, which needs Matt's explicit "
    "approval first and should normally not happen at all. Work in this "
    "session's own checkout. If Matt has explicitly approved a new environment "
    f"in this conversation, create it in the shell with {OVERRIDE_ASSIGNMENT} in "
    "front of the command (for example `sl worktree add`) instead of this tool."
)


def gate_new_environments(event: dict[str, Any]) -> dict[str, Any] | None:
    """Deny a tool call that creates a new working copy or build environment.

    :param event: Omnigent policy event, e.g. ``{"type": "tool_call", "data":
        {"name": "Bash", "arguments": {"command": "sl worktree add ../wt"}}}``.
    :returns: A DENY response for a gated call, else ``None`` to abstain.
    """
    try:
        if event.get("type") != "tool_call":
            return None
        data = event.get("data")
        if not isinstance(data, dict):
            return None
        name = data.get("name")
        arguments = data.get("arguments")
        if not isinstance(arguments, dict):
            arguments = {}
        if name == "EnterWorktree" or (
            name in ("Agent", "Task") and arguments.get("isolation") == "worktree"
        ):
            return {"result": "DENY", "reason": _TOOL_REASON}
        if foreground_wait.is_shell_tool(name) and creates_environment(
            _command(arguments)
        ):
            return {"result": "DENY", "reason": _REASON}
        return None
    except Exception:
        return None


def _command(arguments: dict[str, Any]) -> str:
    # An argv list (Codex) is re-quoted rather than space-joined, so that
    # ``["/bin/zsh", "-lc", "sl worktree add x"]`` keeps its ``-lc`` script intact.
    command = arguments.get("command", "")
    if isinstance(command, (list, tuple)):
        return shlex.join(str(part) for part in command)
    return foreground_wait.command_text(arguments)


def creates_environment(command: str) -> bool:
    """Whether a shell command creates a new, unapproved environment.

    :param command: Shell command text, e.g. ``"cd x && sl worktree add ../wt"``.
    :returns: True if any segment creates a worktree, clone, or Buck isolation
        dir without the override assignment in front of it.
    """
    command = _HEREDOC.sub("", command)
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=_PUNCTUATION)
        lexer.whitespace = " \t\r"
        lexer.whitespace_split = True
        tokens = list(lexer)
    except ValueError:
        tokens = command.split()
    segment: list[str] = []
    for token in [*tokens, ";"]:
        if not token.strip(_PUNCTUATION):
            if _segment_creates(segment):
                return True
            segment = []
        else:
            segment.append(token)
    return False


def _segment_creates(tokens: list[str]) -> bool:
    approved = False
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if _ASSIGNMENT.match(token):
            approved = approved or token == OVERRIDE_ASSIGNMENT
            index += 1
        elif os.path.basename(token) in _WRAPPERS or token.startswith("-"):
            index += 1
        elif os.path.basename(token) == "timeout":
            index += 2
        else:
            break
    argv = tokens[index:]
    if not argv or approved:
        return False
    program = os.path.basename(argv[0])
    rest = argv[1:]
    if program in _SHELLS:
        for position, token in enumerate(rest[:-1]):
            if token.startswith("-") and "c" in token.lstrip("-"):
                return creates_environment(rest[position + 1])
        return False
    if program in _SCM_TOOLS:
        words = _scm_words(rest)
        return words[:1] == ["clone"] or words[:2] in (
            ["worktree", "add"],
            ["wt", "add"],
        )
    if program == "wt":
        return rest[:1] == ["add"]
    if program == "eden":
        return rest[:1] == ["clone"]
    if program == "fbclone":
        return True
    if program in _BUCK_TOOLS:
        if not any(t == "--isolation-dir" or t.startswith("--isolation-dir=") for t in rest):
            return False
        return not _BUCK_NON_CREATING.intersection(_positional(rest))
    return False


def _scm_words(arguments: list[str]) -> list[str]:
    words: list[str] = []
    skip = False
    for token in arguments:
        if skip:
            skip = False
        elif token in _SCM_OPTIONS_WITH_VALUE:
            skip = True
        elif not token.startswith("-"):
            words.append(token)
    return words


def _positional(arguments: list[str]) -> list[str]:
    words: list[str] = []
    skip = False
    for token in arguments:
        if skip:
            skip = False
        elif token == "--isolation-dir":
            skip = True
        elif not token.startswith("-"):
            words.append(token)
    return words


POLICY_REGISTRY: list[dict[str, Any]] = [
    {
        "handler": "no_new_environments.gate_new_environments",
        "kind": "callable",
        "name": "Remind agents that new environments need approval",
        "description": (
            "Refuse the first attempt to create a worktree, clone, or Buck "
            "isolation dir, and tell the agent it needs approval. A command "
            f"prefixed with {OVERRIDE_ASSIGNMENT} runs."
        ),
    },
]
