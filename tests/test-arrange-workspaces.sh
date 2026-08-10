#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

mkdir -p "$TMP/bin"

cat > "$TMP/bin/aerospace" <<'EOF'
#!/usr/bin/env bash
case "${1:-}" in
  list-workspaces)
    [[ "${AEROSPACE_FAIL_DISCOVERY:-false}" == true ]] && exit 1
    printf '[]\n'
    ;;
esac
EOF

cat > "$TMP/bin/jq" <<'EOF'
#!/usr/bin/env bash
cat >/dev/null
EOF

chmod +x "$TMP/bin/aerospace" "$TMP/bin/jq"

output=$(ARRANGEMENT_LOCK_DIR="$TMP/lock" \
  PATH="$TMP/bin:/usr/bin:/bin" \
  /bin/bash "$ROOT/bin-macos/arrange-workspaces")

grep -Fx 'Arranging workspace layouts...' <<< "$output" >/dev/null
grep -Fx 'No workspaces to arrange.' <<< "$output" >/dev/null

if error=$(AEROSPACE_FAIL_DISCOVERY=true \
    ARRANGEMENT_LOCK_DIR="$TMP/failure-lock" \
    PATH="$TMP/bin:/usr/bin:/bin" \
    /bin/bash "$ROOT/bin-macos/arrange-workspaces" 2>&1); then
  echo "workspace discovery failure unexpectedly succeeded" >&2
  exit 1
fi
grep -Fx 'ERROR: failed to discover AeroSpace workspaces' <<< "$error" >/dev/null

echo "arrange-workspaces tests passed"
