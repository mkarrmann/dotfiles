#!/usr/bin/env bash
# shellcheck disable=SC2034  # variables below are read by the extracted functions
# Checks bin-macos/startup-windows' T3 Code window lookup with stub aerospace/ps/workspaces.
# Run with: bash tests/test-startup-windows-t3.sh
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
STARTUP="$ROOT/bin-macos/startup-windows"

failures=0
check() {
  if [[ "$2" == "$3" ]]; then echo "  ok: $1"; else echo "  FAIL: $1 (expected '$2', got '$3')" >&2; failures=$((failures + 1)); fi
}

extract_fn() {
  awk -v name="$1" '
    $0 ~ "^" name "\\(\\) \\{" { p = 1 }
    p { print }
    p && $0 == "}" { exit }
  ' "$STARTUP"
}
for fn in t3_workspace_window t3_window_ids_for_profile t3_window_ids_json t3_profile_running t3_take_pairing_token; do
  body=$(extract_fn "$fn")
  [[ -n "$body" ]] || { echo "could not extract $fn" >&2; exit 1; }
  eval "$body"
done

CHROME_APP="Google Chrome"
T3_CHROME_DATA_ROOT="/Users/me/.t3-workspace-chrome"

workspaces() {
  printf '%s' '{"workspaces":[
    {"workspace":"1","kind":"local"},
    {"workspace":"6","kind":"devserver","host":"cco.example.com","checkout":"/home/me/checkout3","t3Url":"http://cco-checkout3.t3.localhost:13783/"}]}'
}

# Windows 10 and 11 are regular Chrome, 20 belongs to checkout3's profile, 30 to
# checkout30's (a prefix of the same name), 40 is another app.
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
    200) echo "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome --user-data-dir=/Users/me/.t3-workspace-chrome/cco-checkout3 --app=http://cco-checkout3.t3.localhost:13783/" ;;
    300) echo "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome --user-data-dir=/Users/me/.t3-workspace-chrome/cco-checkout30" ;;
  esac
}

check "a devserver workspace resolves to its profile, URL, devserver and checkout" \
  $'/Users/me/.t3-workspace-chrome/cco-checkout3\thttp://cco-checkout3.t3.localhost:13783/\tcco.example.com\tcheckout3' \
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

T3_PAIRING_DIR="$(mktemp -d)"
trap 'rm -rf "$T3_PAIRING_DIR"' EXIT
mkdir -p "$T3_PAIRING_DIR/cco.example.com"
echo TOKEN123 > "$T3_PAIRING_DIR/cco.example.com/checkout3"
t3_token_waited_hosts=" cco.example.com"
check "a saved pairing token is handed out" "TOKEN123" "$(t3_take_pairing_token cco.example.com checkout3)"
check "and only once" "" "$(t3_take_pairing_token cco.example.com checkout3)"

# startup-windows runs under set -euo pipefail and assigns the result directly;
# a lookup that finds nothing must not end the script.
window_id=$(t3_window_ids_for_profile /Users/me/.t3-workspace-chrome/cco-checkout1 | head -1)
check "a lookup that finds nothing does not fail under errexit" "" "$window_id"

if [[ "$failures" -gt 0 ]]; then
  echo "$failures check(s) failed" >&2
  exit 1
fi
echo "startup-windows T3 Code tests passed"
