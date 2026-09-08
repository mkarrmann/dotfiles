#!/usr/bin/env bash
# End-to-end tests for the sway window orchestration (bin-linux/), run inside a
# private headless compositor. Complements tests/test-sway-windows.sh, which
# covers the pure helpers; this one exercises the actual orchestration and the
# layout postconditions the unit tests cannot see.
#
# Isolation from the live desktop session is the whole design:
#   - WLR_BACKENDS is pinned to headless and the live SWAYSOCK/WAYLAND_DISPLAY
#     are unset before the compositor starts, so it can never take the seat.
#   - Where dbus-run-session is available the whole suite re-runs itself under
#     a private session bus with a private XDG_RUNTIME_DIR, so the compositor
#     sockets, the startup lock and every ghostty stub are fully separated
#     from the live session. (GTK needs a bus to register an application id;
#     without one every stub reports app_id "GTK Application".) Otherwise the
#     real runtime dir is used: the IPC socket is still named by pid, the
#     display number allocated fresh, and the lock keyed by socket.
#   - Every "app" is a ghostty stub launched by absolute path; the real
#     Chrome/Omnigent/Obsidian desktop entries are shadowed by XDG_DATA_DIRS.
#   - Cleanup kills only PIDs read out of the test compositor's own tree.
#
# Skips (exit 0) when the compositor or a stub dependency is unavailable.
set -uo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
for dep in sway swaymsg ghostty jq flock zsh; do
  command -v "$dep" >/dev/null 2>&1 || { echo "SKIP: $dep not found"; exit 0; }
done

TMP="$(mktemp -d)"
if [[ -z "${SWAY_E2E_PRIVATE:-}" ]] && command -v dbus-run-session >/dev/null 2>&1; then
  export SWAY_E2E_PRIVATE=1
  export XDG_RUNTIME_DIR="$TMP/run"; mkdir -m 700 -p "$XDG_RUNTIME_DIR"
  dbus-run-session -- "$BASH" "${BASH_SOURCE[0]}" "$@"; rc=$?
  # Bus activation can FUSE-mount gvfs inside the private runtime dir.
  { command -v fusermount3 || command -v fusermount; } >/dev/null 2>&1 && \
    "$(command -v fusermount3 || command -v fusermount)" -u "$XDG_RUNTIME_DIR/gvfs" 2>/dev/null
  rm -rf "$TMP" 2>/dev/null; exit "$rc"
fi
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
export XDG_CONFIG_HOME="$TMP/config"
echo "runtime dir: $XDG_RUNTIME_DIR ($([[ -n "${SWAY_E2E_PRIVATE:-}" ]] && echo private bus || echo shared))"
unset WAYLAND_DISPLAY DISPLAY SWAYSOCK
SWAY_PID=""

failures=0
pass() { echo "  ok: $*"; }
fail() { echo "  FAIL: $*" >&2; failures=$((failures + 1)); }
check() { if [[ "$2" == "$3" ]]; then pass "$1"; else fail "$1 (expected '$2', got '$3')"; return 1; fi; }

