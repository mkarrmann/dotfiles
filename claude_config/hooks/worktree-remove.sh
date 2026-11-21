#!/usr/bin/env bash
# Claude Code WorktreeRemove hook, registered by sync.sh on work machines; the
# inverse of worktree-create.sh. Reads {"worktree_path": ...} on stdin. Both
# removals refuse a worktree with uncommitted changes, which then stays on disk
# for Matt (`wt rm --force`, `git worktree remove --force`).
set -euo pipefail

path=$(jq -r '.worktree_path // empty')
if [[ -z "$path" ]]; then
  echo "WorktreeRemove input has no worktree_path" >&2
  exit 1
fi

case "$path" in
  "$HOME"/wt/*) "$HOME/bin/wt" rm "$path" >&2 ;;
  *) git -C "$path" worktree remove "$path" >&2 ;;
esac
