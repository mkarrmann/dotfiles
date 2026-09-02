#!/usr/bin/env bash
set -euo pipefail

repo_root=$(cd "$(dirname "$0")/.." && pwd)
tmp_dir=$(mktemp -d)
trap 'rm -rf "$tmp_dir"' EXIT

cat > "$tmp_dir/nvim" <<'EOF'
#!/usr/bin/env bash
printf '%s\n' "$@" > "$ORCHEST_OPEN_NOTE_CAPTURE"
if [[ -n "${NVS_ENV_CAPTURE:-}" ]]; then
  printf '%s\n' "$NVS_SESSION_NAME" "$NVS_HOST" "$NVS_WORKDIR" "$ORCHEST_INTEGRATION_URL" > "$NVS_ENV_CAPTURE"
fi
printf 'true\n'
EOF
chmod +x "$tmp_dir/nvim"

export ORCHEST_OPEN_NOTE_CAPTURE="$tmp_dir/args"
session="CCO-checkout1"
note_id="note_11111111111111111111111111111111"
expected_port=$(printf '%s' "$session" | cksum | awk '{ print 7000 + ($1 % 1000) }')

PATH="$tmp_dir:$PATH" "$repo_root/bin-macos/orchest-open-note" "$session" "$note_id"
grep -Fx -- "--server" "$ORCHEST_OPEN_NOTE_CAPTURE" >/dev/null
grep -Fx -- "localhost:$expected_port" "$ORCHEST_OPEN_NOTE_CAPTURE" >/dev/null
grep -F -- "$note_id" "$ORCHEST_OPEN_NOTE_CAPTURE" >/dev/null

if PATH="$tmp_dir:$PATH" "$repo_root/bin-macos/orchest-open-note" "bad session" "$note_id" 2>/dev/null; then
  echo "invalid session was accepted" >&2
  exit 1
fi
if PATH="$tmp_dir:$PATH" "$repo_root/bin-macos/orchest-open-note" "$session" "bad-note" 2>/dev/null; then
  echo "invalid note ID was accepted" >&2
  exit 1
fi

grep -F -- '$ORCHEST_REMOTE_PORT:$ORCHEST_LOCAL_PORT' "$repo_root/bin-macos/nvs-tunnels" >/dev/null
grep -F -- 'ORCHEST_INTEGRATION_URL="${ORCHEST_INTEGRATION_URL:-http://127.0.0.1:13100}"' "$repo_root/bin/nvs" >/dev/null

export NVS_ENV_CAPTURE="$tmp_dir/nvs-env"
PATH="$tmp_dir:$PATH" XDG_STATE_HOME="$tmp_dir/state" WORKDIR="$tmp_dir/work" \
  "$repo_root/bin/nvs" --launch CCO-checkout1 >/dev/null
sed -n '1p' "$NVS_ENV_CAPTURE" | grep -Fx 'CCO-checkout1' >/dev/null
sed -n '3p' "$NVS_ENV_CAPTURE" | grep -Fx "$tmp_dir/work" >/dev/null
sed -n '4p' "$NVS_ENV_CAPTURE" | grep -Fx 'http://127.0.0.1:13100' >/dev/null
unset NVS_ENV_CAPTURE

real_nvim=$(command -v nvim)
live_session="orchest-note-test-$$"
live_port=$(printf '%s' "$live_session" | cksum | awk '{ print 7000 + ($1 % 1000) }')
live_note="$tmp_dir/vault/Pad/live.md"
mkdir -p "$(dirname "$live_note")"
live_note="$(cd "$(dirname "$live_note")" && pwd -P)/$(basename "$live_note")"
printf '%s\n' '---' "orchest_note_id: $note_id" '---' '' '# Live' > "$live_note"
"$real_nvim" --headless --listen "localhost:$live_port" \
  --cmd "set rtp+=$repo_root/nvim" \
  --cmd "lua vim.g.obsidian_vault='$tmp_dir/vault'" > "$tmp_dir/nvim.log" 2>&1 &
live_pid=$!
for _ in $(seq 1 50); do
  "$real_nvim" --server "localhost:$live_port" --remote-expr '1' >/dev/null 2>&1 && break
  sleep 0.1
done
"$repo_root/bin-macos/orchest-open-note" "$live_session" "$note_id"
opened=$("$real_nvim" --server "localhost:$live_port" --remote-expr "luaeval('vim.api.nvim_buf_get_name(0)')")
if [[ "$opened" != "$live_note" ]]; then
  echo "live RPC opened '$opened', expected '$live_note'" >&2
  kill "$live_pid" 2>/dev/null || true
  exit 1
fi
kill "$live_pid" 2>/dev/null || true
wait "$live_pid" 2>/dev/null || true

echo "test-orchest-open-note: ok"
