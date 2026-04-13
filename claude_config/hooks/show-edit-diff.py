#!/usr/bin/env python3
"""PostToolUse hook: notify Neovim about file edits for the diff viewer."""

import filecmp
import hashlib
import json
import os
import subprocess
import sys


def get_session_id(data):
    sid = data.get("session_id", "")
    if sid:
        return sid
    tab_handle = os.environ.get("NVIM_TAB_HANDLE", "")
    if tab_handle:
        pid_file = os.path.expanduser(f"~/.claude/agent-manager/pids/tab-{tab_handle}")
        try:
            with open(pid_file) as f:
                return f.read().strip()
        except FileNotFoundError:
            pass
    return ""


def lua_escape(s):
    return s.replace("\\", "\\\\").replace("'", "\\'")


def main():
    nvim = os.environ.get("NVIM", "")
    if not nvim:
        return

    try:
        data = json.loads(sys.stdin.read())
    except Exception:
        return

    file_path = data.get("tool_input", {}).get("file_path", "")
    if not file_path:
        return

    file_path = os.path.abspath(file_path)
    session_id = get_session_id(data)
    if not session_id:
        return

    h = hashlib.md5(file_path.encode()).hexdigest()

    # Check if the file actually changed (compare against per-edit snapshot)
    edit_snap = f"/tmp/.claude-edit-snap-{session_id}-{h}"
    if os.path.isfile(edit_snap):
        if os.path.isfile(file_path) and filecmp.cmp(edit_snap, file_path, shallow=False):
            os.remove(edit_snap)
            return
        os.remove(edit_snap)
    elif not os.path.isfile(file_path):
        return

    turn_snap = f"/tmp/.claude-turn-snap-{session_id}-{h}"
    session_snap = f"/tmp/.claude-session-snap-{session_id}-{h}"

    expr = "luaeval(\"require('lib.claude-diff').file_edited('{}', '{}', '{}', '{}')\")".format(
        lua_escape(file_path),
        lua_escape(turn_snap),
        lua_escape(session_snap),
        lua_escape(session_id),
    )

    try:
        subprocess.run(
            ["nvim", "--server", nvim, "--remote-expr", expr],
            capture_output=True,
            timeout=5,
        )
    except Exception:
        pass


if __name__ == "__main__":
    main()
