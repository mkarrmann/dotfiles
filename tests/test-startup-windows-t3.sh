#!/usr/bin/env bash
# shellcheck disable=SC2034  # variables below are read by the extracted functions
# Checks bin-macos/startup-windows' T3 Code windows with stub aerospace,
# t3-window-scope and workspaces: the lookup by origin, retiring the old
# per-profile Chrome instances, and a row's whole reconcile against a model of
# app windows in T3 Code's own browser, next to your Chrome's.
# Run with: bash tests/test-startup-windows-t3.sh
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
STARTUP="$ROOT/bin-macos/startup-windows"

# Not "failures": that is startup-windows' own count of warnings, which the
# functions under test add to.
failed_checks=0
check() {
  if [[ "$2" == "$3" ]]; then echo "  ok: $1"; else echo "  FAIL: $1 (expected '$2', got '$3')" >&2; failed_checks=$((failed_checks + 1)); fi
}

extract_fn() {
  awk -v name="$1" '
    $0 ~ "^" name "\\(\\) \\{" { p = 1 }
    p { print }
    p && $0 == "}" { exit }
  ' "$STARTUP"
}
for fn in t3_workspace_window t3_windows t3_window_ids_json t3_window_id_for_origin retire_t3_profile_chromes \
          t3_take_pairing_token t3_wait_for_server t3_environment_id t3_links t3_collect_links t3_set_up t3_mark_set_up t3_url_port t3_window_title reconcile_t3_window close_window_id window_exists \
          snapshot_windows chrome_id_is_claimed find_valid_unclaimed_chrome_window t3_row_waits_on_tunnel stray_window_ids; do
  body=$(extract_fn "$fn")
  [[ -n "$body" ]] || { echo "could not extract $fn" >&2; exit 1; }
  eval "$body"
done

CHROME_APP="Google Chrome"
OMNIGENT_APP="Omnigent"
T3_BROWSER_APP="Google Chrome Beta"
T3_BROWSER_PROFILE="Default"
t3_browser_missing_warned=false
t3_browser_installed() { :; }
T3_STATE_DIR="/Users/me/.local/state/t3-windows"
T3_LEGACY_CHROME_DATA_ROOT="/Users/me/.t3-workspace-chrome"
T3_SERVERS_DIR=".t3-servers"
T3_LOCAL_HOST="local"
T3_PAIRING_TOKEN_MAX_AGE_MIN=660
T3_PAIRING_TOKEN_WAIT=0
T3_SERVER_WAIT=0
T3_LOCAL_SERVER_WAIT=0
t3_unreachable_hosts=""
T3_WINDOWS_CACHE="$(mktemp -u)"

workspaces() {
  printf '%s' '{"workspaces":[
    {"workspace":"1","kind":"local"},
    {"workspace":"6","kind":"devserver","host":"cco.example.com","checkout":"/home/me/checkout3","t3Url":"http://cco-checkout3.t3.localhost:13780/","t3Project":"~/checkout3"}]}'
}
check "a devserver workspace resolves to its state, URL, devserver and project under that host's home" \
  $'/Users/me/.local/state/t3-windows/cco-checkout3\thttp://cco-checkout3.t3.localhost:13780/\tcco.example.com\t~/checkout3' \
  "$(t3_workspace_window 6)"
check "a workspace without a T3 Code server resolves to nothing" "" "$(t3_workspace_window 1 || true)"

