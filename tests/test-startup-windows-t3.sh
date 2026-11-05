#!/usr/bin/env bash
# shellcheck disable=SC2034  # variables below are read by the extracted functions
# Checks bin-macos/startup-windows' T3 Code windows with stub aerospace/ps/workspaces:
# the lookup by Chrome profile, and a row's whole reconcile against a model of
# Chrome on macOS.
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
for fn in t3_workspace_window t3_window_ids_for_profile t3_window_ids_json t3_profile_running t3_profile_debuggable \
          t3_take_pairing_token t3_wait_for_server t3_environment_id t3_links t3_collect_links t3_set_up t3_mark_set_up t3_url_port t3_window_title reconcile_t3_window close_window_id window_exists \
          snapshot_windows; do
  body=$(extract_fn "$fn")
  [[ -n "$body" ]] || { echo "could not extract $fn" >&2; exit 1; }
  eval "$body"
done

CHROME_APP="Google Chrome"
T3_CHROME_DATA_ROOT="/Users/me/.t3-workspace-chrome"
T3_SERVERS_DIR=".t3-servers"
T3_PAIRING_TOKEN_MAX_AGE_MIN=660
T3_PAIRING_TOKEN_WAIT=0
T3_SERVER_WAIT=0
t3_unreachable_hosts=""

workspaces() {
  printf '%s' '{"workspaces":[
    {"workspace":"1","kind":"local"},
    {"workspace":"6","kind":"devserver","host":"cco.example.com","checkout":"/home/me/checkout3","t3Url":"http://cco-checkout3.t3.localhost:13780/"}]}'
}

# Windows 10 and 11 are regular Chrome, 20 belongs to checkout3's profile, 30 to
# checkout30's (a prefix of the same name), 40 is another app. The browser of
# checkout3's profile was started the way startup-windows starts a cold one.
aerospace() {
  printf '%s' '[
    {"window-id": 11, "app-pid": 100, "app-name": "Google Chrome"},
    {"window-id": 10, "app-pid": 100, "app-name": "Google Chrome"},
    {"window-id": 20, "app-pid": 200, "app-name": "Google Chrome"},
    {"window-id": 30, "app-pid": 300, "app-name": "Google Chrome"},
    {"window-id": 40, "app-pid": 200, "app-name": "Omnigent"}]'
}
ps() {
  if [[ "$*" == "-axo command=" ]]; then
    ps -o command= -p 100; ps -o command= -p 200; ps -o command= -p 300
    return
  fi
  case "${@: -1}" in
    100) echo "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome --profile-directory=Default" ;;
    200) echo "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome --user-data-dir=/Users/me/.t3-workspace-chrome/cco-checkout3 --no-first-run --no-default-browser-check --no-startup-window --remote-debugging-port=0" ;;
    300) echo "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome --user-data-dir=/Users/me/.t3-workspace-chrome/cco-checkout30" ;;
  esac
}

check "a devserver workspace resolves to its profile, URL, devserver and checkout" \
  $'/Users/me/.t3-workspace-chrome/cco-checkout3\thttp://cco-checkout3.t3.localhost:13780/\tcco.example.com\tcheckout3' \
  "$(t3_workspace_window 6)"
check "a workspace without a T3 Code server resolves to nothing" "" "$(t3_workspace_window 1 || true)"
check "only windows of that profile's Chrome process match" "20" \
  "$(t3_window_ids_for_profile /Users/me/.t3-workspace-chrome/cco-checkout3 | tr '\n' ' ' | sed 's/ $//')"
check "regular Chrome windows match no T3 Code profile" "" \
  "$(t3_window_ids_for_profile /Users/me/.t3-workspace-chrome/cco-checkout1)"
check "every T3 Code profile's windows are kept out of the Chrome slots" "[20,30]" "$(t3_window_ids_json)"
check "a running profile is detected" "yes" "$(t3_profile_running /Users/me/.t3-workspace-chrome/cco-checkout3 && echo yes)"
check "a profile with no Chrome process is not detected" "no" \
  "$(t3_profile_running /Users/me/.t3-workspace-chrome/cco-checkout1 || echo no)"
