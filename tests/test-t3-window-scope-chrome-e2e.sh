#!/usr/bin/env bash
# End-to-end check of bin/t3-window-scope's @chrome mode on macOS: T3 Code's
# running browser (T3_BROWSER_APP, Google Chrome Beta unless set), an app window
# in it, reached through AppleScript.
# Two throwaway `t3 serve` instances stand in for two devservers' servers, both
# with a project at the same path. The window pairs with the first, the helper
# links the second and scopes the window to the first's project, and
# --list-windows finds the window in AeroSpace. Everything this opens (servers,
# the window) is closed on exit; the browser's profile itself is the user's and stays.
# Needs macOS, that browser running with "Allow JavaScript from Apple Events"
# on, AeroSpace and the t3 CLI; skips without them.
# Run with: bash tests/test-t3-window-scope-chrome-e2e.sh
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
T3="${T3_BIN:-$(command -v t3 || echo "$HOME/.local/bin/t3")}"
SCOPE="$ROOT/bin/t3-window-scope"
BROWSER="${T3_BROWSER_APP:-Google Chrome Beta}"
if [[ "$(uname -s)" != Darwin || ! -x "$T3" ]] || ! command -v aerospace >/dev/null \
    || ! python3 -c 'import importlib.machinery as m, importlib.util as u, sys
l = m.SourceFileLoader("s", sys.argv[1]); s = u.module_from_spec(u.spec_from_loader("s", l)); l.exec_module(s)
s.own_chrome_pid()' "$SCOPE" 2>/dev/null; then
  echo "skip: needs macOS, $BROWSER running, AeroSpace and the t3 CLI"
  exit 0
fi

failed_checks=0
check() {
  if [[ "$2" == "$3" ]]; then echo "  ok: $1"; else echo "  FAIL: $1 (expected '$2', got '$3')" >&2; failed_checks=$((failed_checks + 1)); fi
}

TMP="$(cd "$(mktemp -d)" && pwd -P)"
pids=()
ORIGIN=""
# py SNIPPET ARGS... -- run SNIPPET with t3-window-scope loaded as `scope`.
py() {
  local snippet="$1"
  shift
  python3 - "$SCOPE" "$@" <<PY
import importlib.machinery, importlib.util, json, sys, time
loader = importlib.machinery.SourceFileLoader("t3_window_scope", sys.argv[1])
scope = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
loader.exec_module(scope)
args = sys.argv[2:]
$snippet
PY
}
cleanup() {
  [[ -z "$ORIGIN" ]] || py '
pid = scope.own_chrome_pid()
found = scope.jxa(pid, "find", args[0])
if found:
    scope.jxa(pid, "close", str(found["id"]))
' "$ORIGIN" 2>/dev/null || true
  for pid in "${pids[@]}"; do kill "$pid" 2>/dev/null || true; done
  for pid in "${pids[@]}"; do wait "$pid" 2>/dev/null || true; done
  rm -rf "$TMP"
}
trap cleanup EXIT

free_port() { python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1])'; }
serve() {
  local name="$1" port="$2" project
  shift 2
  mkdir -p "$TMP/$name"
  (cd "$TMP" && T3CODE_HOME="$TMP/$name" exec "$T3" serve --host 127.0.0.1 --port "$port" --no-browser) \
    >"$TMP/$name.log" 2>&1 &
  pids+=("$!")
  for project in "$@"; do
    mkdir -p "$project"
    for _ in $(seq 1 60); do
      T3CODE_HOME="$TMP/$name" "$T3" project add "$project" >/dev/null 2>&1 && break
      sleep 1
    done
  done
}
token() {
  local t=""
  for _ in $(seq 1 30); do
    t=$(T3CODE_HOME="$TMP/$1" "$T3" pair --ttl 5m --label e2e 2>/dev/null | sed -n 's/^Token: //p' | tr -d '\r')
    [[ -n "$t" ]] && break
    sleep 1
  done
  echo "$t"
}

PORT_A=$(free_port)
PORT_B=$(free_port)
serve a "$PORT_A" "$TMP/home/checkout1" "$TMP/home/alpha"
serve b "$PORT_B" "$TMP/home/checkout1" "$TMP/home/bravo"
ORIGIN="http://e2e$PORT_A.t3.localhost:$PORT_A"
open -na "$BROWSER" --args --profile-directory=Default --app="$ORIGIN/pair#token=$(token a)"

