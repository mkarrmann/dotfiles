#!/usr/bin/env bash
# Claude Code WorktreeCreate hook, registered by sync.sh on work machines.
#
# Reads {"name": ..., "cwd": ...} on stdin and prints only the new worktree's
# absolute path on stdout; everything else goes to stderr.
#   - fbsource: `wt add <name> --snapshot --from <session checkout>`, so the
#     worktree is ~/wt/<name> (where ~/.localrc gives it its own Maven build
#     roots) and starts from the session's own commit and uncommitted changes.
#     A session at a workspace root (~/checkoutN) uses that workspace's fbsource.
#   - Other git repositories: Claude Code's default, a git worktree at
#     <repo>/.claude/worktrees/<name> on branch worktree-<name>.
#   - Anything else, configerator included, fails: wt serves fbsource only.
set -euo pipefail

input=$(cat)
name=$(jq -r '.name // empty' <<<"$input")
cwd=$(jq -r '.cwd // empty' <<<"$input")
cwd=${cwd:-$PWD}

if [[ ! "$name" =~ ^[A-Za-z0-9_-]+$ ]]; then
  echo "worktree name must match [A-Za-z0-9_-]+, got '$name'" >&2
  exit 1
fi

if git_root=$(git -C "$cwd" rev-parse --show-toplevel 2>/dev/null); then
  dest="$git_root/.claude/worktrees/$name"
  git -C "$git_root" worktree add -b "worktree-$name" "$dest" HEAD >&2
  echo "$dest"
  exit 0
fi

if [[ -d "$cwd/fbsource" ]] && ! sl --cwd "$cwd" root >/dev/null 2>&1; then
  cwd="$cwd/fbsource"
fi
"$HOME/bin/wt" add "$name" --snapshot --from "$cwd" >&2
echo "$HOME/wt/$name"
