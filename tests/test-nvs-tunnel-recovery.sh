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
    echo "NVS tunnel ready."
    ;;
  serialize)
    echo "$host" >> "$FAKE_ENTRIES"
    if [[ "$host" == primary.example.com ]]; then
      while [[ ! -e "$FAKE_RELEASE" ]]; do sleep 0.05; done
    fi
    echo "NVS tunnel ready."
    ;;
  *) exit 2 ;;
esac
EOF
chmod +x "$TMP/bin/omnigent-server-url" "$TMP/bin/nvs-clip-listen" "$TMP/bin/x2ssh"

run_tunnels() {
  PATH="$TMP/bin:/usr/bin:/bin" \
    TMPDIR="$TMP" \
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

for fn in tunnel_command_is_current tunnel_et_is_stuck; do
  body=$(extract_fn "$fn")
  [[ -n "$body" ]] || { echo "could not extract $fn" >&2; exit 1; }
  eval "$body"
done

OMNIGENT_PRIMARY_FQDN=primary.example.com
OMNIGENT_STANDBY_FQDN=standby.example.com
OMNIGENT_PRIMARY_TUNNEL_PORT=16767
OMNIGENT_STANDBY_TUNNEL_PORT=26767
OMNIGENT_PORT=6767
TRANSPORT_STATE=connected

ps() {
  if [[ "$*" == "-axo command" ]]; then
    echo "x2ssh -et primary.example.com -t 7001:7001,16767:6767"
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

if [[ "$failures" -ne 0 ]]; then
  echo "nvs tunnel recovery tests: $failures failure(s)" >&2
  exit 1
fi
echo "nvs tunnel recovery tests passed"
