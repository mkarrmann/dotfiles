#!/usr/bin/env python3
"""PostToolUse hook: show a diff in the parent Neovim after Edit/Write."""

import filecmp
import hashlib
import json
import os
import subprocess
import sys


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
    h = hashlib.md5(file_path.encode()).hexdigest()
    snapshot = f"/tmp/.claude-edit-snapshot-{h}"

    if os.path.isfile(snapshot):
        if os.path.isfile(file_path) and filecmp.cmp(snapshot, file_path, shallow=False):
            # File unchanged (tool probably failed) — skip diff, clean up.
            os.remove(snapshot)
            return
        snap_arg = snapshot
    else:
        # New file (Write to a path that didn't exist) — diff against empty.
        snap_arg = ""

    def lua_escape(s):
        return s.replace("\\", "\\\\").replace("'", "\\'")

    expr = "luaeval(\"require('lib.claude-edit-diff').show('{}', '{}')\")".format(
        lua_escape(file_path), lua_escape(snap_arg)
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
