#!/usr/bin/env bash
# Omnigent desktop 0.10.0 gives every shell window a permanently-shown 344x1
# transparent child window titled "Omnigent Update"
# (web/electron/src/update_overlay.js), created with resizable:false. AeroSpace
# cannot tile a non-resizable window, so it stays `floating` forever.
#
# Two regressions followed the 0.10.0 upgrade, both covered here:
#
#   1. startup-windows claimed overlays as workspace windows, because it
#      selected Omnigent windows by app name alone. Overlays are created one id
#      after their parent, so ascending-id slot filling put a 1px overlay on
#      every second workspace instead of a chat window.
#   2. arrange-workspaces could never converge on those workspaces, and its
#      spatial walk wrapped off the workspace, so each failed attempt rebuilt an
#      already-correct NEIGHBOUR's tree.
#
# The functions under test are extracted from the real scripts so the assertions
# run against the shipped source rather than a copy of its logic.
set -uo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
STARTUP="$ROOT/bin-macos/startup-windows"
ARRANGE="$ROOT/bin-macos/arrange-workspaces"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

failures=0
fail() { echo "  FAIL: $*" >&2; failures=$((failures + 1)); }

load_fn() {
  local body
  body=$(awk -v fn="$2" '
    index($0, fn "() {") == 1 { p = 1 }
    p { print }
    p && $0 == "}" { exit }
  ' "$1")
  [[ -n "$body" ]] || { echo "could not extract $2 from $1" >&2; exit 1; }
  eval "$body"
}

# ── startup-windows: overlays are not workspace windows ──────────────

OMNIGENT_APP="Omnigent"
OMNIGENT_INVALID_TITLE_PATTERN="^Omnigent Update$"
claimed_omnigent_ids=""

for fn in window_ids_for_app omnigent_window_ids omnigent_id_is_claimed \
          claim_omnigent_window find_unclaimed_omnigent_window \
          workspace_has_claimed_omnigent_window; do
  load_fn "$STARTUP" "$fn"
done

# The real post-upgrade state: four chat windows, each trailed by its overlay
# one window id later.
SNAPSHOT='[
  {"window-id":17720,"workspace":"1","app-name":"Omnigent","window-title":"● Sapphire Velox vector search scaling"},
  {"window-id":17721,"workspace":"1","app-name":"Omnigent","window-title":"Omnigent Update"},
  {"window-id":17725,"workspace":"1","app-name":"Omnigent","window-title":"Identify the Assistant"},
  {"window-id":17726,"workspace":"1","app-name":"Omnigent","window-title":"Omnigent Update"},
  {"window-id":6659,"workspace":"1","app-name":"Google Chrome","window-title":"New Tab"}
]'
aerospace() { printf '%s' "$SNAPSHOT"; }

echo "1. omnigent_window_ids skips the update overlays"
got=$(omnigent_window_ids | tr '\n' ' ')
[[ "$got" == "17720 17725 " ]] || fail "got [$got], want [17720 17725 ]"

echo "2. slots are filled with chat windows only, in ascending id order"
claimed_omnigent_ids=""
first=$(find_unclaimed_omnigent_window); claim_omnigent_window "$first"
second=$(find_unclaimed_omnigent_window); claim_omnigent_window "$second"
third=$(find_unclaimed_omnigent_window) || third=""
[[ "$first" == "17720" ]] || fail "first slot got $first, want 17720"
[[ "$second" == "17725" ]] || fail "second slot got $second, want 17725"
[[ -z "$third" ]] || fail "third slot got overlay $third; only 2 chat windows exist"

echo "3. workspace-scoped lookups skip overlays too"
# ws2's only Omnigent window being an overlay must read as "this slot is empty",
# so the slot falls through to the global pool or to window creation.
claimed_omnigent_ids=""
WS2_ONLY_OVERLAY='[
  {"window-id":17721,"workspace":"2","app-name":"Omnigent","window-title":"Omnigent Update"}
]'
aerospace() { printf '%s' "$WS2_ONLY_OVERLAY"; }
got=$(find_unclaimed_omnigent_window 2) || got=""
[[ -z "$got" ]] || fail "ws2 slot resolved to overlay $got"
workspace_has_claimed_omnigent_window 2 && fail "ws2 counts as filled by an overlay"
aerospace() { printf '%s' "$SNAPSHOT"; }

