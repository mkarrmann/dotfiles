#!/usr/bin/env bash
# arrange-ws11 leaves a dashboard that is already right alone: rebuilding it
# costs seconds of focus changes on every startup-windows run, and its resizes
# are relative, so they are only right on a freshly built grid. Anything off —
# a window in the wrong cell, the wrong widths, a stranger on ws 11, ws 11 on
# the wrong monitor — still gets the full build.
#
# aerospace is faked over a window table, osascript over a table of window
# frames (the CoreGraphics read) and a fixed monitor rect.
set -uo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
mkdir -p "$TMP/bin"

cat > "$TMP/bin/aerospace" <<'STUB'
#!/usr/bin/env bash
# windows.tsv: id \t workspace \t app \t title. ws11-monitor: the monitor ws 11 is on.
WIN="$AEROSPACE_STATE/windows.tsv"
printf '%s\n' "$*" >> "$AEROSPACE_STATE/commands.log"
cmd="${1:-}"; shift
scope=all; fmt=""; json=false; wid=""; mon=""; rest=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --all) scope=all; shift ;;
    --focused) scope=focused; shift ;;
    --workspace) scope="$2"; shift 2 ;;
    --monitor) mon="$2"; shift 2 ;;
    --json) json=true; shift ;;
    --format) fmt="$2"; shift 2 ;;
    --window-id) wid="$2"; shift 2 ;;
    *) rest[${#rest[@]}]="$1"; shift ;;
  esac
done
rows() {
  case "$scope" in
    all) cat "$WIN" ;;
    focused) awk -F'\t' -v w="$(cat "$AEROSPACE_STATE/focused" 2>/dev/null)" '$1==w' "$WIN" ;;
    *) awk -F'\t' -v s="$scope" '$2==s' "$WIN" ;;
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
      '%{workspace-root-container-layout}') rows | awk '{print "v_tiles"}' ;;
      '%{window-parent-container-layout}:%{workspace-root-container-layout}') rows | awk '{print "v_tiles:v_tiles"}' ;;
      '%{window-id}|%{window-parent-container-layout}|%{workspace-root-container-layout}') rows | awk -F'\t' '{print $1 "|h_tiles|v_tiles"}' ;;
      '%{window-id}|%{window-parent-container-layout}') rows | awk -F'\t' '{print $1 "|h_tiles"}' ;;
      '%{window-id}|%{window-title}') rows | awk -F'\t' '{print $1 "|" $4}' ;;
      '%{window-id}|%{workspace}') rows | awk -F'\t' '{print $1 "|" $2}' ;;
      *) rows | awk -F'\t' '{print $1}' ;;
    esac
    ;;
  list-monitors)
    case "$fmt" in
      *nsscreen*) printf '1|2\n2|1\n' ;;
      *) printf '1|LU28R55|false\n2|DELL U3225QE|true\n' ;;
    esac
    ;;
  list-workspaces) [[ "$mon" == "$(cat "$AEROSPACE_STATE/ws11-monitor")" ]] && echo 11 ;;
  move-node-to-workspace)
    awk -F'\t' -v OFS='\t' -v w="$wid" -v t="${rest[0]}" '$1==w{$2=t}1' "$WIN" > "$WIN.new" && mv "$WIN.new" "$WIN" ;;
  move-workspace-to-monitor) echo "${rest[0]}" > "$AEROSPACE_STATE/ws11-monitor" ;;
  focus) printf '%s' "$wid" > "$AEROSPACE_STATE/focused" ;;
  *) : ;;
esac
exit 0
STUB

cat > "$TMP/bin/osascript" <<'STUB'
#!/usr/bin/env bash
# -l JavaScript - IDS...: the CoreGraphics frames; -l JavaScript -e: the monitor
# rect; anything else (System Events) reads nothing, as without Accessibility.
if [[ "${1:-}" == -l && "${3:-}" == - ]]; then
  cat > /dev/null
  shift 3
  for id in "$@"; do awk -v id="$id" '$1==id' "$AEROSPACE_STATE/frames"; done
elif [[ "${1:-}" == -l && "${3:-}" == -e ]]; then
  echo "0 0 3000 1600"
fi
exit 0
STUB
printf '#!/usr/bin/env bash\nexit 0\n' > "$TMP/bin/sleep"
printf '#!/usr/bin/env bash\nexit 0\n' > "$TMP/bin/open"
chmod +x "$TMP/bin/"*

failures=0
fail() { echo "  FAIL: $*" >&2; failures=$((failures + 1)); }

