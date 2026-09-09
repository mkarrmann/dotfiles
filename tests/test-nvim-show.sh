#!/usr/bin/env bash
set -euo pipefail

# End-to-end coverage for bin/nvim-show against a throwaway headless Neovim.
# Never touches the live nvs@ servers: the session name and therefore the port
# are derived from this test's PID, and $HOME is redirected so nvs-sessions-file
# reads a fixture list rather than the real one.

repo_root=$(cd "$(dirname "$0")/.." && pwd)
tmp_dir=$(mktemp -d)
tmp_dir=$(cd "$tmp_dir" && pwd -P)
real_nvim=$(command -v nvim)
live_pid=""
trap 'if [[ -n $live_pid ]]; then kill "$live_pid" 2>/dev/null || true; wait "$live_pid" 2>/dev/null || true; fi; rm -rf "$tmp_dir"' EXIT

fail() {
  echo "test-nvim-show: $*" >&2
  exit 1
}

assert_eq() {
  [[ $1 == "$2" ]] || fail "$3: expected '$2', got '$1'"
}

session="nvim-show-test-$$"
port=$(printf '%s' "$session" | cksum | awk '{ print 7000 + ($1 % 1000) }')

work="$tmp_dir/work"
outside="$tmp_dir/outside"
mkdir -p "$work" "$outside" "$tmp_dir/home/.config/nvs"
for name in a b c; do
  printf 'line1\nline2\nline3\nline4\nline5\n' > "$work/$name.txt"
done
printf 'stray\n' > "$outside/stray.txt"

cat > "$tmp_dir/home/.config/nvs/sessions.$(hostname -s)" <<EOF
# fixture session list
$session $work
EOF

