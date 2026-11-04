#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
TUNNELS="$ROOT/bin-macos/nvs-tunnels"
STARTUP="$ROOT/bin-macos/startup-windows"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

failures=0
fail() { echo "  FAIL: $*" >&2; failures=$((failures + 1)); }

mkdir -p "$TMP/bin"
cat > "$TMP/bin/omnigent-server-url" <<'EOF'
#!/usr/bin/env bash
case "$1" in
  --port) echo 6767 ;;
  --primary) echo primary.example.com ;;
  --standby) echo standby.example.com ;;
  --primary-tunnel-port) echo 16767 ;;
  --standby-tunnel-port) echo 26767 ;;
  *) exit 2 ;;
esac
EOF
cat > "$TMP/bin/nvs-clip-listen" <<'EOF'
#!/usr/bin/env bash
exit 0
EOF
cat > "$TMP/bin/x2ssh" <<'EOF'
#!/usr/bin/env bash
host="$2"
printf '%s\n' "$*" >> "$FAKE_ARGS"
case "$FAKE_MODE" in
  retry)
    count=$(cat "$FAKE_COUNT" 2>/dev/null || echo 0)
    count=$((count + 1))
    echo "$count" > "$FAKE_COUNT"
    if [[ "$count" -eq 1 ]]; then
      trap 'echo terminated > "$FAKE_TERMINATED"; exit 0' TERM INT
      echo "Error connecting to server: 3: Client is not registered"
      while true; do sleep 1; done
    fi
    echo "NVS_TUNNEL_READY"
    ;;
  t3pair)
    echo "NVS_TUNNEL_READY"
    echo "T3_PAIR TOKENabc123"
    echo "T3_PAIR TOKENdef456"
    sleep 0.5
    ;;
  ready)
    echo "NVS_TUNNEL_READY"
    ;;
  serialize)
    echo "$host" >> "$FAKE_ENTRIES"
    if [[ "$host" == primary.example.com ]]; then
      while [[ ! -e "$FAKE_RELEASE" ]]; do sleep 0.05; done
    fi
    echo "NVS_TUNNEL_READY"
    ;;
  *) exit 2 ;;
esac
EOF
chmod +x "$TMP/bin/omnigent-server-url" "$TMP/bin/nvs-clip-listen" "$TMP/bin/x2ssh"
# T3 Code's ports, off the shipped values so the test sees they come from here.
printf '%s\n' T3_PORT=4780 T3_PRIMARY_TUNNEL_PORT=41780 T3_STANDBY_TUNNEL_PORT=42780 > "$TMP/topology.env"

run_tunnels() {
  PATH="$TMP/bin:/usr/bin:/bin" \
    TMPDIR="$TMP" \
    OMNIGENT_TOPOLOGY_FILE="$TMP/topology.env" \
    NVS_TUNNEL_BOOTSTRAP_LOCK_DIR="$TMP/bootstrap.lock" \
    NVS_TUNNEL_MONITOR_INTERVAL=0.05 \
    NVS_TUNNEL_RETRY_DELAY=0 \
    NVS_TUNNEL_MAX_ATTEMPTS="$1" \
    "$TUNNELS" "${@:2}"
}

echo "1. a stuck registration attempt is terminated and retried"
export FAKE_MODE=retry
export FAKE_COUNT="$TMP/count"
export FAKE_TERMINATED="$TMP/terminated"
export FAKE_ARGS="$TMP/args"
if ! run_tunnels 2 primary.example.com TEST-checkout1 > "$TMP/retry.out" 2>&1; then
  fail "nvs-tunnels did not recover from the simulated registration failure"
fi
[[ "$(cat "$FAKE_COUNT" 2>/dev/null || true)" == 2 ]] \
  || fail "expected two x2ssh attempts"
[[ -e "$FAKE_TERMINATED" ]] \
  || fail "the stuck x2ssh attempt was not terminated"
grep -Fq "ET bootstrap failed; terminating this attempt so it can retry." "$TMP/retry.out" \
  || fail "retry reason was not reported"
[[ ! -e "$TMP/bootstrap.lock" ]] \
  || fail "bootstrap lock survived after nvs-tunnels exited"
grep -Fq -- "-c zsh -lc 'echo NVS_TUNNEL_READY;" "$FAKE_ARGS" \
  || fail "remote readiness command is not safely nested inside zsh -lc"

echo "2. concurrent tunnel processes serialize interactive bootstrap"
export FAKE_MODE=serialize
export FAKE_ENTRIES="$TMP/entries"
export FAKE_RELEASE="$TMP/release"
: > "$FAKE_ENTRIES"
run_tunnels 1 primary.example.com TEST-primary > "$TMP/primary.out" 2>&1 &
primary_pid=$!
for _ in $(seq 1 100); do
  grep -Fq primary.example.com "$FAKE_ENTRIES" && break
  sleep 0.05
