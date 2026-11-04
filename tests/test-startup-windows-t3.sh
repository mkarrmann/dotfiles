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
          t3_take_pairing_token t3_wait_for_server t3_window_title reconcile_t3_window close_window_id window_exists \
          snapshot_windows; do
  body=$(extract_fn "$fn")
  [[ -n "$body" ]] || { echo "could not extract $fn" >&2; exit 1; }
  eval "$body"
done

CHROME_APP="Google Chrome"
T3_CHROME_DATA_ROOT="/Users/me/.t3-workspace-chrome"

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
echo TOKEN123 > "$T3_PAIRING_DIR/cco.example.com/checkout3"
curl() { return 0; }
t3_waited_hosts=""
started=$SECONDS
t3_wait_for_server cco.example.com http://cco-checkout3.t3.localhost:13780/ checkout3
check "the server wait returns once the server answers and its token is saved" "0" "$((SECONDS - started))"
check "a saved pairing token is handed out" "TOKEN123" "$(t3_take_pairing_token cco.example.com checkout3)"
check "and only once" "" "$(t3_take_pairing_token cco.example.com checkout3)"

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
    {"workspace":"4","kind":"devserver","host":"cco.example.com","checkout":"/home/me/checkout2","t3Url":"http://cco-checkout2.t3.localhost:13780/"},
    {"workspace":"6","kind":"devserver","host":"cco.example.com","checkout":"/home/me/checkout3","t3Url":"http://cco-checkout3.t3.localhost:13780/"}]}'
}
sim_reset() {
  rm -rf "$SIM"; mkdir -p "$SIM"
  : > "$SIM/windows"; : > "$SIM/browsers"; : > "$SIM/scoped"; echo 100 > "$SIM/next"
  rm -rf "$T3_PAIRING_DIR/cco.example.com"; mkdir -p "$T3_PAIRING_DIR/cco.example.com"
  t3_waited_hosts=""; failures=0; SCOPE_FAILS=""
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
t3-window-scope() {
  local pid
  pid=$(sim_browser "$1")
  [[ -z "$SCOPE_FAILS" && -n "$pid" && "$(ps -o command= -p "$pid")" == *" --remote-debugging-port=0"* ]] || return 1
  printf '%s\n' "$*" >> "$SIM/scoped"
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

sim_reset
echo TOKEN3 > "$T3_PAIRING_DIR/cco.example.com/checkout3"
reconcile_t3_window 6 "T3: ws6" > "$SIM/out" 2>&1
check "a cold profile gets one window, on its workspace and its pairing link, and nothing else" \
  "102 6 http://cco-checkout3.t3.localhost:13780/pair#token=TOKEN3" "$(windows_of "$P3")"
check "its browser runs with a DevTools port and without a startup window" "yes" \
  "$([[ "$(ps -o command= -p "$(sim_browser "$P3")")" == *" --no-startup-window --remote-debugging-port=0"* ]] && echo yes)"
check "the window this opened starts its new threads in its checkout, named from the server's home" \
  "$SCOPE3" "$(cat "$SIM/scoped")"
check "with no warning" "0" "$failures"
check "its pairing token is used up" "no" "$([[ -e "$T3_PAIRING_DIR/cco.example.com/checkout3" ]] && echo yes || echo no)"

aerospace move-node-to-workspace --window-id 102 Z
: > "$SIM/scoped"
reconcile_t3_window 6 "T3: ws6" > "$SIM/out" 2>&1
check "a window it finds goes back to its workspace" "102 6 http://cco-checkout3.t3.localhost:13780/pair#token=TOKEN3" \
  "$(windows_of "$P3")"
check "and is left as it is: not scoped again" "" "$(cat "$SIM/scoped")"
check "with no warning" "0" "$failures"

aerospace close --window-id 102
reconcile_t3_window 6 "T3: ws6" > "$SIM/out" 2>&1
check "after its window closes the profile's browser, still running, opens the next one" \
  "103 6 http://cco-checkout3.t3.localhost:13780/" "$(windows_of "$P3")"
check "which is scoped too: the browser kept its DevTools port" "$SCOPE3" "$(cat "$SIM/scoped")"

sim_reset
pid=$(sim_start_browser "$P3" --user-data-dir="$P3" --app=http://cco-checkout3.t3.localhost:13783/)
sim_window "$pid" "cco-checkout3.t3.localhost" "http://cco-checkout3.t3.localhost:13783/"
awk -F'\t' -v OFS='\t' '{ $4 = "6" } 1' "$SIM/windows" > "$SIM/w" && mv "$SIM/w" "$SIM/windows"
reconcile_t3_window 6 "T3: ws6" > "$SIM/out" 2>&1
check "a window left on an error page is replaced" "103 6 http://cco-checkout3.t3.localhost:13780/" "$(windows_of "$P3")"
check "but a browser from before the DevTools port cannot have its window scoped" "" "$(cat "$SIM/scoped")"
check "which warns once, saying to quit that browser" "1 1" \
  "$failures $(grep -c 'quit that Chrome (Cmd+Q in the window) and rerun' "$SIM/out")"

sim_reset
SCOPE_FAILS=1
reconcile_t3_window 4 "T3: ws4" > "$SIM/out" 2>&1
check "a window whose scoping fails stays on its workspace" "102 4 http://cco-checkout2.t3.localhost:13780/" "$(windows_of "$P2")"
check "and the run counts a warning" "1" "$failures"

sim_reset
reconcile_t3_window 1 "T3: ws1" > "$SIM/out" 2>&1
check "a workspace without a T3 Code server warns and opens nothing" "1 0" "$failures $(wc -l < "$SIM/browsers" | tr -d ' ')"

if [[ "$failed_checks" -gt 0 ]]; then
  echo "$failed_checks check(s) failed" >&2
  exit 1
fi
echo "startup-windows T3 Code tests passed"
