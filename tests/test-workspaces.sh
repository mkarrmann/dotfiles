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

echo "== startup-windows agrees with the table =="

STARTUP="$ROOT/bin-macos/startup-windows"
[[ -r "$STARTUP" ]] || fail "startup-windows not readable: $STARTUP"

sw_rows="$(sed -n '/^WORKSPACES=(/,/^)/p' "$STARTUP" | grep -oE '"[^"]*"' | tr -d '"')"

# Every declared workspace needs an omnigent slot, or it never gets a window to
# pin and silently keeps the app-global default.
declared_ws="$(jq -r '.workspaces[].workspace' <<< "$table" | sort -u)"
omnigent_ws="$(awk -F'|' '$2 == "omnigent" { print $1 }' <<< "$sw_rows" | sort -u)"
if [[ "$declared_ws" == "$omnigent_ws" ]]; then
  pass "every declared workspace has an omnigent slot"
else
  fail "workspaces with an omnigent slot differ from the table"
  diff <(echo "$declared_ws") <(echo "$omnigent_ws") | sed 's/^/    /' >&2
fi

# The nvs session is still written in both places -- the table declares it and
# the ghostty row launches it. Until the rows are derived, pin that they agree.
nvs_mismatch=0
while IFS=$'\t' read -r ws session; do
  [[ -n "$session" && "$session" != "null" ]] || continue
  row="$(awk -F'|' -v ws="$ws" '$1 == ws && $2 == "ghostty"' <<< "$sw_rows")"
  if [[ -z "$row" ]]; then
    fail "workspace $ws declares nvs session '$session' but has no ghostty row"
    nvs_mismatch=$((nvs_mismatch + 1))
  elif [[ "$row" != *"nvs $session"* ]]; then
    fail "workspace $ws launches a different nvs session than the table's '$session'"
    nvs_mismatch=$((nvs_mismatch + 1))
  fi
done < <(jq -r '.workspaces[] | select(.kind == "devserver") | "\(.workspace)\t\(.nvsSession)"' <<< "$table")
[[ "$nvs_mismatch" -eq 0 ]] && pass "every devserver workspace launches the nvs session the table declares"

# The ghostty title and its match regex encode the same site/checkout pair the
# table declares. A title that drifts silently breaks window claiming, because
# the regex is how an existing terminal is recognised.
title_mismatch=0
while IFS=$'\t' read -r ws session; do
  [[ -n "$session" && "$session" != "null" ]] || continue
  site="${session%%-*}"; checkout="${session#*-}"
  row="$(awk -F'|' -v ws="$ws" '$1 == ws && $2 == "ghostty"' <<< "$sw_rows")"
  got_title="$(cut -d'|' -f3 <<< "$row")"
  if [[ "$got_title" != "$site: $checkout" ]]; then
    fail "workspace $ws ghostty title is '$got_title', table implies '$site: $checkout'"
    title_mismatch=$((title_mismatch + 1))
  fi
done < <(jq -r '.workspaces[] | select(.kind == "devserver") | "\(.workspace)\t\(.nvsSession)"' <<< "$table")
[[ "$title_mismatch" -eq 0 ]] && pass "every devserver terminal is titled from the table's site and checkout"

# The tunnel rows restate every devserver FQDN and every nvs session again --
# the densest copy of the mapping left in the script.
tunnel_mismatch=0
while read -r fqdn; do
  [[ -n "$fqdn" ]] || continue
  row="$(grep -F "nvs-tunnels $fqdn" <<< "$sw_rows" || true)"
  if [[ -z "$row" ]]; then
    fail "no tunnel row for $fqdn, which the table declares"
    tunnel_mismatch=$((tunnel_mismatch + 1))
    continue
  fi
  want="$(jq -r --arg h "$fqdn" '
    [ .workspaces[] | select(.kind == "devserver") | select(.host == $h)
      | "\(.nvsSession):~/\(.checkout | split("/") | last)" ] | join(" ")' <<< "$table")"
  got="$(grep -oE '[A-Z]+-checkout[0-9]+:~/[a-z0-9]+' <<< "$row" | tr '\n' ' ' | sed 's/ $//')"
  if [[ "$want" != "$got" ]]; then
    fail "tunnel row for $fqdn lists '$got', table declares '$want'"
    tunnel_mismatch=$((tunnel_mismatch + 1))
  fi
done < <(jq -r '[.workspaces[] | select(.kind == "devserver") | .host] | unique | .[]' <<< "$table")
[[ "$tunnel_mismatch" -eq 0 ]] && pass "every tunnel row forwards exactly the checkouts the table declares"

echo "== macOS Orchest manifest renders from the table =="

RENDER="$ROOT/bin-macos/orchest-plugins-render"
SOURCE_MANIFEST="$ROOT/orchest_plugins.macos.json"
GOLDEN="$ROOT/orchest_plugins.json"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

dst="$TMP/plugins.json"
if "$RENDER" "$SOURCE_MANIFEST" "$dst" >/dev/null; then
  pass "renders"
else
  fail "renderer exited non-zero"
fi

check "leaves no sentinel anywhere in the output" "0" \
  "$(grep -c '__DERIVED_FROM_WORKSPACE_TABLE__' "$dst" || true)"

# The golden: until the hand-written manifest is deleted, the derived one must
# reproduce it exactly. This is what proves the migration changes no behaviour,
# and it keeps the two honest while both exist.
if [[ -r "$GOLDEN" ]]; then
  if diff -q <(jq -S . "$GOLDEN") <(jq -S . "$dst") >/dev/null; then
    pass "derived attribution matches the hand-written manifest"
  else
    fail "derived attribution differs from the hand-written manifest"
    diff <(jq -S . "$GOLDEN") <(jq -S . "$dst") | head -20 | sed 's/^/    /' >&2
  fi
fi

# A bypassed renderer must not ship a manifest that attributes nothing.
jq 'del(.plugins[].config.attribution)' "$SOURCE_MANIFEST" > "$TMP/no-sentinel.json"
if "$RENDER" "$TMP/no-sentinel.json" "$TMP/never.json" >/dev/null 2>&1; then
  fail "a source manifest with no sentinel should not render"
else
  pass "a source manifest with no sentinel fails loudly"
fi
[[ -e "$TMP/never.json" ]] && fail "a failed render must not leave a destination behind"

# sync.sh runs on every login and Orchest reads this file at startup.
before="$(stat -f %m "$dst")"
sleep 1
"$RENDER" "$SOURCE_MANIFEST" "$dst" >/dev/null || fail "re-render exited non-zero"
check "an unchanged render leaves the destination untouched" "$before" "$(stat -f %m "$dst")"

echo
if [[ "$failures" -gt 0 ]]; then
  echo "$failures test(s) failed."
  exit 1
fi
echo "All tests passed."
