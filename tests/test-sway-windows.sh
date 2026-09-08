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
#   * Chrome is an XWayland client: app_id is null and the identity lives in
#     window_properties.class as "Google-chrome" (capital G). A matcher
#     "simplified" to app_id only silently stops finding Chrome at all.
#   * ghostty accepts an invalid --class and falls back to its default, which
#     would collapse every managed terminal onto one app_id.
#   * A slot must never steal a window another slot already claimed.
#   * Omnigent's update overlay must never satisfy a slot.
#   * The Omnigent desktop entry carries --disable-features=WaylandFractionalScaleV1;
#     losing it means the GUI dies with SIGTRAP on a fresh profile.
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
check "workspace 1 orders terminal, chrome, omnigent" \
  "10 11 12" "$(ordered_ids "$SNAP" 1 | tr '\n' ' ' | sed 's/ $//')"
check "unmanaged windows are excluded from the layout" \
  "" "$(ordered_ids "$SNAP" 1 | grep -x 14 || true)"

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

# The real entry must keep carrying the fractional-scale workaround; without it
# a first launch on a scaled output dies with SIGTRAP.
real=$(desktop_entry_exec omnigent-desktop-electron.desktop 2>/dev/null || true)
if [[ -z "$real" ]]; then
  echo "  skip: Omnigent desktop entry not installed on this host"
elif [[ "$real" == *--disable-features=WaylandFractionalScaleV1* ]]; then
  pass "the installed Omnigent entry keeps the fractional-scale workaround"
else
  fail "the installed Omnigent entry lost --disable-features=WaylandFractionalScaleV1: $real"
fi

echo
if [[ "$failures" -gt 0 ]]; then
  echo "$failures test(s) failed." >&2
  exit 1
fi
echo "All sway window tests passed."
