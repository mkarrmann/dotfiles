#!/usr/bin/env bash
# Regression tests for the sway window orchestration (bin-linux/).
#
# These run against the shipped library, not a copy of its logic, and use
# fixture snapshots in the exact shape `swaymsg -t get_tree` produces -- so a
# change that breaks window identity fails here rather than at next login.
#
# Every case below is a bug that actually happened while building this, or a
# property the design depends on:
#
#   * Under XWayland Chrome's app_id is null and the identity lives in
#     window_properties.class as "Google-chrome" (capital G); natively it is
#     the app_id. A matcher "simplified" to one field silently stops finding
#     Chrome on the other backend.
#   * ghostty accepts an invalid --class and falls back to its default, which
#     would collapse every managed terminal onto one app_id.
#   * A slot must never steal a window another slot already claimed.
#   * Omnigent's update overlay must never satisfy a slot.
#   * The Omnigent desktop entry carries --disable-features=WaylandFractionalScaleV1
#     and --ozone-platform=x11; losing either means the GUI dies with SIGTRAP
#     (on a fresh profile, or on the first parent resize, respectively). It also
#     carries --remote-debugging-port=0, which nvim-show-resolver depends on.
#   * A t3 row declares a T3 Code server for bin/t3-ensure through
#     --print-layout, and a window on it. T3 Code windows are Chrome --app
#     windows that sway cannot tell apart, so their identity is the Chrome
#     profile on the browser's command line: a dialog of the same browser, a
#     profile whose name merely starts the same, or a window of an exited
#     process must never pass for one, and no Chrome slot may take one.
#   * bin/t3-window-scope (shared with the Mac) rewrites T3 Code's localStorage
#     UI state: it must keep every key it does not own, put the window's
#     project first, clear the sidebar scope so every project is listed, and
#     never take a page of another origin (a port that merely starts the same)
#     or one still pairing. The Mac names a devserver's project ~/REL, which
#     must resolve to exactly one live project or fail, and its pages want
#     Cmd where Linux pages want Ctrl.
set -uo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
LIB="$ROOT/bin-linux/sway-windows-lib.sh"
ARRANGE="$ROOT/bin-linux/arrange-workspaces"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

failures=0
pass() { echo "  ok: $*"; }
fail() { echo "  FAIL: $*" >&2; failures=$((failures + 1)); }
check() { # check DESC EXPECTED ACTUAL
  if [[ "$2" == "$3" ]]; then pass "$1"; else fail "$1 (expected '$2', got '$3')"; fi
}

# shellcheck source=../bin-linux/sway-windows-lib.sh
source "$LIB"

# Pull a top-level function out of a script so the assertions run against the
# shipped source rather than a re-implementation. Same technique as
# tests/test-startup-windows-ws11.sh.
extract_fn() {
  awk -v fn="$2" '
    index($0, fn "() {") == 1 { p = 1 }
    p { print }
    p && $0 == "}" { exit }
  ' "$1"
}
body=$(extract_fn "$ARRANGE" ordered_ids)
[[ -n "$body" ]] || { echo "could not extract ordered_ids from $ARRANGE" >&2; exit 1; }
eval "$body"

# A snapshot in the shape sway produces: Chrome under XWayland (null app_id,
# class "Google-chrome"), Omnigent plus its update overlay, a managed terminal,
# a stray window, and a window already claimed by another workspace.
SNAP=$(cat <<'JSON'
[
  {"id":10,"ws":"1","app_id":"sway-ws.term.local","class":null,"title":"nvim","marks":[],"pid":1},
  {"id":11,"ws":"1","app_id":null,"class":"Google-chrome","title":"New Tab - Google Chrome","marks":["sw:1:chrome"],"pid":2},
  {"id":12,"ws":"1","app_id":"omnigent","class":null,"title":"Some conversation","marks":["sw:1:omnigent"],"pid":3},
  {"id":13,"ws":"1","app_id":"omnigent","class":null,"title":"Omnigent Update","marks":[],"pid":4},
  {"id":14,"ws":"1","app_id":"org.gnome.Calculator","class":null,"title":"Calculator","marks":[],"pid":5},
  {"id":15,"ws":"2","app_id":null,"class":"Google-chrome","title":"Docs","marks":[],"pid":6},
  {"id":16,"ws":"2","app_id":null,"class":"Google-chrome","title":"Who's using Chrome?","marks":[],"pid":7},
  {"id":17,"ws":"Z","app_id":"omnigent","class":null,"title":"Parked","marks":[],"pid":8}
]
JSON
)

echo "== app matchers =="
check "Chrome is found via its XWayland class, not app_id" \
  "11 15 16" "$(window_ids_matching "$SNAP" "$CHROME_MATCH_JQ" "" | tr '\n' ' ' | sed 's/ $//')"
check "the Chrome profile picker is excluded by title" \
  "11 15" "$(window_ids_matching "$SNAP" "$CHROME_MATCH_JQ" "$CHROME_INVALID_TITLE_RE" | tr '\n' ' ' | sed 's/ $//')"
check "Omnigent matches, minus the update overlay" \
  "12 17" "$(window_ids_matching "$SNAP" "$OMNIGENT_MATCH_JQ" "$OMNIGENT_INVALID_TITLE_RE" | tr '\n' ' ' | sed 's/ $//')"

echo "== slot claiming =="
check "an unclaimed Chrome window is offered" \
  "15" "$(unclaimed_window_id "$SNAP" "$CHROME_MATCH_JQ" "$CHROME_INVALID_TITLE_RE")"
check "a window another workspace claimed is never offered" \
  "17" "$(unclaimed_window_id "$SNAP" "$OMNIGENT_MATCH_JQ" "$OMNIGENT_INVALID_TITLE_RE")"
check "the update overlay is never offered as a slot" \
  "" "$(unclaimed_window_id "$SNAP" '(.title == "Omnigent Update")' "$OMNIGENT_INVALID_TITLE_RE")"
check "the connection loader is never offered as a slot" \
  "" "$(unclaimed_window_id '[{"id":20,"ws":"1","app_id":null,"class":"omnigent","title":"Opening Omnigent","marks":[],"pid":9}]' \
          "$OMNIGENT_MATCH_JQ" "$OMNIGENT_INVALID_TITLE_RE")"