tree() { swaymsg -t get_tree; }
all_pids() { tree | jq -r '[recurse(.nodes[]?,.floating_nodes[]?)|select(.pid!=null)]|.[].pid' | sort -u; }
window_count() { tree | jq '[recurse(.nodes[]?,.floating_nodes[]?)|select(.pid!=null)]|length'; }
# ws_order WS -> "app_id[marks] -> app_id[marks]" in tree order
ws_order() {
  tree | jq -r --arg ws "$1" '
    [.nodes[]|select(.name!="__i3")|.nodes[]?|select(.type=="workspace" and .name==$ws)]|first // {}
    | [recurse(.nodes[]?,.floating_nodes[]?)|select(.pid!=null)]
    | map("\(.app_id)[\(.marks|join(","))]") | join(" -> ")'
}
# largest tab group on WS (window count), 0 if none
ws_tabbed() {
  tree | jq -r --arg ws "$1" '
    [.nodes[]|.nodes[]?|select(.type=="workspace" and .name==$ws)]|first // {}
    | [recurse(.nodes[]?)|select(.layout=="tabbed")|[.nodes[]?|select(.pid!=null)]|length] | max // 0'
}
ws_layout() { tree | jq -r --arg ws "$1" '[.nodes[]|.nodes[]?|select(.type=="workspace" and .name==$ws)]|first|.layout // "none"'; }
ws_floating() { tree | jq -r --arg ws "$1" '[.nodes[]|.nodes[]?|select(.type=="workspace" and .name==$ws)]|first // {} | [.floating_nodes[]?|recurse(.nodes[]?)|select(.pid!=null)|.id]|join(",")'; }
id_by_mark() { tree | jq -r --arg m "$1" '[recurse(.nodes[]?,.floating_nodes[]?)|select(.pid!=null and (.marks|index($m)))]|first|.id // empty'; }
id_by_app() { tree | jq -r --arg a "$1" '[recurse(.nodes[]?,.floating_nodes[]?)|select(.pid!=null and .app_id==$a)]|first|.id // empty'; }
ws_of_id() { tree | jq -r --argjson i "$1" '[.nodes[]|.nodes[]?|select(.type=="workspace")|.name as $w|[recurse(.nodes[]?,.floating_nodes[]?)]|.[]|select(.id==$i)|$w]|first // empty'; }

reset_windows() {
  local pids i
  pids=$(all_pids); [[ -n "$pids" ]] && kill $pids 2>/dev/null
  for i in $(seq 1 20); do [[ "$(window_count)" == 0 ]] && break; sleep 0.3; done
  pids=$(all_pids); [[ -n "$pids" ]] && kill -9 $pids 2>/dev/null
  rm -f "$TMP/count."*
}

cleanup() {
  if [[ -n "$SWAY_PID" ]] && kill -0 "$SWAY_PID" 2>/dev/null; then
    local pids; pids=$(all_pids 2>/dev/null); [[ -n "$pids" ]] && kill -9 $pids 2>/dev/null
    swaymsg exit >/dev/null 2>&1; sleep 0.5
    kill -9 "$SWAY_PID" 2>/dev/null
  fi
  rm -rf "$TMP"
}
trap cleanup EXIT

# ── compositor ─────────────────────────────────────────────────────────
cat > "$TMP/sway.conf" <<'CONF'
output HEADLESS-1 mode 1920x1080
for_window [title="^Omnigent Update$"] floating enable, move scratchpad
CONF
WLR_BACKENDS=headless WLR_RENDERER=pixman WLR_LIBINPUT_NO_DEVICES=1 LIBGL_ALWAYS_SOFTWARE=1 \
  sway -c "$TMP/sway.conf" >"$TMP/sway.log" 2>&1 </dev/null &
SWAY_PID=$!
export SWAYSOCK="$XDG_RUNTIME_DIR/sway-ipc.$(id -u).$SWAY_PID.sock"
for _ in $(seq 1 40); do swaymsg -t get_version >/dev/null 2>&1 && break; sleep 0.25; done
swaymsg -t get_version >/dev/null 2>&1 || { echo "SKIP: headless sway did not start (see $TMP/sway.log)"; cat "$TMP/sway.log" | tail -5; exit 0; }
swaymsg exec -- "sh -c 'echo \$WAYLAND_DISPLAY > $TMP/wd'" >/dev/null
for _ in $(seq 1 20); do [[ -s "$TMP/wd" ]] && break; sleep 0.25; done
export WAYLAND_DISPLAY; WAYLAND_DISPLAY="$(cat "$TMP/wd")"
[[ -n "$WAYLAND_DISPLAY" ]] || { echo "SKIP: could not discover the test WAYLAND_DISPLAY"; exit 0; }
echo "headless sway pid=$SWAY_PID display=$WAYLAND_DISPLAY"

