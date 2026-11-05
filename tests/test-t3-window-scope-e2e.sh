#!/usr/bin/env bash
# End-to-end check of bin/t3-window-scope against real T3 Code: two throwaway
# `t3 serve` instances stand in for two devservers' servers, both with a project
# at the same path, and a headless Chrome in a temporary profile is the window.
# The window pairs with the first server, then the helper links the second and
# scopes the window to the first server's project. Everything runs on loopback
# ports and under a temporary directory, and is stopped on exit.
# Needs the t3 CLI and Google Chrome; skips without them.
# Run with: bash tests/test-t3-window-scope-e2e.sh
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
T3="${T3_BIN:-$(command -v t3 || echo "$HOME/.local/bin/t3")}"
CHROME="${CHROME_BIN:-}"
for candidate in "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" google-chrome google-chrome-stable chromium; do
  [[ -n "$CHROME" ]] && break
  if [[ -x "$candidate" ]] || command -v "$candidate" >/dev/null 2>&1; then CHROME="$candidate"; fi
done
if [[ ! -x "$T3" || -z "$CHROME" ]]; then
  echo "skip: needs the t3 CLI and Google Chrome"
  exit 0
fi

failed_checks=0
check() {
  if [[ "$2" == "$3" ]]; then echo "  ok: $1"; else echo "  FAIL: $1 (expected '$2', got '$3')" >&2; failed_checks=$((failed_checks + 1)); fi
}

TMP="$(mktemp -d)"
pids=()
cleanup() {
  for pid in "${pids[@]}"; do kill "$pid" 2>/dev/null || true; done
  for pid in "${pids[@]}"; do wait "$pid" 2>/dev/null || true; done
  rm -rf "$TMP"
}
trap cleanup EXIT

free_port() { python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1])'; }

# serve NAME PORT PROJECT... -- a server with its data in $TMP/NAME and those
# projects added through the CLI, as bin/t3-ensure adds them.
serve() {
  local name="$1" port="$2" project
  shift 2
  mkdir -p "$TMP/$name"
  (cd "$TMP" && T3CODE_HOME="$TMP/$name" exec "$T3" serve --host 127.0.0.1 --port "$port" --no-browser) \
    >"$TMP/$name.log" 2>&1 &
  pids+=("$!")
  for _ in $(seq 1 120); do
    curl -s --noproxy '*' -o /dev/null -m 1 "http://127.0.0.1:$port/" && break
    sleep 0.5
  done
  for project in "$@"; do
    mkdir -p "$project"
    # A server that answers may not take commands yet, as bin/t3-ensure retries.
    for _ in $(seq 1 30); do
      T3CODE_HOME="$TMP/$name" "$T3" project add "$project" >"$TMP/$name.add" 2>&1 && break
      sleep 1
    done || true
  done
}
token() { T3CODE_HOME="$TMP/$1" "$T3" pair --ttl 5m --label e2e 2>/dev/null | sed -n 's/^Token: //p' | tr -d '\r'; }

PORT_A=$(free_port)
PORT_B=$(free_port)
serve a "$PORT_A" "$TMP/home/checkout1" "$TMP/home/alpha"
serve b "$PORT_B" "$TMP/home/checkout1" "$TMP/home/bravo"
PORT_C=$(free_port)
serve c "$PORT_C" "$TMP/home/charlie"

ORIGIN="http://w1.t3.localhost:$PORT_A"
PROFILE="$TMP/chrome"
mkdir -p "$PROFILE"
"$CHROME" --headless=new --user-data-dir="$PROFILE" --no-first-run --no-default-browser-check \
  --remote-debugging-port=0 "$ORIGIN/pair#token=$(token a)" >"$TMP/chrome.log" 2>&1 &
pids+=("$!")

echo "== a window paired with one server, linked to another =="
rc=0
token b | python3 "$ROOT/bin/t3-window-scope" "$PROFILE" "$ORIGIN" "~/checkout1" \
  --link "http://127.0.0.1:$PORT_B" second >"$TMP/scope.out" 2>&1 || rc=$?
sed 's/^/    /' "$TMP/scope.out"
check "the helper links the second server and scopes the window" "0" "$rc"