check "id lookup by mark" "12" "$(id_with_mark "$SNAP" "sw:1:omnigent")"
check "id lookup by app_id" "10" "$(id_with_app_id "$SNAP" "sway-ws.term.local")"
check "workspace lookup" "2" "$(workspace_of "$SNAP" 15)"

# The desktop entry declares StartupWMClass=Omnigent, but the live Wayland
# window reports app_id "omnigent" (verified against a running app). A
# case-sensitive matcher anchored on the capitalised name finds nothing at all,
# which would leave every Omnigent slot permanently empty.
CASE_SNAP=$(cat <<'JSON'
[
  {"id":1,"ws":"1","app_id":"omnigent","class":null,"title":"wayland","marks":[],"pid":1},
  {"id":2,"ws":"1","app_id":"Omnigent","class":null,"title":"capitalised","marks":[],"pid":2},
  {"id":3,"ws":"1","app_id":null,"class":"Omnigent","title":"xwayland","marks":[],"pid":3}
]
JSON
)
check "Omnigent matches in either case, on app_id or class" \
  "1 2 3" "$(window_ids_matching "$CASE_SNAP" "$OMNIGENT_MATCH_JQ" "" | tr '\n' ' ' | sed 's/ $//')"

echo "== dashboard identity lookup =="
check "matches a native Wayland pane by app_id" "12" "$(id_with_app_identity "$SNAP" omnigent)"
check "matches an XWayland pane by class, case-insensitively" "11" "$(id_with_app_identity "$SNAP" google-chrome)"
check "is anchored: a prefix does not match a longer id" "" "$(id_with_app_identity "$SNAP" omni)"
# A dashboard pane must never take a window a standard workspace has claimed:
# the same app can legitimately fill both kinds of slot.
check "unclaimed lookup skips a window another slot holds" \
  "15" "$(unclaimed_id_with_app_identity "$SNAP" google-chrome)"
check "unclaimed lookup returns nothing when every match is claimed" \
  "" "$(unclaimed_id_with_app_identity '[{"id":5,"app_id":"obsidian","class":null,"marks":["sw:9:obsidian"]}]' obsidian)"

echo "== new-window detection (the shipped wait_for_new_window, canned snapshots) =="
# A fresh session has NO window of a given app yet, so the before-set is
# empty. The first implementation used `awk 'NR==FNR{...}' before current`,
# which treats the whole second file as the first when the first is empty:
# nothing was ever reported new, and every Chrome and Omnigent slot failed on
# first login. Drive the real function rather than a copy of its arithmetic.
FAKE_SNAP=""
snapshot_windows() { printf '%s' "$FAKE_SNAP"; }
c37='{"id":37,"ws":"1","app_id":null,"class":"Google-chrome","title":"Docs","marks":[],"pid":1}'
c38='{"id":38,"ws":"1","app_id":null,"class":"Google-chrome","title":"Mail","marks":[],"pid":2}'
c99='{"id":99,"ws":"1","app_id":null,"class":"Google-chrome","title":"a","marks":[],"pid":4}'
c100='{"id":100,"ws":"1","app_id":null,"class":"Google-chrome","title":"b","marks":[],"pid":5}'
picker='{"id":39,"ws":"1","app_id":null,"class":"Google-chrome","title":"Who'"'"'s using Chrome?","marks":[],"pid":3}'
FAKE_SNAP="[$c37]"
check "an empty before-set reports the first window as new" \
  "37" "$(wait_for_new_window "$CHROME_MATCH_JQ" "" "" 2)"
FAKE_SNAP="[$c37,$c38]"
check "a non-empty before-set reports only the addition" \
  "38" "$(wait_for_new_window "$CHROME_MATCH_JQ" "37" "" 2)"
FAKE_SNAP="[$c37]"
wait_for_new_window "$CHROME_MATCH_JQ" "37" "" 1 >/dev/null \
  && fail "reported a new window when none appeared" \
  || pass "returns nonzero when nothing new appears before the timeout"
FAKE_SNAP="[$c37,$picker]"
wait_for_new_window "$CHROME_MATCH_JQ" "37" "$CHROME_INVALID_TITLE_RE" 1 >/dev/null \
  && fail "a profile picker was reported as the new window" \
  || pass "a profile picker is never reported as the new window"
FAKE_SNAP="[$c37,$c99,$c100]"
check "picks the lowest new id numerically, not lexicographically" \
  "99" "$(wait_for_new_window "$CHROME_MATCH_JQ" "37" "" 2)"
unset -f snapshot_windows
# shellcheck source=../bin-linux/sway-windows-lib.sh
source "$LIB"

echo "== IPC failure is reported, not swallowed =="
# `if cmd; then ...; fi; status=$?` captures the status of the if itself,
# which is 0 when no branch ran. Every failed swaymsg -- timeouts included --
# therefore looked like a success: the preflight passed against a dead
# compositor and snapshot_windows returned an empty list that made every slot
# look vacant, which is a recipe for launching a duplicate of everything.
if command -v swaymsg >/dev/null 2>&1; then
  SWAYSOCK="$TMP/no-such.sock" sway_ipc -t get_version >/dev/null 2>&1 \
    && fail "sway_ipc returned 0 against a dead socket" || pass "sway_ipc fails against a dead socket"
  SWAYSOCK="$TMP/no-such.sock" snapshot_windows >/dev/null 2>&1 \
    && fail "snapshot_windows returned 0 against a dead socket" \
    || pass "snapshot_windows fails rather than returning an empty window list"
  SWAYSOCK="$TMP/no-such.sock" sway_cmd "workspace 1" >/dev/null 2>&1 \
    && fail "sway_cmd returned 0 against a dead socket" || pass "sway_cmd fails against a dead socket"
else
  echo "  skip: swaymsg not installed"
fi

echo "== startup lock =="
# The previous mkdir-based lock had a stale-lock path a second invocation
# could take between the first's mkdir and its pid write, so both ran. flock
# has no such window and needs no recovery path.
if command -v swaymsg >/dev/null 2>&1 && command -v flock >/dev/null 2>&1; then
  mkdir -p "$TMP/run"
  lock="$TMP/run/sway-startup-windows.no-such.sock.lock"
  ( flock 9; sleep 3 ) 9>"$lock" &
  holder=$!
  sleep 0.3
  out=$(XDG_RUNTIME_DIR="$TMP/run" XDG_CONFIG_HOME="$TMP" SWAYSOCK="$TMP/no-such.sock" \
        "$ROOT/bin-linux/startup-windows" 2>&1); rc=$?
  check "a second invocation exits 0 without doing anything" "0" "$rc"
  [[ "$out" == *"already running"* ]] && pass "and says why" || fail "unexpected output: $out"
  wait "$holder" 2>/dev/null
  out=$(XDG_RUNTIME_DIR="$TMP/run" XDG_CONFIG_HOME="$TMP" SWAYSOCK="$TMP/no-such.sock" \
        "$ROOT/bin-linux/startup-windows" 2>&1); rc=$?
  check "with the lock free, a dead compositor fails the preflight" "1" "$rc"