echo "4. window_ids_for_app honours the exclusion, and is inert without one"
got=$(window_ids_for_app "$SNAPSHOT" Omnigent "$OMNIGENT_INVALID_TITLE_PATTERN" | tr '\n' ' ')
[[ "$got" == "17720 17725 " ]] || fail "filtered: got [$got], want [17720 17725 ]"
got=$(window_ids_for_app "$SNAPSHOT" Omnigent "" | tr '\n' ' ')
[[ "$got" == "17720 17721 17725 17726 " ]] \
  || fail "unfiltered: got [$got], want all four"

echo "5. the Omnigent creation path passes the exclusion to wait_for_new_window"
grep -A1 'wait_for_new_window "$app_name" "$before_snapshot" \\' "$STARTUP" \
  | grep -q 'OMNIGENT_INVALID_TITLE_PATTERN' \
  || fail "create path can still latch onto the overlay created after its parent"

unset -f aerospace omnigent_window_ids find_unclaimed_omnigent_window \
  workspace_has_claimed_omnigent_window

# ── arrange-workspaces: overlays are evicted, not laid out ───────────

NON_LAYOUT_TITLE_PATTERN="^Omnigent Update$"
load_fn "$ARRANGE" evict_non_layout_windows

MOVES="$TMP/moves"
aerospace() { printf '%s\n' "$*" >> "$MOVES"; }

echo "6. overlays are evicted to Z whether AeroSpace floated them or tiled them"
: > "$MOVES"
MIXED='[
  {"window-id":175,"window-title":"CCO-checkout1 — zsh"},
  {"window-id":17721,"window-title":"Omnigent Update"},
  {"window-id":17740,"window-title":"Omnigent Update"},
  {"window-id":17725,"window-title":"● Sapphire Velox vector search scaling"},
  {"window-id":244,"window-title":"client info — Orchest [23b219fd]"}
]'
evict_non_layout_windows 2 "$MIXED" >/dev/null \
  || fail "reported nothing evicted when two overlays were present"
for wid in 17721 17740; do
  grep -q "move-node-to-workspace --window-id $wid Z" "$MOVES" \
    || fail "overlay $wid was left on the workspace"
done
for wid in 175 17725 244; do
  grep -q -- "--window-id $wid " "$MOVES" && fail "evicted real window $wid"
done

echo "7. a workspace with no overlays is left untouched"
: > "$MOVES"
CLEAN='[{"window-id":175,"window-title":"zsh"},{"window-id":244,"window-title":"Orchest [x]"}]'
evict_non_layout_windows 2 "$CLEAN" >/dev/null \
  && fail "reported an eviction on a clean workspace"
[[ ! -s "$MOVES" ]] || fail "issued moves on a clean workspace: $(cat "$MOVES")"

unset -f aerospace

# ── arrange-workspaces: untileable windows leave the layout ──────────

load_fn "$ARRANGE" untileable_ids

PARENTS="$TMP/parents"
FOCUSED="$TMP/focused"
TILEABLE="$TMP/tileable"
LIST_CALLS="$TMP/list-calls"
: > "$TILEABLE"
: > "$LIST_CALLS"

# `layout tiling` succeeds only for ids listed in $TILEABLE, mirroring
# AeroSpace's refusal to tile a resizable:false window.
aerospace() {
  case "$1 ${2:-}" in
    "workspace ") : ;;
    "focus --window-id") printf '%s' "$3" > "$FOCUSED" ;;
    "layout tiling")
      local f; f=$(cat "$FOCUSED")
      if grep -qx "$f" "$TILEABLE"; then
        awk -F'|' -v w="$f" 'BEGIN{OFS="|"} $1==w{$2="h_tiles"} {print}' \
          "$PARENTS" > "$PARENTS.new" && mv "$PARENTS.new" "$PARENTS"
      fi
      ;;
    "list-windows --workspace") echo x >> "$LIST_CALLS"; cat "$PARENTS" ;;
  esac
  return 0
}

