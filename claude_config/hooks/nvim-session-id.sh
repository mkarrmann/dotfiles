#!/bin/bash
# SessionStart hook: push the Claude session ID into the parent Neovim instance.
# $NVIM is set automatically when running inside a Neovim terminal.
[ -z "$NVIM" ] && cat > /dev/null && exit 0

sid=$(cat | jq -r '.session_id // empty')
[ -z "$sid" ] && exit 0

nvim --server "$NVIM" --remote-expr "execute('let g:claude_session_id = \"$sid\"')" > /dev/null 2>&1
