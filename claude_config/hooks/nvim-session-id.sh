#!/bin/bash
# SessionStart hook: push the Claude session ID into the parent Neovim instance
# and write a per-tab file for multi-session disambiguation.
# $NVIM is set automatically when running inside a Neovim terminal.
[ -z "$NVIM" ] && cat > /dev/null && exit 0

sid=$(cat | jq -r '.session_id // empty')
[ -z "$sid" ] && exit 0

nvim --server "$NVIM" --remote-expr "execute('let g:claude_session_id = \"$sid\"')" > /dev/null 2>&1

if [ -n "$NVIM_TAB_HANDLE" ]; then
  mkdir -p ~/.claude/agent-manager/pids
  echo "$sid" > ~/.claude/agent-manager/pids/tab-${NVIM_TAB_HANDLE}
fi