# ── stubs ──────────────────────────────────────────────────────────────
# Each stub is a ghostty window whose class carries the identity the real app
# would present. Chrome and Omnigent get a distinct class per launch so the
# order windows were created in is visible in assertions.
mkdir -p "$TMP/bin" "$TMP/data/applications"
stub() { # NAME CLASS_EXPR
  cat > "$TMP/bin/$1" <<EOF
#!/bin/bash
n=\$(( \$(cat "$TMP/count.$1" 2>/dev/null || echo 0) + 1 )); echo "\$n" > "$TMP/count.$1"
exec ghostty --class=$2 -e sleep 600
EOF
  chmod +x "$TMP/bin/$1"
}
stub chrome-stub    'google-chrome.stub$n'
stub chrome-fixed   'google-chrome.fixed'
stub omnigent-stub  'Omnigent.stub$n'
stub obsidian-stub  'obsidian.stub'
cat > "$TMP/bin/wtype" <<EOF
#!/bin/bash
# Stand-in for the accelerator: the stub has no menu, so emulate Ctrl+Shift+N.
setsid "$TMP/bin/omnigent-stub" >/dev/null 2>&1 &
EOF
chmod +x "$TMP/bin/wtype"
printf '[Desktop Entry]\nExec=%s %%U\nType=Application\n' "$TMP/bin/omnigent-stub" > "$TMP/data/applications/omnigent-desktop-electron.desktop"
printf '[Desktop Entry]\nExec=%s %%U\nType=Application\n' "$TMP/bin/obsidian-stub"  > "$TMP/data/applications/obsidian.desktop"

layout_main() {
  cat > "$TMP/layout.sh" <<EOF
WORKSPACES=(
  "1|term|alpha|~|sleep 600"
  "1|chrome"
  "1|omnigent"
  "2|term|beta|~|sleep 600"
  "2|chrome"
  "2|omnigent"
)
DASHBOARD_WS=${1:-9}
DASHBOARD_PANES=("obsidian.stub|__desktop_entry__ obsidian.desktop")
CHROME_CMD=("$TMP/bin/chrome-stub")
OMNIGENT_NEW_WINDOW_KEYS=()
EOF
}
layout_retired() {   # slot "1|chrome" deleted, terminal slot alpha renamed to gamma
  cat > "$TMP/layout.sh" <<EOF
WORKSPACES=(
  "1|term|gamma|~|sleep 600"
  "1|omnigent"
  "2|term|beta|~|sleep 600"
  "2|chrome"
  "2|omnigent"
)
DASHBOARD_WS=9
DASHBOARD_PANES=("obsidian.stub|__desktop_entry__ obsidian.desktop")
CHROME_CMD=("$TMP/bin/chrome-stub")
OMNIGENT_NEW_WINDOW_KEYS=()
EOF
}
layout_prefix() {    # custom terminal app_id prefix
  cat > "$TMP/layout.sh" <<EOF
TERM_APP_ID_PREFIX="custom.term."
WORKSPACES=(
  "1|term|alpha|~|sleep 600"
  "1|chrome"
  "1|omnigent"
)
DASHBOARD_WS=9
DASHBOARD_PANES=()
CHROME_CMD=("$TMP/bin/chrome-stub")
OMNIGENT_NEW_WINDOW_KEYS=()
EOF
}
layout_steal() {
  cat > "$TMP/layout.sh" <<EOF
WORKSPACES=(
  "1|term|alpha|~|sleep 600"
  "1|chrome"
)
DASHBOARD_WS=9
DASHBOARD_PANES=("google-chrome.fixed|$TMP/bin/chrome-fixed")
CHROME_CMD=("$TMP/bin/chrome-fixed")
EOF
}

run_startup() {
  PATH="$TMP/bin:$PATH" XDG_DATA_HOME="$TMP/data" XDG_DATA_DIRS="$TMP/data" \
  SWAY_WINDOWS_LAYOUT="$TMP/layout.sh" \
    "$ROOT/bin-linux/startup-windows" "$@" >"$TMP/run.log" 2>&1
  local rc=$?
  # A run that bowed out to a lock did nothing; every assertion after it
  # would be checking stale state. Treat it as its own failure.
  if grep -q "already running" "$TMP/run.log"; then
    fail "startup-windows refused to run: lock still held (leaked to a child?)"
    return 99
  fi
  return "$rc"
}
run_arrange() { "$ROOT/bin-linux/arrange-workspaces" "$@" >"$TMP/arrange.log" 2>&1; }
show_log() { sed 's/^/      | /' "$1" | tail -12; }

