#!/usr/bin/env python3
"""PreToolUse hook: save a snapshot of the file before Edit/Write modifies it."""

import hashlib
import json
import os
import shutil
import sys


def main():
    if not os.environ.get("NVIM"):
        return

    try:
        data = json.loads(sys.stdin.read())
    except Exception:
        return

    file_path = data.get("tool_input", {}).get("file_path", "")
    if file_path and os.path.isfile(file_path):
        h = hashlib.md5(os.path.abspath(file_path).encode()).hexdigest()
        shutil.copy2(file_path, f"/tmp/.claude-edit-snapshot-{h}")


if __name__ == "__main__":
    main()