done
run_tunnels 1 standby.example.com TEST-standby > "$TMP/standby.out" 2>&1 &
standby_pid=$!
sleep 0.25
if grep -Fq standby.example.com "$FAKE_ENTRIES"; then
  fail "standby x2ssh started before primary released the bootstrap lock"
fi
touch "$FAKE_RELEASE"
wait "$primary_pid" || fail "primary serialized run failed"
wait "$standby_pid" || fail "standby serialized run failed"
[[ "$(sed -n '1p' "$FAKE_ENTRIES")" == primary.example.com ]] \
  || fail "primary did not enter bootstrap first"
[[ "$(sed -n '2p' "$FAKE_ENTRIES")" == standby.example.com ]] \
  || fail "standby did not enter bootstrap second"

extract_fn() {
  awk -v fn="$1" '
    index($0, fn "() {") == 1 { p = 1 }
    p { print }
    p && $0 == "}" { exit }
  ' "$STARTUP"
}

for fn in tunnel_command_is_current tunnel_et_is_stuck t3_forwards_for_host; do
  body=$(extract_fn "$fn")
  [[ -n "$body" ]] || { echo "could not extract $fn" >&2; exit 1; }
  eval "$body"
done

OMNIGENT_PRIMARY_FQDN=primary.example.com
OMNIGENT_STANDBY_FQDN=standby.example.com
OMNIGENT_PRIMARY_TUNNEL_PORT=16767
OMNIGENT_STANDBY_TUNNEL_PORT=26767
OMNIGENT_PORT=6767
T3_PORT=4780
T3_PRIMARY_TUNNEL_PORT=41780
T3_STANDBY_TUNNEL_PORT=42780
STARTUP_T3_WINDOWS=1
TRANSPORT_STATE=connected
TUNNEL_HOST=primary.example.com
TUNNEL_FORWARDS="7001:7001,16767:6767,41780:4780"
TUNNEL_REMOTE='($HOME/dotfiles/bin/t3-pairing-tokens 14 || true) & nvs-status; exec sleep infinity'

workspaces() {
  echo '{"workspaces":[{"workspace":"2","kind":"devserver","host":"primary.example.com","checkout":"/home/u/checkout1"}]}'
}

ps() {
  if [[ "$*" == "-axo command" ]]; then
    echo "x2ssh -et $TUNNEL_HOST -t $TUNNEL_FORWARDS -c zsh -lc 'echo NVS_TUNNEL_READY; $TUNNEL_REMOTE'"
  else
    echo "123|50001"
  fi
}

# The production function asks ps for ET command lines and parses them with awk.
# Feed its parser an already-delimited fixture by shadowing awk for that call.
awk() {
  if [[ "$*" == *'HostName='* ]]; then
    cat >/dev/null
    echo "123|50001"
  else
    command awk "$@"
  fi
}

lsof() {
  if [[ "$*" == *ESTABLISHED* ]]; then
    if [[ "$TRANSPORT_STATE" == connected ]]; then
      echo "et 123 user 9u IPv4 0t0 TCP 127.0.0.1:40000->127.0.0.1:50001 (ESTABLISHED)"
      return 0
    fi
    return 1
  fi
  if [[ "$TRANSPORT_STATE" == stuck ]]; then
    echo "et 123 user 11u IPv4 0t0 TCP 127.0.0.1:7001 (LISTEN)"
    return 0
  fi
  return 1
}

echo "2b. T3 Code pairing tokens from the session are pooled privately, not shown, replacing an earlier session's"
export FAKE_MODE=t3pair
mkdir -p "$TMP/pairing/primary.example.com"
echo STALEtoken > "$TMP/pairing/primary.example.com/token.old"
T3_PAIRING_DIR="$TMP/pairing" run_tunnels 1 primary.example.com --t3-pairing TEST-checkout1 > "$TMP/t3pair.out" 2>&1 || true
[[ "$(cat "$TMP/pairing/primary.example.com/"* 2>/dev/null | sort | tr '\n' ' ')" == "TOKENabc123 TOKENdef456 " ]] \
  || fail "the pool of primary.example.com is not exactly this session's tokens: $(cat "$TMP/pairing/primary.example.com/"* 2>/dev/null | tr '\n' ' ')"
for token_file in "$TMP/pairing/primary.example.com/"*; do
  [[ "$(stat -c %a "$token_file" 2>/dev/null || stat -f %Lp "$token_file")" == 600 ]] \
    || fail "pairing token file $token_file is not private"
done
if grep -Fq TOKENabc123 "$TMP/t3pair.out"; then
  fail "pairing token was printed to the tunnel window"
