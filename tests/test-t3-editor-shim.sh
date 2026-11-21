#!/usr/bin/env bash
set -euo pipefail

# End-to-end coverage for t3code/editor-bin/code, T3 Code's "Open in VS Code"
# answered by Neovim, against a throwaway headless Neovim reached the way a
# devserver's T3 Code server reaches one: bin/nvim-show routing the target's
# path to the nvs session whose workdir holds it. Never touches a live editor:
# $HOME is redirected to a fixture session list on a port derived from this PID.

repo_root=$(cd "$(dirname "$0")/.." && pwd)
unset OMNIGENT_RUNNER_PRIMARY_SESSION_ID OMNIGENT_SESSION_ID NVIM_SHOW_ANCESTOR_SESSION_IDS CC_SESSION_ID CLAUDE_CODE_CURRENT_SESSION_ID NVIM
export NVIM_SHOW_RESOLVER=
tmp_dir=$(mktemp -d)
tmp_dir=$(cd "$tmp_dir" && pwd -P)
real_nvim=$(command -v nvim)
live_pid=""
trap 'if [[ -n $live_pid ]]; then kill "$live_pid" 2>/dev/null || true; wait "$live_pid" 2>/dev/null || true; fi; rm -rf "$tmp_dir"' EXIT

fail() {
  echo "test-t3-editor-shim: $*" >&2
  exit 1
}

assert_eq() {
  [[ $1 == "$2" ]] || fail "$3: expected '$2', got '$1'"
}

session="t3-editor-shim-test-$$"
port=$(printf '%s' "$session" | cksum | awk '{ print 7000 + ($1 % 1000) }')

work="$tmp_dir/work"
mkdir -p "$work/sub" "$tmp_dir/home/.config/nvs"
printf 'l1\nl2\nl3\nl4\nl5\nl6\n' > "$work/a.txt"
printf 'x1\nx2\nx3\n' > "$work/sub/b.txt"
printf 'y1\ny2\ny3\n' > "$work/build:2"
echo "$session $work" > "$tmp_dir/home/.config/nvs/sessions.$(hostname -s)"