cat > "$tmp_dir/helper.lua" <<'LUA'
function _G.NvimShowTestProbe(agent)
  local target
  for _, tab in ipairs(vim.api.nvim_list_tabpages()) do
    local ok, value = pcall(vim.api.nvim_tabpage_get_var, tab, "show_in_nvim_agent")
    if ok and value == agent then
      target = tab
    end
  end
  if not target then
    return "missing"
  end
  local win = vim.api.nvim_tabpage_get_win(target)
  local ns = vim.api.nvim_create_namespace("show-in-nvim:" .. agent)
  local marks = 0
  for _, buf in ipairs(vim.api.nvim_list_bufs()) do
    if vim.api.nvim_buf_is_loaded(buf) then
      marks = marks + #vim.api.nvim_buf_get_extmarks(buf, ns, 0, -1, {})
    end
  end
  return table.concat({
    vim.fn.fnamemodify(vim.api.nvim_buf_get_name(vim.api.nvim_win_get_buf(win)), ":t"),
    tostring(vim.api.nvim_win_get_cursor(win)[1]),
    tostring(#vim.fn.getloclist(win)),
    tostring(marks),
    tostring(#vim.api.nvim_list_tabpages()),
    tostring(vim.api.nvim_tabpage_get_var(target, "tab_name")),
  }, "|")
end

function _G.NvimShowTestListWins(agent)
  for _, tab in ipairs(vim.api.nvim_list_tabpages()) do
    local ok, value = pcall(vim.api.nvim_tabpage_get_var, tab, "show_in_nvim_agent")
    if ok and value == agent then
      local count = 0
      for _, win in ipairs(vim.api.nvim_tabpage_list_wins(tab)) do
        if vim.bo[vim.api.nvim_win_get_buf(win)].buftype == "quickfix" then
          count = count + 1
        end
      end
      return tostring(count)
    end
  end
  return "missing"
end
LUA

# -u NONE keeps the fixture hermetic: the real config runs lazy.nvim, which
# rebuilds 'runtimepath' and would drop the repo directory added here.
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
  "$real_nvim" --server "localhost:$port" --remote-expr "luaeval(\"_G.NvimShowTestProbe(_A)\", \"$1\")"
}

list_wins() {
  "$real_nvim" --server "localhost:$port" --remote-expr "luaeval(\"_G.NvimShowTestListWins(_A)\", \"$1\")"
}

state="$tmp_dir/state"
empty_runtime="$tmp_dir/empty-runtime"
mkdir -p "$empty_runtime"

show() {
  env HOME="$tmp_dir/home" XDG_STATE_HOME="$state" XDG_RUNTIME_DIR="$empty_runtime" \
    CC_SESSION_ID="$1" AGENT=claude_code \
    "$repo_root/bin/nvim-show" "${@:2}"
}

# --- resolution by workdir prefix, and one tab per agent ---------------------

show agent-one "$work/a.txt" 3 2 > "$tmp_dir/out"
grep -q "$session" "$tmp_dir/out" || fail "did not report the resolved session"
assert_eq "$(probe agent-one)" "a.txt|3|1|0|2|claude_code:agent-on" "first show"

# A second jump from the same agent reuses that agent's tab.
show agent-one "$work/b.txt" 5 > /dev/null
assert_eq "$(probe agent-one)" "b.txt|5|2|0|2|claude_code:agent-on" "second show reuses the tab"

# A different agent gets its own tab.
show agent-two "$work/c.txt" 2 > /dev/null
assert_eq "$(probe agent-two)" "c.txt|2|1|0|3|claude_code:agent-tw" "second agent gets its own tab"
assert_eq "$(probe agent-one)" "b.txt|5|2|0|3|claude_code:agent-on" "first agent's tab is untouched"

# --- location list + virtual text -------------------------------------------

cat > "$tmp_dir/findings.json" <<EOF
[
  {"file": "$work/a.txt", "line": 1, "col": 1, "text": "first finding"},
  {"file": "$work/b.txt", "line": 2, "col": 1, "text": "second finding"},
  {"file": "$work/c.txt", "line": 3, "col": 1, "text": "third finding"}
]
EOF
show agent-one --list "$tmp_dir/findings.json" > /dev/null
assert_eq "$(probe agent-one)" "a.txt|1|3|3|3|claude_code:agent-on" "--list replaces the loclist and annotates"
assert_eq "$(probe agent-two)" "c.txt|2|1|0|3|claude_code:agent-tw" "--list does not leak into another agent"
assert_eq "$(list_wins agent-one)" "1" "--list opens the location-list window"
assert_eq "$(list_wins agent-two)" "0" "a single jump opens no list window"

show agent-one --list - < "$tmp_dir/findings.json" > /dev/null
assert_eq "$(probe agent-one)" "a.txt|1|3|3|3|claude_code:agent-on" "--list reads stdin"

# The source may also trail other options rather than follow --list directly.
show agent-one --list --title "trailing source" - < "$tmp_dir/findings.json" > /dev/null
assert_eq "$(probe agent-one)" "a.txt|1|3|3|3|claude_code:agent-on" "--list source after another flag"
show agent-one --list --title "trailing path" "$tmp_dir/findings.json" > /dev/null
assert_eq "$(probe agent-one)" "a.txt|1|3|3|3|claude_code:agent-on" "--list path after another flag"

# --clear carries no path, so it can only resolve through the session this agent
# is remembered to own.
assert_eq "$(head -1 "$state/nvim-show/agent-one")" "$session" "the resolved target is remembered"
assert_eq "$(sed -n 2p "$state/nvim-show/agent-one")" "localhost:$port" "the remembered address is recorded"
show agent-one --clear > /dev/null
assert_eq "$(probe agent-one)" "a.txt|1|0|0|3|claude_code:agent-on" "--clear empties the list and annotations"
assert_eq "$(probe agent-two)" "c.txt|2|1|0|3|claude_code:agent-tw" "--clear is scoped to one agent"
assert_eq "$(list_wins agent-one)" "0" "--clear closes the location-list window"

# --- resolution order and failure modes --------------------------------------

# A path outside every declared workdir, with no pin, must fail loudly.
if (cd "$outside" && show agent-three "$outside/stray.txt" 1) > "$tmp_dir/out" 2>&1; then
  fail "unresolvable path was accepted"
fi
grep -q "cannot determine which Neovim to use" "$tmp_dir/out" || fail "unhelpful resolution error"
grep -q "nvs sessions declared" "$tmp_dir/out" || fail "did not offer the declared nvs sessions"

# NVS_TARGET_SESSION is the fallback for exactly that case.
(cd "$outside" && env NVS_TARGET_SESSION="$session" HOME="$tmp_dir/home" XDG_STATE_HOME="$state" XDG_RUNTIME_DIR="$empty_runtime" \
  CC_SESSION_ID=agent-three AGENT=claude_code "$repo_root/bin/nvim-show" "$outside/stray.txt" 1 > /dev/null)
assert_eq "$(probe agent-three)" "stray.txt|1|1|0|4|claude_code:agent-th" "NVS_TARGET_SESSION fallback"

# Having resolved once, the same agent no longer needs the pin.
(cd "$outside" && show agent-three "$outside/stray.txt" 1 > /dev/null)
assert_eq "$(probe agent-three)" "stray.txt|1|2|0|4|claude_code:agent-th" "remembered session"

# --session wins over inference, and $NVIM wins over both.
show agent-four --session "$session" "$work/a.txt" 4 > /dev/null
assert_eq "$(probe agent-four)" "a.txt|4|1|0|5|claude_code:agent-fo" "--session"
env HOME="$tmp_dir/home" XDG_STATE_HOME="$state" XDG_RUNTIME_DIR="$empty_runtime" \
  CC_SESSION_ID=agent-five NVIM="localhost:$port" \
  AGENT=claude_code "$repo_root/bin/nvim-show" "$work/b.txt" 1 > /dev/null
assert_eq "$(probe agent-five)" "b.txt|1|1|0|6|claude_code:agent-fi" "\$NVIM"

# Missing agent identity, bad arguments, and missing files are all rejected.
if env HOME="$tmp_dir/home" XDG_STATE_HOME="$state" XDG_RUNTIME_DIR="$empty_runtime" \
  -u CC_SESSION_ID -u CLAUDE_CODE_CURRENT_SESSION_ID -u OMNIGENT_SESSION_ID \
  "$repo_root/bin/nvim-show" "$work/a.txt" 2>/dev/null; then
  fail "missing agent identity was accepted"
fi
if show agent-six "$work/a.txt" 0 2>/dev/null; then
  fail "line 0 was accepted"
fi
if show agent-six "$work/nope.txt" 1 2>/dev/null; then
  fail "missing file was accepted"
fi
if echo 'not json' | show agent-six --list - 2>/dev/null; then
  fail "invalid --list JSON was accepted"
fi

# --- portable layer: no nvs session list at all -------------------------------

# A second Neovim, started the ordinary way, publishes a default server socket.
# Nothing below declares an nvs session, so only the portable rules can resolve.
bare_home="$tmp_dir/bare"
runtime="$tmp_dir/runtime"
mkdir -p "$bare_home" "$runtime"

bare() {
  env HOME="$bare_home" XDG_RUNTIME_DIR="$runtime" XDG_STATE_HOME="$state" \
    CC_SESSION_ID="$1" AGENT=claude_code "$repo_root/bin/nvim-show" "${@:2}"
}

# With no Neovim running and no nvs list, it must fail with actionable advice.
if bare bare-one "$work/a.txt" 1 > "$tmp_dir/out" 2>&1; then
  fail "resolved a target with nothing running"
fi
grep -q "Start Neovim, or name one with --server" "$tmp_dir/out" || fail "unhelpful portable error"
grep -q "nvs sessions declared" "$tmp_dir/out" && fail "advertised an nvs list that does not apply"

start_bare_nvim() {
  env HOME="$bare_home" XDG_RUNTIME_DIR="$runtime" "$real_nvim" --headless -u NONE \
    --cmd "set rtp+=$repo_root/nvim" > "$tmp_dir/bare-$1.log" 2>&1 &
  bare_pids+=($!)
  for _ in $(seq 1 50); do
    (( $(ls "$runtime"/nvim.*.0 2>/dev/null | wc -l) >= $1 )) && return 0
    sleep 0.1
  done
  fail "bare Neovim $1 never published a socket"
}

bare_pids=()
start_bare_nvim 1
bare_sock=$(ls "$runtime"/nvim.*.0)

# Sole running Neovim, discovered from its socket. No configuration at all.
bare bare-one "$work/a.txt" 3 > "$tmp_dir/out"
grep -q "$bare_sock" "$tmp_dir/out" || fail "did not discover the sole running Neovim"
assert_eq "$("$real_nvim" --server "$bare_sock" --remote-expr "luaeval(\"_G and 1 or 1\")")" "1" "bare Neovim still responds"

# A second instance makes discovery ambiguous, and it must say so rather than pick.
start_bare_nvim 2
if env HOME="$bare_home" XDG_RUNTIME_DIR="$runtime" XDG_STATE_HOME="$tmp_dir/state-fresh" \
  CC_SESSION_ID=bare-two AGENT=claude_code "$repo_root/bin/nvim-show" "$work/b.txt" 1 > "$tmp_dir/out" 2>&1; then
  fail "ambiguous discovery was resolved anyway"
fi
grep -q "Several are running" "$tmp_dir/out" || fail "did not report the ambiguity"

# --server names one explicitly, and $NVIM_SHOW_SERVER pins one.
bare bare-three --server "$bare_sock" "$work/c.txt" 2 > /dev/null
env HOME="$bare_home" XDG_RUNTIME_DIR="$runtime" XDG_STATE_HOME="$tmp_dir/state-fresh" \
  NVIM_SHOW_SERVER="$bare_sock" CC_SESSION_ID=bare-four AGENT=claude_code \
  "$repo_root/bin/nvim-show" "$work/a.txt" 1 > /dev/null

# An agent whose remembered target has died falls through instead of erroring.
kill "${bare_pids[1]}" 2>/dev/null || true
wait "${bare_pids[1]}" 2>/dev/null || true
bare bare-three "$work/a.txt" 1 > "$tmp_dir/out"
grep -q "$bare_sock" "$tmp_dir/out" || fail "did not fall through a dead remembered target"

for pid in "${bare_pids[@]}"; do
  kill "$pid" 2>/dev/null || true
  wait "$pid" 2>/dev/null || true
done

echo "test-nvim-show: ok"