echo "== an app window in $BROWSER, linked to a second server and scoped =="
rc=0
token b | python3 "$SCOPE" @chrome "$ORIGIN" "~/checkout1" --link "http://127.0.0.1:$PORT_B" second >"$TMP/scope.out" 2>&1 || rc=$?
sed 's/^/    /' "$TMP/scope.out"
check "the helper links the second server and scopes the window" "0" "$rc"

result=$(py '
page, _ = scope.chrome_page(args[0])
projects = scope.fetch_json(page, scope.PROJECTS_PATH)["projects"]
checkout1 = scope.project_for(projects, "~/checkout1")["id"]
print(json.dumps({
    "linked": scope.environment_id("http://127.0.0.1:" + args[1]) in scope.saved_environment_ids(page),
    "draftOnA": scope.draft_project_id(page) == checkout1,
}))' "$ORIGIN" "$PORT_B")
echo "    $result"
field() { python3 -c 'import json,sys; print(json.loads(sys.argv[1])[sys.argv[2]])' "$result" "$1"; }
check "the window's saved connections name the second server" "True" "$(field linked)"
check "its draft is in the first server's checkout1, though both servers have one" "True" "$(field draftOnA)"

echo "== --list-windows ties the window to AeroSpace =="
listed=$(python3 "$SCOPE" --list-windows | awk -v o="$ORIGIN" '$2 == o { print $1 }')
check "the window is listed once, by its origin" "1" "$(printf '%s\n' "$listed" | grep -c '^[0-9]')"
check "under a window of the browser AeroSpace knows" "$BROWSER" \
  "$(aerospace list-windows --all --json | python3 -c 'import json,sys; w=[x for x in json.load(sys.stdin) if str(x["window-id"])==sys.argv[1]]; print(w[0]["app-name"] if w else "")' "$listed")"
check "and its title is back to the page's own" "no" \
  "$(aerospace list-windows --all --json | grep -q 't3ws' && echo yes || echo no)"

echo "== linking again is a no-op =="
rc=0
echo unused-token | python3 "$SCOPE" @chrome "$ORIGIN" --link-only --link "http://127.0.0.1:$PORT_B" second >"$TMP/again.out" 2>&1 || rc=$?
sed 's/^/    /' "$TMP/again.out"
check "a window that already has the server is not paired again" "0" "$rc"

echo "== t3-client-settings syncs the window through AppleScript =="
mkdir -p "$TMP/dotfiles"
echo '{"mathRenderingEnabled": true}' > "$TMP/dotfiles/client-settings.json"
client_settings() {
  T3_CLIENT_SETTINGS_ORIGINS="$ORIGIN" T3_WINDOW_STATE_DIR="$TMP/state" T3_CHROME_PROFILES_DIR="$TMP/no-profiles" \
    T3_CLIENT_SETTINGS_FILE="$TMP/dotfiles/client-settings.json" T3_DESKTOP_CLIENT_SETTINGS="$TMP/no-desktop-app.json" \
    python3 "$ROOT/bin/t3-client-settings"
}
stored_math() {
  sleep 2  # the page reloads after a write
  py '
page, _ = scope.chrome_page(args[0])
print(scope.parsed_document(page.evaluate("localStorage.getItem(\"t3code:client-settings:v1\")")).get("mathRenderingEnabled"))
' "$ORIGIN"
}
client_settings | sed 's/^/    /'
check "the file's setting reaches the window" "True" "$(stored_math)"
check "and the window's state records what was written" '{"mathRenderingEnabled": true}' \
  "$(python3 -c 'import json,sys; print(json.dumps(json.load(open(sys.argv[1]))))' "$TMP/state/e2e$PORT_A/.t3-client-settings.json")"
py '
page, _ = scope.chrome_page(args[0])
doc = scope.parsed_document(page.evaluate("localStorage.getItem(\"t3code:client-settings:v1\")"))
doc["mathRenderingEnabled"] = False
page.evaluate("localStorage.setItem(\"t3code:client-settings:v1\", " + json.dumps(json.dumps(doc)) + ")")
' "$ORIGIN"
client_settings | sed 's/^/    /'
check "a setting changed in the window reaches the file" "{}" "$(tr -d ' \n' < "$TMP/dotfiles/client-settings.json")"

if [[ "$failed_checks" -gt 0 ]]; then
  echo "$failed_checks check(s) failed" >&2
  exit 1
fi
echo "t3-window-scope @chrome end-to-end tests passed"
