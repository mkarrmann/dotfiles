#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

test_plugin_bootstrap() {
  local home="$TMP/plugins-home"
  local cfg="$TMP/agent-config"
  local fake_bin="$TMP/plugin-bin"
  mkdir -p "$home/.claude/plugins" "$cfg" "$fake_bin"

  cat > "$cfg/drop-plugins.list" <<'EOF'
# Comments may contain unmatched quotes, such as agent_config/sync's docs.

plugin-one
  plugin-two  
EOF
  : > "$cfg/plugins.list"
  printf '{"plugins":{"plugin-one@market":{}}}\n' \
    > "$home/.claude/plugins/installed_plugins.json"
  cat > "$cfg/sync" <<EOF
#!/usr/bin/env bash
printf '%s\n' "\$*" >> "$TMP/sync.log"
EOF
  cat > "$fake_bin/agent-market" <<EOF
#!/usr/bin/env bash
if [[ "\${1:-}" == list ]]; then
  printf 'plugin plugin-one\nplugin retained-plugin\n'
  exit 0
fi
printf '%s\n' "\$*" >> "$TMP/agent-market.log"
EOF
  cat > "$fake_bin/jq" <<'EOF'
#!/usr/bin/env bash
echo plugin-one@market
EOF
  chmod +x "$cfg/sync" "$fake_bin/agent-market" "$fake_bin/jq"

  if sed -n '/^[[:space:]]*mapfile[[:space:]]/p' "$ROOT/agent_config/bootstrap-plugins" \
      | sed -n '1p' | read -r _; then
    fail "bootstrap-plugins still depends on Bash 4 mapfile"
  fi

  HOME="$home" PATH="$fake_bin:/usr/bin:/bin" AGENT_CONFIG_DIR="$cfg" \
    bash "$ROOT/agent_config/bootstrap-plugins" >/dev/null

  [[ "$(wc -l < "$TMP/agent-market.log" | tr -d ' ')" == 4 ]] \
    || fail "expected only the installed dropped plugin to be uninstalled"
  if sed -n '/plugin-two/p' "$TMP/agent-market.log" | sed -n '1p' | read -r _; then
    fail "bootstrap attempted to uninstall a plugin that was not installed"
  fi
  [[ "$(cat "$TMP/sync.log")" == apply ]] || fail "plugin sync was not applied"

  local before
  before="$(wc -l < "$TMP/agent-market.log")"
  HOME="$home" PATH="$fake_bin:/usr/bin:/bin" AGENT_CONFIG_DIR="$cfg" \
    bash "$ROOT/agent_config/bootstrap-plugins" >/dev/null
  [[ "$(wc -l < "$TMP/agent-market.log")" == "$before" ]] \
    || fail "unchanged second run did not use the fast path"
}

test_client_dvsc_reconciliation() {
  local home="$TMP/dvsc-home"
  local dotfiles="$TMP/dotfiles"
  local fake_bin="$TMP/dvsc-bin"
  mkdir -p "$home/.omnigent" "$home/bin" "$dotfiles/bin" "$fake_bin"

  cat > "$fake_bin/python" <<'EOF'
#!/usr/bin/env bash
cat >/dev/null
mkdir -p "$(dirname "$2")"
printf 'server: %s\n' "$4" > "$2"
EOF
  cat > "$fake_bin/omnigent" <<EOF
#!/usr/bin/env bash
echo invoked >> "$TMP/omnigent-invocations.log"
exit 99
EOF
  cat > "$dotfiles/bin/omnigent-server-url" <<'EOF'
#!/usr/bin/env bash
if [[ "${1:-}" == --is-hub ]]; then
  exit 1
fi
echo http://active.example:6767
EOF
  chmod +x "$fake_bin/python" "$fake_bin/omnigent" \
    "$dotfiles/bin/omnigent-server-url"

  HOME="$home" DOTFILES_DIR="$dotfiles" OMNIGENT_BIN="$fake_bin/omnigent" \
    OMNIGENT_PY="$fake_bin/python" PATH="$fake_bin:/usr/bin:/bin" \
    bash "$ROOT/bin/omnigent-dvsc-ensure" > "$TMP/dvsc-output.log"

  [[ ! -e "$TMP/omnigent-invocations.log" ]] \
    || fail "client reconciliation invoked Omnigent and touched local DB state"
  [[ "$(cat "$home/.omnigent/config.yaml")" == 'server: http://active.example:6767' ]] \
    || fail "client configuration was not reconciled"
  sed -n '/skipping local agent-store seed/p' "$TMP/dvsc-output.log" | sed -n '1p' \
    | read -r _ || fail "client seed skip was not reported"
}

test_active_hub_dvsc_idempotency() {
  local home="$TMP/hub-home"
  local dotfiles="$TMP/hub-dotfiles"
  local fake_bin="$TMP/hub-bin"
  mkdir -p "$home/.omnigent" "$dotfiles/bin" \
    "$dotfiles/omnigent_config/agents/dvsc" "$fake_bin"

  cat > "$fake_bin/python" <<'EOF'
#!/usr/bin/env bash
cat >/dev/null
mkdir -p "$(dirname "$2")"
printf 'server: %s\n' "$4" > "$2"
EOF
  cat > "$fake_bin/omnigent" <<EOF
#!/usr/bin/env bash
if [[ "\${1:-}" == server && "\${2:-}" == status ]]; then
  echo '{}'
  exit 0
fi
echo "\$*" >> "$TMP/hub-seed.log"
exit 99
EOF
  cat > "$fake_bin/curl" <<'EOF'
#!/usr/bin/env bash
echo '{"data":[{"name":"dvsc"}]}'
EOF
  cat > "$dotfiles/bin/omnigent-server-url" <<'EOF'
#!/usr/bin/env bash
if [[ "${1:-}" == --is-hub ]]; then
  exit 0
fi
echo http://127.0.0.1:6767
EOF
  chmod +x "$fake_bin/python" "$fake_bin/omnigent" "$fake_bin/curl" \
    "$dotfiles/bin/omnigent-server-url"

  HOME="$home" DOTFILES_DIR="$dotfiles" OMNIGENT_BIN="$fake_bin/omnigent" \
    OMNIGENT_PY="$fake_bin/python" PATH="$fake_bin:/usr/bin:/bin" \
    bash "$ROOT/bin/omnigent-dvsc-ensure" > "$TMP/hub-output.log"

  [[ ! -e "$TMP/hub-seed.log" ]] \
    || fail "active hub reseeded an agent already visible from the live server"
  sed -n '/already registered/p' "$TMP/hub-output.log" | sed -n '1p' | read -r _ \
    || fail "active hub did not recognize the live systemd-managed server"
}

test_plugin_bootstrap
test_client_dvsc_reconciliation
test_active_hub_dvsc_idempotency
echo "macOS/client init portability tests passed"
