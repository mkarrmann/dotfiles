#!/bin/bash
# UserPromptSubmit hook: clear turn snapshots so the next turn starts fresh.
[ -z "$NVIM" ] && cat > /dev/null && exit 0

INPUT=$(cat)

# Resolve session ID
SID=$(echo "$INPUT" | jq -r '.session_id // empty' 2>/dev/null)
if [ -z "$SID" ] && [ -n "$NVIM_TAB_HANDLE" ]; then
  PID_FILE="$HOME/.claude/agent-manager/pids/tab-${NVIM_TAB_HANDLE}"
  [ -f "$PID_FILE" ] && SID=$(cat "$PID_FILE")
fi
[ -z "$SID" ] && exit 0

# Delete turn and per-edit snapshots for this session
rm -f /tmp/.claude-turn-snap-${SID}-* /tmp/.claude-edit-snap-${SID}-*

# Notify Lua
nvim --server "$NVIM" --remote-expr \
  "luaeval(\"require('lib.claude-diff').new_turn('${SID}')\")" > /dev/null 2>&1

exit 0