# What the window shows afterwards, over the same DevTools port: the servers its
# saved connections name, the project of the draft it was left on, and the
# project picker that chat.new (mod+n) opens, which lists every server's.
result=$(python3 - "$ROOT/bin/t3-window-scope" "$PROFILE" "$ORIGIN" "$PORT_A" "$PORT_B" <<'PY'
import importlib.machinery, importlib.util, json, sys, time
loader = importlib.machinery.SourceFileLoader("t3_window_scope", sys.argv[1])
scope = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
loader.exec_module(scope)
profile, origin, port_a, port_b = sys.argv[2:6]
page = scope.app_page(scope.debug_targets(profile) or [], origin)
devtools = scope.DevToolsPage(page["webSocketDebuggerUrl"])
projects_a = scope.fetch_json(devtools, scope.PROJECTS_PATH)["projects"]
checkout1_a = scope.project_for(projects_a, "~/checkout1")["id"]
saved = scope.saved_environment_ids(devtools)
draft = scope.draft_project_id(devtools)
devtools.call("Emulation.setFocusEmulationEnabled", {"enabled": True})
key = dict(scope.new_local_key(devtools.evaluate("navigator.platform") or ""))
key["modifiers"] &= ~scope.SHIFT
key["key"] = "n"
text = ""
for _ in range(20):
    scope.press(devtools, key)
    time.sleep(1)
    text = devtools.evaluate("document.body.innerText") or ""
    if "bravo" in text and "alpha" in text:
        break
    scope.press(devtools, scope.ESCAPE_KEY)
print(json.dumps({"linked": scope.environment_id(f"http://127.0.0.1:{port_b}") in saved,
                  "draftOnA": draft == checkout1_a,
                  "pickerA": "alpha" in text, "pickerB": "bravo" in text}))
PY
)
echo "    $result"
field() { python3 -c 'import json,sys; print(json.loads(sys.argv[1])[sys.argv[2]])' "$result" "$1"; }
check "the window's saved connections name the second server" "True" "$(field linked)"
check "its draft is in the first server's checkout1, though both servers have one" "True" "$(field draftOnA)"
check "its project picker lists the first server's projects" "True" "$(field pickerA)"
check "and the second server's" "True" "$(field pickerB)"

echo "== linking again is a no-op =="
rc=0
echo unused-token | python3 "$ROOT/bin/t3-window-scope" "$PROFILE" "$ORIGIN" "~/checkout1" \
  --link "http://127.0.0.1:$PORT_B" second >"$TMP/again.out" 2>&1 || rc=$?
sed 's/^/    /' "$TMP/again.out"
check "a window that already has the server is not paired again" "0" "$rc"

echo "== a link whose token the server refuses =="
rc=0
echo not-a-token | python3 "$ROOT/bin/t3-window-scope" "$PROFILE" "$ORIGIN" "~/checkout1" \
  --link "http://127.0.0.1:$PORT_C" third >"$TMP/refused.out" 2>&1 || rc=$?
sed 's/^/    /' "$TMP/refused.out"
check "fails, naming the link" "1 yes" "$rc $(grep -q 'could not save third' "$TMP/refused.out" && echo yes || echo no)"
check "but still scopes the window" "yes" "$(grep -q 'starts its new threads in' "$TMP/refused.out" && echo yes || echo no)"

echo "== --link-only links a window in use and leaves it where it was =="
where() {
  python3 - "$ROOT/bin/t3-window-scope" "$PROFILE" "$ORIGIN" "$PORT_C" <<'PY'
import importlib.machinery, importlib.util, json, sys, time
loader = importlib.machinery.SourceFileLoader("t3_window_scope", sys.argv[1])
scope = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
loader.exec_module(scope)
profile, origin, port_c = sys.argv[2:5]
page = None
for _ in range(40):
    page = scope.app_page(scope.debug_targets(profile) or [], origin)
    if page and "/draft/" in page["url"]:
        break
    time.sleep(0.5)
devtools = scope.DevToolsPage(page["webSocketDebuggerUrl"])
print(json.dumps({"url": page["url"], "linked": scope.environment_id(f"http://127.0.0.1:{port_c}") in scope.saved_environment_ids(devtools)}))
PY
}
before=$(where)
rc=0
token c | python3 "$ROOT/bin/t3-window-scope" "$PROFILE" "$ORIGIN" --link-only \
  --link "http://127.0.0.1:$PORT_C" third >"$TMP/linkonly.out" 2>&1 || rc=$?
sed 's/^/    /' "$TMP/linkonly.out"
after=$(where)
echo "    before $before"
echo "    after  $after"
check "links the third server" "0 True" "$rc $(python3 -c 'import json,sys; print(json.loads(sys.argv[1])["linked"])' "$after")"
check "and the window is back on its draft" \
  "$(python3 -c 'import json,sys; print(json.loads(sys.argv[1])["url"])' "$before")" \
  "$(python3 -c 'import json,sys; print(json.loads(sys.argv[1])["url"])' "$after")"

if [[ "$failed_checks" -gt 0 ]]; then
  echo "$failed_checks check(s) failed" >&2
  exit 1
fi
echo "t3-window-scope end-to-end tests passed"
