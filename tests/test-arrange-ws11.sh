#!/usr/bin/env bash
# Regression tests for arrange-ws11 window selection.
#
# The bug these pin down: workspace 11's dashboard windows were identified only
# by a title regex. '^Calendar\b' matches any Chrome window showing a calendar,
# so arrange-ws11 could resolve a different window than the one startup-windows
# had reserved, pull that one onto ws 11 on top of the reserved one, and leave
# the managed workspace it came from with no Chrome window at all.
#
# aerospace is faked by a mutable window table so move-node-to-workspace really
# moves windows and the assertions can look at the resulting workspace layout.
set -uo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

mkdir -p "$TMP/bin"

cat > "$TMP/bin/aerospace" <<'STUB'
#!/usr/bin/env bash
# Fake aerospace over a TSV table: id \t workspace \t app \t title
WIN="$AEROSPACE_STATE/windows.tsv"
LOG="$AEROSPACE_STATE/commands.log"
FOCUSED="$AEROSPACE_STATE/focused"

printf '%s\n' "$*" >> "$LOG"

cmd="${1:-}"; shift
scope=all; fmt=""; json=false; wid=""; rest=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --all)       scope=all; shift ;;
    --focused)   scope=focused; shift ;;
    --workspace) scope="$2"; shift 2 ;;
    --json)      json=true; shift ;;
    --format)    fmt="$2"; shift 2 ;;
    --window-id) wid="$2"; shift 2 ;;
    *)           rest[${#rest[@]}]="$1"; shift ;;
  esac
done

rows() {
  case "$scope" in
    all)     cat "$WIN" ;;
    focused) awk -F'\t' -v w="$(cat "$FOCUSED" 2>/dev/null)" '$1==w' "$WIN" ;;
    *)       awk -F'\t' -v s="$scope" '$2==s' "$WIN" ;;
  esac
}

case "$cmd" in
  list-windows)
    if [[ "$json" == true ]]; then
      rows | jq -R -s 'split("\n") | map(select(length>0) | split("\t") |
        {"window-id": (.[0]|tonumber), "workspace": .[1], "app-name": .[2], "window-title": .[3]})'
      exit 0
    fi
    case "$fmt" in
      # layouts: flatten_flat wants a flat v_tiles column, the post-build
      # membership check wants every window under an h_tiles row.
      '%{workspace-root-container-layout}')                                rows | awk '{print "v_tiles"}' ;;
      '%{window-parent-container-layout}:%{workspace-root-container-layout}') rows | awk '{print "v_tiles:v_tiles"}' ;;
      '%{window-parent-container-layout}')                                 rows | awk '{print "h_tiles"}' ;;
      '%{window-id}|%{window-parent-container-layout}')                    rows | awk -F'\t' '{print $1 "|h_tiles"}' ;;
      '%{window-id}|%{window-title}')                                      rows | awk -F'\t' '{print $1 "|" $4}' ;;
      '%{window-id}|%{workspace}')                                         rows | awk -F'\t' '{print $1 "|" $2}' ;;
      '%{window-id}')                                                      rows | awk -F'\t' '{print $1}' ;;
      *)                                                                   rows | awk -F'\t' '{print $1}' ;;
    esac
    ;;
  list-monitors)
    case "$fmt" in
      *nsscreen*) echo "1|1" ;;
      *)          echo "1|Built-in Retina Display|true" ;;
    esac
    ;;
  move-node-to-workspace)
    target="${rest[0]}"
    awk -F'\t' -v OFS='\t' -v w="$wid" -v t="$target" '$1==w{$2=t}1' "$WIN" > "$WIN.new"
    mv "$WIN.new" "$WIN"
    ;;
  focus) printf '%s' "$wid" > "$FOCUSED" ;;
  *) : ;;
esac
exit 0
STUB

# Geometry is deliberately unavailable: these tests are about which window gets
# selected, not about pixel verification. arrange-ws11 then trusts its
# deterministic build, which is the documented fallback.
printf '#!/usr/bin/env bash\nexit 0\n' > "$TMP/bin/osascript"
printf '#!/usr/bin/env bash\nexit 0\n' > "$TMP/bin/sleep"
printf '#!/usr/bin/env bash\nexit 0\n' > "$TMP/bin/open"
chmod +x "$TMP/bin/aerospace" "$TMP/bin/osascript" "$TMP/bin/sleep" "$TMP/bin/open"

