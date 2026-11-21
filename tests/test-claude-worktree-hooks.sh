#!/usr/bin/env bash
set -euo pipefail

# Coverage for claude_config/hooks/worktree-{create,remove}.sh. Properties
# under test: stdout carries only the new worktree's path (Claude Code reads it
# as the path); an fbsource worktree starts from the session's own checkout,
# including from a workspace root; other git repositories keep Claude Code's
# default; and removal refuses a worktree with uncommitted changes.
#
# Stubs stand in for `sl` and `wt`, so no EdenFS checkout is created. The stub
# wt copies the origin checkout's WIP file, the observable stand-in for
# `--snapshot`; the git fallback runs against a real repository.

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
CREATE="$ROOT/claude_config/hooks/worktree-create.sh"
REMOVE="$ROOT/claude_config/hooks/worktree-remove.sh"
TMP="$(cd -- "$(mktemp -d)" && pwd -P)"
trap 'rm -rf "$TMP"' EXIT

export HOME="$TMP/home"
mkdir -p "$HOME/bin" "$TMP/bin"
export PATH="$TMP/bin:$PATH"
export GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@t GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@t

# A directory holding .sl is a Sapling checkout root.
cat >"$TMP/bin/sl" <<'EOF'
#!/usr/bin/env bash
[[ "$1" == --cwd && "$3" == root ]] || exit 2
dir="$2"
while [[ "$dir" != / ]]; do
  [[ -d "$dir/.sl" ]] && { echo "$dir"; exit 0; }
  dir="$(dirname "$dir")"
done
exit 1
EOF
cat >"$HOME/bin/wt" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
case "$1" in
  add)
    [[ "$3 $4" == "--snapshot --from" ]] || exit 2
    origin=$(sl --cwd "$5" root) || { echo "not a checkout: $5" >&2; exit 1; }
    mkdir -p "$HOME/wt/$2"
    cp "$origin/WIP" "$HOME/wt/$2/WIP"
    echo "Created $HOME/wt/$2"
    ;;
  rm)
    [[ ! -e "$2/dirty" ]] || { echo "$2 is DIRTY" >&2; exit 1; }
    rm -rf "$2"
    ;;
esac
EOF
chmod +x "$TMP/bin/sl" "$HOME/bin/wt"

for n in 1 2; do
  mkdir -p "$HOME/checkout$n/fbsource/.sl"
  echo "checkout$n" >"$HOME/checkout$n/fbsource/WIP"
done

create() { printf '{"name":"%s","cwd":"%s"}' "$1" "$2" | bash "$CREATE"; }
remove() { printf '{"worktree_path":"%s"}' "$1" | bash "$REMOVE"; }

# A session inside checkout2's fbsource gets checkout2's state, not checkout1's.
out=$(create from-checkout "$HOME/checkout2/fbsource/fbcode" 2>/dev/null)
[[ "$out" == "$HOME/wt/from-checkout" ]]
[[ "$(cat "$HOME/wt/from-checkout/WIP")" == checkout2 ]]

# A session at a workspace root uses that workspace's fbsource.
out=$(create from-root "$HOME/checkout2" 2>/dev/null)
[[ "$out" == "$HOME/wt/from-root" ]]
[[ "$(cat "$HOME/wt/from-root/WIP")" == checkout2 ]]

# A name that could escape ~/wt is refused before anything runs.
if out=$(create "../escape" "$HOME/checkout1/fbsource" 2>/dev/null); then
  echo "unsafe name accepted" >&2; exit 1
fi
[[ -z "$out" && ! -e "$HOME/escape" ]]

# Outside fbsource and outside git, creation fails.
mkdir -p "$TMP/plain"
if create plain "$TMP/plain" >/dev/null 2>&1; then
  echo "created a worktree outside any repository" >&2; exit 1
fi

# Removal under ~/wt keeps a dirty worktree and removes a clean one.
touch "$HOME/wt/from-root/dirty"
if remove "$HOME/wt/from-root" 2>/dev/null; then
  echo "removed a dirty worktree" >&2; exit 1
fi
[[ -d "$HOME/wt/from-root" ]]
remove "$HOME/wt/from-checkout" 2>/dev/null
[[ ! -e "$HOME/wt/from-checkout" ]]

# A git repository gets Claude Code's default layout, and its removal also
# keeps a dirty worktree.
repo="$TMP/repo"
git init -q "$repo"
git -C "$repo" commit -q --allow-empty -m init
out=$(create g "$repo" 2>/dev/null)
[[ "$out" == "$repo/.claude/worktrees/g" ]]
[[ "$(git -C "$out" rev-parse --abbrev-ref HEAD)" == worktree-g ]]
touch "$out/untracked"
if remove "$out" 2>/dev/null; then
  echo "removed a dirty git worktree" >&2; exit 1
fi
[[ -d "$out" ]]
rm "$out/untracked"
remove "$out" 2>/dev/null
[[ ! -e "$out" ]]

echo "ok"