check "a browser started with a DevTools port is debuggable" "yes" \
  "$(t3_profile_debuggable /Users/me/.t3-workspace-chrome/cco-checkout3 && echo yes)"
check "one without is not, even when a profile it prefixes has one" "no" \
  "$(t3_profile_debuggable /Users/me/.t3-workspace-chrome/cco-checkout30 || echo no)"

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
check "and fails, warning, when it does not" "1 1" \
  "$({ t3_wait_for_server ftw.example.com http://ftw-checkout1.t3.localhost:23780/; } 2>&1 | grep -c 'did not answer' | tr '\n' ' ')$(t3_wait_for_server ftw.example.com http://x:23780/ >/dev/null 2>&1; echo $?)"
curl() { return 0; }

# startup-windows runs under set -euo pipefail and assigns the result directly;
# a lookup that finds nothing must not end the script.
window_id=$(t3_window_ids_for_profile /Users/me/.t3-workspace-chrome/cco-checkout1 | head -1)
check "a lookup that finds nothing does not fail under errexit" "" "$window_id"

echo "== a t3 row's window, on a model of Chrome on macOS =="
# One browser per profile: the launch that starts it fixes its command line, and
# a later launch for a running profile only adds the window its --app names,
# ignoring every other flag. A cold start without --no-startup-window also opens
# a startup window. Windows are "WID<TAB>PID<TAB>TITLE<TAB>WORKSPACE<TAB>URL" in
# $SIM/windows, browsers "PID<TAB>COMMAND" in $SIM/browsers. The stand-in
# t3-window-scope succeeds only when the window's browser has a DevTools port,
# as the real one needs, and records each window it scoped.
SIM="$T3_PAIRING_DIR/sim"
T3_CHROME_DATA_ROOT="$SIM/chrome"
T3_TITLE_PATTERN="^T3 Code"
CHROME_BIN="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
sleep() { :; }
curl() { return 0; }
workspaces() {
  printf '%s' '{"workspaces":[
    {"workspace":"1","kind":"local"},
    {"workspace":"3","kind":"devserver","host":"ftw.example.com","checkout":"/home/me/checkout1","nvsSession":"FTW-checkout1","t3Url":"http://ftw-checkout1.t3.localhost:23780/"},
    {"workspace":"4","kind":"devserver","host":"cco.example.com","checkout":"/home/me/checkout2","nvsSession":"CCO-checkout2","t3Url":"http://cco-checkout2.t3.localhost:13780/"},
    {"workspace":"5","kind":"devserver","host":"ftw.example.com","checkout":"/home/me/checkout2","nvsSession":"FTW-checkout2","t3Url":"http://ftw-checkout2.t3.localhost:23780/"},
    {"workspace":"6","kind":"devserver","host":"cco.example.com","checkout":"/home/me/checkout3","nvsSession":"CCO-checkout3","t3Url":"http://cco-checkout3.t3.localhost:13780/"}]}'
}
check "a window links every other devserver's server once, through its forward here, by its sessions' prefix" \
  $'ftw.example.com\thttp://127.0.0.1:23780\tFTW' "$(t3_links 6)"
