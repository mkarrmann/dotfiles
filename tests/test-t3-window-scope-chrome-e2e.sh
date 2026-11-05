#!/usr/bin/env bash
# End-to-end check of bin/t3-window-scope's @chrome mode on macOS: the user's own
# running Chrome, an app window in its T3 profile, reached through AppleScript.
# Two throwaway `t3 serve` instances stand in for two devservers' servers, both
# with a project at the same path. The window pairs with the first, the helper
# links the second and scopes the window to the first's project, and
# --list-windows finds the window in AeroSpace. Everything this opens (servers,
# the window) is closed on exit; the T3 profile itself is the user's and stays.
# Needs macOS, the user's Chrome running with "Allow JavaScript from Apple
# Events" on in its T3 profile, AeroSpace and the t3 CLI; skips without them.
# Run with: bash tests/test-t3-window-scope-chrome-e2e.sh
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
T3="${T3_BIN:-$(command -v t3 || echo "$HOME/.local/bin/t3")}"
SCOPE="$ROOT/bin/t3-window-scope"
CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
if [[ "$(uname -s)" != Darwin || ! -x "$T3" || ! -x "$CHROME" ]] || ! command -v aerospace >/dev/null \
    || ! ps -axo command= | awk -v c="$CHROME" 'index($0, c) == 1 && !/--user-data-dir|--type=/ { f = 1 } END { exit !f }'; then
  echo "skip: needs macOS, your Chrome running, AeroSpace and the t3 CLI"
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
open -na "Google Chrome" --args --profile-directory=T3 --app="$ORIGIN/pair#token=$(token a)"

echo "== an app window in your Chrome, linked to a second server and scoped =="
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
check "under a Chrome window AeroSpace knows" "Google Chrome" \
  "$(aerospace list-windows --all --json | python3 -c 'import json,sys; w=[x for x in json.load(sys.stdin) if str(x["window-id"])==sys.argv[1]]; print(w[0]["app-name"] if w else "")' "$listed")"
check "and its title is back to the page's own" "no" \
  "$(aerospace list-windows --all --json | grep -q 't3ws' && echo yes || echo no)"

echo "== linking again is a no-op =="
rc=0
echo unused-token | python3 "$SCOPE" @chrome "$ORIGIN" --link-only --link "http://127.0.0.1:$PORT_B" second >"$TMP/again.out" 2>&1 || rc=$?
sed 's/^/    /' "$TMP/again.out"
check "a window that already has the server is not paired again" "0" "$rc"

if [[ "$failed_checks" -gt 0 ]]; then
  echo "$failed_checks check(s) failed" >&2
  exit 1
fi
echo "t3-window-scope @chrome end-to-end tests passed"