else
  echo "  skip: swaymsg or flock not installed"
fi

echo "== stray sweep =="
KEEP_MARKS=$'sw:1:chrome\nsw:1:omnigent'
KEEP_APPS='sway-ws.term.local'
check "sweeps only windows the current table does not claim" \
  "13 14 15 16" "$(stray_window_ids "$SNAP" '^(1|2)$' "$KEEP_MARKS" "$KEEP_APPS" | sort -n | tr '\n' ' ' | sed 's/ $//')"
check "the overflow workspace is never swept" \
  "" "$(stray_window_ids "$SNAP" '^(1|2)$' "$KEEP_MARKS" "$KEEP_APPS" | grep -x 17 || true)"
# Claims are checked against the table as it is now, not against "any sw:
# mark ever": a slot removed from the table must lose its window, and a
# renamed terminal slot must lose the old terminal.
check "a slot deleted from the table retires its window" \
  "11" "$(stray_window_ids "$SNAP" '^1$' $'sw:1:omnigent' "$KEEP_APPS" | grep -x 11 || true)"
check "a renamed terminal slot retires the old terminal" \
  "10" "$(stray_window_ids "$SNAP" '^1$' "$KEEP_MARKS" 'sway-ws.term.renamed' | grep -x 10 || true)"
check "lists the stale claims to strip from a swept window" \
  "sw:1:chrome" "$(sw_marks_of "$SNAP" 11)"

echo "== new-window wait is bounded even when the tree cannot be read =="
# `snapshot=$(snapshot_windows) || continue` used to jump past the deadline
# check, so a compositor that stopped answering mid-wait spun here forever
# while holding the startup lock. Run it in a subshell under timeout: a hang
# shows up as exit 124 rather than as a hung test suite.
timeout 5 bash -c "source '$LIB'; snapshot_windows() { return 1; }; wait_for_new_window '\$CHROME_MATCH_JQ' '' '' 1" >/dev/null 2>&1
rc=$?
check "gives up after the deadline (exit 1, not a hang)" "1" "$rc"

echo "== layout order =="
check "workspace 1 orders terminal, chrome, omnigent, then whatever else is tiled" \
  "10 11 12 14" "$(ordered_ids "$SNAP" 1 | tr '\n' ' ' | sed 's/ $//')"
check "the update overlay never joins a layout" \
  "" "$(ordered_ids "$SNAP" 1 | grep -x 13 || true)"
check "only the ranked slots when asked for them" \
  "10 11 12" "$(ordered_ids "$SNAP" 1 3 | tr '\n' ' ' | sed 's/ $//')"

# The sidebar is the mark holder, else the lowest-id Orchest-titled window;
# it is never a stack member. An unmanaged floating window (a dialog) is left
# floating; a managed one that was floated is put back in the stack.
SIDE_SNAP=$(cat <<'JSON'
[
  {"id":20,"ws":"1","app_id":"sway-ws.term.local","class":null,"title":"nvim","marks":[],"pid":1,"floating":false},
  {"id":21,"ws":"1","app_id":null,"class":"Google-chrome","title":"Docs","marks":["sw:1:chrome"],"pid":2,"floating":false},
  {"id":22,"ws":"1","app_id":null,"class":"orchest","title":"Workspace — Orchest [abcd1234]","marks":[],"pid":3,"floating":false},
  {"id":23,"ws":"1","app_id":null,"class":"orchest","title":"Fix the build — Orchest [0000ffff]","marks":["sw:1:orchest"],"pid":3,"floating":false},
  {"id":24,"ws":"1","app_id":"org.gnome.Calculator","class":null,"title":"Calculator","marks":[],"pid":5,"floating":true},
  {"id":25,"ws":"1","app_id":"omnigent","class":null,"title":"chat","marks":["sw:1:omnigent"],"pid":6,"floating":true}
]
JSON
)
check "the marked Orchest window is the sidebar even when another has a lower id" \
  "23" "$(sidebar_id_of "$SIDE_SNAP" 1)"
check "without a mark, the lowest-id Orchest-titled window is the sidebar" \
  "22" "$(sidebar_id_of "$(jq -c 'map(.marks -= ["sw:1:orchest"])' <<< "$SIDE_SNAP")" 1)"
check "the sidebar is excluded; a spare Orchest window and a floated managed slot are stacked" \
  "20 21 25 22" "$(ordered_ids "$SIDE_SNAP" 1 | tr '\n' ' ' | sed 's/ $//')"
check "an unmanaged floating window is left floating" \
  "" "$(ordered_ids "$SIDE_SNAP" 1 | grep -x 24 || true)"
DASH_SNAP='[{"id":40,"ws":"9","app_id":"obsidian","class":null,"title":"vault","marks":["sw:9:obsidian"],"pid":1,"floating":false},
            {"id":39,"ws":"9","app_id":null,"class":"@orchest/desktop","title":"Orchest","marks":["sw:9:orchest-overview"],"pid":2,"floating":false}]'
check "dashboard panes follow DASHBOARD_ORDER, not window id" \
  "40 39" "$(DASHBOARD_ORDER="obsidian orchest-overview" ordered_ids "$DASH_SNAP" 9 | tr '\n' ' ' | sed 's/ $//')"
check "without an order, dashboard panes fall back to window id" \
  "39 40" "$(DASHBOARD_ORDER="" ordered_ids "$DASH_SNAP" 9 | tr '\n' ' ' | sed 's/ $//')"
check "the sweep spares Orchest windows by title, marked or not" \
  "20 21 24 25" "$(stray_window_ids "$SIDE_SNAP" '^1$' "" "" "$ORCHEST_SIDEBAR_TITLE_RE" | sort -n | tr '\n' ' ' | sed 's/ $//')"

