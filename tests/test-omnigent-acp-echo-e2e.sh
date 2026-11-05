#!/usr/bin/env bash
# End-to-end check that turns started outside T3 Code show in its thread: a
# throwaway `t3 serve` whose Omnigent provider is this checkout's
# bin/omnigent-acp, one thread started through it, and messages posted
# straight into that thread's Omnigent session the way a watcher or a
# background task's notification arrives. Each must show in the T3 Code thread
# as an echo followed by the agent's one reply, and never reach the agent twice.
#
# Uses the real Omnigent server (OMNIGENT_URL, default http://127.0.0.1:6767)
# and its host on this machine: the agent (claude-native-ui) runs for real, in
# a temporary directory, and its Omnigent session is deleted on exit, as is
# everything else. Needs the t3 CLI, uv and a reachable Omnigent; skips without.
# E2E_IDLE_S (default 0) also waits that long, past T3 Code's 30-minute idle
# release, before a last outside message.
# Run with: bash tests/test-omnigent-acp-echo-e2e.sh
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
T3="${T3_BIN:-$(command -v t3 || echo "$HOME/.local/bin/t3")}"
OMNIGENT_URL="${OMNIGENT_URL:-http://127.0.0.1:6767}"
MODEL="${E2E_MODEL:-sonnet}"
IDLE_S="${E2E_IDLE_S:-0}"
if [[ ! -x "$T3" ]] || ! command -v uv >/dev/null || ! curl -sf --noproxy '*' -m 5 -o /dev/null "$OMNIGENT_URL/v1/hosts"; then
  echo "skip: needs the t3 CLI, uv and Omnigent at $OMNIGENT_URL"
  exit 0
fi

failed_checks=0
check() {
  if [[ "$2" == "$3" ]]; then echo "  ok: $1"; else echo "  FAIL: $1 (expected '$2', got '$3')" >&2; failed_checks=$((failed_checks + 1)); fi
}

TMP="$(cd "$(mktemp -d)" && pwd -P)"
WS="$TMP/ws"
mkdir -p "$TMP/home/userdata" "$WS" "$TMP/bin"
ln -s "$ROOT/bin/omnigent-acp" "$TMP/bin/omnigent-acp"
pids=()
conversation=""
cleanup() {
  [[ -z "$conversation" ]] || curl -s --noproxy '*' -m 10 -X DELETE -o /dev/null "$OMNIGENT_URL/v1/sessions/$conversation" || true
  for pid in "${pids[@]}"; do kill "$pid" 2>/dev/null || true; done
  for pid in "${pids[@]}"; do wait "$pid" 2>/dev/null || true; done
  [[ -n "${KEEP_TMP:-}" ]] && echo "kept $TMP" || rm -rf "$TMP"
}
trap cleanup EXIT

cat > "$TMP/home/userdata/settings.json" <<JSON
{"providerInstances": {
  "omnigent-claude": {"driver": "acpRegistry", "displayName": "Omnigent · Claude",
    "config": {"source": "local", "commandPath": "omnigent-acp",
               "commandArgs": ["--agent", "claude-native-ui", "--trace", "$TMP/trace.jsonl", "--log", "$TMP/shim.log"]}},
  "codex": {"driver": "codex", "enabled": false},
  "claudeAgent": {"driver": "claudeAgent", "enabled": false}}}
JSON

PORT=$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1])')
(cd "$TMP" && PATH="$TMP/bin:$HOME/.local/bin:$PATH" OMNIGENT_URL="$OMNIGENT_URL" OMNIGENT_ACP_LOG=DEBUG \
  T3CODE_HOME="$TMP/home" exec "$T3" serve --host 127.0.0.1 --port "$PORT" --no-browser) >"$TMP/server.log" 2>&1 &
pids+=("$!")
t3() { T3CODE_HOME="$TMP/home" "$T3" "$@"; }
for _ in $(seq 1 60); do t3 project add "$WS" >/dev/null 2>&1 && break; sleep 1; done

# omnigent GET PATH -> its JSON body.
omnigent() { curl -sf --noproxy '*' -m 20 "$OMNIGENT_URL$1"; }
post_outside() {
  python3 -c 'import json,sys; print(json.dumps({"type": "message", "data": {"role": "user", "content": [{"type": "input_text", "text": sys.argv[1]}]}}))' "$1" \
    | curl -sf --noproxy '*' -m 20 -H 'Content-Type: application/json' -d @- "$OMNIGENT_URL/v1/sessions/$conversation/events" >/dev/null
}
# The thread's messages as "role|text" lines (newlines in a text as \n).
cat > "$TMP/transcript.py" <<'PY'
import json, sys
doc = json.load(sys.stdin)
def text_of(m):
    t = m.get("text") if m.get("text") is not None else m.get("content", "")
    return "".join(c.get("text", "") for c in t if isinstance(c, dict)) if isinstance(t, list) else str(t)
for m in (doc.get("messages") if isinstance(doc, dict) else doc) or []:
    print((str(m.get("role") or m.get("kind") or m.get("type")) + "|" + text_of(m)).replace("\n", "\\n"))
