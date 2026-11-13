#!/usr/bin/env bash
# Checks for bin/omnigent-codex-login-ensure against fake /etc/codex layouts,
# a temp CODEX_HOME and a temp Omnigent config.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRIPT="$ROOT/bin/omnigent-codex-login-ensure"
OMNIGENT_PY="${OMNIGENT_PY:-$(uv tool dir 2>/dev/null || echo "$HOME/.local/share/uv/tools")/omnigent/bin/python3}"
if [[ ! -x "$OMNIGENT_PY" ]]; then
  echo "skipped: omnigent's python is not installed"
  exit 0
fi
export OMNIGENT_PY

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
failures=0
check() {
  if eval "$2"; then echo "  ok: $1"; else echo "  FAIL: $1"; failures=$((failures + 1)); fi
}

run() { # NAME: a fresh machine whose /etc/codex is $TMP/NAME/etc
  local dir="$TMP/$1"
  mkdir -p "$dir/etc" "$dir/codex" "$dir/omnigent"
  CODEX_SYSTEM_DIR="$dir/etc" CODEX_HOME="$dir/codex" OMNIGENT_DATA_DIR="$dir/omnigent" "$SCRIPT" >"$dir/out"
}
dismissed() {
  "$OMNIGENT_PY" -c 'import sys, yaml; print("codex" in (yaml.safe_load(open(sys.argv[1])) or {}).get("dismissed_detections", []))' \
    "$TMP/$1/omnigent/config.yaml" 2>/dev/null | grep -qx True
}

echo "pinned in config.toml (Meta's layout since 2026-11-12)"
mkdir -p "$TMP/new/etc"
printf 'model_provider = "responses"  # an OpenAI opt-in home can select its own provider\n' >"$TMP/new/etc/config.toml"
printf '# NOTE: model_providers.responses section is set by launcher via -c flags\n' >"$TMP/new/etc/managed_config.toml"
run new
check "writes the placeholder auth.json" 'grep -q placeholder-managed-gateway-auth "$TMP/new/codex/auth.json"'
check "dismisses the codex detection" 'dismissed new'

echo "pinned in managed_config.toml (the layout before)"
mkdir -p "$TMP/old/etc"
printf 'model_provider = "responses"\n' >"$TMP/old/etc/managed_config.toml"
run old
check "writes the placeholder auth.json" '[[ -f "$TMP/old/codex/auth.json" ]]'

echo "no pin anywhere"
mkdir -p "$TMP/plain/etc"
printf '# model_provider = "responses"\n' >"$TMP/plain/etc/managed_config.toml"
run plain
check "leaves auth.json absent" '[[ ! -e "$TMP/plain/codex/auth.json" ]]'
check "leaves the codex detection on" '! dismissed plain'

echo "a real login"
mkdir -p "$TMP/real/etc" "$TMP/real/codex"
printf 'model_provider = "responses"\n' >"$TMP/real/etc/config.toml"
printf '{"tokens": {"refresh_token": "r"}}\n' >"$TMP/real/codex/auth.json"
run real
check "keeps the real auth.json" 'grep -q refresh_token "$TMP/real/codex/auth.json"'
check "leaves the codex detection on" '! dismissed real'

if ((failures)); then
  echo "$failures check(s) failed"
  exit 1
fi
echo "omnigent-codex-login-ensure tests passed"