echo "== ghostty app_id validity =="
# ghostty silently falls back to com.mitchellh.ghostty on an invalid class, so
# every value the workspace table can produce has to be checked up front.
for good in sway-ws.term.local sway-ws.term.omnigent-repo a.b dev.mkarrmann.x.y; do
  valid_term_app_id "$good" && pass "valid: $good" || fail "should be valid: $good"
done
for bad in obsidian google-chrome "" .leading trailing. 1bad.x "has space.x"; do
  valid_term_app_id "$bad" && fail "should be invalid: '$bad'" || pass "invalid: '${bad}'"
done
# Guard the real table: every slot it declares must produce a usable app_id.
prefix="$TERM_APP_ID_PREFIX"
slots=$(grep -oE '^\s*"[^|]+\|term\|[^|]+' "$ROOT/bin-linux/startup-windows" | awk -F'|' '{print $3}')
[[ -n "$slots" ]] || fail "could not read any term slots from the workspace table"
while IFS= read -r slot; do
  [[ -n "$slot" ]] || continue
  valid_term_app_id "${prefix}${slot}" \
    && pass "table slot '$slot' -> ${prefix}${slot}" \
    || fail "table slot '$slot' yields an invalid app id: ${prefix}${slot}"
done <<< "$slots"

echo "== desktop entry resolution =="
mkdir -p "$TMP/home/applications" "$TMP/sys/applications"
cat > "$TMP/sys/applications/x.desktop" <<EOF
[Desktop Entry]
Exec=/usr/bin/packaged %U
EOF
cat > "$TMP/home/applications/x.desktop" <<EOF
[Desktop Entry]
Exec=/usr/bin/packaged --disable-features=WaylandFractionalScaleV1 %U
EOF
out=$(XDG_DATA_HOME="$TMP/home" XDG_DATA_DIRS="$TMP/sys" desktop_entry_exec x.desktop)
check "the user override wins over the packaged entry, field codes stripped" \
  "/usr/bin/packaged --disable-features=WaylandFractionalScaleV1" "$out"
out=$(XDG_DATA_HOME="$TMP/missing" XDG_DATA_DIRS="$TMP/sys" desktop_entry_exec x.desktop)
check "falls back to the packaged entry" "/usr/bin/packaged" "$out"

# The real entry must keep carrying both SIGTRAP workarounds (see the HACK note
# in omnigent_config/omnigent-desktop-electron.desktop for when each can go) and
# the debug port that bin-linux/nvim-show-resolver reads window URLs from.
real=$(desktop_entry_exec omnigent-desktop-electron.desktop 2>/dev/null || true)
if [[ -z "$real" ]]; then
  echo "  skip: Omnigent desktop entry not installed on this host"
else
  for flag in --disable-features=WaylandFractionalScaleV1 --ozone-platform=x11 --remote-debugging-port=0; do
    if [[ "$real" == *"$flag"* ]]; then
      pass "the installed Omnigent entry keeps $flag"
    else
      fail "the installed Omnigent entry lost $flag: $real"
    fi
  done
fi

echo "== New Window injector follows the window's backend =="
# An XWayland client (the launcher override's --ozone-platform=x11) carries its
# identity in .class with a null app_id; a native Wayland client the reverse.
# wtype's virtual keyboard loses its modifiers through XWayland (verified
# 2026-09-13: Ctrl+Shift+N arrived as a typed "N"), so the X11 window must get
# XTEST via xdotool, and a Wayland window must not.
INJ=$(cat <<'JSON'
[
  {"id":1,"ws":"1","app_id":null,"class":"omnigent","title":"Omnigent","marks":[],"pid":1},
  {"id":2,"ws":"1","app_id":"omnigent","class":null,"title":"Omnigent","marks":[],"pid":2}
]
JSON
)
check "XWayland Omnigent -> xdotool" "xdotool" "$(new_window_injector "$INJ" 1)"
check "native Wayland Omnigent -> wtype" "wtype" "$(new_window_injector "$INJ" 2)"

echo "== New Window accelerator version gate =="
# 0.10.0's Server > New Window item had no accelerator; it exists from 0.13.0.
# The gate reads the installed package so an old install skips the keystroke
# path outright instead of timing out once per slot.
STARTUP="$ROOT/bin-linux/startup-windows"
body=$(extract_fn "$STARTUP" omnigent_new_window_supported)
[[ -n "$body" ]] || { echo "could not extract omnigent_new_window_supported from $STARTUP" >&2; exit 1; }
eval "$body"
mkdir -p "$TMP/dpkg-bin"
cat > "$TMP/dpkg-bin/dpkg-query" <<'EOF'
#!/bin/bash
[[ -n "${FAKE_OMNIGENT_VERSION:-}" ]] || exit 1
printf '%s' "$FAKE_OMNIGENT_VERSION"
EOF
chmod +x "$TMP/dpkg-bin/dpkg-query"
gate() { # MIN INSTALLED -> "yes"/"no"
  ( export PATH="$TMP/dpkg-bin:$PATH" FAKE_OMNIGENT_VERSION="$2"
    OMNIGENT_NEW_WINDOW_MIN_VERSION="$1"
    omnigent_new_window_supported && echo yes || echo no )
}
check "0.10.0 is below the 0.13.0 minimum" "no" "$(gate 0.13.0 0.10.0)"
check "0.13.0 meets the minimum" "yes" "$(gate 0.13.0 0.13.0)"
check "a later release meets the minimum" "yes" "$(gate 0.13.0 0.14.1)"
check "an empty minimum disables the gate" "yes" "$(gate "" 0.10.0)"
check "no Debian package installed: let the keystroke try" "yes" "$(gate 0.13.0 "")"
check "the shipped minimum is the accelerator's first release" "0.13.0" \
  "$(grep -m1 '^OMNIGENT_NEW_WINDOW_MIN_VERSION=' "$STARTUP" | cut -d= -f2)"

echo "== t3 rows in --print-layout =="
# A private HOME and config dir: the expected workdirs are spelled from it, and
# no machine-local layout override is read.
mkdir -p "$TMP/t3-home" "$TMP/t3-config"
print_layout() { env -u SWAYSOCK HOME="$TMP/t3-home" XDG_CONFIG_HOME="$TMP/t3-config" "$@" "$STARTUP" --print-layout; }
layout=$(print_layout) || fail "--print-layout exited non-zero"
check "workspaces 1-6 each declare a window, named after their terminal slot" \
  '["local","dotfiles","orchest","omnigent-repo","presto","gatech"]' "$(jq -c '[.t3[].slot]' <<< "$layout")"