DASHBOARD=$(printf '%s\n' \
  $'102\t11\tGoogle Chrome\tCalendar - Google Chrome - Matt (Meta)' \
  $'122\t11\tGoogle Chrome\tSomeone (DM) - Meta - Slack - Google Chrome - Matt (Meta)' \
  $'501\t11\tObsidian\tRelease Notes - Obsidian' \
  $'2012\t11\tOrchest\tOrchest')
# 3000px wide: Calendar 27% (810), Slack 70% (2100).
RIGHT=$(printf '%s\n' "102 0 22 810 789" "501 810 22 2190 789" "122 0 811 2100 789" "2012 2100 811 900 789")

# run_case NAME WINDOWS FRAMES [WS11_MONITOR]
run_case() {
  CASE_DIR="$TMP/case-$1"
  mkdir -p "$CASE_DIR"
  printf '%s\n' "$2" > "$CASE_DIR/windows.tsv"
  printf '%s\n' "$3" > "$CASE_DIR/frames"
  echo "${4:-1}" > "$CASE_DIR/ws11-monitor"
  : > "$CASE_DIR/commands.log"
  OUT=$(AEROSPACE_STATE="$CASE_DIR" PATH="$TMP/bin:/usr/bin:/bin" \
    /bin/bash "$ROOT/bin-macos/arrange-ws11" --ensure-windows 2>&1)
  RC=$?
  CHANGES=$(grep -E '^(move-node-to-workspace|move-workspace-to-monitor|focus|flatten-workspace-tree|join-with|layout|resize)( |$)' \
    "$CASE_DIR/commands.log")
}

echo "1. a dashboard already in place is left as it is"
run_case right "$DASHBOARD" "$RIGHT"
[[ "$RC" -eq 0 ]] || fail "exit $RC, want 0: $OUT"
[[ -z "$CHANGES" ]] || fail "changed windows anyway: $(tr '\n' ';' <<< "$CHANGES")"
grep -q 'already correct' <<< "$OUT" || fail "did not say it was already correct"

echo "2. rows still at 50/50 are rebuilt and resized"
run_case halves "$DASHBOARD" "$(printf '%s\n' "102 0 22 1500 789" "501 1500 22 1500 789" "122 0 811 1500 789" "2012 1500 811 1500 789")"
grep -q '^join-with' <<< "$CHANGES" || fail "did not rebuild the grid"
grep -q '^resize --window-id 102 ' <<< "$CHANGES" || fail "did not resize Calendar"
grep -q '^resize --window-id 122 ' <<< "$CHANGES" || fail "did not resize Slack"

echo "3. Calendar and Slack in each other's cells are rebuilt"
run_case swapped "$DASHBOARD" "$(printf '%s\n' "122 0 22 810 789" "501 810 22 2190 789" "102 0 811 2100 789" "2012 2100 811 900 789")"
grep -q '^join-with' <<< "$CHANGES" || fail "left Calendar and Slack swapped"

echo "4. a stranger on ws 11 gets the rebuild, which evicts it"
run_case stranger "$(printf '%s\n' "$DASHBOARD" $'72\t11\tTodoist\tToday')" "$RIGHT"
grep -q 'evicting foreign window 72' <<< "$OUT" || fail "did not evict 72"
[[ "$(awk -F'\t' '$1==72{print $2}' "$CASE_DIR/windows.tsv")" == Z ]] || fail "72 is still on ws 11"

echo "5. frames off the dashboard monitor (ws 11 hidden) are rebuilt"
run_case offscreen "$DASHBOARD" "$(printf '%s\n' "102 5000 22 810 789" "501 5810 22 2190 789" "122 5000 811 2100 789" "2012 7100 811 900 789")"
grep -q '^join-with' <<< "$CHANGES" || fail "trusted frames that are not on the dashboard monitor"

echo "6. ws 11 on the wrong monitor is moved there"
run_case wrongmon "$DASHBOARD" "$RIGHT" 2
grep -q '^move-workspace-to-monitor --workspace 11 1$' <<< "$CHANGES" || fail "did not pin ws 11 to monitor 1"

echo "7. frames that cannot be read get the rebuild"
run_case noframes "$DASHBOARD" ""
grep -q '^join-with' <<< "$CHANGES" || fail "skipped the build without geometry"

if [[ "$failures" -ne 0 ]]; then
  echo "arrange-ws11 skip tests: $failures failure(s)" >&2
  exit 1
fi
echo "arrange-ws11 skip tests passed"