cat > "$tmp_dir/helper.lua" <<'LUA'
-- name|line|col|tcd|tab count|tab_name, for the current tab.
function _G.T3ShimProbe()
  local tab = vim.api.nvim_get_current_tabpage()
  local win = vim.api.nvim_tabpage_get_win(tab)
  local cursor = vim.api.nvim_win_get_cursor(win)
  local ok, name = pcall(vim.api.nvim_tabpage_get_var, tab, "tab_name")
  return table.concat({
    vim.fn.fnamemodify(vim.api.nvim_buf_get_name(vim.api.nvim_win_get_buf(win)), ":t"),
    tostring(cursor[1]),
    tostring(cursor[2] + 1),
    vim.fn.haslocaldir(-1, 0) == 1 and vim.fn.fnamemodify(vim.fn.getcwd(-1, 0), ":t") or "-",
    tostring(#vim.api.nvim_list_tabpages()),
    ok and name or "-",
  }, "|")
end
LUA

"$real_nvim" --headless -u NONE --listen "localhost:$port" \
  --cmd "set rtp+=$repo_root/nvim" \
  -c "luafile $tmp_dir/helper.lua" > "$tmp_dir/nvim.log" 2>&1 &
live_pid=$!
for _ in $(seq 1 50); do
  "$real_nvim" --server "localhost:$port" --remote-expr '1' >/dev/null 2>&1 && break
  sleep 0.1
done
"$real_nvim" --server "localhost:$port" --remote-expr '1' >/dev/null 2>&1 \
  || fail "throwaway Neovim never came up (see $tmp_dir/nvim.log)"

probe() {
  "$real_nvim" --server "localhost:$port" --remote-expr 'luaeval("_G.T3ShimProbe()")'
}

state="$tmp_dir/state"
log_file="$state/t3-open-in-nvim.log"
# As T3 Code runs it: detached, from $HOME, with no agent identity of its own.
code() {
  (cd "$tmp_dir/home" && env HOME="$tmp_dir/home" XDG_STATE_HOME="$state" \
    XDG_RUNTIME_DIR="$tmp_dir/empty-runtime" DOTFILES_DIR="$repo_root" \
    "$repo_root/t3code/editor-bin/code" "$@")
}

# A positioned file, as T3 Code passes it: `--goto FILE:LINE:COL`.
code --goto "$work/a.txt:4:2"
assert_eq "$(probe)" "a.txt|4|2|-|2|t3:a.txt" "--goto FILE:LINE:COL opens a new tab at the position"

# Every open gets a tab of its own, even the same file again.
code --goto "$work/sub/b.txt:3"
assert_eq "$(probe)" "b.txt|3|1|-|3|t3:b.txt" "--goto FILE:LINE opens another tab"
code --goto "$work/a.txt:2:1"
assert_eq "$(probe)" "a.txt|2|1|-|4|t3:a.txt" "reopening a file still opens a new tab"

# A file without a position.
code "$work/a.txt"
assert_eq "$(probe)" "a.txt|1|1|-|5|t3:a.txt" "a bare path opens at the top"

# An existing name that looks positioned is a file, not FILE:LINE.
code --goto "$work/build:2"
assert_eq "$(probe)" "build:2|1|1|-|6|t3:build:2" "an existing FILE:N path is not split"

# The project folder: `--new-window DIR` opens a tab whose working directory it is.
code --new-window "$work/sub"
assert_eq "$(probe | cut -d'|' -f4,5,6)" "sub|7|t3:sub" "--new-window DIR opens a tab working in it"

# Failures exit non-zero and are logged, since T3 Code discards the output.
if code --goto "$work/missing.txt:3" 2>/dev/null; then
  fail "a missing file was accepted"
fi
grep -q "missing.txt:3:1 -> exit 1: nvim-show: file does not exist" "$log_file" \
  || fail "the failure was not logged: $(cat "$log_file")"
grep -q "a.txt:4:2 -> exit 0: nvim-show: $session <- " "$log_file" \
  || fail "a success was not logged with its target: $(cat "$log_file")"
assert_eq "$(probe | cut -d'|' -f5)" "7" "a failed open adds no tab"

# On the Mac, a target outside every nvs workdir goes to workspace 1's Neovim,
# which listens on a socket of its own (bin-macos/startup-windows).
if [[ $(uname -s) == Darwin ]]; then
  # A Unix socket path is limited to 104 bytes on macOS, more than $TMPDIR leaves.
  mac_home=$(mktemp -d /tmp/t3shim.XXXXXX)
  trap 'kill "${mac_pid:-}" 2>/dev/null || true; rm -rf "$mac_home"; if [[ -n $live_pid ]]; then kill "$live_pid" 2>/dev/null || true; wait "$live_pid" 2>/dev/null || true; fi; rm -rf "$tmp_dir"' EXIT
  mkdir -p "$mac_home/.local/state" "$tmp_dir/outside"
  cp -R "$tmp_dir/home/.config" "$mac_home/"
  printf 'm1\nm2\nm3\n' > "$tmp_dir/outside/mac.txt"
  mac_sock="$mac_home/.local/state/nvim-local-macos.sock"
  "$real_nvim" --headless -u NONE --listen "$mac_sock" --cmd "set rtp+=$repo_root/nvim" \
    -c "luafile $tmp_dir/helper.lua" > "$tmp_dir/mac-nvim.log" 2>&1 &
  mac_pid=$!
  for _ in $(seq 1 50); do [[ -S $mac_sock ]] && break; sleep 0.1; done
  (cd "$mac_home" && env HOME="$mac_home" XDG_STATE_HOME="$state" XDG_RUNTIME_DIR="$tmp_dir/empty-runtime" \
    DOTFILES_DIR="$repo_root" "$repo_root/t3code/editor-bin/code" --goto "$tmp_dir/outside/mac.txt:2") \
    || fail "the Mac's workspace 1 socket was not used: $(tail -1 "$log_file")"
  got=$("$real_nvim" --server "$mac_sock" --remote-expr 'luaeval("_G.T3ShimProbe()")')
  kill "$mac_pid" 2>/dev/null || true
  wait "$mac_pid" 2>/dev/null || true
  assert_eq "$got" "mac.txt|2|1|-|2|t3:mac.txt" "the Mac opens outside targets in workspace 1's Neovim"
fi

echo "test-t3-editor-shim: ok"