check "all of them on the one server" '"desktop" 3780 [3780]' \
  "$(jq -c '.t3Server, .t3Port, ([.t3[].port] | unique)' <<< "$layout" | paste -sd' ')"
check "a row's project is its terminal's workdir, ~ expanded" \
  "{\"workspace\":\"3\",\"slot\":\"orchest\",\"workdir\":\"$TMP/t3-home/dev/orchest\",\"port\":3780}" \
  "$(jq -c '.t3[] | select(.slot == "orchest")' <<< "$layout")"
printf '%s\n' 'WORKSPACES=("7|term|elsewhere|~/x|true" "7|t3")' 'T3_SERVER=other' 'T3_PORT=4000' > "$TMP/t3-port.sh"
check "a layout override can rename and move the server" '"other" 4000 4000' \
  "$(print_layout SWAY_WINDOWS_LAYOUT="$TMP/t3-port.sh" 2>/dev/null | jq -c '.t3Server, .t3Port, .t3[0].port' | paste -sd' ')"
printf '%s\n' 'WORKSPACES=("7|term|elsewhere|~/x|true" "8|t3")' > "$TMP/t3-orphan.sh"
out=$(print_layout SWAY_WINDOWS_LAYOUT="$TMP/t3-orphan.sh" 2>&1 >/dev/null); rc=$?
check "a t3 row on a workspace with no terminal fails the whole layout" "1" "$rc"
[[ "$out" == *"workspace 8 has no term row"* ]] && pass "and says why" || fail "unexpected output: $out"

echo "== T3 Code window identity: the browser's profile =="
# A fake /proc. Pid 100 is a T3 Code profile's browser with two --app windows
# (one claimed), a dialog, and a regular window a link opened; 200 runs a
# profile whose name merely starts the same; 300 is the everyday Chrome; 400
# the first profile's browser under XWayland; 500 Orchest; 600 has exited.
#
# A Chrome browser's cmdline is ONE space-joined string with a trailing NUL:
# Chrome rewrites its process title (verified against the live browser). A
# reader that only splits on NUL never finds --user-data-dir in it, which is
# exactly how a stub with an ordinary argv passed while real Chrome was never
# recognised. 400 keeps an ordinary NUL-separated argv, so both forms are read.
PROC="$TMP/proc"
fake_proc() { # PID ARG... -> $PROC/PID/cmdline, NUL-separated like the kernel's
  local pid="$1"; shift
  mkdir -p "$PROC/$pid"; printf '%s\0' "$@" > "$PROC/$pid/cmdline"
}
fake_chrome_proc() { # PID ARG... -> the cmdline Chrome leaves after retitling itself
  local pid="$1"; shift
  mkdir -p "$PROC/$pid"; printf '%s\0' "$*" > "$PROC/$pid/cmdline"
}
PROFILES=/home/me/.t3-workspace-chrome
fake_chrome_proc 100 /opt/google/chrome/chrome --no-first-run "--user-data-dir=$PROFILES/orchest" "--app=http://orchest.t3.localhost:3783/pair#token=X"
fake_chrome_proc 200 /opt/google/chrome/chrome "--user-data-dir=$PROFILES/orchest2" "--app=http://orchest2.t3.localhost:3787/"
fake_chrome_proc 300 /opt/google/chrome/chrome --profile-directory=Default --new-window
fake_proc 400 /opt/google/chrome/chrome --ozone-platform=x11 "--user-data-dir=$PROFILES/orchest"
fake_proc 500 /home/me/dev/orchest/node_modules/electron/dist/electron /home/me/dev/orchest/apps/desktop
T3_SNAP=$(cat <<'JSON'
[
  {"id":30,"ws":"3","app_id":"chrome-orchest.t3.localhost__pair-Default","class":null,"title":"T3 Code (Beta)","marks":["sw:3:t3"],"pid":100},
  {"id":31,"ws":"3","app_id":"chrome-orchest.t3.localhost__-Default","class":null,"title":"T3 Code (Beta)","marks":[],"pid":100},
  {"id":32,"ws":"3","app_id":null,"class":null,"title":"","marks":[],"pid":100},
  {"id":33,"ws":"3","app_id":"google-chrome","class":null,"title":"Pull request - Google Chrome","marks":[],"pid":100},
  {"id":34,"ws":"7","app_id":"chrome-orchest2.t3.localhost__-Default","class":null,"title":"T3 Code (Beta)","marks":[],"pid":200},
  {"id":35,"ws":"3","app_id":"google-chrome","class":null,"title":"Docs - Google Chrome","marks":["sw:3:chrome"],"pid":300},
  {"id":36,"ws":"Z","app_id":null,"class":"Google-chrome","title":"T3 Code (Beta)","marks":[],"pid":400},
  {"id":37,"ws":"3","app_id":null,"class":"@orchest/desktop","title":"x — Orchest [abcd1234]","marks":["sw:3:orchest"],"pid":500},
  {"id":38,"ws":"3","app_id":"chrome-orchest.t3.localhost__-Default","class":null,"title":"T3 Code (Beta)","marks":[],"pid":600},
  {"id":39,"ws":"Z","app_id":"google-chrome","class":null,"title":"New Tab - Google Chrome","marks":[],"pid":300}
]
JSON
)
SWAY_WINDOWS_PROC="$PROC"
check "a profile's --app windows, native and XWayland; not its dialog or browser window, a sibling profile's, or an exited process's" \
  "30 31 36" "$(chrome_profile_window_ids "$T3_SNAP" "$PROFILES/orchest" | tr '\n' ' ' | sed 's/ $//')"
check "unclaimed: only those holding no sw: mark" \
  "31 36" "$(chrome_profile_window_ids "$T3_SNAP" "$PROFILES/orchest" unclaimed | tr '\n' ' ' | sed 's/ $//')"
check "the sibling profile is matched exactly too" "34" "$(chrome_profile_window_ids "$T3_SNAP" "$PROFILES/orchest2")"
check "the profile is read from Chrome's retitled command line" "$PROFILES/orchest" "$(chrome_user_data_dir 100)"
check "and from an ordinary argv" "$PROFILES/orchest" "$(chrome_user_data_dir 400)"
chrome_user_data_dir 300 >/dev/null && fail "a browser without --user-data-dir reported one" || pass "a browser without --user-data-dir has none"
out=$(chrome_user_data_dir 600 2>&1) && fail "an exited process reported a profile" || check "an exited process has none, silently" "" "$out"

