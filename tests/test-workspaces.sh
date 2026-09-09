#!/usr/bin/env bash
# Regression tests for the macOS workspace table.
#
# bin-macos/workspaces is the single place the workspace -> (devserver,
# checkout, nvs session, Omnigent origin) mapping is declared. Consumers that
# can read JSON derive from it; the Caddy front door cannot, so its address list
# is hand-written and this test is what keeps the two honest.
#
# The properties worth pinning:
#
#   * The table emits one entry per workspace, with ~ expanded, and every field
#     a consumer indexes on is present.
#   * Origins are unique -- two workspaces sharing one would silently re-merge
#     the localStorage partitions the whole design exists to separate.
#   * Every origin in the table is served by services/omnigent-tls/Caddyfile at
#     the declared TLS port, and the Caddyfile lists no origin the table does not
#     declare. A window pinned to an unserved origin fails to load entirely.
set -uo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
WORKSPACES="$ROOT/bin-macos/workspaces"
CADDYFILE="$ROOT/services/omnigent-tls/Caddyfile"

failures=0
pass() { echo "  ok: $*"; }
fail() { echo "  FAIL: $*" >&2; failures=$((failures + 1)); }
check() { # check DESC EXPECTED ACTUAL
  if [[ "$2" == "$3" ]]; then pass "$1"; else fail "$1 (expected '$2', got '$3')"; fi
}

for dep in jq; do
  command -v "$dep" >/dev/null 2>&1 || { echo "SKIP: $dep not installed"; exit 0; }
done

echo "== workspaces --json =="

table="$("$WORKSPACES" --json)" || fail "workspaces --json exited non-zero"

check "emits one entry per workspace" "8" "$(jq -r '.workspaces | length' <<< "$table")"
check "expands ~ for the local workspace" "$HOME" \
  "$(jq -r '.workspaces[] | select(.kind == "local") | .cwd' <<< "$table")"
check "every devserver row carries a checkout" "0" \
  "$(jq -r '[.workspaces[] | select(.kind == "devserver") | select((.checkout // "") == "")] | length' <<< "$table")"
check "every devserver row carries an nvs session" "0" \
  "$(jq -r '[.workspaces[] | select(.kind == "devserver") | select((.nvsSession // "") == "")] | length' <<< "$table")"
check "every row carries an origin" "0" \
  "$(jq -r '[.workspaces[] | select((.origin // "") == "")] | length' <<< "$table")"

# Two workspaces sharing an origin share a localStorage partition, which is
# exactly the failure this table exists to prevent.
check "origins are unique" "$(jq -r '.workspaces | length' <<< "$table")" \
  "$(jq -r '[.workspaces[].origin] | unique | length' <<< "$table")"
check "workspace numbers are unique" "$(jq -r '.workspaces | length' <<< "$table")" \
  "$(jq -r '[.workspaces[].workspace] | unique | length' <<< "$table")"

echo "== Caddyfile agrees with the table =="

[[ -r "$CADDYFILE" ]] || fail "Caddyfile not readable: $CADDYFILE"

tls_port="$(jq -r .tlsPort <<< "$table")"

# The site address list is the comma-separated run between the end of the
# leading comment block and the opening brace. Everything the listener serves
# has to appear there, so read it rather than the whole file: a hostname in a
# comment must not count as served.
served="$(sed -n '/^localhost:/,/{$/p' "$CADDYFILE" \
  | tr ',' '\n' \
  | sed -e 's/{[[:space:]]*$//' -e 's/[[:space:]]//g' \
  | grep -v '^$' \
  | sort -u)"

declared="$(jq -r --arg port "$tls_port" '.workspaces[] | "\(.origin):\($port)"' <<< "$table" | sort -u)"

# localhost / 127.0.0.1 are the pre-existing front door, not per-window origins:
# the CLI, health probes and any un-pinned window still use them.
baseline="$(printf '127.0.0.1:%s\nlocalhost:%s\n' "$tls_port" "$tls_port" | sort -u)"
expected="$(printf '%s\n%s\n' "$baseline" "$declared" | sort -u)"

if [[ "$served" == "$expected" ]]; then
  pass "Caddyfile serves exactly the declared origins at :$tls_port"
else
  fail "Caddyfile address list has drifted from bin-macos/workspaces"
  diff <(echo "$expected") <(echo "$served") | sed 's/^/    /' >&2
fi

echo
if [[ "$failures" -gt 0 ]]; then
  echo "$failures test(s) failed."
  exit 1
fi
echo "All tests passed."
