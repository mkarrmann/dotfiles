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
  "list-windows --all --json --format "*)
    if [[ "$TREE" == flat ]]; then side=h_tiles acc=h_tiles; else side=h_tiles acc=v_accordion; fi
    printf '[{"window-id":30,"workspace":"3","app-name":"Orchest","window-title":"ws — Orchest [abc]","window-parent-container-layout":"%s","workspace-root-container-layout":"h_tiles"},
      {"window-id":31,"workspace":"3","app-name":"Google Chrome","window-title":"New Tab","window-parent-container-layout":"%s","workspace-root-container-layout":"h_tiles"},
      {"window-id":32,"workspace":"3","app-name":"Brave Browser","window-title":"T3 Code","window-parent-container-layout":"%s","workspace-root-container-layout":"h_tiles"},
      {"window-id":40,"workspace":"4","app-name":"Ghostty","window-title":"zsh","window-parent-container-layout":"v_accordion","workspace-root-container-layout":"v_accordion"}]' \
      "$side" "$acc" "$acc" ;;
  "list-windows --focused --format %{window-id}|%{workspace}") echo "$(cat "$FAKE_STATE/focused")|1" ;;
  "list-windows --focused --format %{window-id}") cat "$FAKE_STATE/focused" ;;
  "list-windows --focused --format %{window-parent-container-layout}") echo h_tiles ;;
  "focus --window-id "*) echo "$3" > "$FAKE_STATE/focused" ;;
  "list-monitors --format %{monitor-id}|%{monitor-appkit-nsscreen-screens-id}") echo "1|1" ;;
  "list-workspaces --all --format %{workspace}|%{monitor-id}") printf '3|1\n4|1\nZ|1\n' ;;
esac
exit 0
EOF
cat > "$FAKE/bin/osascript" <<'EOF'
#!/usr/bin/env bash
# The screen, and each window's width as CoreGraphics reports it: by default
# workspace 3 at its split for a 3000px monitor (sidebar 360, accordion 2640).
echo "osascript $*" >> "$FAKE_STATE/calls"
[[ -n "${SCREEN_WIDTH-3000}" ]] || exit 1
echo "screen 1 ${SCREEN_WIDTH-3000}"
printf 'win %s %s\n' ${FRAMES:-30 360 31 2640 32 2640 40 3000}
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

# ── A split that is off is reset alone ───────────────────────────────
# split_reset NAME TREE [ARGS...] -- the split of workspace 3 was reset for its
# 3000px monitor (accordion 2640), by showing the workspace first (AeroSpace
# lays out, and so rescales the weights of, only a visible workspace), with
# focus given back and nothing rebuilt.
split_reset() {
  local name="$1" calls="$FAKE/$1/calls" order
  grep -Fxq 'resize --window-id 31 width 2640' "$calls" \
    || { echo "$name: the split was not set for the monitor:" >&2; cat "$calls" >&2; exit 1; }
  order=$(grep -E '^(workspace 3|resize --window-id 31 )' "$calls" | cut -d' ' -f1 | paste -sd' ' -)
  [[ "$order" == "workspace resize" ]] \
    || { echo "$name: the workspace was not shown before its split was set: $order" >&2; exit 1; }
  grep -Fxq 'focus --window-id 99' "$calls" \
    || { echo "$name: focus was not given back" >&2; exit 1; }
  if grep -Eq "$REARRANGING" "$calls"; then
    echo "$name: resetting the split moved windows or rebuilt the tree:" >&2
    grep -E "$REARRANGING" "$calls" >&2
    exit 1
  fi
}
# left_alone NAME -- the run moved no focus, window or split.
left_alone() {
  if grep -Eq "$MUTATING" "$FAKE/$1/calls"; then
    echo "$1: changed the layout:" >&2
    grep -E "$MUTATING" "$FAKE/$1/calls" >&2
    exit 1
  fi
}

# Last laid out on a 1728px laptop screen, then the external monitor came back:
# the same windows and tree, and the same monitor width as before the unplug.
out=$(FRAMES="30 300 31 1728 32 1728" arrange unplugged tiled)
grep -Fq '[3] same windows, but the split is off; resetting it' <<< "$out" \
  || { echo "a workspace last laid out on another monitor was not re-split: $out" >&2; exit 1; }
split_reset unplugged

# Laid out on this monitor, but not at the split (a manual resize, or the
# workspace that was on screen when the monitor came back).
out=$(FRAMES="30 952 31 2048 32 2048" arrange resized tiled)
grep -Fq '[3] same windows, but the split is off; resetting it' <<< "$out" \
  || { echo "a workspace off its split was not re-split: $out" >&2; exit 1; }
split_reset resized

# Within a few points of the split is the split.
out=$(FRAMES="30 362 31 2638 32 2638" arrange rounding tiled)
grep -Fq '[3] already arranged, skipping' <<< "$out" \
  || { echo "a split a point or two off was reset: $out" >&2; exit 1; }
left_alone rounding

# Frames that can't be read are no reason to touch the layout.
out=$(SCREEN_WIDTH="" arrange unreadable tiled)
grep -Fq '[3] already arranged, skipping' <<< "$out" \
  || { echo "unreadable frames did not leave the workspace alone: $out" >&2; exit 1; }
left_alone unreadable
out=$(FRAMES="31 1728 32 1728" arrange no-sidebar-frame tiled)
grep -Fq '[3] already arranged, skipping' <<< "$out" \
  || { echo "a sidebar with no frame did not leave the workspace alone: $out" >&2; exit 1; }
left_alone no-sidebar-frame

# ── --splits: only the splits, from one listing ──────────────────────
out=$(arrange splits-right tiled --splits 4)
[[ -z "$out" ]] || { echo "--splits printed something for splits that are right: $out" >&2; exit 1; }
left_alone splits-right
out=$(FRAMES="30 300 31 1728 32 1728 40 3000" arrange splits-off tiled --splits 4)
[[ "$out" == '  [3] the split is off; resetting it' ]] \
  || { echo "--splits did not reset only workspace 3's split: $out" >&2; exit 1; }
split_reset splits-off
out=$(FRAMES="30 300 31 1728 32 1728" arrange splits-flat flat --splits)
grep -Fq '[3] not in its sidebar | accordion shape; leaving it to a full pass' <<< "$out" \
  || { echo "--splits did not leave a flattened tree alone: $out" >&2; exit 1; }
left_alone splits-flat
if grep -Eq '^list-windows --workspace ' "$FAKE/splits-off/calls"; then
  echo "--splits listed windows a workspace at a time" >&2
  exit 1
fi

cp "$FAKE/marker" "$FAKE/markers/ws-3"
arrange forced tiled --force >/dev/null
grep -Eq '^resize ' "$FAKE/forced/calls" \
  || { echo "--force did not re-enforce the layout of an unchanged workspace" >&2; exit 1; }

echo "arrange-workspaces tests passed"
