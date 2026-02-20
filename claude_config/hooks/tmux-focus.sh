#!/bin/bash
# Tmux pane-focus-in hook: downgrades ✓ (unread) → ~ (seen).
# Installed via .tmux.conf.local:
#   set-hook -g pane-focus-in "run-shell 'bash ~/.claude/hooks/tmux-focus.sh #{pane_id}'"

PANE_ID="$1"
[ -z "$PANE_ID" ] && exit 0

STATE=$(tmux show-options -wqv -t "$PANE_ID" @claude_state 2>/dev/null)
if [ "$STATE" = "✓" ]; then
    tmux set-option -wq -t "$PANE_ID" @claude_state "~" 2>/dev/null
fi
