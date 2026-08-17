#!/usr/bin/env bash
# Tests the startup-windows half of the workspace-11 window-identity contract:
# reserve_ws11_chrome must record the exact window ids it claimed, and those ids
# must survive the trip through arrange-workspaces into arrange-ws11. Without
# that handoff the arranger re-derives the windows from '^Calendar\b' and can
# land on a different Chrome window that happens to show a calendar.
#
# The functions under test are extracted from the real script so the assertions
# run against the shipped source rather than a copy of its logic.
set -uo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
SRC="$ROOT/bin-macos/startup-windows"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

failures=0
fail() { echo "  FAIL: $*" >&2; failures=$((failures + 1)); }

# Pull a top-level function definition out of the source file verbatim.
extract_fn() {
  awk -v fn="$1" '
    index($0, fn "() {") == 1 { p = 1 }
    p { print }
    p && $0 == "}" { exit }
  ' "$SRC"
}

for fn in find_chrome_by_title chrome_id_is_claimed claim_chrome_window reserve_ws11_chrome; do
  body=$(extract_fn "$fn")
  [[ -n "$body" ]] || { echo "could not extract $fn from $SRC" >&2; exit 1; }
  eval "$body"
done

# Shadow the real CLI for the whole run. A lookup that reaches the live
# AeroSpace instead of the fixture would silently assert against this machine's
# actual windows, which is how a lookup ignoring its snapshot argument could
# appear to pass.
: > "$TMP/aerospace.log"
aerospace() { printf '%s\n' "aerospace $*" >> "$TMP/aerospace.log"; }

# Globals the extracted functions close over, mirroring the real script.
CHROME_APP="Google Chrome"
CHROME_INVALID_TITLE_PATTERN="^Who.s using Chrome\\?$"
WS11_WORKSPACE=11
WS11_CAL_URL="https://www.internalfb.com/calendar"
WS11_CHAT_URL="https://chat.google.com/u/0/app/home"
WS11_CAL_TITLE_RE='^Calendar\b'
WS11_CHAT_TITLE_RE='^Chat\b'
WS11_CAL_WINDOW_ID=""
WS11_CHAT_WINDOW_ID=""
claimed_chrome_ids=""
chrome_restore_settled=true

# The state the reported failure ran into: the reserved Calendar window (102) on
# ws 11, and ws 2's Chrome window (162) navigated to a calendar so it carries an
# identical title. AeroSpace enumerates 162 first here, which is what made a
# `| first` lookup return the wrong one.
SNAPSHOT='[
  {"window-id":162,"workspace":"2","app-name":"Google Chrome","window-title":"Calendar - Google Chrome - Matt (Meta)"},
  {"window-id":102,"workspace":"11","app-name":"Google Chrome","window-title":"Calendar - Google Chrome - Matt (Meta)"},
  {"window-id":122,"workspace":"11","app-name":"Google Chrome","window-title":"Chat - Google Chrome - Matt (Meta)"},
  {"window-id":77,"workspace":"1","app-name":"Google Chrome","window-title":"Who'"'"'s using Chrome?"}
]'

echo "1. find_chrome_by_title prefers the workspace-11 window"
got=$(find_chrome_by_title "$WS11_CAL_TITLE_RE" "$SNAPSHOT")
[[ "$got" == "102" ]] || fail "picked $got, want 102"

echo "2. find_chrome_by_title ignores the profile-picker window"
got=$(find_chrome_by_title '^Who' "$SNAPSHOT")
[[ -z "$got" ]] || fail "returned invalid window $got"

echo "3. equal titles off workspace 11 resolve to the lowest id, repeatably"
OFF_WS='[
  {"window-id":300,"workspace":"Z","app-name":"Google Chrome","window-title":"Calendar - x"},
  {"window-id":200,"workspace":"Z","app-name":"Google Chrome","window-title":"Calendar - x"}
]'
for run in 1 2 3; do
  got=$(find_chrome_by_title "$WS11_CAL_TITLE_RE" "$OFF_WS")
  [[ "$got" == "200" ]] || fail "run $run picked $got, want 200"
done

echo "4. reserve_ws11_chrome records the ids it claimed"
snapshot_windows() { printf '%s' "$SNAPSHOT"; }
wait_for_window_settle() { return 0; }
: > "$TMP/aerospace.log"
reserve_ws11_chrome > "$TMP/reserve.log" 2>&1
[[ "$WS11_CAL_WINDOW_ID" == "102" ]] || fail "WS11_CAL_WINDOW_ID=$WS11_CAL_WINDOW_ID, want 102"
[[ "$WS11_CHAT_WINDOW_ID" == "122" ]] || fail "WS11_CHAT_WINDOW_ID=$WS11_CHAT_WINDOW_ID, want 122"
grep -q 'move-node-to-workspace --window-id 102 11' "$TMP/aerospace.log" \
  || fail "did not move the reserved Calendar window to workspace 11"
grep -q 'move-node-to-workspace --window-id 162' "$TMP/aerospace.log" \
  && fail "touched ws2's Calendar window 162"

echo "5. the call site hands both reserved ids to the arranger"
grep -q 'WS11_CAL_WINDOW_ID="\$WS11_CAL_WINDOW_ID" \\' "$SRC" \
  || fail "startup-windows does not export WS11_CAL_WINDOW_ID to arrange-workspaces"
grep -q 'WS11_CHAT_WINDOW_ID="\$WS11_CHAT_WINDOW_ID" \\' "$SRC" \
  || fail "startup-windows does not export WS11_CHAT_WINDOW_ID to arrange-workspaces"

echo "6. arrange-workspaces propagates the ids through to arrange-ws11"
mkdir -p "$TMP/bin"
cp "$ROOT/bin-macos/arrange-workspaces" "$TMP/bin/"
cat > "$TMP/bin/arrange-ws11" <<'EOF'
#!/usr/bin/env bash
echo "cal=${WS11_CAL_WINDOW_ID:-unset} chat=${WS11_CHAT_WINDOW_ID:-unset} args=$*"
EOF
cat > "$TMP/bin/aerospace" <<'EOF'
#!/usr/bin/env bash
exit 0
EOF
chmod +x "$TMP/bin/arrange-ws11" "$TMP/bin/aerospace"
out=$(WS11_CAL_WINDOW_ID=102 WS11_CHAT_WINDOW_ID=122 \
  ARRANGEMENT_LOCK_DIR="$TMP/lock" PATH="$TMP/bin:/usr/bin:/bin" \
  /bin/bash "$TMP/bin/arrange-workspaces" --ensure-windows 11 2>&1)
grep -q 'cal=102 chat=122 args=--ensure-windows' <<< "$out" \
  || fail "arrange-ws11 did not receive the ids; got: $out"

if [[ "$failures" -ne 0 ]]; then
  echo "startup-windows ws11 tests: $failures failure(s)" >&2
  exit 1
fi
echo "startup-windows ws11 tests passed"