failures=0
fail() { echo "  FAIL: $*" >&2; failures=$((failures + 1)); }

# run_case NAME FIXTURE EXTRA_ENV... -- ARGS...
# Populates a fresh window table and runs arrange-ws11 against it.
run_case() {
  CASE_DIR="$TMP/case-$1"; shift
  mkdir -p "$CASE_DIR"
  printf '%s' "$1" > "$CASE_DIR/windows.tsv"; shift
  : > "$CASE_DIR/commands.log"
  OUT=$(AEROSPACE_STATE="$CASE_DIR" PATH="$TMP/bin:/usr/bin:/bin" \
    env "$@" /bin/bash "$ROOT/bin-macos/arrange-ws11" --ensure-windows 2>&1)
  RC=$?
  WS11=$(awk -F'\t' '$2=="11"{print $1}' "$CASE_DIR/windows.tsv" | sort -n | tr '\n' ' ')
  WS11="${WS11% }"
  LOGGED=$(cat "$CASE_DIR/commands.log")
}

# The reported failure: Calendar 102 is the reserved dashboard window on ws 11,
# Calendar 162 is ws 2's Chrome window that the user navigated to a calendar.
DUPLICATE_CALENDAR=$(printf '%s\n' \
  $'102\t11\tGoogle Chrome\tCalendar - Google Chrome - Matt (Meta)' \
  $'122\t11\tGoogle Chrome\tChat - Google Chrome - Matt (Meta)' \
  $'501\t11\tObsidian\tRelease Notes - Obsidian' \
  $'2012\t11\tOrchest\tOrchest' \
  $'158\t2\tGhostty\tCCO-checkout1' \
  $'162\t2\tGoogle Chrome\tCalendar - Google Chrome - Matt (Meta)')

echo "1. duplicate Calendar titles: keeps the ws-11 window, leaves ws 2 alone"
run_case dup "$DUPLICATE_CALENDAR"
[[ "$WS11" == "102 122 501 2012" ]] || fail "ws11 = [$WS11], want [102 122 501 2012]"
grep -q '^move-node-to-workspace --window-id 162 11$' <<< "$LOGGED" \
  && fail "stole ws2's Calendar window 162 onto workspace 11"
[[ "$(awk -F'\t' '$1==162{print $2}' "$CASE_DIR/windows.tsv")" == "2" ]] \
  || fail "window 162 no longer on workspace 2"
[[ "$RC" -eq 0 ]] || fail "exit $RC, want 0"

# AeroSpace sorts list-windows by title and breaks ties arbitrarily: successive
# calls really do reshuffle same-titled windows. This is the ordering that
# produced the reported failure — the ws-2 Calendar window enumerated first, so
# a `| first` lookup returned it instead of the reserved one.
echo "1b. same, with the foreign Calendar enumerated first"
run_case dup_shuffled "$(printf '%s\n' \
  $'162\t2\tGoogle Chrome\tCalendar - Google Chrome - Matt (Meta)' \
  $'102\t11\tGoogle Chrome\tCalendar - Google Chrome - Matt (Meta)' \
  $'122\t11\tGoogle Chrome\tChat - Google Chrome - Matt (Meta)' \
  $'501\t11\tObsidian\tRelease Notes - Obsidian' \
  $'2012\t11\tOrchest\tOrchest' \
  $'158\t2\tGhostty\tCCO-checkout1')"
[[ "$WS11" == "102 122 501 2012" ]] || fail "ws11 = [$WS11], want [102 122 501 2012]"
[[ "$(awk -F'\t' '$1==162{print $2}' "$CASE_DIR/windows.tsv")" == "2" ]] \
  || fail "window 162 was pulled off workspace 2"