STARTUP="$ROOT/bin-linux/startup-windows"
for fn in chrome_slot_match reconcile_entry current_claim_marks current_term_app_ids managed_workspaces t3_rows \
          t3_pairing_token t3_wait_for_server; do
  body=$(extract_fn "$STARTUP" "$fn")
  [[ -n "$body" ]] || { echo "could not extract $fn from $STARTUP" >&2; exit 1; }
  eval "$body"
done
T3_CHROME_PROFILES_DIR="$PROFILES"
SNAPSHOT="$T3_SNAP"
check "without the T3 Code exclusion a Chrome slot would adopt the profile's browser window" \
  "33" "$(unclaimed_window_id "$SNAPSHOT" "$CHROME_MATCH_JQ" "$CHROME_INVALID_TITLE_RE")"
check "a Chrome slot adopts only a window of a browser outside the T3 Code profiles" \
  "39" "$(unclaimed_window_id "$SNAPSHOT" "$(chrome_slot_match)" "$CHROME_INVALID_TITLE_RE")"
check "and never counts one as its new window" \
  "35 39" "$(window_ids_matching "$SNAPSHOT" "$(chrome_slot_match)" "$CHROME_INVALID_TITLE_RE" | tr '\n' ' ' | sed 's/ $//')"
check "without T3 Code windows the matcher is CHROME_MATCH_JQ itself" \
  "$CHROME_MATCH_JQ" "$(SNAPSHOT=$(jq -c 'map(select(.pid != 100 and .pid != 400))' <<< "$T3_SNAP") chrome_slot_match)"
unset SWAY_WINDOWS_PROC
# shellcheck source=../bin-linux/sway-windows-lib.sh
source "$LIB"

echo "== an Orchest sidebar is told from the orchest slot's T3 Code window (sway-auto-stack) =="
# sway-auto-stack never stacks a window whose process runs from the Orchest
# checkout. The orchest slot's T3 Code browser has "orchest" in its profile
# path and URL, and must still be stacked like any other window.
body=$(extract_fn "$ROOT/bin-linux/sway-auto-stack" is_orchest_process)
[[ -n "$body" ]] || { echo "could not extract is_orchest_process" >&2; exit 1; }
eval "$body"
ORCHEST_DIR="$HOME/dev/orchest"
bash -c 'sleep 30; true' --no-first-run "--user-data-dir=$HOME/.t3-workspace-chrome/orchest" \
  "--app=http://orchest.t3.localhost:3783/pair#token=X" &
t3_pid=$!
bash -c 'sleep 30; true' "$ORCHEST_DIR/node_modules/electron/dist/electron" "$ORCHEST_DIR/apps/desktop" &
orchest_pid=$!
sleep 0.2
is_orchest_process "$t3_pid" && fail "the orchest slot's T3 Code browser was taken for Orchest" \
  || pass "the orchest slot's T3 Code browser is not Orchest"
is_orchest_process "$orchest_pid" && pass "an Orchest process still is" || fail "an Orchest process was not recognised"
kill "$t3_pid" "$orchest_pid" 2>/dev/null; wait "$t3_pid" "$orchest_pid" 2>/dev/null

echo "== t3 rows in the reconcile pass =="
# The shipped dispatch and claim helpers, with the per-type reconcilers
# replaced by recorders.
reconcile_term() { echo "term $1 $2"; }
reconcile_marked() { echo "marked $1 $2"; }
reconcile_t3() { echo "t3 $*"; }
warn() { echo "warn $*"; }
refresh() { :; }
SNAPSHOT='[]'
WORKSPACES=("2|term|dotfiles|~/dotfiles|true" "2|chrome" "2|omnigent" "2|t3" "3|term|orchest|~/dev/orchest|true" "3|t3")
T3_PORT=3780
T3_ROWS=$(t3_rows)
check "a t3 row reconciles its workspace's slot and project on the server's port" \
  "t3 2 dotfiles $HOME/dotfiles 3780" "$(reconcile_entry "2|t3")"
check "each row its own slot and project" "t3 3 orchest $HOME/dev/orchest 3780" "$(reconcile_entry "3|t3")"
check "the rest of its workspace still reconciles" $'term 2 dotfiles\nmarked 2 chrome' \
  "$(reconcile_entry "2|term|dotfiles|~/dotfiles|true"; reconcile_entry "2|chrome")"
DASHBOARD_WS=9; DASHBOARD_PANES=("obsidian|x")
claims=$(current_claim_marks)
grep -qx 'sw:2:t3' <<< "$claims" && grep -qx 'sw:3:t3' <<< "$claims" \
  && pass "the sweep keeps each t3 row's claim" || fail "t3 claims missing from: $(tr '\n' ' ' <<< "$claims")"
check "and adds no terminal or managed workspace" $'sway-ws.term.dotfiles\nsway-ws.term.orchest\n2\n3' \
  "$(current_term_app_ids; managed_workspaces)"

echo "== a T3 Code window is the last of the stack's slots =="
# Rank 3 in arrange-workspaces: after the terminal, Chrome and Omnigent, ahead
# of anything unclaimed, even when it is the oldest window there.
RANK_SNAP=$(cat <<'JSON'
[
  {"id":50,"ws":"2","app_id":"chrome-dotfiles.t3.localhost__pair-Default","class":null,"title":"T3 Code (Beta)","marks":["sw:2:t3"],"pid":1,"floating":false},
  {"id":51,"ws":"2","app_id":"sway-ws.term.dotfiles","class":null,"title":"nvim","marks":[],"pid":2,"floating":false},
  {"id":52,"ws":"2","app_id":"google-chrome","class":null,"title":"Docs","marks":["sw:2:chrome"],"pid":3,"floating":false},
  {"id":53,"ws":"2","app_id":"omnigent","class":null,"title":"chat","marks":["sw:2:omnigent"],"pid":4,"floating":false},
  {"id":54,"ws":"2","app_id":"org.gnome.Calculator","class":null,"title":"Calculator","marks":[],"pid":5,"floating":false}
]
JSON
)
check "terminal, Chrome, Omnigent, T3 Code, then the rest" \
  "51 52 53 50 54" "$(DASHBOARD_ORDER="obsidian orchest-overview" ordered_ids "$RANK_SNAP" 2 | tr '\n' ' ' | sed 's/ $//')"