WS1_EXPECT='sway-ws.term.alpha[] -> google-chrome.stub1[sw:1:chrome] -> Omnigent.stub1[sw:1:omnigent]'
WS2_EXPECT='sway-ws.term.beta[] -> google-chrome.stub2[sw:2:chrome] -> Omnigent.stub2[sw:2:omnigent]'

# ── 0. dry run ─────────────────────────────────────────────────────────
echo "== --dry-run =="
layout_main
run_startup --dry-run; rc=$?
check "exits 0" "0" "$rc"
check "creates nothing" "0" "$(window_count)"
grep -q "DRY-RUN launch" "$TMP/run.log" && pass "prints the plan" || fail "no plan printed"

# ── 1. fresh build ─────────────────────────────────────────────────────
echo "== fresh build =="
run_startup; rc=$?
check "exits 0 with no warnings" "0" "$rc"; [[ "$rc" == 0 ]] || show_log "$TMP/run.log"
check "workspace 1 order and claims" "$WS1_EXPECT" "$(ws_order 1)"
check "workspace 2 order and claims" "$WS2_EXPECT" "$(ws_order 2)"
check "workspace 1 is one tab group of 3" "3" "$(ws_tabbed 1)"
check "workspace 2 is one tab group of 3" "3" "$(ws_tabbed 2)"
check "dashboard holds the pane, claimed" "obsidian.stub[sw:9:obsidian.stub]" "$(ws_order 9)"
check "dashboard is laid out side by side, not tabbed" "0" "$(ws_tabbed 9)"

# ── 2. idempotent ──────────────────────────────────────────────────────
echo "== idempotent re-run =="
before=$(window_count); run_startup; rc=$?
check "re-run exits 0" "0" "$rc"
check "re-run creates no windows" "$before" "$(window_count)"
check "workspace 1 unchanged" "$WS1_EXPECT" "$(ws_order 1)"

# ── 3. self-healing ────────────────────────────────────────────────────
echo "== self-healing =="
swaymsg "[con_id=$(id_by_mark sw:1:chrome)] move container to workspace 7" >/dev/null
swaymsg "[con_id=$(id_by_app sway-ws.term.beta)] move container to workspace 7" >/dev/null
sleep 0.5; run_startup
check "displaced claimed window returns to its workspace, in order" "$WS1_EXPECT" "$(ws_order 1)"
check "displaced terminal returns to its workspace, in order" "$WS2_EXPECT" "$(ws_order 2)"
check "nothing left on workspace 7" "" "$(ws_order 7)"

# ── 4. stray sweep ─────────────────────────────────────────────────────
echo "== stray sweep =="
swaymsg "workspace 2" >/dev/null
swaymsg exec -- "ghostty --class=intruder.app -e sleep 600" >/dev/null
for _ in $(seq 1 20); do [[ -n "$(id_by_app intruder.app)" ]] && break; sleep 0.25; done
run_startup
check "an unmanaged window on a managed workspace is swept to Z" "Z" "$(ws_of_id "$(id_by_app intruder.app)")"
check "workspace 2 intact after the sweep" "$WS2_EXPECT" "$(ws_order 2)"

# ── 5. floating repair (arranger) ──────────────────────────────────────
echo "== floating managed window =="
cid=$(id_by_mark sw:1:chrome)
swaymsg "[con_id=$cid] floating enable" >/dev/null; sleep 0.3
check "setup: the window is floating" "$cid" "$(ws_floating 1)"
# verify_workspace must reject this state on its own
extract_fn() { awk -v fn="$2" 'index($0, fn "() {") == 1 { p = 1 } p { print } p && $0 == "}" { exit }' "$1"; }
( source "$ROOT/bin-linux/sway-windows-lib.sh"
  eval "$(extract_fn "$ROOT/bin-linux/arrange-workspaces" ordered_ids)"
  eval "$(extract_fn "$ROOT/bin-linux/arrange-workspaces" verify_workspace)"
  verify_workspace 1 tabbed ) && fail "verify_workspace accepted a floating managed window" \
                              || pass "verify_workspace rejects a floating managed window"
