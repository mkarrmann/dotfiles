#!/bin/bash
# Claude Code hook: sets per-tab claude_state in the parent Neovim instance.
# $NVIM is set automatically when running inside a Neovim terminal.
# $NVIM_TAB_HANDLE is set by claude-nvim-wrapper.sh and shell functions.
#
# States (rendered in the tabline by claude-tab-state.lua):
#   "⚙"  = Claude is actively working
#   "!"  = Claude needs user input (question, permission)
#   "✓"  = Claude finished its turn, user hasn't viewed the tab
#   "~"  = Claude finished, user has viewed the tab (set by TabEnter autocmd)
#   ""   = No active Claude session

[ -z "$NVIM" ] && exit 0
[ -z "$NVIM_TAB_HANDLE" ] && exit 0

INPUT=$(cat)
EVENT=$(echo "$INPUT" | jq -r '.hook_event_name // empty' 2>/dev/null)

_nvim_lua() {
    nvim --server "$NVIM" --remote-expr "execute('lua $1')" >/dev/null 2>&1
}

set_state() {
    _nvim_lua "_G._claude_set_tab_state(${NVIM_TAB_HANDLE}, \"$1\")"
}

case "$EVENT" in
    UserPromptSubmit|SessionStart)
        set_state "⚙"
        ;;
    Notification)
        set_state "!"
        ;;
    PreToolUse)
        _nvim_lua "_G._claude_on_pretooluse(${NVIM_TAB_HANDLE})"
        ;;
    Stop)
        _nvim_lua "_G._claude_on_stop(${NVIM_TAB_HANDLE})"
        ;;
    SessionEnd)
        set_state ""
        ;;
esac

exit 0
