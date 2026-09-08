# Shared helpers for the sway window orchestration (startup-windows,
# arrange-workspaces). Sourced, not executed.
#
# Window identity on sway, and why it differs from the AeroSpace original:
#
#   Terminals are identified DECLARATIVELY. `ghostty --class=X` sets the
#   Wayland app_id and, because GTK keys its single-instance lock on the
#   application id, also forces a separate process per window. So a managed
#   terminal is found by an exact app_id and never needs title matching,
#   creation-order polling, or an id claim.
#
#   The class must be a valid GTK application id (src/config/Config.zig
#   documents the requirement) -- crucially it must contain a period.
#   ghostty does NOT fail on an invalid one: it silently falls back to
#   com.mitchellh.ghostty, so every managed terminal would collide on one
#   app_id and the layout would be unbuildable. valid_term_app_id guards it.
#
#   Chrome and Omnigent cannot do that. Chrome's second window INHERITS the
#   first window's --class (verified: two --class values, one class in the
#   tree), and Electron takes its app_id from the app name. Both are therefore
#   claimed by MARK, the way the Mac script claims them by window id.
#
#   Marks are the right primitive because sway enforces their uniqueness:
#   cmd_mark calls container_find_and_unmark() unconditionally
#   (sway/commands/mark.c), so assigning a mark to a window removes it from
#   whichever window held it before. A slot therefore cannot be double-claimed,
#   the claim lives in the compositor rather than in script-local state, and a
#   re-run reads back the claims the previous run made.

SWAY_IPC_TIMEOUT="${SWAY_IPC_TIMEOUT:-8}"

# app_id prefix for managed terminals. Defined here, not in either script:
# startup-windows sets it when launching and arrange-workspaces reads it back
# when ordering, so a divergence between the two would silently drop every
# terminal out of the layout.
TERM_APP_ID_PREFIX="${TERM_APP_ID_PREFIX:-sway-ws.term.}"

# swaymsg can block indefinitely if the compositor is wedged. Bound every call
# so one bad request cannot freeze the whole login sequence, mirroring the
# AeroSpace wrapper's contract.
sway_ipc() {
  local out status
  # The status MUST be captured in an else branch. After a false `if` with no
  # else, $? is the status of the `if` compound command itself, which POSIX
  # defines as 0 when no branch ran -- so `fi; status=$?` silently reported
  # every failed request as a success, including timeouts.
  if out=$(timeout --foreground --kill-after=1 "$SWAY_IPC_TIMEOUT" swaymsg "$@" 2>/dev/null); then
    printf '%s' "$out"
    return 0
  else
    status=$?
  fi
  if [[ "$status" -eq 124 || "$status" -eq 137 ]]; then
    echo "WARNING: swaymsg timed out: swaymsg $*" >&2
  fi
  return "$status"
}

# Run a sway command (mutation). Returns nonzero if sway reports failure.
sway_cmd() {
  local out
  out=$(sway_ipc -t command "$@") || return 1
  echo "$out" | jq -e 'all(.[]; .success)' >/dev/null 2>&1
}