check "and an FTW window links CCO's" $'cco.example.com\thttp://127.0.0.1:13780\tCCO' "$(t3_links 3)"
sim_reset() {
  rm -rf "$SIM"; mkdir -p "$SIM"
  : > "$SIM/windows"; : > "$SIM/browsers"; : > "$SIM/scoped"; echo 100 > "$SIM/next"
  rm -rf "$T3_PAIRING_DIR/cco.example.com" "$T3_PAIRING_DIR/ftw.example.com"
  mkdir -p "$T3_PAIRING_DIR/cco.example.com" "$T3_PAIRING_DIR/ftw.example.com"
  t3_waited_hosts=""; t3_unreachable_hosts=""; failures=0; SCOPE_FAILS=""
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
sim_browser() { # PROFILE -> the pid of the browser running it
  awk -F'\t' -v dir="--user-data-dir=$1" '{ n = split($2, a, " "); for (i = 1; i <= n; i++) if (a[i] == dir) { print $1; exit } }' "$SIM/browsers"
}
sim_window() { # PID TITLE URL -> a new window of that browser, on workspace Z
  printf '%s\t%s\t%s\tZ\t%s\n' "$(sim_id)" "$1" "$2" "${3:-}" >> "$SIM/windows"
}
sim_start_browser() { # PROFILE ARG... -> pid; started as `open -na` would
  local pid
  pid=$(sim_id)
  printf '%s\t%s\n' "$pid" "$CHROME_BIN ${*:2}" >> "$SIM/browsers"
  echo "$pid"
}
open() {
  [[ "$1" == -na && "$2" == "$CHROME_APP" && "$3" == --args ]] || return 1
  shift 3
  local arg profile="" app="" pid
  for arg; do
    case "$arg" in
      --user-data-dir=*) profile="${arg#--user-data-dir=}" ;;
      --app=*) app="${arg#--app=}" ;;
    esac
  done
  pid=$(sim_browser "$profile")
  if [[ -z "$pid" ]]; then
    pid=$(sim_start_browser "$profile" "$@")
    [[ -n "$app" || " $* " == *" --no-startup-window "* ]] || sim_window "$pid" "New Tab"
  fi
  [[ -z "$app" ]] || sim_window "$pid" "T3 Code (Beta)" "$app"
}
aerospace() {
  case "$1" in
    list-windows)
      jq -Rn '[inputs | split("\t") | {"window-id": (.[0] | tonumber), "app-pid": (.[1] | tonumber),
        "app-name": "Google Chrome", "window-title": .[2], workspace: .[3]}]' "$SIM/windows" ;;
    move-node-to-workspace)
      awk -F'\t' -v OFS='\t' -v wid="$3" -v ws="$4" '$1 == wid { $4 = ws } 1' "$SIM/windows" > "$SIM/w" && mv "$SIM/w" "$SIM/windows" ;;
    close)
      awk -F'\t' -v wid="$3" '$1 != wid' "$SIM/windows" > "$SIM/w" && mv "$SIM/w" "$SIM/windows" ;;
  esac
}
ps() {
  if [[ "$*" == "-axo command=" ]]; then
    echo "/usr/sbin/cfprefsd agent"
    cut -f2 "$SIM/browsers"
  else
    awk -F'\t' -v pid="${@: -1}" '$1 == pid { print $2 }' "$SIM/browsers"
  fi
}
# Records its arguments and the tokens it read for its links, "ARGS | TOKENS";
# with --link-only it needs only a DevTools port, as the real one.
t3-window-scope() {
  local pid tokens
  tokens=$(tr '\n' ' ' | sed 's/ $//')
  pid=$(sim_browser "$1")
  [[ -z "$SCOPE_FAILS" && -n "$pid" && "$(ps -o command= -p "$pid")" == *" --remote-debugging-port=0"* ]] || return 1
  printf '%s | %s\n' "$*" "$tokens" >> "$SIM/scoped"
}
windows_of() { # PROFILE -> "WID WORKSPACE URL" for each of its browser's windows
  local pid
  pid=$(sim_browser "$1")
  [[ -n "$pid" ]] || return 0
  awk -F'\t' -v pid="$pid" '$2 == pid { print $1, $4, $5 }' "$SIM/windows"
}
P3="$T3_CHROME_DATA_ROOT/cco-checkout3"
P2="$T3_CHROME_DATA_ROOT/cco-checkout2"
SCOPE3="$P3 http://cco-checkout3.t3.localhost:13780 ~/checkout3"
LINKED3="$SCOPE3 --link http://127.0.0.1:23780 FTW | FTWTOKEN"
pooled() { find "$T3_PAIRING_DIR/$1" -type f | wc -l | tr -d ' '; }