WINDOWS='[{"window-id":175},{"window-id":6659},{"window-id":17721},{"window-id":244}]'

echo "8. a window AeroSpace refuses to tile is reported as untileable"
printf '175|h_tiles\n6659|h_tiles\n17721|floating\n244|h_tiles\n' > "$PARENTS"
: > "$TILEABLE"
got=$(untileable_ids 2 "$WINDOWS" 2>/dev/null | tr '\n' ' ')
[[ "$got" == "17721 " ]] || fail "got [$got], want [17721 ]"

echo "9. a floating window that DOES tile is not reported"
printf '175|h_tiles\n6659|h_tiles\n17721|floating\n244|h_tiles\n' > "$PARENTS"
printf '17721\n' > "$TILEABLE"
got=$(untileable_ids 2 "$WINDOWS" 2>/dev/null | tr '\n' ' ')
[[ -z "$got" ]] || fail "got [$got], want nothing"
grep -q '17721|h_tiles' "$PARENTS" || fail "the unfloat attempt never ran"

echo "10. no floating windows costs no extra round trip"
printf '175|h_tiles\n6659|h_tiles\n17721|h_tiles\n244|h_tiles\n' > "$PARENTS"
: > "$LIST_CALLS"
got=$(untileable_ids 2 "$WINDOWS" 2>/dev/null | tr '\n' ' ')
[[ -z "$got" ]] || fail "got [$got], want nothing"
[[ "$(wc -l < "$LIST_CALLS" | tr -d ' ')" == "1" ]] \
  || fail "queried parent layouts $(wc -l < "$LIST_CALLS") times, want 1"

unset -f aerospace

# ── arrange-workspaces: the spatial walk stays on its workspace ──────

load_fn "$ARRANGE" discover_spatial_order

# Left-to-right across three workspaces. `--boundaries-action stop` is modelled
# as broken at the workspace seams (what an untileable floating window causes)
# and honoured only at the ends of the whole row.
SPATIAL_IDS=(178 245 246 6677 300)
spatial_ws_of() {
  case "$1" in
    178|245) echo 3 ;;
    246|6677) echo 4 ;;
    *) echo 5 ;;
  esac
}
FOCUS_IDX=2  # 246, workspace 4's sidebar

aerospace() {
  local n i
  case "$1 ${2:-}" in
    "list-windows --focused")
      n="${SPATIAL_IDS[$FOCUS_IDX]}"
      if [[ "$4" == '%{window-id}' ]]; then
        printf '%s' "$n"
      else
        printf '%s|%s' "$n" "$(spatial_ws_of "$n")"
      fi
      ;;
    "focus --boundaries-action")
      case "$4" in
        left)  [[ "$FOCUS_IDX" -gt 0 ]] && FOCUS_IDX=$((FOCUS_IDX - 1)) ;;
        right) [[ "$FOCUS_IDX" -lt $(( ${#SPATIAL_IDS[@]} - 1 )) ]] \
                 && FOCUS_IDX=$((FOCUS_IDX + 1)) ;;
      esac
      ;;
    "focus --window-id")
      for i in $(seq 0 $(( ${#SPATIAL_IDS[@]} - 1 ))); do
        [[ "${SPATIAL_IDS[$i]}" == "$3" ]] && FOCUS_IDX="$i"
      done
      ;;
  esac
  return 0
}

echo "11. the walk stops at both workspace seams instead of wandering"
_spatial=()
discover_spatial_order 4
got="${_spatial[*]:-}"
[[ "$got" == "246 6677" ]] || fail "walked [$got], want [246 6677]"
for foreign in 178 245 300; do
  case " $got " in
    *" $foreign "*) fail "walk picked up neighbour window $foreign" ;;
  esac
done

echo "12. focus is handed back to a window on the target workspace"
[[ "$(spatial_ws_of "${SPATIAL_IDS[$FOCUS_IDX]}")" == "4" ]] \
  || fail "left focus on workspace $(spatial_ws_of "${SPATIAL_IDS[$FOCUS_IDX]}")"

if [[ "$failures" -ne 0 ]]; then
  echo "omnigent update-overlay tests: $failures failure(s)" >&2
  exit 1
fi
echo "omnigent update-overlay tests passed"