# jq prelude: `wins` flattens the tree into one record per real window.
# Scratchpad (__i3 output) is excluded; it is where non-layout windows are
# parked, so it must never look like a managed or stray window.
SWAY_WINS_JQ='
def wins:
  [ .nodes[]?
    | select(.name != "__i3")
    | .nodes[]?
    | select(.type == "workspace")
    | .name as $ws
    | [recurse(.nodes[]?, .floating_nodes[]?)]
    | .[]
    | select(.pid != null)
    | { id: .id,
        ws: $ws,
        app_id: (.app_id // null),
        class: (.window_properties.class // null),
        title: (.name // ""),
        marks: (.marks // []),
        pid: .pid }
  ];
'

# snapshot_windows -> JSON array of window records (see SWAY_WINS_JQ).
# Fails (nonzero, no output) rather than reporting an empty window list when
# the compositor cannot be read. The distinction is load-bearing: callers treat
# an empty list as "no window fills this slot" and create one, so a wedged
# compositor that returned success would make every slot look missing and the
# reconciler would launch a duplicate of everything.
snapshot_windows() {
  local tree
  tree=$(sway_ipc -t get_tree) || return 1
  [[ -n "$tree" ]] || return 1
  printf '%s' "$tree" | jq -c "$SWAY_WINS_JQ wins"
}

# App matchers. Chrome runs under XWayland here so it carries an X11 class and
# a null app_id; matching both fields keeps this correct if it is ever run
# with --ozone-platform=wayland.
CHROME_MATCH_JQ='((.app_id // "") | test("^(google-chrome|Google-chrome)"; "i")) or ((.class // "") | test("^google-chrome"; "i"))'
# Matched case-insensitively and on both fields: the desktop entry says
# StartupWMClass=Omnigent, but the launcher override currently forces
# --ozone-platform=x11 (see omnigent_config/omnigent-desktop-electron.desktop),
# under which sway reports a null app_id and window_properties.class /
# .instance "omnigent". Run as a native Wayland client (once that flag is
# dropped) the identity moves to app_id "omnigent". Both verified against
# live windows.
OMNIGENT_MATCH_JQ='((.app_id // "") | test("^omnigent"; "i")) or ((.class // "") | test("^omnigent"; "i"))'

# Chrome's profile picker is a Chrome window but cannot satisfy a slot.
CHROME_INVALID_TITLE_RE='^Who.s using Chrome\?$'
# Omnigent's 344x1 transparent update overlay (desktop 0.10.0,
# web/electron/src/update_overlay.js) is a real window one id after its parent.
# It can never fill a slot and must never join a layout.
OMNIGENT_INVALID_TITLE_RE='^Omnigent Update$'

# window_ids_matching SNAPSHOT MATCH_JQ [INVALID_TITLE_RE] -> ascending ids
window_ids_matching() {
  local snapshot="$1" match="$2" invalid="${3:-}"
  echo "$snapshot" | jq -r --arg invalid "$invalid" \
    "[ .[] | select($match) | select(\$invalid == \"\" or (.title | test(\$invalid) | not)) ] | sort_by(.id) | .[].id"
}

# mark_window ID MARK — claim a slot. Sway removes the mark from any previous
# holder, so this is also how a re-run repairs a mis-claimed slot.
mark_window() {
  local id="$1" mark="$2"
  sway_cmd "[con_id=$id] mark --add \"$mark\""
}

# id_with_mark SNAPSHOT MARK -> window id currently holding MARK (or empty)
id_with_mark() {
  local snapshot="$1" mark="$2"
  echo "$snapshot" | jq -r --arg m "$mark" \
    '[.[] | select(.marks | index($m))] | first | .id // empty'
}

# id_with_app_id SNAPSHOT APP_ID -> window id (or empty)
id_with_app_id() {
  local snapshot="$1" app_id="$2"
  echo "$snapshot" | jq -r --arg a "$app_id" \
    '[.[] | select(.app_id == $a)] | sort_by(.id) | first | .id // empty'
}

# workspace_of SNAPSHOT ID -> workspace name (or empty)
workspace_of() {
  local snapshot="$1" id="$2"
  echo "$snapshot" | jq -r --argjson i "$id" \
    '[.[] | select(.id == $i)] | first | .ws // empty'
}

# move_to_workspace ID WS
move_to_workspace() {
  local id="$1" ws="$2"
  sway_cmd "[con_id=$id] move container to workspace $ws"
}

# wait_for_new_window MATCH_JQ BEFORE_IDS [INVALID_TITLE_RE] [TIMEOUT_S] -> new id
#
# Only needed for Chrome and Omnigent, whose windows carry no identity we can
# set at launch. INVALID_TITLE_RE keeps a helper window created alongside the
# real one (Omnigent's update overlay) from being mistaken for it.
wait_for_new_window() {
  local match="$1" before_ids="$2" invalid="${3:-}" timeout="${4:-20}"
  local deadline snapshot current new
  deadline=$(( $(date +%s) + timeout ))
  # The deadline is the loop condition, so a compositor that stops answering
  # mid-wait ends this in TIMEOUT seconds like anything else. An unbounded
  # `|| continue` on the read used to skip the check and spin here forever --
  # holding the startup lock, so no later run could repair the session.
  while [[ $(date +%s) -lt "$deadline" ]]; do
    sleep 0.5
    snapshot=$(snapshot_windows) || continue
    current=$(window_ids_matching "$snapshot" "$match" "$invalid")
    # Set difference. Deliberately not the usual `awk 'NR==FNR{...}' a b`:
    # when the first file is EMPTY -- which is exactly the case on a fresh
    # session, where no window of this app exists yet -- NR==FNR is still true
    # for the second file, so every window is recorded as already-seen and no
    # window is ever reported as new. That silently broke every Chrome and
    # Omnigent slot on first login. Passing the before-set as a variable has
    # no such edge case. comm is unsuitable too: it demands lexicographic
    # input, and window ids must be compared numerically (100 sorts before 99).
    new=$(awk -v before="$before_ids" '
      BEGIN { n = split(before, a, "\n"); for (i = 1; i <= n; i++) if (a[i] != "") seen[a[i]] }
      $0 != "" && !($0 in seen) { print }
    ' <<< "$current" | sort -n | head -1)
    if [[ -n "$new" ]]; then
      echo "$new"
      return 0
    fi
  done
  return 1
}

# desktop_entry_exec BASENAME -> the Exec line of the winning .desktop entry,
# stripped of field codes. Search order is XDG_DATA_HOME then XDG_DATA_DIRS,
# which is the same precedence the desktop environment applies.
#
# Omnigent MUST be launched through its desktop entry rather than
# /opt/Omnigent/omnigent-desktop-electron directly: the entry in
# ~/.local/share/applications adds the flags without which the GUI dies with
# SIGTRAP under sway, on first launch and again on the first parent resize
# (see omnigent_config/omnigent-desktop-electron.desktop for the full
# diagnosis). Reading the Exec line here means this inherits those flags, and
# any future change to them, instead of duplicating the workaround.
desktop_entry_exec() {
  local base="$1" dir f
  local -a dirs=()
  IFS=':' read -r -a dirs <<< "${XDG_DATA_HOME:-$HOME/.local/share}:${XDG_DATA_DIRS:-/usr/local/share:/usr/share}"
  for dir in "${dirs[@]}"; do
    [[ -n "$dir" ]] || continue
    f="$dir/applications/$base"
    if [[ -r "$f" ]]; then
      grep -m1 '^Exec=' "$f" | sed -e 's/^Exec=//' -e 's/ *%[UufFdDnNickvm]//g'
      return 0
    fi
  done
  return 1
}

# valid_term_app_id ID -> 0 if ID is a usable GTK application id.
#
# ghostty accepts an invalid --class silently and falls back to its default,
# which would make every managed terminal share one app_id. Checking up front
# turns that into a loud error naming the offending slot.
valid_term_app_id() {
  local id="$1"
  [[ "$id" == *.* ]] || return 1
  [[ "$id" =~ ^[A-Za-z][A-Za-z0-9_-]*(\.[A-Za-z][A-Za-z0-9_-]*)+$ ]] || return 1
  [[ "${#id}" -lt 255 ]]
}

# unclaimed_window_id SNAPSHOT MATCH_JQ INVALID_TITLE_RE -> lowest id of a
# window matching MATCH_JQ that no slot has claimed yet.
#
# "Unclaimed" means carrying no sw: mark at all. Checking the prefix rather
# than one specific mark is what stops workspace N from stealing the window
# workspace M already owns -- the failure the Mac script's claimed_*_ids lists
# exist to prevent, except here the state lives in the compositor.
unclaimed_window_id() {
  local snapshot="$1" match="$2" invalid="$3"
  echo "$snapshot" | jq -r --arg invalid "$invalid" \
    "[ .[] | select($match)
          | select(\$invalid == \"\" or (.title | test(\$invalid) | not))
          | select([.marks[] | select(startswith(\"sw:\"))] | length == 0) ]
     | sort_by(.id) | first | .id // empty"
}

# stray_window_ids SNAPSHOT MANAGED_WS_REGEX KEEP_MARKS KEEP_APP_IDS -> ids to
# sweep. KEEP_MARKS and KEEP_APP_IDS are newline-separated lists of the claims
# the CURRENT table declares.
#
# A window earns its place on a managed workspace only by holding one of those
# marks or one of those app_ids. Exempting "any sw: mark" or "any terminal
# prefix" instead meant a slot deleted from the table -- or a terminal slot
# renamed -- left its old window in the layout forever, because nothing ever
# compared a claim against what the table says today.
stray_window_ids() {
  local snapshot="$1" managed_re="$2" keep_marks="$3" keep_app_ids="$4"
  echo "$snapshot" | jq -r --arg managed "$managed_re" \
      --arg marks "$keep_marks" --arg apps "$keep_app_ids" '
    ($marks | split("\n") | map(select(. != ""))) as $keepm
    | ($apps | split("\n") | map(select(. != ""))) as $keepa
    | .[] | select(.ws | test($managed))
          | select(any(.marks[]; . as $m | $keepm | index($m)) | not)
          | (.app_id // "") as $a
          | select(($keepa | index($a)) | not)
          | .id'
}

# sw_marks_of SNAPSHOT ID -> the sw: marks a window holds, one per line.
sw_marks_of() {
  local snapshot="$1" id="$2"
  echo "$snapshot" | jq -r --argjson i "$id" \
    '.[] | select(.id == $i) | .marks[] | select(startswith("sw:"))'
}

# id_with_app_identity SNAPSHOT NAME -> window id whose app_id OR X11 class
# matches NAME, case-insensitively.
#
# Which of the two fields carries the identity depends on whether the app is a
# native Wayland client or goes through XWayland, and the case depends on the
# toolkit -- Chrome reports class "Google-chrome", Electron reports app_id
# "omnigent" even though its desktop entry declares StartupWMClass=Omnigent.
# Dashboard panes are matched this way so a pane keeps working if its app
# switches backends.
id_with_app_identity() {
  local snapshot="$1" name="$2"
  # Exact, case-insensitive comparison rather than a regex: pane names come
  # from a config table and can contain regex metacharacters (a dotted
  # app_id already does), which a pattern match would silently misinterpret.
  echo "$snapshot" | jq -r --arg n "$name" '
    ($n | ascii_downcase) as $want
    | [ .[] | select((((.app_id // "") | ascii_downcase) == $want)
                  or (((.class // "") | ascii_downcase) == $want)) ]
    | sort_by(.id) | first | .id // empty'
}

# unclaimed_id_with_app_identity SNAPSHOT NAME -> like id_with_app_identity,
# but skips windows already holding any sw: mark. Dashboard panes use this so
# a pane whose app also fills standard-workspace slots (a Chrome calendar, a
# ghostty monitor) adopts a spare window rather than stealing a claimed one.
# Marks are unique per NAME, not per window: a window can carry both
# sw:1:chrome and sw:9:google-chrome, so "already marked" has to be checked
# explicitly rather than relied on.
unclaimed_id_with_app_identity() {
  local snapshot="$1" name="$2"
  echo "$snapshot" | jq -r --arg n "$name" '
    ($n | ascii_downcase) as $want
    | [ .[] | select((((.app_id // "") | ascii_downcase) == $want)
                  or (((.class // "") | ascii_downcase) == $want))
            | select([.marks[] | select(startswith("sw:"))] | length == 0) ]
    | sort_by(.id) | first | .id // empty'
}

# focused_window_id -> id of the focused window, or empty.
focused_window_id() {
  sway_ipc -t get_tree | jq -r '
    [recurse(.nodes[]?, .floating_nodes[]?) | select(.focused == true and .pid != null)]
    | first | .id // empty'
}
