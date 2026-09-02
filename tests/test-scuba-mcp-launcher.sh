#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
LAUNCHER="$ROOT/bin/scuba-mcp-launcher"
TMP="$(cd -- "$(mktemp -d)" && pwd -P)"
trap 'rm -rf "$TMP"' EXIT

install_fake_server() {
  local fbsource="$1"
  local binary="$fbsource/tools/devmate/python_servers_dotslash/scuba_mcp_server"
  mkdir -p "${binary%/*}"
  # These variables belong to the generated fake.
  # shellcheck disable=SC2016
  printf '%s\n' \
    '#!/usr/bin/env bash' \
    'printf "root=%s\n" "$FBSOURCE_ROOT"' \
    'printf "cwd=%s\n" "$PWD"' >"$binary"
  chmod +x "$binary"
}

assert_root() {
  local cwd="$1" expected="$2"
  shift 2
  (cd "$cwd" && env "$@" "$LAUNCHER") >"$TMP/out"
  grep -Fx "root=$expected" "$TMP/out" >/dev/null
}

mkdir -p "$TMP/checkout4/fbsource/fbcode/project" "$TMP/checkout4/configerator/source/project"
install_fake_server "$TMP/checkout4/fbsource"

assert_root "$TMP/checkout4" "$TMP/checkout4/fbsource" -u FBSOURCE_ROOT
assert_root "$TMP/checkout4/fbsource/fbcode/project" "$TMP/checkout4/fbsource" -u FBSOURCE_ROOT
assert_root "$TMP/checkout4/configerator/source/project" "$TMP/checkout4/fbsource" -u FBSOURCE_ROOT

mkdir -p "$TMP/explicit"
install_fake_server "$TMP/explicit"
assert_root "$TMP/checkout4" "$TMP/explicit" FBSOURCE_ROOT="$TMP/explicit"

echo "scuba MCP launcher tests passed"
