"""Recognize catastrophic, effectively irreversible shell commands.

The DENY tier of Omnigent's ``blast_radius`` policy
(``omnigent/policies/builtins/orchestration.py``, Apache-2.0), adapted so a
harness hook outside Omnigent can apply the same rules, and extended to the
Sapling/Mercurial pushes an fbsource checkout actually uses:

- a recursive ``rm`` of ``/``, ``~``/``$HOME``, ``/*``, a critical top-level
  directory, or anything under a system directory;
- a force or deleting push: ``git``/``sl``/``hg push`` with ``--force*``,
  ``--delete``, ``--mirror``, ``--prune``, a bundled ``-f``/``-d``, or a ``+``/
  ``:`` refspec;
- ``git reset --hard <remote>/<ref>``.

Like the original this is a safety net against accidental damage, not a
security boundary: it splits statements heuristically and does not model
subshells, command substitution or ``eval``.
"""

from __future__ import annotations

import re
import shlex
from typing import List

_RM_CRITICAL_DIRS = frozenset(
    {"/", "/etc", "/usr", "/bin", "/sbin", "/lib", "/lib64", "/var", "/boot", "/root", "/home", "/opt", "/dev", "/proc", "/sys"}
)
_RM_SYSTEM_PARENTS = frozenset({"/etc", "/usr", "/bin", "/sbin", "/lib", "/lib64", "/var", "/boot", "/dev", "/proc", "/sys"})
_SUDO_VALUE_OPTS = frozenset(
    {"-C", "-D", "-g", "-h", "-p", "-R", "-r", "-T", "-t", "-U", "-u", "--chdir", "--chroot", "--group", "--host",
     "--other-user", "--prompt", "--role", "--type", "--user"}
)
_VCS_GLOBAL_VALUE_OPTS = frozenset(
    {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path", "-R", "--repository", "--cwd", "--config"}
)
_PUSH_TOOLS = frozenset({"git", "sl", "hg"})
_ASSIGNMENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=.*")
_HARD_RESET_TO_REMOTE = re.compile(r"\bgit\b.*\breset\s+--hard\s+\w+/")

REASON = (
    "Refused, and nothing in this command ran: it is catastrophic and effectively "
    "irreversible (a recursive delete of a system or home directory, a force or "
    "deleting push, or a hard reset to a remote ref). Do not look for another way "
    "to do the same thing; stop and ask Matt."
)


def _statements(command: str) -> List[List[str]]:
    out: List[List[str]] = []
    for piece in re.split(r"&&|\|\||[;|\n]", command):
        piece = piece.strip()
        if not piece:
            continue
        try:
            argv = shlex.split(piece)
        except ValueError:
            argv = piece.split()
        if argv:
            out.append(argv)
    return out


def _skip_assignments(argv: List[str], i: int) -> int:
    while i < len(argv) and _ASSIGNMENT.fullmatch(argv[i]):
        i += 1
    return i


def _command_index(argv: List[str]) -> int:
    """Index of the real command after env assignments and an optional ``sudo``."""
    i = _skip_assignments(argv, 0)
    if i >= len(argv) or argv[i] != "sudo":
        return i
    i += 1
    while i < len(argv):
        tok = argv[i]
        if tok == "--":
            return _skip_assignments(argv, i + 1)
        if tok.startswith("-") and tok != "-":
            takes_value = tok in _SUDO_VALUE_OPTS and "=" not in tok
            i += 2 if takes_value and i + 1 < len(argv) else 1
            continue
        return _skip_assignments(argv, i)
    return len(argv)


def _rm_target_is_catastrophic(target: str) -> bool:
    norm = target.rstrip("/") or "/"
    if norm in ("~", "$HOME", "${HOME}") or target.startswith("/*"):
        return True
    if norm in _RM_CRITICAL_DIRS:
        return True
    return target.startswith("/") and "/" + target.lstrip("/").split("/", 1)[0] in _RM_SYSTEM_PARENTS


def _catastrophic_rm(argv: List[str]) -> bool:
    i = _command_index(argv)
    if i >= len(argv) or argv[i] != "rm":
        return False
    recursive, positional_only, targets = False, False, []
    for tok in argv[i + 1 :]:
        if positional_only or not tok.startswith("-"):
            targets.append(tok)
        elif tok == "--":
            positional_only = True
        elif tok == "--recursive":
            recursive = True
        elif not tok.startswith("--"):
            recursive = recursive or "r" in tok[1:] or "R" in tok[1:]
    return recursive and any(_rm_target_is_catastrophic(t) for t in targets)


def _destructive_push(argv: List[str]) -> bool:
    i = _command_index(argv)
    if i >= len(argv) or argv[i] not in _PUSH_TOOLS:
        return False
    j = i + 1
    while j < len(argv) and argv[j].startswith("-"):
        j += 2 if argv[j] in _VCS_GLOBAL_VALUE_OPTS and j + 1 < len(argv) else 1
    if j >= len(argv) or argv[j] != "push":
        return False
    for tok in argv[j + 1 :]:
        if tok.startswith("--force") or tok in ("--delete", "--mirror", "--prune"):
            return True
        if tok.startswith("-") and not tok.startswith("--"):
            for opt in tok[1:]:
                if opt in ("f", "d"):
                    return True
                if opt == "o":
                    break
        if len(tok) > 1 and tok[0] in "+:":
            return True
    return False


def is_catastrophic(command: str) -> bool:
    """Whether *command* contains a statement this module refuses.

    :param command: A shell command line, e.g. ``"cd x && sl push -f --to main"``.
    """
    if _HARD_RESET_TO_REMOTE.search(command):
        return True
    return any(_catastrophic_rm(argv) or _destructive_push(argv) for argv in _statements(command))