# Windows 10 and 11 are your Chrome's, 20 (on checkout3's origin) and 30 (on
# checkout30's, a prefix of the same name) T3 Code's browser's, 40 is another app.
CHROME_WINDOWS="10 11"
BROWSER_WINDOWS="20 30"
aerospace() {
  local wid
  printf '['
  for wid in $CHROME_WINDOWS; do printf '{"window-id": %s, "app-name": "Google Chrome"},' "$wid"; done
  for wid in $BROWSER_WINDOWS; do printf '{"window-id": %s, "app-name": "Google Chrome Beta"},' "$wid"; done
  printf '{"window-id": 40, "app-name": "Omnigent"}]'
}
LISTINGS="$(mktemp)"
t3-window-scope() {
  [[ "$*" == --list-windows ]] || return 2
  echo x >> "$LISTINGS"
  printf '%s\n' "20 http://cco-checkout3.t3.localhost:13780" "30 http://cco-checkout30.t3.localhost:13780"
}
check "only the window on that origin matches" "20" "$(t3_window_id_for_origin http://cco-checkout3.t3.localhost:13780)"
check "an origin with no window matches nothing" "" "$(t3_window_id_for_origin http://cco-checkout1.t3.localhost:13780)"
check "every T3 Code window is kept out of the Chrome slots" "[20,30]" "$(t3_window_ids_json)"
: > "$LISTINGS"
: "$(t3_windows)" "$(t3_windows)"
check "windows are listed again only once the browser's windows change" "0" "$(wc -l < "$LISTINGS" | tr -d ' ')"
CHROME_WINDOWS="10 11 12"
: "$(t3_windows)"
check "which a new window of your own Chrome is not" "0" "$(wc -l < "$LISTINGS" | tr -d ' ')"
BROWSER_WINDOWS="20 30 50"
: "$(t3_windows)" "$(t3_windows)"
check "and then once" "1" "$(wc -l < "$LISTINGS" | tr -d ' ')"
t3-window-scope() { return 1; }
rm -f "$T3_WINDOWS_CACHE"
check "when the browser cannot be asked (not running, Apple Events off) there are none" "[]" "$(t3_window_ids_json)"
window_id=$(t3_window_id_for_origin http://cco-checkout1.t3.localhost:13780)
check "and a lookup that finds nothing does not fail under errexit" "" "$window_id"
rm -f "$LISTINGS" "$T3_WINDOWS_CACHE"

echo "== retiring the old per-profile Chrome instances =="
ps() {
  printf '%s\n' \
    "  100 /Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
    "  200 /Applications/Google Chrome.app/Contents/MacOS/Google Chrome --user-data-dir=/Users/me/.t3-workspace-chrome/cco-checkout3 --remote-debugging-port=0" \
    "  201 /Applications/Google Chrome.app/Contents/Frameworks/Google Chrome Helper (Renderer).app/Contents/MacOS/Google Chrome Helper (Renderer) --type=renderer --user-data-dir=/Users/me/.t3-workspace-chrome/cco-checkout3" \
    "  300 /Applications/Google Chrome.app/Contents/MacOS/Google Chrome --user-data-dir=/Users/me/.t3-workspace-chrome/local"
}
KILLED=""
kill() { [[ "$1" == -0 ]] && return 1; KILLED="$*"; }
sleep() { :; }
retire_t3_profile_chromes >/dev/null
check "the old profiles' browsers are asked to quit; your Chrome and their helpers are not signalled" "-TERM 200 300" "$KILLED"
kill() { [[ "$1" == -0 && "$2" == 300 ]]; }
check "one that outlives the wait is named in a warning" "1" \
  "$(retire_t3_profile_chromes 2>&1 >/dev/null | grep -c 'still running (pids: 300)')"
ps() { echo "  100 /Applications/Google Chrome.app/Contents/MacOS/Google Chrome"; }
KILLED=""
check "with none left it does nothing, quietly" "|" "$KILLED|$(retire_t3_profile_chromes)"
unset -f kill ps

T3_PAIRING_DIR="$(mktemp -d)"
trap 'rm -rf "$T3_PAIRING_DIR"' EXIT
mkdir -p "$T3_PAIRING_DIR/cco.example.com"
echo TOKEN123 > "$T3_PAIRING_DIR/cco.example.com/token.a"
echo STALE > "$T3_PAIRING_DIR/cco.example.com/token.b"
touch -t "$(date -v-12H +%Y%m%d%H%M 2>/dev/null || date -d '12 hours ago' +%Y%m%d%H%M)" "$T3_PAIRING_DIR/cco.example.com/token.b"
check "a pooled pairing token is handed out, and one past its 12 hours is not" "TOKEN123" \
  "$(t3_take_pairing_token cco.example.com)"
check "each only once" "" "$(t3_take_pairing_token cco.example.com)"
check "a host with no pool has no token" "" "$(t3_take_pairing_token ftw.example.com)"

curl() { return 0; }
t3_waited_hosts=""
check "the server wait passes once the server answers" "0" \
  "$(t3_wait_for_server cco.example.com http://cco-checkout3.t3.localhost:13780/; echo $?)"
curl() { return 7; }
t3_waited_hosts=""
failures=0
check "and fails, warning, when it does not" "1 1" \
  "$({ t3_wait_for_server ftw.example.com http://ftw-checkout1.t3.localhost:23780/; } 2>&1 | grep -c 'not answering' | tr '\n' ' ')$(t3_wait_for_server ftw.example.com http://x:23780/ >/dev/null 2>&1; echo $?)"
curl() { return 0; }

echo "== a t3 row's window, on a model of T3 Code's browser and your Chrome =="
# Each launch of T3 Code's browser with --app adds a window on that URL, titled as
# T3 Code titles it. Windows are "WID<TAB>TITLE<TAB>WORKSPACE<TAB>URL<TAB>APP"
# in $SIM/windows; the stand-in t3-window-scope lists those of T3 Code's browser
# on a .t3.localhost origin, and records each window it scoped.
SIM="$T3_PAIRING_DIR/sim"
T3_STATE_DIR="$SIM/state"
T3_TITLE_PATTERN="^T3 Code"
sleep() { :; }
curl() { return 0; }
workspaces() {
  printf '%s' '{"workspaces":[
    {"workspace":"1","kind":"local"},
    {"workspace":"3","kind":"devserver","host":"ftw.example.com","checkout":"/home/me/checkout1","nvsSession":"FTW-checkout1","t3Url":"http://ftw-checkout1.t3.localhost:23780/","t3Project":"~/checkout1"},
    {"workspace":"4","kind":"devserver","host":"cco.example.com","checkout":"/home/me/checkout2","nvsSession":"CCO-checkout2","t3Url":"http://cco-checkout2.t3.localhost:13780/","t3Project":"~/checkout2"},
    {"workspace":"5","kind":"devserver","host":"ftw.example.com","checkout":"/home/me/checkout2","nvsSession":"FTW-checkout2","t3Url":"http://ftw-checkout2.t3.localhost:23780/","t3Project":"~/checkout2"},
    {"workspace":"6","kind":"devserver","host":"cco.example.com","checkout":"/home/me/checkout3","nvsSession":"CCO-checkout3","t3Url":"http://cco-checkout3.t3.localhost:13780/","t3Project":"~/checkout3"}]}'
}
check "a window links every other devserver's server once, through its forward here, by its sessions' prefix" \
  $'ftw.example.com\thttp://127.0.0.1:23780\tFTW' "$(t3_links 6)"
