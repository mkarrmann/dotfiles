#!/bin/bash
# Claude Code hook: sets per-window @claude_state in tmux.
#
# States (rendered by window-status-format in .tmux.conf):
#   "⟳"  = Claude is working
#   "!"  = Claude needs user input (question, permission)
#   "✓"  = Claude finished its turn, user hasn't viewed the window
#   "~"  = Claude finished, user has viewed the window but hasn't acted
#   ""   = No active Claude session / idle
#
# The tmux pane-focus-in hook (in .tmux.conf) handles ✓ → ~ on view.

[ -z "$TMUX" ] && exit 0

INPUT=$(cat)
EVENT=$(echo "$INPUT" | jq -r '.hook_event_name // empty' 2>/dev/null)

get_state() {
    tmux show-options -wqv @claude_state 2>/dev/null || echo ""
}

set_state() {
    tmux set-option -wq @claude_state "$1" 2>/dev/null
}

case "$EVENT" in
    Notification)
        set_state "!"
        ;;
    Stop)
        CURRENT=$(get_state)
        # Don't downgrade ! to ✓ — Claude still needs input
        [ "$CURRENT" = "!" ] && exit 0
        set_state "✓"
        ;;
    UserPromptSubmit)
        set_state "⟳"
        ;;
    SessionStart)
        set_state "⟳"
        ;;
    SessionEnd)
        set_state ""
        ;;
esac

exit 0
