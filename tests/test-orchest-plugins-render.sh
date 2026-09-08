#!/usr/bin/env bash
# Regression tests for the Linux Orchest plugin manifest rendering.
#
# The point of the renderer is that the Omnigent bridge's workspace attribution
# is DERIVED from the sway layout table rather than restated, so the properties
# worth pinning are:
#
#   * --print-layout reports the declared table with no compositor running, and
#     expands ~ the same way the terminal launcher does.
#   * A machine-local layout override reaches the rendered manifest, since that
#     is the whole reason the mapping is not a checked-in literal.
#   * A source manifest with no sentinel fails loudly instead of shipping a
#     manifest whose bridge attributes nothing.
#   * Re-rendering an unchanged manifest leaves the destination alone; sync.sh
#     runs on every login and Orchest reads this file at startup.
set -uo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
STARTUP="$ROOT/bin-linux/startup-windows"
RENDER="$ROOT/bin-linux/orchest-plugins-render"
SOURCE_MANIFEST="$ROOT/orchest_plugins.linux.json"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

failures=0
pass() { echo "  ok: $*"; }
fail() { echo "  FAIL: $*" >&2; failures=$((failures + 1)); }
check() { # check DESC EXPECTED ACTUAL
  if [[ "$2" == "$3" ]]; then pass "$1"; else fail "$1 (expected '$2', got '$3')"; fi
}

for dep in jq; do
  command -v "$dep" >/dev/null 2>&1 || { echo "SKIP: $dep not installed"; exit 0; }
done

echo "== startup-windows --print-layout =="

# No SWAYSOCK: the declared layout must be readable with no compositor.
layout="$(env -u SWAYSOCK "$STARTUP" --print-layout)" || fail "--print-layout exited non-zero"

check "reports the sweep workspace" "Z" "$(jq -r .sweepWorkspace <<< "$layout")"
check "reports the dashboard workspace" "9" "$(jq -r .dashboardWorkspace <<< "$layout")"
check "reports one entry per terminal slot" "6" "$(jq -r '.terminals | length' <<< "$layout")"
check "expands ~ to an absolute workdir" "$HOME/dev/orchest" \
  "$(jq -r '.terminals[] | select(.slot == "orchest") | .workdir' <<< "$layout")"
check "keeps the workspace a terminal declares" "3" \
  "$(jq -r '.terminals[] | select(.slot == "orchest") | .workspace' <<< "$layout")"
check "emits no relative workdirs" "0" \
  "$(jq -r '[.terminals[] | select(.workdir | startswith("/") | not)] | length' <<< "$layout")"

echo "== orchest-plugins-render =="

dst="$TMP/plugins.json"
"$RENDER" "$SOURCE_MANIFEST" "$dst" >/dev/null || fail "render exited non-zero"

bridge_attribution() { jq '.plugins[] | select(.id == "orchest-omnigent-bridge") | .config.attribution' "$1"; }

check "resolves the attribution sentinel" "object" "$(bridge_attribution "$dst" | jq -r 'type')"
check "maps each terminal workdir to its workspace" "3" \
  "$(bridge_attribution "$dst" | jq -r '.byCwd["'"$HOME"'/dev/orchest"]')"
check "carries every terminal slot into byCwd" "6" "$(bridge_attribution "$dst" | jq -r '.byCwd | length')"
check "leaves no sentinel anywhere in the output" "0" \
  "$(grep -c '__DERIVED_FROM_SWAY_LAYOUT__' "$dst")"
check "preserves the other plugins" "task-status" "$(jq -r '.plugins[0].id' "$dst")"

echo "== layout override reaches the manifest =="

cat > "$TMP/layout.sh" <<'OVERRIDE'
WORKSPACES=(
  "7|term|elsewhere|~/somewhere/else|true"
)
OVERRIDE

override_dst="$TMP/plugins-override.json"
SWAY_WINDOWS_LAYOUT="$TMP/layout.sh" "$RENDER" "$SOURCE_MANIFEST" "$override_dst" >/dev/null ||
  fail "render with layout override exited non-zero"

check "an override replaces the derived mapping" "7" \
  "$(bridge_attribution "$override_dst" | jq -r '.byCwd["'"$HOME"'/somewhere/else"]')"
check "an override drops the repo table's slots" "1" \
  "$(bridge_attribution "$override_dst" | jq -r '.byCwd | length')"

echo "== failure and idempotence =="

jq 'del(.plugins[] | select(.id == "orchest-omnigent-bridge"))' "$SOURCE_MANIFEST" > "$TMP/no-sentinel.json"
if "$RENDER" "$TMP/no-sentinel.json" "$TMP/never.json" >/dev/null 2>&1; then
  fail "a source manifest with no sentinel should not render"
else
  pass "a source manifest with no sentinel fails loudly"
fi
[[ -e "$TMP/never.json" ]] && fail "a failed render must not leave a destination behind"

before="$(stat -c %Y "$dst")"
sleep 1
"$RENDER" "$SOURCE_MANIFEST" "$dst" >/dev/null || fail "re-render exited non-zero"
check "an unchanged render leaves the destination untouched" "$before" "$(stat -c %Y "$dst")"

echo
if [[ "$failures" -gt 0 ]]; then
  echo "$failures failure(s)" >&2
  exit 1
fi
echo "all passed"
