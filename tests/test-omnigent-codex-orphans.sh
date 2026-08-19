#!/usr/bin/env bash
set -euo pipefail

# Coverage for bin/omnigent-codex-orphans, which omnigent-host.service runs at
# ExecStartPre/ExecStopPost. The classifier is what matters: reaping an
# "owned" scope would kill a live session, and missing an orphan leaves the
# thread-writer lock held and the next resume wedged.
#
# The scanner is pointed at a synthetic /proc via OMNIGENT_CODEX_ORPHANS_PROC,
# so every case below is exercised without touching real processes -- except
# the SIGKILL-escalation case, which signals a `sleep` this script owns.

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
TOOL="$ROOT/bin/omnigent-codex-orphans"
TMP="$(cd -- "$(mktemp -d)" && pwd -P)"
trap 'rm -rf "$TMP"' EXIT

PROC="$TMP/proc"
NATIVE="$TMP/codex-native"
mkdir -p "$PROC" "$NATIVE"
printf 'btime %s\n' "$(($(date +%s) - 1000))" >"$PROC/stat"

export OMNIGENT_CODEX_ORPHANS_PROC="$PROC"
export OMNIGENT_CODEX_NATIVE_DIR="$NATIVE"
export OMNIGENT_CODEX_ORPHANS_SYSTEMCTL="$TMP/fake-systemctl"

# Records its arguments, then simulates a cgroup kill by removing the synthetic
# /proc entries named in FAKE_KILL_PIDS.
# shellcheck disable=SC2016
printf '%s\n' \
  '#!/usr/bin/env bash' \
  'printf "%s\n" "$*" >>"$FAKE_SYSTEMCTL_LOG"' \
  'for pid in ${FAKE_KILL_PIDS:-}; do rm -rf "$OMNIGENT_CODEX_ORPHANS_PROC/$pid"; done' \
  'exit 0' >"$TMP/fake-systemctl"
chmod +x "$TMP/fake-systemctl"
export FAKE_SYSTEMCTL_LOG="$TMP/systemctl.log"
: >"$FAKE_SYSTEMCTL_LOG"

# fake_proc <pid> <ppid> <cgroup-leaf> <arg>...
fake_proc() {
  local pid="$1" ppid="$2" leaf="$3"
  shift 3
  mkdir -p "$PROC/$pid/fd"
  printf '%s\0' "$@" >"$PROC/$pid/cmdline"
  printf 'Name:\tcodex\nPPid:\t%s\n' "$ppid" >"$PROC/$pid/status"
  printf '0::/user.slice/user-1000.slice/user@1000.service/%s\n' "$leaf" >"$PROC/$pid/cgroup"
  # `stat` field 22 (index 19 after the comm) is the start time in ticks. The
  # comm deliberately contains a space and parens: the parser must split on the
  # LAST ')', not the first.
  printf '%s (co dex (x)) S 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0\n' "$pid" >"$PROC/$pid/stat"
}

bridge() {
  local hash="$1" session="$2"
  mkdir -p "$NATIVE/$hash"
  printf '{"session_id": "%s"}\n' "$session" >"$NATIVE/$hash/tool_relay.json"
}

HASH_ORPHAN=00000000000000000000000000000001
HASH_OWNED=00000000000000000000000000000002
HASH_PROBE=00000000000000000000000000000003
bridge "$HASH_ORPHAN" sess_orphan
bridge "$HASH_OWNED" sess_owned
bridge "$HASH_PROBE" sess_probe

# The host itself, plus a runner child: anything rooted here is still owned.
fake_proc 1000 1 omnigent-host.service /usr/bin/omnigent host
fake_proc 1010 1000 omnigent-host.service python -m omnigent.runner._zygote

# Orphan: sandboxed scope, reparented to init, holding a thread-writer lock.
fake_proc 2000 1 run-p2000-i1.scope \
  /usr/local/bin/codex_cli/codex.real \
  "-c" "model_routes_json=\"$NATIVE/$HASH_ORPHAN/codex-home/meta/model_routes.json\"" \
  app-server --listen ws://127.0.0.1:1
mkdir -p "$NATIVE/$HASH_ORPHAN/codex-home/thread-writer-locks"
touch "$NATIVE/$HASH_ORPHAN/codex-home/thread-writer-locks/thread-aaa.lock"
ln -s "$NATIVE/$HASH_ORPHAN/codex-home/thread-writer-locks/thread-aaa.lock" "$PROC/2000/fd/49"

# Owned: same shape, but its parent chain reaches the running host.
fake_proc 3000 1010 run-p3000-i1.scope \
  /usr/local/bin/codex_cli/codex.real \
  "-c" "model_routes_json=\"$NATIVE/$HASH_OWNED/codex-home/meta/model_routes.json\"" \
  app-server --listen ws://127.0.0.1:2