fi
grep -Eq -- "t3-pairing-tokens [0-9]+" "$FAKE_ARGS" || fail "remote command does not mint a pool of T3 Code pairing tokens"
grep -Fq -- "--t3-pairing" "$FAKE_ARGS" && fail "the option reached the remote side as a session"
: > "$FAKE_ARGS"
T3_PAIRING_DIR="$TMP/pairing-off" run_tunnels 1 primary.example.com TEST-checkout1 > "$TMP/t3pair-off.out" 2>&1 || true
grep -Fq -- "t3-pairing-tokens" "$FAKE_ARGS" && fail "a tunnel without --t3-pairing mints T3 Code pairing tokens"
[[ ! -e "$TMP/pairing-off/primary.example.com" ]] || [[ -z "$(ls -A "$TMP/pairing-off/primary.example.com")" ]] \
  || fail "a tunnel without --t3-pairing pooled tokens"

echo "2c. a candidate's tunnel forwards its one T3 Code server, the forward startup-windows expects"
export FAKE_MODE=ready
# t3_forwards HOST SESSION... -> the forwards nvs-tunnels gives x2ssh other than
# each nvim session's (PORT:PORT) and the candidate's Omnigent one (to 6767).
t3_forwards() {
  : > "$FAKE_ARGS"
  run_tunnels 1 "$@" > /dev/null 2>&1 || true
  sed -n 's/.* -t \([^ ]*\) .*/\1/p' "$FAKE_ARGS" | head -1 | tr ',' '\n' \
    | awk -F: '$1 != $2 && $2 != 6767' | paste -sd, -
}
got=$(t3_forwards primary.example.com TEST-checkout1 TEST-checkout2 TEST-checkout4)
[[ "$got" == 41780:4780 ]] || fail "the primary's T3 Code forwards are '$got', not its one server's 41780:4780"
[[ "$got" == "$(t3_forwards_for_host primary.example.com)" ]] \
  || fail "startup-windows expects '$(t3_forwards_for_host primary.example.com)' of the primary's tunnel, which forwards '$got'"
got=$(t3_forwards standby.example.com TEST-checkout1)
[[ "$got" == 42780:4780 ]] || fail "the standby's T3 Code forwards are '$got', not its one server's 42780:4780"
got=$(t3_forwards other.example.com TEST-checkout1)
[[ -z "$got" ]] || fail "a host that is not a candidate got T3 Code forwards '$got'"

echo "3. startup-windows accepts a tunnel with a connected ET transport"
TRANSPORT_STATE=connected
tunnel_command_is_current primary.example.com \
  || fail "connected ET transport was classified as stale"

echo "4. startup-windows rejects listeners left by an unregistered ET client"
TRANSPORT_STATE=stuck
if tunnel_command_is_current primary.example.com; then
  fail "listener-only ET process was classified as healthy"
fi

echo "5. startup-windows preserves an ET process still waiting for Duo"
TRANSPORT_STATE=pending
tunnel_command_is_current primary.example.com \
  || fail "pre-listener ET bootstrap was classified as stale"

echo "6. startup-windows rebuilds a tunnel that lacks its T3 Code forward"
TRANSPORT_STATE=connected
TUNNEL_FORWARDS="7001:7001,16767:6767"
if tunnel_command_is_current primary.example.com; then
  fail "tunnel without its T3 Code forward was classified as current"
fi

echo "6b. startup-windows rebuilds a tunnel that forwards a T3 Code server per checkout"
TUNNEL_FORWARDS="7001:7001,16767:6767,41781:4781,41782:4782"
if tunnel_command_is_current primary.example.com; then
  fail "tunnel with the old per-checkout T3 Code forwards was classified as current"
fi

echo "7. startup-windows rebuilds a tunnel whose session does not mint T3 Code pairing tokens"
TUNNEL_FORWARDS="7001:7001,16767:6767,41780:4780"
TUNNEL_REMOTE='nvs-status; exec sleep infinity'
if tunnel_command_is_current primary.example.com; then
  fail "tunnel without the pairing-token step was classified as current"
fi
TUNNEL_REMOTE='($HOME/dotfiles/bin/t3-pairing-tokens || true) & nvs-status; exec sleep infinity'
if tunnel_command_is_current primary.example.com; then
  fail "tunnel minting a token per checkout, from before the pool, was classified as current"
fi

echo "7b. a candidate with no devserver workspace needs no T3 Code forward"
TUNNEL_HOST=standby.example.com
TUNNEL_FORWARDS="7001:7001,26767:6767"
tunnel_command_is_current standby.example.com \
  || fail "the standby's tunnel was rebuilt for a T3 Code forward no workspace uses"
TUNNEL_HOST=primary.example.com

echo "8. with T3 Code windows off, a tunnel without T3 Code forwards is current"
STARTUP_T3_WINDOWS=0
TUNNEL_FORWARDS="7001:7001,16767:6767"
tunnel_command_is_current primary.example.com \
  || fail "tunnel without T3 Code forwards was rebuilt while T3 Code windows are off"

if [[ "$failures" -ne 0 ]]; then
  echo "nvs tunnel recovery tests: $failures failure(s)" >&2
  exit 1
fi
echo "nvs tunnel recovery tests passed"