check "T3 Code is among the slots whose order is enforced" \
  "51 52 53 50" "$(DASHBOARD_ORDER="obsidian orchest-overview" ordered_ids "$RANK_SNAP" 2 3 | tr '\n' ' ' | sed 's/ $//')"

echo "== T3 Code pairing tokens =="
cat > "$TMP/t3-pair" <<EOF
#!/bin/bash
echo "\$T3CODE_HOME \$*" > "$TMP/t3-pair.args"
[[ -z "\${FAKE_PAIR_FAIL:-}" ]] || { echo "No running T3 Code server found." >&2; exit 1; }
cat <<OUT
Pairing with matt-System-Version (http://127.0.0.1:3782).

  █▀▀▀▀▀█ █▄█▀██▀▄ ███▀█ █▀ █▀▀▀▀▀█
  █ ███ █ █ ▀ ██▄ █▄▀█▀▄█▀▄ █ ███ █

Pairing URL: http://127.0.0.1:3782/pair#token=QL7XTEQS8LKL
App link: t3code://pair?pairingUrl=http%3A%2F%2F127.0.0.1%3A3782%2Fpair%23token%3DQL7XTEQS8LKL
Token: \${FAKE_PAIR_TOKEN:-QL7XTEQS8LKL}
Expires: 2026-11-03T20:13:08.740Z

Note: This URL is only reachable from this machine.
OUT
EOF
chmod +x "$TMP/t3-pair"
T3_BIN="$TMP/t3-pair"; T3_HOMES_DIR="$TMP/t3-homes"; T3_SERVER=desktop
check "the token is read from t3 pair's output" "QL7XTEQS8LKL" "$(t3_pairing_token)"
check "minted by the server, short-lived and labelled" \
  "$TMP/t3-homes/desktop pair --ttl 5m --label sway-startup-windows" "$(cat "$TMP/t3-pair.args")"
FAKE_PAIR_FAIL=1 t3_pairing_token >/dev/null && fail "a failed t3 pair produced a token" || pass "a failed t3 pair produces none"
FAKE_PAIR_TOKEN='not a token' t3_pairing_token >/dev/null && fail "garbage was taken for a token" || pass "neither does output without a usable token"

echo "== waiting for a T3 Code server is bounded and shared =="
if command -v curl >/dev/null 2>&1 && command -v python3 >/dev/null 2>&1; then
  free_port() { python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1])'; }
  mkdir -p "$TMP/www/t3/.well-known/t3" "$TMP/www/other"
  echo '{"serverVersion":"0.1.6"}' > "$TMP/www/t3/.well-known/t3/environment"
  t3_port=$(free_port); other_port=$(free_port); closed_port=$(free_port)
  python3 -m http.server --bind 127.0.0.1 --directory "$TMP/www/t3" "$t3_port" >/dev/null 2>&1 &
  t3_server=$!
  python3 -m http.server --bind 127.0.0.1 --directory "$TMP/www/other" "$other_port" >/dev/null 2>&1 &
  other_server=$!
  for _ in $(seq 1 40); do curl -s -o /dev/null "http://127.0.0.1:$t3_port/" && curl -s -o /dev/null "http://127.0.0.1:$other_port/" && break; sleep 0.1; done
  T3_SERVER_WAIT=2; T3_SERVER_DEADLINE=""
  t3_wait_for_server "$t3_port" && pass "a server answering T3 Code's descriptor route is ready" || fail "a T3 Code server was not seen"
  check "and an answering server starts no deadline" "" "$T3_SERVER_DEADLINE"
  start=$(date +%s)
  t3_wait_for_server "$other_port" && fail "a server without the descriptor route was taken for T3 Code" \
    || pass "a server that 404s the descriptor route is not T3 Code"
  t3_wait_for_server "$closed_port" && fail "a closed port was ready" || pass "nor is a closed port"
  elapsed=$(( $(date +%s) - start ))
  (( elapsed <= 3 )) && pass "both share one ${T3_SERVER_WAIT}s deadline (${elapsed}s in all)" \
    || fail "two dead servers took ${elapsed}s; the deadline is not shared"
  kill "$t3_server" "$other_server" 2>/dev/null; wait "$t3_server" "$other_server" 2>/dev/null
else
  echo "  skip: curl or python3 not installed"
fi