check "and an FTW window links CCO's" $'cco.example.com\thttp://127.0.0.1:13780\tCCO' "$(t3_links 3)"
sim_reset() {
  rm -rf "$SIM"; mkdir -p "$SIM"
  : > "$SIM/windows"; : > "$SIM/scoped"; : > "$SIM/opened"; : > "$SIM/loading"; echo 100 > "$SIM/next"
  rm -rf "$T3_PAIRING_DIR/cco.example.com" "$T3_PAIRING_DIR/ftw.example.com"
  mkdir -p "$T3_PAIRING_DIR/cco.example.com" "$T3_PAIRING_DIR/ftw.example.com"
  t3_waited_hosts=""; t3_unreachable_hosts=""; rm -f "$T3_WINDOWS_CACHE"; failures=0; SCOPE_FAILS=""
  FTW_ENV=env-ftw-1
  curl() { sim_curl "$@"; }
}
sim_curl() { # both servers answer, FTW's as $FTW_ENV, CCO's as env-cco-1
  [[ "$*" == *"/.well-known/t3/environment"* ]] || return 0
  case "$*" in
    *:23780*) echo "{\"environmentId\": \"$FTW_ENV\"}" ;;
    *) echo '{"environmentId": "env-cco-1"}' ;;
  esac
}
pool() { # HOST TOKEN -- pool a token as nvs-tunnels does
  printf '%s\n' "$2" > "$(mktemp "$T3_PAIRING_DIR/$1/token.XXXXXX")"
}
sim_id() { local n; n=$(($(cat "$SIM/next") + 1)); echo "$n" > "$SIM/next"; echo "$n"; }
sim_window() { # TITLE URL [APP] -> a new window on workspace Z, of T3 Code's browser unless APP
  printf '%s\t%s\tZ\t%s\t%s\n' "$(sim_id)" "$1" "$2" "${3:-$T3_BROWSER_APP}" >> "$SIM/windows"
}
open() {
  [[ "$1" == -na && "$2" == "$T3_BROWSER_APP" && "$3" == --args ]] || return 1
  shift 3
  printf '%s\n' "$*" >> "$SIM/opened"
  local arg profile="" app=""
  for arg; do
    case "$arg" in
      --profile-directory=*) profile="${arg#--profile-directory=}" ;;
      --app=*) app="${arg#--app=}" ;;
    esac
  done
  [[ "$profile" == Default && -n "$app" ]] || return 0
  sim_window "T3 Code (Beta)" "$app"
  [[ -z "${SIM_LOADING:-}" ]] || tail -n 1 "$SIM/windows" | cut -f1 >> "$SIM/loading"
}
aerospace() {
  case "$1" in
    list-windows)
      jq -Rn '[inputs | split("\t") | {"window-id": (.[0] | tonumber), "app-name": .[4],
        "window-title": .[1], workspace: .[2]}] | if $scope == "--workspace"
        then map(select(.workspace == $ws)) else . end' --arg scope "$2" --arg ws "${3:-}" "$SIM/windows" ;;
    move-node-to-workspace)
      awk -F'\t' -v OFS='\t' -v wid="$3" -v ws="$4" '$1 == wid { $3 = ws } 1' "$SIM/windows" > "$SIM/w" && mv "$SIM/w" "$SIM/windows" ;;
    close)
      awk -F'\t' -v wid="$3" '$1 != wid' "$SIM/windows" > "$SIM/w" && mv "$SIM/w" "$SIM/windows" ;;
  esac
}
# --list-windows lists the windows on T3 Code origins, missing a window still
# loading (SIM_LOADING) the first time. Otherwise it records its
# arguments and the tokens it read for its links, "ARGS | TOKENS", and needs a
# window on the origin, as the real one does.
t3-window-scope() {
  if [[ "$1" == --list-windows ]]; then
    awk -F'\t' -v loading=" $(tr '\n' ' ' < "$SIM/loading") " -v app="$T3_BROWSER_APP" \
      '$5 == app && $4 ~ /\.t3\.localhost/ && index(loading, " " $1 " ") == 0 { split($4, u, "/"); print $1, u[1] "//" u[3] }' "$SIM/windows"
    : > "$SIM/loading"
    return 0
  fi
  local tokens
  tokens=$(tr '\n' ' ' | sed 's/ $//')
  [[ -z "$SCOPE_FAILS" && "$1" == @chrome ]] && awk -F'\t' -v o="$2" 'index($4, o "/") == 1 { f = 1 } END { exit !f }' "$SIM/windows" || return 1
  printf '%s | %s\n' "$*" "$tokens" >> "$SIM/scoped"
}
windows_of() { # ORIGIN -> "WID WORKSPACE URL" for each window on it
  awk -F'\t' -v o="$1" 'index($4, o "/") == 1 { print $1, $3, $4 }' "$SIM/windows"
}
O3="http://cco-checkout3.t3.localhost:13780"
O2="http://cco-checkout2.t3.localhost:13780"
P3="$T3_STATE_DIR/cco-checkout3"
SCOPE3="@chrome $O3 ~/checkout3"
LINKED3="$SCOPE3 --link http://127.0.0.1:23780 FTW | FTWTOKEN"
pooled() { find "$T3_PAIRING_DIR/$1" -type f | wc -l | tr -d ' '; }

