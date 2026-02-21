#!/bin/bash
# Claude Code wrapper for Neovim's claudecode.nvim terminal_cmd.
# Auto-names sessions from the tmux window name and renames the tmux window
# to match the agent name.

GENERIC_NAMES="nvim|bash|zsh|sh|fish|tmux|systemd"

# If no name is queued (e.g., from `cn`), derive one from the tmux window
if [ ! -f ~/.claude-next-name ] && [ -n "$TMUX" ]; then
  win_name=$(tmux display-message -p '#W' 2>/dev/null)
  if [ -n "$win_name" ] && ! echo "$win_name" | grep -qxE "$GENERIC_NAMES"; then
    echo "$win_name" > ~/.claude-next-name
  fi
fi

# Rename the tmux window to the agent name (if we have one)
if [ -n "$TMUX" ] && [ -f ~/.claude-next-name ]; then
  agent_name=$(cat ~/.claude-next-name)
  tmux rename-window "$agent_name" 2>/dev/null
fi

exec claude --dangerously-skip-permissions "$@"
