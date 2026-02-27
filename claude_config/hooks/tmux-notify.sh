#!/bin/bash
# Claude Code hook: sets per-window @claude_state in tmux.
#
# States (rendered by window-status-format in .tmux.conf):
#   "⚙"  = Claude is actively working
#   "!"  = Claude needs user input (question, permission)
#   "✓"  = Claude finished its turn, user hasn't viewed the window
#   "~"  = Claude finished, user has viewed the window but hasn't acted
#   ""   = No active Claude session
#
# The tmux pane-focus-in hook (in .tmux.conf) handles ✓ → ~ on view.
# Stop and Notification ring the terminal bell so tmux flags the window.

[ -z "$TMUX" ] && exit 0

INPUT=$(cat)
EVENT=$(echo "$INPUT" | jq -r '.hook_event_name // empty' 2>/dev/null)

set_state() {
    tmux set-option -wq -t "$TMUX_PANE" @claude_state "$1" 2>/dev/null
}

ring_bell() {
    local tty
    tty=$(tmux display-message -t "$TMUX_PANE" -p '#{pane_tty}' 2>/dev/null)
    [ -n "$tty" ] && printf '\a' > "$tty"
}

echo "$INPUT" >> /tmp/claude-hook-debug.log

case "$EVENT" in
    UserPromptSubmit|SessionStart)
        set_state "⚙"
        ;;
    Notification)
        set_state "!"
        ring_bell
        ;;
    Stop)
        # Don't downgrade ! to ✓ — Claude still needs input
        CURRENT=$(tmux show-options -wqv -t "$TMUX_PANE" @claude_state 2>/dev/null)
        [ "$CURRENT" = "!" ] && exit 0
        set_state "✓"
        ring_bell
        ;;
    SessionEnd)
        set_state ""
        ;;
esac

exit 0