# Inside the host cgroup: systemd's own kill reaches it, so it is not ours.
fake_proc 4000 1010 omnigent-host.service \
  /usr/local/bin/codex "--bridge-dir" "$NATIVE/$HASH_OWNED" app-server

# A codex-native process that is not an app-server (e.g. a `--version` probe)
# holds no thread lock and must not be reaped.
fake_proc 5000 1 run-p5000-i1.scope \
  /usr/local/bin/codex_cli/codex.real "--bridge-dir" "$NATIVE/$HASH_PROBE" --version

# An interactive codex against ~/.codex shares the binary but not the marker.
fake_proc 6000 1 run-p6000-i1.scope \
  /usr/local/bin/codex_cli/codex.real "-c" "model_routes_json=\"$HOME/.codex/x.json\"" app-server

listing="$("$TOOL" list)"

grep -qE '^ORPHAN run-p2000-i1\.scope .*session=sess_orphan' <<<"$listing"
grep -qE '^owned  run-p3000-i1\.scope .*session=sess_owned' <<<"$listing"
grep -q 'locks=thread-aaa' <<<"$listing"
grep -q '2 scope(s), 1 orphaned' <<<"$listing"
for absent in run-p4000 run-p5000 run-p6000; do
  if grep -q "$absent" <<<"$listing"; then
    echo "scope $absent must not be listed: $listing" >&2
    exit 1
  fi
done

# Reported age comes from btime + stat field 22, seeded 1000s ago above.
grep -q 'age=16m40s' <<<"$listing"

# JSON carries the same classification.
"$TOOL" list --json | python3 -c '
import json, sys
scopes = {s["unit"]: s for s in json.load(sys.stdin)}
assert set(scopes) == {"run-p2000-i1.scope", "run-p3000-i1.scope"}, scopes
assert scopes["run-p2000-i1.scope"]["orphaned"] is True
assert scopes["run-p2000-i1.scope"]["session_ids"] == ["sess_orphan"]
assert scopes["run-p2000-i1.scope"]["locks"] == ["thread-aaa"]
assert scopes["run-p3000-i1.scope"]["orphaned"] is False
'

# Default reap is orphan-only; --all opts into the owned one.
dry="$("$TOOL" reap --dry-run)"
grep -q 'run-p2000-i1.scope' <<<"$dry"
if grep -q 'run-p3000-i1.scope' <<<"$dry"; then
  echo "default reap must not select an owned scope: $dry" >&2
  exit 1
fi
"$TOOL" reap --dry-run --all | grep -q 'run-p3000-i1.scope'

# --session narrows to one session, and skips a session with no orphan.
"$TOOL" reap --dry-run --session sess_orphan | grep -q 'run-p2000-i1.scope'
"$TOOL" reap --dry-run --session sess_owned | grep -q 'no native-Codex scopes to reap'

# A real reap stops the scope by unit name.
FAKE_KILL_PIDS=2000 "$TOOL" reap >"$TMP/reap.out"
grep -qx -- '--user stop run-p2000-i1.scope' "$FAKE_SYSTEMCTL_LOG"
"$TOOL" list | grep -q '1 scope(s), 0 orphaned'

# When `systemctl stop` reports success but the processes survive -- the whole
# reason this tool exists -- reap escalates and, failing that, exits nonzero so
# the caller sees it. The SIGKILL itself is deliberately NOT exercised here:
# signals go to the real kernel, and a synthetic pid number belongs to whatever
# real process happens to hold it, so the tool refuses to signal unless it
# scanned the real /proc. That guard is what is asserted instead.
fake_proc 7000 1 run-p7000-i1.scope \
  /usr/local/bin/codex_cli/codex.real \
  "-c" "model_routes_json=\"$NATIVE/$HASH_ORPHAN/codex-home/meta/model_routes.json\"" \
  app-server
if FAKE_KILL_PIDS='' "$TOOL" reap --session sess_orphan --timeout 1 \
  >"$TMP/kill.out" 2>"$TMP/kill.err"; then
  echo "reap must fail when a scope survives the stop" >&2
  exit 1
fi
grep -qx -- '--user stop run-p7000-i1.scope' "$FAKE_SYSTEMCTL_LOG"
grep -q 'refusing to signal \[7000\]' "$TMP/kill.err"
rm -rf "${PROC:?}/7000"

# Nothing left to do is success, not an error.
"$TOOL" reap | grep -q 'no native-Codex scopes to reap'

# An empty machine must not trip the scanner.
rm -rf "$PROC"
mkdir -p "$PROC"
"$TOOL" list | grep -q 'no native-Codex scopes found'

echo "omnigent-codex-orphans tests passed"