sim_reset
pool cco.example.com TOKEN3
pool ftw.example.com FTWTOKEN
reconcile_t3_window 6 "T3: ws6" > "$SIM/out" 2>&1
check "a new origin gets one window, on its workspace and its own server's pairing link" \
  "101 6 $O3/pair#token=TOKEN3" "$(windows_of "$O3")"
check "an app window of T3 Code's browser, with no first-run windows" \
  "--profile-directory=Default --no-first-run --no-default-browser-check --app=$O3/pair#token=TOKEN3" "$(cat "$SIM/opened")"
check "the window starts its new threads in its checkout and links FTW's server with an FTW token" \
  "$LINKED3" "$(cat "$SIM/scoped")"
check "with no warning" "0" "$failures"
check "both tokens are used up" "0 0" "$(pooled cco.example.com) $(pooled ftw.example.com)"
check "its state records both servers it is set up with, by environment" "env-cco-1 env-ftw-1" \
  "$(ls "$P3/.t3-servers" | tr '\n' ' ' | sed 's/ $//')"

sim_reset
pool cco.example.com TOKEN3
pool ftw.example.com FTWTOKEN
SIM_LOADING=1 reconcile_t3_window 6 "T3: ws6" > "$SIM/out" 2>&1
check "a new window the listing misses while it loads is still found, moved and scoped" \
  "101 6 $O3/pair#token=TOKEN3|$LINKED3|0" "$(windows_of "$O3")|$(cat "$SIM/scoped")|$failures"

