#!/bin/bash
# tmux-resurrect post-restore-all hook.
# Creates a timestamp flag so nvim knows a restore just happened and it
# should consume the session manifest.

RESURRECT_DIR="$HOME/.claude/agent-manager/resurrect"
mkdir -p "$RESURRECT_DIR"
date +%s > "${RESURRECT_DIR}/.restore-ts"