PY
transcript() {
  t3 thread show --json --messages 100 "$thread" > "$TMP/thread.json" 2>/dev/null || true
  python3 "$TMP/transcript.py" < "$TMP/thread.json" 2>/dev/null || true
}
# wait_for WHAT SECONDS CMD... -- until CMD succeeds.
wait_for() {
  local what="$1" seconds="$2"; shift 2
  for _ in $(seq 1 "$seconds"); do "$@" && return 0; sleep 1; done
  echo "  (gave up waiting for $what)" >&2
  return 1
}
replied() { transcript | grep -q "^assistant|.*$1"; }
thread_updated() { t3 thread show --json --messages 1 "$thread" | python3 -c 'import json,sys; print(json.load(sys.stdin)["thread"].get("updatedAt"))'; }
# Omnigent's assistant replies containing WORD, and whether any user message there carries an echo marker.
omnigent_replies() {
  omnigent "/v1/sessions/$conversation/items?order=asc&limit=1000" | python3 -c '
import json, sys
word = sys.argv[1]
items = json.load(sys.stdin)["data"]
text = lambda i: "".join(c.get("text", "") for c in i.get("content") or [] if isinstance(c, dict))
print(sum(1 for i in items if i.get("type") == "message" and i.get("role") == "assistant" and word in text(i)))' "$1"
}
echo_reached_agent() {
  omnigent "/v1/sessions/$conversation/items?order=asc&limit=1000" | python3 -c '
import json, sys
items = json.load(sys.stdin)["data"]
print(any("omnigent-echo:" in "".join(c.get("text", "") for c in i.get("content") or [] if isinstance(c, dict)) for i in items))'
}

echo "== a thread started in T3 Code =="
thread=$(t3 thread start --provider omnigent-claude --model "$MODEL" --json --wait "$WS" "Reply with exactly the word: ready" \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["threadId"])')
check "it replies" "yes" "$(replied ready && echo yes || echo no)"
conversation=$(omnigent "/v1/sessions?limit=100&sort_by=updated_at" | python3 -c '
import json, sys
ws = sys.argv[1]
print(next(s["id"] for s in json.load(sys.stdin)["data"] if s.get("workspace") == ws and (s.get("labels") or {}).get("t3.client") == "t3code"))' "$WS")
echo "    thread $thread, Omnigent session $conversation"

echo "== a watcher's message posted straight into the Omnigent session =="
post_outside "[Watcher e2e] Reply with exactly the word: mango"
wait_for "the reply in T3 Code" 180 replied mango || true
check "T3 Code shows the agent's reply" "yes" "$(replied mango && echo yes || echo no)"
check "after an echo of the watcher's message" "yes" "$(transcript | grep -q '\[Watcher e2e\] Reply with exactly the word: mango' && echo yes || echo no)"
check "the agent answered it once" "1" "$(omnigent_replies mango)"
check "and the echo never reached the agent" "False" "$(echo_reached_agent)"

echo "== a background task's notification =="
post_outside "Use the Bash tool with run_in_background=true to run: sleep 20; echo banana. End your turn right away saying only: started. When the task finishes, reply with only the word it printed."
wait_for "the notification's reply in T3 Code" 240 replied banana || true
check "T3 Code shows the reply to the notification" "yes" "$(replied banana && echo yes || echo no)"
check "after an echo naming the background task" "yes" "$(transcript | grep -q 'Background task' && echo yes || echo no)"
check "the agent answered the notification once" "1" "$(omnigent_replies banana)"
check "and no echo reached the agent" "False" "$(echo_reached_agent)"

echo "== the thread's own next message still works =="
t3 thread send "$thread" "Reply with exactly the word: kiwi" >/dev/null
wait_for "kiwi" 180 replied kiwi || true
check "T3 Code shows the reply" "yes" "$(replied kiwi && echo yes || echo no)"
check "the agent answered once" "1" "$(omnigent_replies kiwi)"

if [[ "$IDLE_S" -gt 0 ]]; then
  echo "== after $IDLE_S idle seconds =="
  shim_pid() { pgrep -f "$TMP/trace.jsonl" | head -n 1; }
  before=$(shim_pid)
  updated_before=$(thread_updated)
  sleep "$IDLE_S"
  check "the shim is still running" "$before" "$(shim_pid)"
  updated_after=$(thread_updated)
  check "the heartbeats left the thread's updatedAt alone" "$updated_before" "$updated_after"
  post_outside "[Watcher e2e] Reply with exactly the word: papaya"
  wait_for "papaya" 180 replied papaya || true
  check "a watcher's message after the idle wait still reaches T3 Code" "yes" "$(replied papaya && echo yes || echo no)"
fi

echo "    the shim's memory (uv and Python): $(pgrep -f "$TMP/trace.jsonl" | xargs ps -o rss= -p | awk '{ kb += $1 } END { printf "%d MB", kb / 1024 }')"
echo "    transcript:"
transcript | sed 's/^/      /' | cut -c1-200
if [[ "$failed_checks" -gt 0 ]]; then
  echo "$failed_checks check(s) failed" >&2
  exit 1
fi
echo "omnigent-acp echo end-to-end tests passed"