sim_reset
CHROME_INVALID_TITLE_PATTERN="^Who.s using Chrome\\?$"
T3_ANY_TITLE_PATTERN='^T3 Code|\.t3\.localhost'
claimed_chrome_ids=""
sim_window "T3 Code (Beta)" "about:blank"
sim_window "cco-checkout3.t3.localhost" "chrome-error://chromewebdata/"
sim_window "T3 Code" "http://cco-checkout1.t3.localhost:13780/" "Google Chrome"
sim_window "New Tab - Google Chrome - Me" "chrome://newtab/" "Google Chrome"
check "neither T3 Code's browser's windows nor one left in your Chrome is handed to a Chrome slot" "104" \
  "$(find_valid_unclaimed_chrome_window)"
aerospace move-node-to-workspace --window-id 101 6
aerospace move-node-to-workspace --window-id 104 6
sim_window "Finder" "" "Finder"
aerospace move-node-to-workspace --window-id 105 6
check "on a managed workspace, T3 Code's browser's and Chrome's windows stay; another app's is stray" "105" \
  "$(stray_window_ids 6)"

sim_reset
pool cco.example.com TOKEN3
pool ftw.example.com FTWTOKEN
reconcile_t3_window 6 "T3: ws6" > "$SIM/out" 2>&1
aerospace move-node-to-workspace --window-id 101 Z
: > "$SIM/scoped"
reconcile_t3_window 6 "T3: ws6" > "$SIM/out" 2>&1
check "a window it finds goes back to its workspace" "101 6 $O3/pair#token=TOKEN3" "$(windows_of "$O3")"
check "and is left as it is: not scoped again" "" "$(cat "$SIM/scoped")"
check "with no warning" "0" "$failures"

aerospace close --window-id 101
T3_PAIRING_TOKEN_WAIT=30
started=$SECONDS
reconcile_t3_window 6 "T3: ws6" > "$SIM/out" 2>&1
check "after its window closes the next one opens unpaired, with no token: the origin keeps its cookie" \
  "102 6 $O3/" "$(windows_of "$O3")"
check "without waiting for a token" "yes" "$( (( SECONDS - started < 5 )) && echo yes)"
check "which is scoped too, without linking FTW again" "$SCOPE3 | " "$(cat "$SIM/scoped")"
T3_PAIRING_TOKEN_WAIT=0

aerospace close --window-id 102
: > "$SIM/scoped"
pool cco.example.com TOKEN3B
pool ftw.example.com FTWTOKEN2
FTW_ENV=env-ftw-2
reconcile_t3_window 6 "T3: ws6" > "$SIM/out" 2>&1
check "an FTW server whose state was reset, a new environment, is linked again" \
  "$SCOPE3 --link http://127.0.0.1:23780 FTW | FTWTOKEN2" "$(cat "$SIM/scoped")"

sim_reset
pool cco.example.com TOKEN3
reconcile_t3_window 6 "T3: ws6" > "$SIM/out" 2>&1
check "with no FTW token the window is still opened and scoped, without the link" "$SCOPE3 | " "$(cat "$SIM/scoped")"
check "which warns once, and is not remembered as linked" "1 1 no" \
  "$failures $(grep -c 'no pairing token from ftw.example.com' "$SIM/out") $([[ -e "$P3/.t3-servers/env-ftw-1" ]] && echo yes || echo no)"

sim_reset
pool cco.example.com TOKEN3
pool ftw.example.com FTWTOKEN
curl() { [[ "$*" != *":23780"* ]] || return 7; sim_curl "$@"; }
reconcile_t3_window 6 "T3: ws6" > "$SIM/out" 2>&1
check "an FTW server that does not answer is not linked, and its token is kept" "$SCOPE3 | 1" \
  "$(cat "$SIM/scoped")$(pooled ftw.example.com)"