echo "== the state t3-window-scope writes =="
# The pure parts of the helper, imported from the shipped file; the CDP steps
# need a real Chrome and a real server (see docs/t3code-omnigent.md).
if command -v python3 >/dev/null 2>&1; then
  mkdir -p "$TMP/devtools"
  echo '[{"type": "page", "url": "http://a.t3.localhost:3780/", "webSocketDebuggerUrl": "ws://x"}]' > "$TMP/devtools/json"
  devtools_port=$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1])')
  python3 -m http.server --bind 127.0.0.1 --directory "$TMP/devtools" "$devtools_port" >/dev/null 2>&1 &
  devtools_server=$!
  for _ in $(seq 1 40); do curl -sf -o /dev/null "http://127.0.0.1:$devtools_port/json" 2>/dev/null && break; sleep 0.1; done
  while IFS=$'\t' read -r desc expected actual; do
    check "$desc" "$expected" "$actual"
  done < <(python3 - "$ROOT/bin/t3-window-scope" "$TMP" "$devtools_port" <<'PY'
import importlib.machinery, importlib.util, json, os, socket, sys
loader = importlib.machinery.SourceFileLoader("t3_window_scope", sys.argv[1])
scope = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
loader.exec_module(scope)
tmp, live_port = sys.argv[2], sys.argv[3]

def case(desc, expected, actual):
    print(f"{desc}\t{json.dumps(expected)}\t{json.dumps(actual)}")

case("paths compare as T3 Code compares them: trimmed, no trailing slash",
     ["/home/me/dotfiles", "/", "/home/me"],
     [scope.normalized_path(p) for p in ("/home/me/dotfiles/", "/", " /home/me// ")])
case("a window with no stored state gets the project first and every project listed",
     {"projectOrder": ["legacy-project-cwd:/home/me/dotfiles"],
      "sidebarProjectScopeKeys": [], "sidebarProjectScopeKey": None},
     scope.scoped_ui_state({}, "/home/me/dotfiles"))
stored = {"projectOrder": ["env-1:/home/me/x", "legacy-project-cwd:/home/me/dotfiles"],
          "sidebarProjectScopeKeys": ["env-1:/home/me/x", "env-1:/home/me/y"], "sidebarProjectScopeKey": None,
          "threadLastVisitedAtById": {"t": "2026-11-03T00:00:00Z"}, "pullRequestMergeMethod": "squash"}
merged = scope.scoped_ui_state(stored, "/home/me/dotfiles")
case("an existing order keeps its other projects, after this one and without a duplicate",
     ["legacy-project-cwd:/home/me/dotfiles", "env-1:/home/me/x"], merged["projectOrder"])
case("a sidebar scope set earlier is cleared, so the sidebar lists every project", [[], None],
     [merged["sidebarProjectScopeKeys"], merged["sidebarProjectScopeKey"]])
case("and every key the helper does not own is kept",
     [{"t": "2026-11-03T00:00:00Z"}, "squash"], [merged["threadLastVisitedAtById"], merged["pullRequestMergeMethod"]])
case("a missing or unreadable document reads as empty, as T3 Code reads it",
     [{}, {}, {}, {"a": 1}],
     [scope.parsed_document(raw) for raw in (None, "{not json", "[1]", '{"a": 1}')])
def resolved(projects, workdir):
    try:
        return scope.project_for(projects, workdir)["id"]
    except scope.ScopeError as exc:
        return f"error: {exc}"
def project(id, root, archived=None, deleted=None):
    return {"id": id, "workspaceRoot": root, "archivedAt": archived, "deletedAt": deleted}
projects = [project("old", "/home/me/dotfiles", archived="2026-01-01"), project("other", "/home/me/dotfiles-2"),
            project("live", "/home/me/dotfiles/")]
case("the project at an absolute workdir is the live one there, not an archived one or a longer path",
     "live", resolved(projects, "/home/me/dotfiles"))
case("a workdir that is no project is an error", True, resolved(projects, "/home/me/nope").startswith("error: "))
devserver = [project("c1", "/home/u/checkout1"), project("c10", "/home/u/checkout10"),
             project("c2-old", "/home/u/checkout2", archived="2026-01-01"), project("c2", "/home/u/checkout2/"),
             project("c3", "/home/u/checkout3"), project("c3-data", "/data/users/u/checkout3"),
             project("c4", "/home/u/checkout4", deleted="2026-01-01"), project("c5", "/home/u/mycheckout5")]
case("~/REL is the one live project whose root ends in /REL, not one that only ends in REL",
     ["c1", "c2"], [resolved(devserver, "~/checkout1"), resolved(devserver, "~/checkout2")])
case("~/REL that no live project ends in is an error: none, a deleted one, a name that only ends the same",
     [True, True, True], [resolved(devserver, f"~/{rel}").startswith("error: ") for rel in ("checkout9", "checkout4", "checkout5")])
ambiguous = resolved(devserver, "~/checkout3")
case("~/REL that several live projects end in is an error naming them", [True, True, True],
     [ambiguous.startswith("error: "), "/home/u/checkout3" in ambiguous, "/data/users/u/checkout3" in ambiguous])
case("a workdir is absolute or ~/REL", [True, True, False, False, False],
     [scope.valid_workdir(w) for w in ("/home/me/dotfiles", "~/checkout1", "~/", "~", "checkout1")])
# Input.dispatchKeyEvent modifier bits: Ctrl 2, Meta 4, Shift 8.
case("chat.newLocal is pressed as the page reads mod: Cmd+Shift+N on the Mac, Ctrl+Shift+N on Linux",
     [12, 10, 12], [scope.new_local_key(p)["modifiers"] for p in ("MacIntel", "Linux x86_64", "iPad")])
origin = "http://dotfiles.t3.localhost:3780"
page = lambda url, kind="page": {"type": kind, "url": url, "webSocketDebuggerUrl": "ws://x/" + url}
case("the window's page is the one on its own origin past pairing",
     origin + "/draft/1",
     (scope.app_page([page(origin + "/pair#token=x"), page("http://dotfiles.t3.localhost:37800/"),
                      page("http://orchest.t3.localhost:3780/"), page(origin + "/sw.js", "service_worker"),
                      page(origin + "/draft/1")], origin) or {}).get("url"))
case("a page still pairing is not taken", None, scope.app_page([page(origin + "/pair")], origin))
stale = socket.socket(); stale.bind(("127.0.0.1", 0)); stale_port = stale.getsockname()[1]; stale.close()
os.makedirs(f"{tmp}/stale-profile", exist_ok=True)
open(f"{tmp}/stale-profile/DevToolsActivePort", "w").write(f"{stale_port}\n/devtools/browser/x\n")
os.makedirs(f"{tmp}/live-profile", exist_ok=True)
open(f"{tmp}/live-profile/DevToolsActivePort", "w").write(f"{live_port}\n/devtools/browser/x\n")
# A proxy in the environment, as x2pagentd leaves one on the Mac, must not
# carry the loopback DevTools listing.
os.environ.update({"http_proxy": f"http://127.0.0.1:{stale_port}", "HTTP_PROXY": f"http://127.0.0.1:{stale_port}",
                   "no_proxy": "", "NO_PROXY": ""})
case("a DevToolsActivePort left by an exited browser is not used", None, scope.debug_targets(f"{tmp}/stale-profile"))
case("a profile without one has no targets", None, scope.debug_targets(f"{tmp}/no-profile"))
case("a live one lists the browser's targets, directly even with a proxy set", ["http://a.t3.localhost:3780/"],
     [t["url"] for t in scope.debug_targets(f"{tmp}/live-profile") or []])
PY
)
  kill "$devtools_server" 2>/dev/null; wait "$devtools_server" 2>/dev/null
  rc=$(python3 "$ROOT/bin/t3-window-scope" one two >/dev/null 2>&1; echo $?)
  check "the helper rejects a call without its three arguments" "2" "$rc"
  rc=$(python3 "$ROOT/bin/t3-window-scope" "$TMP/no-profile" http://a.t3.localhost:3780 checkout1 >/dev/null 2>&1; echo $?)
  check "and one whose workdir is neither absolute nor ~/REL" "2" "$rc"
else
  echo "  skip: python3 not installed"
fi

echo
if [[ "$failures" -gt 0 ]]; then
  echo "$failures test(s) failed." >&2
  exit 1
fi
echo "All sway window tests passed."
