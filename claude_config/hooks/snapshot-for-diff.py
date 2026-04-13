#!/usr/bin/env python3
"""PreToolUse hook: save turn, session, and per-edit snapshots before Edit/Write."""

import hashlib
import json
import os
import shutil
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


def main():
    if not os.environ.get("NVIM"):
        return

    try:
        data = json.loads(sys.stdin.read())
    except Exception:
        return

    file_path = data.get("tool_input", {}).get("file_path", "")
    if not file_path or not os.path.isfile(file_path):
        return

    session_id = get_session_id(data)
    if not session_id:
        return

    abs_path = os.path.abspath(file_path)
    h = hashlib.md5(abs_path.encode()).hexdigest()

    # Per-edit snapshot (always overwrite; used for change detection in PostToolUse)
    shutil.copy2(file_path, f"/tmp/.claude-edit-snap-{session_id}-{h}")

    # Turn snapshot: first edit of this file in the current turn
    turn_snap = f"/tmp/.claude-turn-snap-{session_id}-{h}"
    if not os.path.exists(turn_snap):
        shutil.copy2(file_path, turn_snap)

    # Session snapshot: first edit of this file in the entire session
    session_snap = f"/tmp/.claude-session-snap-{session_id}-{h}"
    if not os.path.exists(session_snap):
        shutil.copy2(file_path, session_snap)


if __name__ == "__main__":
    main()