run_arrange --dashboard-ws 9 1 2 9; rc=$?
check "arranger exits 0" "0" "$rc"; [[ "$rc" == 0 ]] || show_log "$TMP/arrange.log"
check "the window is tiled again" "" "$(ws_floating 1)"
check "and back in the tab group, in order" "$WS1_EXPECT" "$(ws_order 1)"
check "tab group is whole again" "3" "$(ws_tabbed 1)"

# ── 6. --no-dashboard ──────────────────────────────────────────────────
echo "== --no-dashboard =="
oid=$(id_by_mark sw:9:obsidian.stub)
swaymsg "[con_id=$oid] move container to workspace 2" >/dev/null; sleep 0.3
check "setup: pane moved to workspace 2" "2" "$(ws_of_id "$oid")"
run_startup --no-dashboard; rc=$?
check "exits 0" "0" "$rc"
check "the dashboard pane is left where it was" "2" "$(ws_of_id "$oid")" || show_log "$TMP/run.log"
run_startup
check "a normal run brings it back" "9" "$(ws_of_id "$oid")"

# ── 6b. slots removed or renamed retire their windows ──────────────────
echo "== retired slots =="
old_chrome=$(id_by_mark sw:1:chrome); old_term=$(id_by_app sway-ws.term.alpha)
layout_retired; run_startup; rc=$?
check "exits 0" "0" "$rc"; [[ "$rc" == 0 ]] || show_log "$TMP/run.log"
check "the deleted chrome slot's window is swept to Z" "Z" "$(ws_of_id "$old_chrome")"
check "and its stale claim is removed" "" "$(tree | jq -r --argjson i "$old_chrome" '[recurse(.nodes[]?,.floating_nodes[]?)|select(.id==$i)]|first|.marks|join(",")')"
check "the renamed terminal slot's old window is swept to Z" "Z" "$(ws_of_id "$old_term")"
check "workspace 1 holds the new terminal and its remaining slot, in order" \
  "sway-ws.term.gamma[] -> Omnigent.stub1[sw:1:omnigent]" "$(ws_order 1)"
check "workspace 2 untouched" "$WS2_EXPECT" "$(ws_order 2)"

# ── 6c. a custom terminal prefix reaches the arranger ──────────────────
echo "== TERM_APP_ID_PREFIX override =="
reset_windows; layout_prefix
run_startup; rc=$?
check "exits 0" "0" "$rc"; [[ "$rc" == 0 ]] || show_log "$TMP/run.log"
check "custom-prefix terminal is placed and ordered first" \
  "custom.term.alpha[] -> google-chrome.stub1[sw:1:chrome] -> Omnigent.stub1[sw:1:omnigent]" "$(ws_order 1)"
check "and is part of the tab group" "3" "$(ws_tabbed 1)"

# ── 7. dashboard must not steal a claimed window ───────────────────────
echo "== dashboard pane vs a claimed window of the same app =="
reset_windows; layout_steal
run_startup; rc=$?
check "exits 0" "0" "$rc"; [[ "$rc" == 0 ]] || show_log "$TMP/run.log"
w1=$(id_by_mark sw:1:chrome); w9=$(id_by_mark sw:9:google-chrome.fixed)
check "workspace 1 keeps its Chrome" "1" "$(ws_of_id "${w1:-0}")"
check "the dashboard got a window of its own" "9" "$(ws_of_id "${w9:-0}")"
[[ -n "$w1" && -n "$w9" && "$w1" != "$w9" ]] && pass "two distinct windows" || fail "expected two distinct windows (ws1=$w1 ws9=$w9)"

# ── 8. a relocated dashboard is still laid out as a dashboard ──────────
echo "== DASHBOARD_WS override =="
reset_windows; layout_main 8
run_startup; rc=$?
check "exits 0" "0" "$rc"; [[ "$rc" == 0 ]] || show_log "$TMP/run.log"
check "pane lands on workspace 8" "obsidian.stub[sw:8:obsidian.stub]" "$(ws_order 8)"
check "workspace 8 is not tabbed like a standard workspace" "0" "$(ws_tabbed 8)"
check "standard workspaces unaffected" "3" "$(ws_tabbed 1)"

echo
if [[ "$failures" -gt 0 ]]; then echo "$failures e2e test(s) failed." >&2; exit 1; fi
echo "All sway window e2e tests passed."
