#!/usr/bin/env bash
# Checks bin-macos/startup-windows' hand-off to --deferred: who waits on the
# lock for whom, that a run which never got to its rows leaves the pending list
# alone, and the list's own bookkeeping. Runs the real script with AeroSpace
# absent, so a run that gets the lock stops right after taking it.
# Run with: bash tests/test-startup-windows-deferred.sh
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
STARTUP="$ROOT/bin-macos/startup-windows"
TMP="$(mktemp -d)"
holder=""
trap '[[ -z "$holder" ]] || kill "$holder" 2>/dev/null || true; rm -rf "$TMP"' EXIT

failures=0
fail() { echo "  FAIL: $*" >&2; failures=$((failures + 1)); }

mkdir -p "$TMP/bin"
printf '#!/bin/sh\ncase "$1" in --port) echo 6767 ;; --tls-port) echo 6443 ;; *) echo x ;; esac\n' \
  > "$TMP/bin/omnigent-server-url"
chmod +x "$TMP/bin/omnigent-server-url"

LOCK="$TMP/lock"
PENDING="$TMP/pending"
run_startup() {
  PATH="$TMP/bin:/usr/bin:/bin" STARTUP_LOCK_DIR="$LOCK" STARTUP_PENDING_FILE="$PENDING" \
    STARTUP_WINDOWS_TIMESTAMPS=0 "$STARTUP" "$@"
}

# hold_lock MODE -- a live process owns the lock, as a run in MODE would.
hold_lock() {
  sleep 60 &
  holder=$!
  mkdir "$LOCK"
  echo "$holder" > "$LOCK/pid"
  echo "$1" > "$LOCK/mode"
}
release_lock() {
  kill "$holder" 2>/dev/null || true
  wait "$holder" 2>/dev/null || true
  holder=""
  rm -rf "$LOCK"
}

# wait_for_output FILE TEXT -- until FILE says TEXT (5s at most).
wait_for_output() {
  for _ in $(seq 1 100); do
    grep -Fq "$2" "$1" 2>/dev/null && return 0
    sleep 0.05
  done
  return 1
}

echo "1. a tunnel connecting with nothing deferred takes no lock and opens nothing"
rm -f "$PENDING"
out=$(run_startup --deferred 2>&1) || fail "--deferred with nothing pending exited non-zero"
[[ "$out" == "No deferred windows." ]] || fail "unexpected output: $out"
[[ ! -e "$LOCK" ]] || fail "--deferred with nothing pending took the lock"

echo "2. --deferred queues behind a full run still going, and then runs"
printf '%s\n' '2|omnigent' '3|t3' > "$PENDING"
hold_lock full
run_startup --deferred > "$TMP/deferred.out" 2>&1 &
deferred=$!
wait_for_output "$TMP/deferred.out" "Waiting for startup-windows (PID $holder)" \
  || fail "--deferred did not wait for the full run"
grep -Fq "aerospace CLI not found" "$TMP/deferred.out" && fail "--deferred went ahead while the full run held the lock"
release_lock
wait "$deferred" || true
grep -Fq "aerospace CLI not found" "$TMP/deferred.out" \
  || fail "--deferred did not go ahead once the full run finished"
[[ "$(cat "$PENDING")" == $'2|omnigent\n3|t3' ]] \
  || fail "a run that stopped before its rows changed the pending list"
[[ ! -e "$LOCK" ]] || fail "--deferred left the lock behind"

echo "3. --deferred also queues when nothing is pending yet but a run holds the lock"
rm -f "$PENDING"
hold_lock full
run_startup --deferred > "$TMP/deferred2.out" 2>&1 &
deferred=$!
wait_for_output "$TMP/deferred2.out" "Waiting for startup-windows" \
  || fail "--deferred returned before the run that may defer rows had finished"
release_lock
wait "$deferred" || true

echo "3b. a stale list (over 12 hours) counts as nothing pending"
printf '%s\n' '2|omnigent' > "$PENDING"
touch -t "$(date -v-13H +%Y%m%d%H%M)" "$PENDING"
out=$(run_startup --deferred 2>&1) || fail "--deferred with a stale list exited non-zero"
[[ "$out" == "No deferred windows." ]] || fail "a stale list was acted on: $out"

echo "4. a full run leaves the work to a full run already going"
hold_lock full
out=$(run_startup 2>&1) || fail "a full run behind a full run exited non-zero"
[[ "$out" == "startup-windows is already running as PID $holder; exiting." ]] \
  || fail "unexpected output: $out"
release_lock

echo "5. a full run waits for a --deferred run rather than giving up"
hold_lock deferred
run_startup > "$TMP/full.out" 2>&1 &
full=$!
wait_for_output "$TMP/full.out" "Waiting for startup-windows (PID $holder)" \
  || fail "the full run did not wait for the --deferred run"
release_lock
wait "$full" || true
grep -Fq "aerospace CLI not found" "$TMP/full.out" \
  || fail "the full run did not go ahead once the --deferred run finished"

echo "6. a lock whose owner died is taken over"
mkdir "$LOCK"
echo 999999 > "$LOCK/pid"
out=$(run_startup 2>&1) || true
grep -Fq "aerospace CLI not found" <<< "$out" || fail "a dead owner's lock was not taken over: $out"

echo "7. the pending list's bookkeeping"
extract_fn() {
  awk -v name="$1" '
    $0 ~ "^" name "\\(\\) \\{" { p = 1 }
    p { print }
    p && $0 == "}" { exit }
  ' "$STARTUP"
}
for fn in row_pending defer_row read_pending_file save_pending_rows; do
  body=$(extract_fn "$fn")
  [[ -n "$body" ]] || { echo "could not extract $fn" >&2; exit 1; }
  eval "$body"
done
STARTUP_PENDING_FILE="$TMP/state/pending"
pending_rows=""
defer_row "2|omnigent"
defer_row "2|t3"
defer_row "2|omnigent"
[[ "$pending_rows" == $'2|omnigent\n2|t3' ]] || fail "a row deferred twice is listed twice: $pending_rows"
row_pending "2|t3" || fail "a deferred row is not pending"
row_pending "2|t" && fail "a prefix of a deferred row counts as pending"
pending_ready=false
save_pending_rows
[[ ! -e "$STARTUP_PENDING_FILE" ]] || fail "a run that does not own the list wrote it"
pending_ready=true
save_pending_rows
[[ "$(cat "$STARTUP_PENDING_FILE")" == $'2|omnigent\n2|t3' ]] || fail "the list was not saved"
STARTUP_PENDING_MAX_AGE_MIN=720
[[ "$(read_pending_file)" == $'2|omnigent\n2|t3' ]] || fail "a fresh list was not read back"
touch -t "$(date -v-13H +%Y%m%d%H%M)" "$STARTUP_PENDING_FILE"
[[ -z "$(read_pending_file)" ]] || fail "a list older than 12 hours was still read"
pending_rows=""
save_pending_rows
[[ ! -e "$STARTUP_PENDING_FILE" ]] || fail "an emptied list was left on disk"

if [[ "$failures" -ne 0 ]]; then
  echo "startup-windows deferred tests: $failures failure(s)" >&2
  exit 1
fi
echo "startup-windows deferred tests passed"
