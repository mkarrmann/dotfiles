"""File edits as T3 Code shows them: a file-change item with the file, its old and new text, and a diff.

T3 Code's OpenCode driver makes a file-change item of a tool whose name has
`edit`, `write` or `patch` in it, and reads `filePath`, `oldString`,
`newString` (or `content`) from its input and a unified diff from the
completed call's `metadata.diff` (toolItems.ts). The harnesses name these
differently: Claude's Edit/MultiEdit/Write take `file_path`, `old_string`,
`new_string`, `content` and `edits`; codex-native's apply_patch carries
`changes`, each a path, a kind and a unified diff (or the whole file for an
added or deleted one), as `bin/omnigent-acp` read them.
"""

from __future__ import annotations

import difflib
import os
from dataclasses import dataclass
from typing import Any

from .wire import JsonObject


@dataclass(frozen=True)
class Edit:
    input: JsonObject
    """The call's input with the keys T3 Code reads added."""
    diff: str | None


def _absolute(path: str, cwd: str) -> str:
    return os.path.normpath(os.path.join(cwd, path))


def _unified(path: str, old: str, new: str) -> str:
    name = path.lstrip("/")
    lines = difflib.unified_diff(
        old.splitlines(keepends=True), new.splitlines(keepends=True), fromfile=f"a/{name}", tofile=f"b/{name}"
    )
    return "".join(line if line.endswith("\n") else line + "\n" for line in lines)


def _codex_change(change: dict[str, Any], cwd: str) -> tuple[str, str] | None:
    path, diff, kind = change.get("path"), change.get("diff"), change.get("kind")
    if not isinstance(path, str) or not isinstance(diff, str) or not isinstance(kind, dict):
        return None
    moved = kind.get("move_path") or kind.get("movePath")
    target = _absolute(moved if isinstance(moved, str) else path, cwd)
    match kind.get("type"):
        case "add":
            return target, _unified(target, "", diff)
        case "delete":
            return target, _unified(target, diff, "")
        case _:
            name = target.lstrip("/")
            body = diff if diff.startswith("---") else f"--- a/{name}\n+++ b/{name}\n{diff}"
            return target, body if body.endswith("\n") else body + "\n"


def edit_of(name: str, arguments: Any, cwd: str) -> Edit | None:
    """The file-change view of a harness's editing call, or `None` when it is not one."""
    if not isinstance(arguments, dict):
        return None
    lowered = name.lower()
    if lowered == "apply_patch":
        changes = arguments.get("changes")
        parsed = (
            [c for c in (_codex_change(c, cwd) for c in changes if isinstance(c, dict)) if c] if isinstance(changes, list) else []
        )
        if not parsed:
            return None
        return Edit({**arguments, "filePath": parsed[0][0]}, "".join(diff for _, diff in parsed))
    file_path = arguments.get("file_path") or arguments.get("notebook_path")
    if not isinstance(file_path, str):
        return None
    path = _absolute(file_path, cwd)
    if lowered == "write":
        content = arguments.get("content")
        if not isinstance(content, str):
            return None
        return Edit({**arguments, "filePath": path, "content": content}, None)
    if lowered == "edit":
        old, new = arguments.get("old_string"), arguments.get("new_string")
        if not isinstance(old, str) or not isinstance(new, str):
            return None
        return Edit({**arguments, "filePath": path, "oldString": old, "newString": new}, _unified(path, old, new))
    if lowered == "multiedit":
        edits = [e for e in arguments.get("edits") or [] if isinstance(e, dict)]
        pairs = [
            (e["old_string"], e["new_string"])
            for e in edits
            if isinstance(e.get("old_string"), str) and isinstance(e.get("new_string"), str)
        ]
        if not pairs:
            return None
        diff = "".join(_unified(path, old, new) for old, new in pairs)
        return Edit({**arguments, "filePath": path}, diff)
    return None
