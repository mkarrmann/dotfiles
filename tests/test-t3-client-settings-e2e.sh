#!/usr/bin/env bash
# End-to-end check of bin/t3-client-settings against real T3 Code: a throwaway
# `t3 serve` and two headless Chromes, each paired with it in a temporary
# profile under a temporary profiles directory, stand in for two workspace
# windows. Render math is switched on in one window's own settings page; a
# sync puts it in the (temporary) dotfiles file and in the other window, which
# shows it on its settings page after the reload. Everything runs on loopback
# ports and under a temporary directory, and is stopped on exit.
# Needs the t3 CLI and Google Chrome; skips without them.
# Run with: bash tests/test-t3-client-settings-e2e.sh
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

PORT=$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1])')
mkdir -p "$TMP/server" "$TMP/profiles" "$TMP/dotfiles"
(cd "$TMP" && T3CODE_HOME="$TMP/server" exec "$T3" serve --host 127.0.0.1 --port "$PORT" --no-browser) >"$TMP/server.log" 2>&1 &
pids+=("$!")
for _ in $(seq 1 120); do
  curl -s --noproxy '*' -o /dev/null -m 1 "http://127.0.0.1:$PORT/" && break
  sleep 0.5
done
# A server that answers HTTP may not have recorded itself for `t3 pair` yet.
token() {
  local t=""
  for _ in $(seq 1 30); do
    t=$(T3CODE_HOME="$TMP/server" "$T3" pair --ttl 5m --label e2e 2>/dev/null | sed -n 's/^Token: //p' | tr -d '\r')
    [[ -n "$t" ]] && break
    sleep 1
  done
  echo "$t"
}

for w in w1 w2; do
  mkdir -p "$TMP/profiles/$w"
  "$CHROME" --headless=new --user-data-dir="$TMP/profiles/$w" --no-first-run --no-default-browser-check \
    --remote-debugging-port=0 "http://$w.t3.localhost:$PORT/pair#token=$(token)" >"$TMP/$w.chrome.log" 2>&1 &
  pids+=("$!")
done

# page W JS -> the value of JS in window W's page once it is past pairing.
page() {
  python3 - "$ROOT/bin/t3-window-scope" "$TMP/profiles/$1" "http://$1.t3.localhost:$PORT" "$2" <<'PY'
import importlib.machinery, importlib.util, json, sys, time
loader = importlib.machinery.SourceFileLoader("t3_window_scope", sys.argv[1])
scope = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
loader.exec_module(scope)
profile, origin, js = sys.argv[2:5]
deadline = time.monotonic() + 60
while True:
    try:
        page = scope.app_page(scope.debug_targets(profile) or [], origin)
        if page:
            print(json.dumps(scope.DevTools(page["webSocketDebuggerUrl"]).evaluate(js, await_promise=True)))
            break
    except OSError:
        pass
    if time.monotonic() > deadline:
        sys.exit("gave up on " + origin)
    time.sleep(0.5)
PY
}
# The Render math switch on W's appearance settings, once that page shows it.
SWITCH='(async () => {
  if (location.pathname !== "/settings/appearance") { location.assign("/settings/appearance"); return "navigating"; }
  for (let i = 0; i < 100; i++) {
    const s = document.querySelector("[aria-label=\"Render math\"]");
    if (s) return s.getAttribute("aria-checked") ?? String(s.checked ?? s.dataset.checked);
    await new Promise(r => setTimeout(r, 200));
  }
  return "missing";
})()'
switch_state() {
  local state
  for _ in $(seq 1 20); do
    state=$(page "$1" "$SWITCH")
    [[ "$state" == '"navigating"' ]] || { echo "$state"; return; }
    sleep 1
  done
  echo "$state"
}

sync() {
  T3_CLIENT_SETTINGS_ORIGINS="http://w1.t3.localhost:$PORT http://w2.t3.localhost:$PORT" \
    T3_CHROME_PROFILES_DIR="$TMP/profiles" T3_CLIENT_SETTINGS_FILE="$TMP/dotfiles/client-settings.json" \
    T3_DESKTOP_CLIENT_SETTINGS="$TMP/no-desktop-app.json" python3 "$ROOT/bin/t3-client-settings" "$@"
}

echo "== both windows start at T3 Code's default =="
check "w1's Render math is off" '"false"' "$(switch_state w1)"
check "w2's Render math is off" '"false"' "$(switch_state w2)"
sync | sed 's/^/    /'
check "a sync with nothing changed puts nothing in the file" "{}" "$(cat "$TMP/dotfiles/client-settings.json" 2>/dev/null | tr -d ' \n' || echo '{}')"

echo "== Render math switched on in w1's settings page =="
page w1 'document.querySelector("[aria-label=\"Render math\"]").click(); "clicked"' >/dev/null
sleep 1
check "w1 shows it on" '"true"' "$(switch_state w1)"
before=$(page w2 'performance.timeOrigin')
sync | sed 's/^/    /'
check "the dotfiles file holds only that setting" '{"mathRenderingEnabled":true}' "$(tr -d ' \n' < "$TMP/dotfiles/client-settings.json")"
sleep 2
check "w2 was reloaded" "yes" "$([[ "$(page w2 'performance.timeOrigin')" != "$before" ]] && echo yes || echo no)"
check "and its settings page shows Render math on" '"true"' "$(switch_state w2)"

echo "== switched back off in w2 =="
page w2 'document.querySelector("[aria-label=\"Render math\"]").click(); "clicked"' >/dev/null
sleep 1
sync | sed 's/^/    /'
check "the file is back to holding nothing" "{}" "$(tr -d ' \n' < "$TMP/dotfiles/client-settings.json")"
sleep 2
check "and w1 shows it off" '"false"' "$(switch_state w1)"

if [[ "$failed_checks" -gt 0 ]]; then
  echo "$failed_checks check(s) failed" >&2
  exit 1
fi
echo "t3-client-settings end-to-end tests passed"
