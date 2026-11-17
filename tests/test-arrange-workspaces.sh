#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

mkdir -p "$TMP/bin"

cat > "$TMP/bin/aerospace" <<'EOF'
#!/usr/bin/env bash
case "${1:-}" in
  list-workspaces)
    [[ "${AEROSPACE_FAIL_DISCOVERY:-false}" == true ]] && exit 1
    printf '[]\n'
    ;;
esac
EOF

cat > "$TMP/bin/jq" <<'EOF'
#!/usr/bin/env bash
cat >/dev/null
EOF

chmod +x "$TMP/bin/aerospace" "$TMP/bin/jq"

output=$(ARRANGEMENT_LOCK_DIR="$TMP/lock" \
  PATH="$TMP/bin:/usr/bin:/bin" \
  /bin/bash "$ROOT/bin-macos/arrange-workspaces")

grep -Fx 'Arranging workspace layouts...' <<< "$output" >/dev/null
grep -Fx 'No workspaces to arrange.' <<< "$output" >/dev/null

if error=$(AEROSPACE_FAIL_DISCOVERY=true \
    ARRANGEMENT_LOCK_DIR="$TMP/failure-lock" \
    PATH="$TMP/bin:/usr/bin:/bin" \
    /bin/bash "$ROOT/bin-macos/arrange-workspaces" 2>&1); then
  echo "workspace discovery failure unexpectedly succeeded" >&2
  exit 1
fi
grep -Fx 'ERROR: failed to discover AeroSpace workspaces' <<< "$error" >/dev/null

# ── An unchanged workspace is skipped by reading alone ───────────────
# Workspace 3: an Orchest sidebar (30) and two windows (31, 32) the stub's tree
# holds as tiles | accordion, or, with TREE=flat, all in one h_tiles root.
FAKE="$TMP/fake"
mkdir -p "$FAKE/bin"
cat > "$FAKE/bin/aerospace" <<'EOF'
#!/usr/bin/env bash
echo "$*" >> "$FAKE_STATE/calls"
case "$*" in
  "list-windows --workspace 3 --json")
    printf '%s' '[{"window-id":30,"app-name":"Orchest","window-title":"ws — Orchest [abc]"},
      {"window-id":31,"app-name":"Google Chrome","window-title":"New Tab"},
      {"window-id":32,"app-name":"Brave Browser","window-title":"T3 Code"}]' ;;
  "list-windows --workspace 3 --format %{window-id}|%{window-parent-container-layout}")
    if [[ "$TREE" == flat ]]; then printf '30|h_tiles\n31|h_tiles\n32|h_tiles\n'
    else printf '30|h_tiles\n31|v_accordion\n32|v_accordion\n'; fi ;;
  "list-windows --workspace 3 --format %{workspace-root-container-layout}") echo h_tiles ;;
  "list-windows --focused --format %{window-id}|%{workspace}") echo "$(cat "$FAKE_STATE/focused")|1" ;;
  "list-windows --focused --format %{window-id}") cat "$FAKE_STATE/focused" ;;
  "list-windows --focused --format %{window-parent-container-layout}") echo h_tiles ;;
  "focus --window-id "*) echo "$3" > "$FAKE_STATE/focused" ;;
  "list-monitors --format %{monitor-id}|%{monitor-appkit-nsscreen-screens-id}") echo "1|1" ;;
  "list-workspaces --all --format %{workspace}|%{monitor-id}") printf '3|1\nZ|1\n' ;;
esac
exit 0
EOF
cat > "$FAKE/bin/osascript" <<'EOF'
#!/usr/bin/env bash
echo "osascript $*" >> "$FAKE_STATE/calls"
[[ -n "${SCREEN_WIDTH-3000}" ]] || exit 1
echo "1 ${SCREEN_WIDTH-3000}"
EOF
chmod +x "$FAKE/bin/aerospace" "$FAKE/bin/osascript"

# What moves focus, windows or the tree.
MUTATING='^(focus|move|move-node-to-workspace|join-with|layout|resize|flatten-workspace-tree|workspace) |^flatten-workspace-tree$'
# What rebuilds the tree or moves windows.
REARRANGING='^(move|move-node-to-workspace|join-with|layout|flatten-workspace-tree) |^flatten-workspace-tree$'
arrange() { # STATE_NAME TREE [ARGS...] -> output; its aerospace calls in $FAKE/STATE_NAME/calls
  local state="$FAKE/$1" tree="$2"
  shift 2
  mkdir -p "$state"
  : > "$state/calls"
  echo 99 > "$state/focused"
  FAKE_STATE="$state" TREE="$tree" ARRANGE_MARKER_DIR="$FAKE/markers" ARRANGE_ATTEMPTS=1 \
    ARRANGEMENT_LOCK_DIR="$FAKE/lock" PATH="$FAKE/bin:$PATH" \
    /bin/bash "$ROOT/bin-macos/arrange-workspaces" "$@" 3 2>&1 || true
}
mkdir -p "$FAKE/markers"
printf '30:Orchest\n31:Google Chrome\n32:Brave Browser\n' | sort | md5 -q > "$FAKE/markers/ws-3"
cp "$FAKE/markers/ws-3" "$FAKE/marker"
# The split was last set on a 3000px monitor, the stub's default width.
echo 3000 > "$FAKE/markers/ws-3.split"

