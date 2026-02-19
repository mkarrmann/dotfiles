#!/usr/bin/env python3
"""
PreToolUse hook that auto-accepts Edit/Write tool calls when the target
file is under source control (git or sapling/hg).

Files not under source control fall through to the normal permission flow.
"""

import json
import os
import sys


def is_under_source_control(file_path):
    dir_path = os.path.dirname(os.path.abspath(file_path))
    while True:
        if any(
            os.path.isdir(os.path.join(dir_path, vcs_dir))
            for vcs_dir in (".git", ".hg", ".sl")
        ):
            return True
        parent = os.path.dirname(dir_path)
        if parent == dir_path:
            break
        dir_path = parent
    return False


def main():
    try:
        data = json.loads(sys.stdin.read())
        file_path = data.get("tool_input", {}).get("file_path", "")

        if file_path and is_under_source_control(file_path):
            print(json.dumps({
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "allow",
                    "permissionDecisionReason": "File is under source control",
                }
            }))
    except Exception:
        pass

    sys.exit(0)


if __name__ == "__main__":
    main()