sim_reset
pool cco.example.com TOKEN3
pool ftw.example.com FTWTOKEN
reconcile_t3_window 6 "T3: ws6" > "$SIM/out" 2>&1
check "a cold profile gets one window, on its workspace and its own server's pairing link, and nothing else" \
  "102 6 http://cco-checkout3.t3.localhost:13780/pair#token=TOKEN3" "$(windows_of "$P3")"
check "its browser runs with a DevTools port and without a startup window" "yes" \
  "$(c=$(ps -o command= -p "$(sim_browser "$P3")"); [[ "$c" == *" --no-startup-window"* && "$c" == *" --remote-debugging-port=0"* ]] && echo yes)"
check "the window starts its new threads in its checkout and links FTW's server with an FTW token" \
  "$LINKED3" "$(cat "$SIM/scoped")"
check "with no warning" "0" "$failures"
check "both tokens are used up" "0 0" "$(pooled cco.example.com) $(pooled ftw.example.com)"
check "the profile records both servers it is set up with, by environment" "env-cco-1 env-ftw-1" \
  "$(ls "$P3/.t3-servers" | tr '\n' ' ' | sed 's/ $//')"

aerospace move-node-to-workspace --window-id 102 Z
: > "$SIM/scoped"
reconcile_t3_window 6 "T3: ws6" > "$SIM/out" 2>&1
check "a window it finds goes back to its workspace" "102 6 http://cco-checkout3.t3.localhost:13780/pair#token=TOKEN3" \
  "$(windows_of "$P3")"
check "and is left as it is: not scoped again" "" "$(cat "$SIM/scoped")"
check "with no warning" "0" "$failures"

aerospace close --window-id 102
T3_PAIRING_TOKEN_WAIT=30
started=$SECONDS
reconcile_t3_window 6 "T3: ws6" > "$SIM/out" 2>&1
check "after its window closes the profile's browser, still running, opens the next one, unpaired with no token" \
  "103 6 http://cco-checkout3.t3.localhost:13780/" "$(windows_of "$P3")"
check "without waiting for a token: the profile paired before" "yes" "$( (( SECONDS - started < 5 )) && echo yes)"
check "which is scoped too, without linking FTW again: the browser kept its DevTools port" "$SCOPE3 | " "$(cat "$SIM/scoped")"
T3_PAIRING_TOKEN_WAIT=0

aerospace close --window-id 103
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
pid=$(sim_start_browser "$P3" --user-data-dir="$P3" --no-startup-window --remote-debugging-port=0)
sim_window "$pid" "T3 Code" "http://cco-checkout3.t3.localhost:13780/pair"
reconcile_t3_window 6 "T3: ws6" > "$SIM/out" 2>&1
check "a found window of a profile never set up is opened again, paired, linked and scoped" \
  "103 6 http://cco-checkout3.t3.localhost:13780/pair#token=TOKEN3|$LINKED3" \
  "$(windows_of "$P3")|$(cat "$SIM/scoped")"

sim_reset
pool cco.example.com TOKEN3
pool ftw.example.com FTWTOKEN
pid=$(sim_start_browser "$P3" --user-data-dir="$P3" --no-startup-window --remote-debugging-port=0)
sim_window "$pid" "T3 Code" "http://cco-checkout3.t3.localhost:13780/pair"
mkdir -p "$P3/.t3-servers"; : > "$P3/.t3-paired"; : > "$P3/.t3-servers/env-of-an-old-server"
reconcile_t3_window 6 "T3: ws6" > "$SIM/out" 2>&1
check "a window whose profile was set up only with another server (an old one) is opened again, paired" \
  "103 6 http://cco-checkout3.t3.localhost:13780/pair#token=TOKEN3" "$(windows_of "$P3")"