out=$(arrange skip tiled)
grep -Fq '[3] already arranged, skipping' <<< "$out" \
  || { echo "an unchanged, arranged workspace was not skipped: $out" >&2; exit 1; }
if grep -Eq "$MUTATING" "$FAKE/skip/calls"; then
  echo "skipping an unchanged workspace moved focus, windows or the tree:" >&2
  grep -E "$MUTATING" "$FAKE/skip/calls" >&2
  exit 1
fi

out=$(arrange flattened flat)
grep -Fq '[3] same windows, but the layout changed; rearranging' <<< "$out" \
  || { echo "a workspace with the same windows but a flattened tree was skipped: $out" >&2; exit 1; }
grep -Fxq 'flatten-workspace-tree' "$FAKE/flattened/calls" \
  || { echo "a flattened workspace was not rearranged" >&2; exit 1; }
grep -Fxq 'focus --window-id 99' "$FAKE/flattened/calls" \
  || { echo "focus was not given back after rearranging" >&2; exit 1; }

# ── A monitor change resets the split alone ──────────────────────────
# Same windows and tree, but workspace 3 is now on a 4000px monitor: the split
# is set for it (88% of 4000) without rebuilding the tree, and a run after that
# leaves it alone. The workspace is shown first, as AeroSpace lays out (and so
# rescales the weights of) only a visible workspace, and focus is given back.
cp "$FAKE/marker" "$FAKE/markers/ws-3"
echo 3000 > "$FAKE/markers/ws-3.split"
out=$(SCREEN_WIDTH=4000 arrange monitor tiled)
grep -Fq '[3] same windows, but its monitor changed; resetting the split' <<< "$out" \
  || { echo "a workspace whose monitor changed was not re-split: $out" >&2; exit 1; }
grep -Fxq 'resize --window-id 31 width 3520' "$FAKE/monitor/calls" \
  || { echo "the split was not set for the new monitor's width:" >&2; cat "$FAKE/monitor/calls" >&2; exit 1; }
check_order=$(grep -nE '^(workspace 3|resize --window-id 31 )' "$FAKE/monitor/calls" | cut -d: -f2- | cut -d' ' -f1 | paste -sd' ' -)
[[ "$check_order" == "workspace resize" ]] \
  || { echo "the workspace was not shown before its split was set: $check_order" >&2; exit 1; }
grep -Fxq 'focus --window-id 99' "$FAKE/monitor/calls" \
  || { echo "focus was not given back after re-splitting" >&2; exit 1; }
if grep -Eq "$REARRANGING" "$FAKE/monitor/calls"; then
  echo "re-splitting after a monitor change moved windows or rebuilt the tree:" >&2
  grep -E "$REARRANGING" "$FAKE/monitor/calls" >&2
  exit 1
fi
out=$(SCREEN_WIDTH=4000 arrange monitor-again tiled)
grep -Fq '[3] already arranged, skipping' <<< "$out" \
  || { echo "a workspace already split for its monitor was not skipped: $out" >&2; exit 1; }
if grep -Eq "$MUTATING" "$FAKE/monitor-again/calls"; then
  echo "a run after the re-split changed the layout again:" >&2
  grep -E "$MUTATING" "$FAKE/monitor-again/calls" >&2
  exit 1
fi

# A marker from before splits were recorded resets the split once.
rm -f "$FAKE/markers/ws-3.split"
arrange unrecorded tiled >/dev/null
grep -Fxq 'resize --window-id 31 width 2640' "$FAKE/unrecorded/calls" \
  || { echo "a workspace with no recorded split was not split:" >&2; cat "$FAKE/unrecorded/calls" >&2; exit 1; }

# A width that can't be read is no reason to touch the layout.
rm -f "$FAKE/markers/ws-3.split"
out=$(SCREEN_WIDTH="" arrange unreadable tiled)
grep -Fq '[3] already arranged, skipping' <<< "$out" \
  || { echo "an unreadable monitor width did not leave the workspace alone: $out" >&2; exit 1; }
if grep -Eq "$MUTATING" "$FAKE/unreadable/calls"; then
  echo "an unreadable monitor width changed the layout:" >&2
  grep -E "$MUTATING" "$FAKE/unreadable/calls" >&2
  exit 1
fi

cp "$FAKE/marker" "$FAKE/markers/ws-3"
arrange forced tiled --force >/dev/null
grep -Eq '^resize ' "$FAKE/forced/calls" \
  || { echo "--force did not re-enforce the layout of an unchanged workspace" >&2; exit 1; }

echo "arrange-workspaces tests passed"