sim_reset
pool cco.example.com TOKEN3
pool ftw.example.com FTWTOKEN
sim_window "T3 Code" "$O3/pair"
reconcile_t3_window 6 "T3: ws6" > "$SIM/out" 2>&1
check "a found window of an origin never set up is opened again, paired, linked and scoped" \
  "102 6 $O3/pair#token=TOKEN3|$LINKED3" "$(windows_of "$O3")|$(cat "$SIM/scoped")"

sim_reset
pool cco.example.com TOKEN3
pool ftw.example.com FTWTOKEN
sim_window "T3 Code" "$O3/pair"
mkdir -p "$P3/.t3-servers"; : > "$P3/.t3-servers/env-of-an-old-server"
reconcile_t3_window 6 "T3: ws6" > "$SIM/out" 2>&1
check "a window whose origin was set up only with another server (an old one) is opened again, paired" \
  "102 6 $O3/pair#token=TOKEN3" "$(windows_of "$O3")"

sim_reset
sim_window "T3 Code" "$O3/draft/1"
sim_window "T3 Code" "$O2/draft/2"
mkdir -p "$P3/.t3-servers"; : > "$P3/.t3-servers/env-cco-1"
pool ftw.example.com FTWTOKEN
reconcile_t3_window 6 "T3: ws6" > "$SIM/out" 2>&1
check "a set-up window FTW is not linked into yet is kept and linked in place, its project left alone" \
  "101 6 $O3/draft/1|@chrome $O3 --link-only --link http://127.0.0.1:23780 FTW | FTWTOKEN|yes" \
  "$(windows_of "$O3")|$(cat "$SIM/scoped")|$([[ -e "$P3/.t3-servers/env-ftw-1" ]] && echo yes || echo no)"
check "and another origin's window is not this workspace's" "102 Z $O2/draft/2" "$(windows_of "$O2")"
: > "$SIM/scoped"
reconcile_t3_window 6 "T3: ws6" > "$SIM/out" 2>&1
check "and not again" "" "$(cat "$SIM/scoped")"

sim_reset
pool ftw.example.com FTWTOKEN
sim_window "cco-checkout3.t3.localhost - Network error" "$O3/"
mkdir -p "$P3/.t3-servers"; : > "$P3/.t3-servers/env-cco-1"
reconcile_t3_window 6 "T3: ws6" > "$SIM/out" 2>&1
check "a window left on an error page is replaced, and the new one scoped and linked" \
  "102 6 $O3/|$SCOPE3 --link http://127.0.0.1:23780 FTW | FTWTOKEN" "$(windows_of "$O3")|$(cat "$SIM/scoped")"
check "with no warning" "0" "$failures"

sim_reset
pool ftw.example.com FTWTOKEN
SCOPE_FAILS=1
reconcile_t3_window 4 "T3: ws4" > "$SIM/out" 2>&1
check "a window whose scoping fails stays on its workspace" "101 4 $O2/" "$(windows_of "$O2")"
check "and the run counts a warning" "1" "$failures"

sim_reset
eval "$(declare -f open | sed '1s/open/sim_open/')"
open() { return 0; }
reconcile_t3_window 6 "T3: ws6" > "$SIM/out" 2>&1
check "a window that never shows up in the listing warns, pointing at Apple Events" "1 1" \
  "$failures $(grep -c 'Allow JavaScript from Apple Events' "$SIM/out")"
eval "$(declare -f sim_open | sed '1s/sim_open/open/')"

sim_reset
mkdir -p "$P3"
pool ftw.example.com FTWTOKEN
T3_PAIRING_TOKEN_WAIT=2
started=$SECONDS
reconcile_t3_window 6 "T3: ws6" > "$SIM/out" 2>&1
check "an origin left by a run that never paired it still waits for its token" "yes" \
  "$( (( SECONDS - started >= 2 )) && echo yes)"
T3_PAIRING_TOKEN_WAIT=0

sim_reset
pool cco.example.com TOKEN3
curl() { return 7; }
reconcile_t3_window 6 "T3: ws6" > "$SIM/out" 2>&1
check "a window whose own server does not answer is not opened, and its token is kept" "0 1" \
  "$(wc -l < "$SIM/windows" | tr -d ' ') $(pooled cco.example.com)"
check "which says so once, as a wait on its tunnel rather than a warning" "0 1 1" \
  "$failures $(grep -c 'its windows wait for its tunnel' "$SIM/out") $(grep -c 'not opened until' "$SIM/out")"