sim_reset
pid=$(sim_start_browser "$P3" --user-data-dir="$P3" --no-startup-window --remote-debugging-port=0)
sim_window "$pid" "T3 Code" "http://cco-checkout3.t3.localhost:13780/draft/1"
mkdir -p "$P3/.t3-servers"; : > "$P3/.t3-servers/env-cco-1"
pool ftw.example.com FTWTOKEN
reconcile_t3_window 6 "T3: ws6" > "$SIM/out" 2>&1
check "a set-up window FTW is not linked into yet is kept and linked in place, its project left alone" \
  "102 6 http://cco-checkout3.t3.localhost:13780/draft/1|$P3 http://cco-checkout3.t3.localhost:13780 --link-only --link http://127.0.0.1:23780 FTW | FTWTOKEN|yes" \
  "$(windows_of "$P3")|$(cat "$SIM/scoped")|$([[ -e "$P3/.t3-servers/env-ftw-1" ]] && echo yes || echo no)"
: > "$SIM/scoped"
reconcile_t3_window 6 "T3: ws6" > "$SIM/out" 2>&1
check "and not again" "" "$(cat "$SIM/scoped")"

sim_reset
pool ftw.example.com FTWTOKEN
pid=$(sim_start_browser "$P3" --user-data-dir="$P3" --app=http://cco-checkout3.t3.localhost:13783/)
sim_window "$pid" "cco-checkout3.t3.localhost" "http://cco-checkout3.t3.localhost:13783/"
awk -F'\t' -v OFS='\t' '{ $4 = "6" } 1' "$SIM/windows" > "$SIM/w" && mv "$SIM/w" "$SIM/windows"
reconcile_t3_window 6 "T3: ws6" > "$SIM/out" 2>&1
check "a window left on an error page is replaced" "103 6 http://cco-checkout3.t3.localhost:13780/" "$(windows_of "$P3")"
check "but a browser from before the DevTools port cannot have its window scoped" "" "$(cat "$SIM/scoped")"
check "which warns once, saying to quit that browser" "1 1" \
  "$failures $(grep -c 'quit that Chrome (Cmd+Q in the window) and rerun' "$SIM/out")"
check "and takes no token for a link it cannot make" "1" "$(pooled ftw.example.com)"

sim_reset
pool ftw.example.com FTWTOKEN
SCOPE_FAILS=1
reconcile_t3_window 4 "T3: ws4" > "$SIM/out" 2>&1
check "a window whose scoping fails stays on its workspace" "102 4 http://cco-checkout2.t3.localhost:13780/" "$(windows_of "$P2")"
check "and the run counts a warning" "1" "$failures"

sim_reset
mkdir -p "$P3"
pool ftw.example.com FTWTOKEN
T3_PAIRING_TOKEN_WAIT=2
started=$SECONDS
reconcile_t3_window 6 "T3: ws6" > "$SIM/out" 2>&1
check "a profile left by a run that never paired it still waits for its token" "yes" \
  "$( (( SECONDS - started >= 2 )) && echo yes)"
T3_PAIRING_TOKEN_WAIT=0

sim_reset
pool cco.example.com TOKEN3
curl() { return 7; }
reconcile_t3_window 6 "T3: ws6" > "$SIM/out" 2>&1
check "a window whose own server does not answer is not opened, and its token is kept" "0 1" \
  "$(wc -l < "$SIM/windows" | tr -d ' ') $(pooled cco.example.com)"
check "which warns once, saying to rerun" "1 1" "$failures $(grep -c 'rerun' "$SIM/out")"
reconcile_t3_window 4 "T3: ws4" > "$SIM/out" 2>&1
check "and the host's other windows do not wait for it again" "0 2" "$(grep -c 'did not answer' "$SIM/out") $failures"

sim_reset
reconcile_t3_window 1 "T3: ws1" > "$SIM/out" 2>&1
check "a workspace without a T3 Code server warns and opens nothing" "1 0" "$failures $(wc -l < "$SIM/browsers" | tr -d ' ')"

if [[ "$failed_checks" -gt 0 ]]; then
  echo "$failed_checks check(s) failed" >&2
  exit 1
fi
echo "startup-windows T3 Code tests passed"