echo "2. reserved id wins over the ws-11 title match"
run_case reserved "$DUPLICATE_CALENDAR" WS11_CAL_WINDOW_ID=162
[[ "$WS11" == "122 162 501 2012" ]] || fail "ws11 = [$WS11], want [122 162 501 2012]"
[[ "$(awk -F'\t' '$1==102{print $2}' "$CASE_DIR/windows.tsv")" == "Z" ]] \
  || fail "unreserved Calendar 102 was not evicted to Z"

echo "3. stale reserved id falls back to the title match"
run_case stale "$DUPLICATE_CALENDAR" WS11_CAL_WINDOW_ID=999999
grep -q 'reserved Calendar window 999999 is gone' <<< "$OUT" \
  || fail "no warning about the dead reserved id"
[[ "$WS11" == "102 122 501 2012" ]] || fail "ws11 = [$WS11], want [102 122 501 2012]"

echo "4. reserved id of the wrong app is rejected"
run_case wrongapp "$DUPLICATE_CALENDAR" WS11_CAL_WINDOW_ID=501
grep -q 'reserved Calendar window 501 is gone' <<< "$OUT" \
  || fail "accepted an Obsidian window as the Calendar slot"
[[ "$WS11" == "102 122 501 2012" ]] || fail "ws11 = [$WS11], want [102 122 501 2012]"

echo "5. foreign windows on ws 11 are evicted"
run_case evict "$(printf '%s\n' "$DUPLICATE_CALENDAR" \
  $'72\t11\tTodoist\tToday' \
  $'805\t11\tGoogle Chrome\tThe Rust Programming Language')"
[[ "$WS11" == "102 122 501 2012" ]] || fail "ws11 = [$WS11], want [102 122 501 2012]"
grep -q 'evicting foreign window 72' <<< "$OUT" || fail "did not report evicting 72"
grep -q 'evicting foreign window 805' <<< "$OUT" || fail "did not report evicting 805"

echo "6. never takes a Calendar window off another workspace"
run_case nosteal "$(printf '%s\n' \
  $'122\t11\tGoogle Chrome\tChat - Google Chrome - Matt (Meta)' \
  $'501\t11\tObsidian\tRelease Notes - Obsidian' \
  $'2012\t11\tOrchest\tOrchest' \
  $'162\t2\tGoogle Chrome\tCalendar - Google Chrome - Matt (Meta)')"
[[ "$(awk -F'\t' '$1==162{print $2}' "$CASE_DIR/windows.tsv")" == "2" ]] \
  || fail "took ws2's Calendar window instead of opening a new one"
grep -q 'opening Chrome https://www.internalfb.com/calendar' <<< "$OUT" \
  || fail "did not open a replacement Calendar window"

echo "7. reclaims a dashboard window parked on the sweep workspace"
run_case reclaim "$(printf '%s\n' \
  $'122\t11\tGoogle Chrome\tChat - Google Chrome - Matt (Meta)' \
  $'501\t11\tObsidian\tRelease Notes - Obsidian' \
  $'2012\t11\tOrchest\tOrchest' \
  $'102\tZ\tGoogle Chrome\tCalendar - Google Chrome - Matt (Meta)' \
  $'162\t2\tGoogle Chrome\tCalendar - Google Chrome - Matt (Meta)')"
[[ "$WS11" == "102 122 501 2012" ]] || fail "ws11 = [$WS11], want [102 122 501 2012]"

echo "8. equal-title candidates resolve deterministically by window id"
for run in 1 2 3; do
  run_case "det$run" "$(printf '%s\n' \
    $'122\t11\tGoogle Chrome\tChat - Google Chrome - Matt (Meta)' \
    $'501\t11\tObsidian\tRelease Notes - Obsidian' \
    $'2012\t11\tOrchest\tOrchest' \
    $'300\tZ\tGoogle Chrome\tCalendar - Google Chrome - Matt (Meta)' \
    $'200\tZ\tGoogle Chrome\tCalendar - Google Chrome - Matt (Meta)')"
  [[ "$WS11" == "122 200 501 2012" ]] || fail "run $run picked [$WS11], want [122 200 501 2012]"
done

if [[ "$failures" -ne 0 ]]; then
  echo "arrange-ws11 tests: $failures failure(s)" >&2
  exit 1
fi
echo "arrange-ws11 tests passed"