check "and records the host, which the row then waits on" " cco.example.com yes" \
  "$t3_unreachable_hosts $(t3_row_waits_on_tunnel 6 && echo yes)"
reconcile_t3_window 4 "T3: ws4" > "$SIM/out" 2>&1
check "and the host's other windows are skipped without checking again" "0 1 0" \
  "$(grep -c 'not answering' "$SIM/out") $(grep -c 'not opened until' "$SIM/out") $failures"
check "a row that uses only servers that answered does not wait on a tunnel" "no" \
  "$(t3_unreachable_hosts=" local"; t3_row_waits_on_tunnel 6 && echo yes || echo no)"
check "a row whose window was opened but missed a link waits on that link's tunnel" "yes" \
  "$(t3_unreachable_hosts=" ftw.example.com"; t3_row_waits_on_tunnel 6 && echo yes)"

echo "== the Mac's own server, workspace 1's =="
workspaces_without_local() { :; }
eval "$(declare -f workspaces | sed '1s/workspaces/workspaces_without_local/')"
workspaces() {
  workspaces_without_local | jq -c '.workspaces[0] += {"t3Url": "http://local.t3.localhost:3780/", "t3Project": "/Users/me/dotfiles"}'
}
check "workspace 1 resolves to the Mac's server and its absolute project" \
  "$T3_STATE_DIR/local	http://local.t3.localhost:3780/	local	/Users/me/dotfiles" "$(t3_workspace_window 1)"
check "workspace 1's window links both devservers" $'cco.example.com\thttp://127.0.0.1:13780\tCCO\nftw.example.com\thttp://127.0.0.1:23780\tFTW' \
  "$(t3_links 1)"
check "and a devserver window links the Mac's server as well" $'ftw.example.com\thttp://127.0.0.1:23780\tFTW\nlocal\thttp://127.0.0.1:3780\tMac' \
  "$(t3_links 6)"
T3_BIN="$SIM/t3"
printf '#!/bin/sh\n[ "$T3CODE_HOME" = "$HOME/.t3-workspaces/local" ] && printf "Token: LOCALTOKEN\\n"\n' > "$T3_BIN"
chmod +x "$T3_BIN"
check "the Mac's server mints its token on the spot, from its own state" "LOCALTOKEN" "$(t3_take_pairing_token local)"
sim_reset
printf '#!/bin/sh\n[ "$T3CODE_HOME" = "$HOME/.t3-workspaces/local" ] && printf "Token: LOCALTOKEN\\n"\n' > "$T3_BIN"
chmod +x "$T3_BIN"
pool cco.example.com CCOTOKEN
pool ftw.example.com FTWTOKEN
O1="http://local.t3.localhost:3780"
reconcile_t3_window 1 "T3: ws1" > "$SIM/out" 2>&1
check "workspace 1 gets a window on the Mac's server, paired with a local token, linked to both devservers, in ~/dotfiles" \
  "101 1 $O1/pair#token=LOCALTOKEN|@chrome $O1 /Users/me/dotfiles --link http://127.0.0.1:13780 CCO --link http://127.0.0.1:23780 FTW | CCOTOKEN FTWTOKEN" \
  "$(windows_of "$O1")|$(cat "$SIM/scoped")"
eval "$(declare -f workspaces_without_local | sed '1s/workspaces_without_local/workspaces/')"

sim_reset
reconcile_t3_window 1 "T3: ws1" > "$SIM/out" 2>&1
check "a workspace without a T3 Code server warns and opens nothing" "1 0" "$failures $(wc -l < "$SIM/windows" | tr -d ' ')"

sim_reset
pool cco.example.com TOKEN3
t3_browser_installed() { return 1; }
{ reconcile_t3_window 6 "T3: ws6"; reconcile_t3_window 4 "T3: ws4"; } > "$SIM/out" 2>&1
check "without T3 Code's browser installed nothing opens, its token is kept, and the run warns once how to install it" \
  "0 1 1 1" \
  "$(wc -l < "$SIM/windows" | tr -d ' ') $(pooled cco.example.com) $failures $(grep -c 'brew install --cask google-chrome@beta' "$SIM/out")"
t3_browser_installed() { :; }
t3_browser_missing_warned=false

if [[ "$failed_checks" -gt 0 ]]; then
  echo "$failed_checks check(s) failed" >&2
  exit 1
fi
echo "startup-windows T3 Code tests passed"
