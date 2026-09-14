#!/usr/bin/env bash
# End-to-end test for bin-linux/nvim-show-resolver and its hook in bin/nvim-show,
# inside a private headless sway: the same isolation as test-sway-windows-e2e.sh
# (headless backend, private bus and runtime dir, stubs by absolute path, cleanup
# by the compositor's own pid list).
#
# The desktop under test: each workspace holds a "terminal" (a ghostty running a
# real Neovim, publishing its default server socket) and an "Omnigent window"
# (a ghostty whose title is driven by a Neovim inside it, standing in for the
# page title). A fake Chromium debug endpoint (nvim-show-fake-devtools.py) says
# which window shows which session. The assertions are about routing: the
# session's window decides the workspace, the workspace decides the editor.
#
# Skips (exit 0) when the compositor or a dependency is unavailable.
set -uo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
for dep in sway swaymsg ghostty jq nvim python3; do
  command -v "$dep" >/dev/null 2>&1 || { echo "SKIP: $dep not found"; exit 0; }
done

TMP="$(mktemp -d)"
if [[ -z "${SWAY_E2E_PRIVATE:-}" ]] && command -v dbus-run-session >/dev/null 2>&1; then
  export SWAY_E2E_PRIVATE=1
  export XDG_RUNTIME_DIR="$TMP/run"; mkdir -m 700 -p "$XDG_RUNTIME_DIR"
  dbus-run-session -- "$BASH" "${BASH_SOURCE[0]}" "$@"; rc=$?
  { command -v fusermount3 || command -v fusermount; } >/dev/null 2>&1 && \
    "$(command -v fusermount3 || command -v fusermount)" -u "$XDG_RUNTIME_DIR/gvfs" 2>/dev/null
  rm -rf "$TMP" 2>/dev/null; exit "$rc"
fi
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
export XDG_CONFIG_HOME="$TMP/config"
unset WAYLAND_DISPLAY DISPLAY SWAYSOCK NVIM NVIM_SHOW_SERVER NVS_TARGET_SESSION CC_SESSION_ID
unset CLAUDE_CODE_CURRENT_SESSION_ID OMNIGENT_SESSION_ID OMNIGENT_RUNNER_PRIMARY_SESSION_ID
unset NVIM_SHOW_ANCESTOR_SESSION_IDS
SWAY_PID=""
FAKE_PID=""

failures=0
pass() { echo "  ok: $*"; }
fail() { echo "  FAIL: $*" >&2; failures=$((failures + 1)); }
check() { if [[ "$2" == "$3" ]]; then pass "$1"; else fail "$1 (expected '$2', got '$3')"; return 1; fi; }

tree() { swaymsg -t get_tree; }
all_pids() { tree | jq -r '[recurse(.nodes[]?,.floating_nodes[]?)|select(.pid!=null)]|.[].pid' | sort -u; }
# Views are addressed by workspace + app_id: the Omnigent stubs share one
# identity, exactly as the real windows all present class "omnigent".
view_on_ws() { # WS APP_ID FIELD
  tree | jq -r --arg ws "$1" --arg a "$2" --arg f "$3" '
    [.nodes[]|.nodes[]?|select(.type=="workspace" and .name==$ws)]|first // {}
    | [recurse(.nodes[]?,.floating_nodes[]?)|select(.pid!=null and .app_id==$a)]|first|.[$f] // empty'
}
id_on_ws() { view_on_ws "$1" "$2" id; }
name_on_ws() { view_on_ws "$1" "$2" name; }
focused_id() { tree | jq -r '[recurse(.nodes[]?,.floating_nodes[]?)|select(.pid!=null and .focused)]|first|.id // empty'; }
wait_for_app() { # WS APP_ID
  for _ in $(seq 1 60); do [[ -n "$(id_on_ws "$1" "$2")" ]] && return 0; sleep 0.25; done
  fail "window $2 never appeared on workspace $1"; return 1
}
wait_for_name() { # WS APP_ID NAME
  for _ in $(seq 1 40); do [[ "$(name_on_ws "$1" "$2")" == "$3" ]] && return 0; sleep 0.25; done
  fail "window $2 on workspace $1 never took the title '$3' (has '$(name_on_ws "$1" "$2")')"; return 1
}
set_title() { # WS TITLE  (the stub's Neovim drives the window title)
  nvim --server "${omni_sock[$1]}" --remote-expr "execute('set titlestring=$2 | redraw')" >/dev/null
}

cleanup() {
  [[ -n "$FAKE_PID" ]] && kill "$FAKE_PID" 2>/dev/null
  if [[ -n "$SWAY_PID" ]] && kill -0 "$SWAY_PID" 2>/dev/null; then
    local pids; pids=$(all_pids 2>/dev/null); [[ -n "$pids" ]] && kill -9 $pids 2>/dev/null
    swaymsg exit >/dev/null 2>&1; sleep 0.5
    kill -9 "$SWAY_PID" 2>/dev/null
  fi
  rm -rf "$TMP"
}
trap cleanup EXIT

# ── compositor ─────────────────────────────────────────────────────────
cat > "$TMP/sway.conf" <<'CONF'
output HEADLESS-1 mode 1920x1080
CONF
WLR_BACKENDS=headless WLR_RENDERER=pixman WLR_LIBINPUT_NO_DEVICES=1 LIBGL_ALWAYS_SOFTWARE=1 \
  sway -c "$TMP/sway.conf" >"$TMP/sway.log" 2>&1 </dev/null &
SWAY_PID=$!
export SWAYSOCK="$XDG_RUNTIME_DIR/sway-ipc.$(id -u).$SWAY_PID.sock"
for _ in $(seq 1 40); do swaymsg -t get_version >/dev/null 2>&1 && break; sleep 0.25; done
swaymsg -t get_version >/dev/null 2>&1 || { echo "SKIP: headless sway did not start (see $TMP/sway.log)"; tail -5 "$TMP/sway.log"; exit 0; }
swaymsg exec -- "sh -c 'echo \$WAYLAND_DISPLAY > $TMP/wd'" >/dev/null
for _ in $(seq 1 20); do [[ -s "$TMP/wd" ]] && break; sleep 0.25; done
export WAYLAND_DISPLAY; WAYLAND_DISPLAY="$(cat "$TMP/wd")"
[[ -n "$WAYLAND_DISPLAY" ]] || { echo "SKIP: could not discover the test WAYLAND_DISPLAY"; exit 0; }
echo "headless sway pid=$SWAY_PID display=$WAYLAND_DISPLAY"

# ── the desktop ────────────────────────────────────────────────────────
# Workspace N: a terminal running Neovim (default socket, so the resolver has
# to find it through the process tree) and an "Omnigent window" whose title is
# a Neovim 'titlestring' (--listen, so it publishes no default socket and can
# never be mistaken for the editor).
mkdir -p "$TMP/work" "$TMP/home" "$TMP/ud" "$TMP/state"
printf 'line1\nline2\nline3\nline4\n' > "$TMP/work/a.txt"
default_sockets() { ls "$XDG_RUNTIME_DIR"/nvim.*.0 2>/dev/null | sort; }

term_sock=()
omni_sock=()
for ws in 1 2; do
  swaymsg "workspace $ws" >/dev/null
  before=$(default_sockets)
  setsid ghostty --class="sway-ws.term.w$ws" -e nvim -u NONE --cmd "set rtp+=$ROOT/nvim" >/dev/null 2>&1 &
  wait_for_app "$ws" "sway-ws.term.w$ws" || exit 1
  for _ in $(seq 1 40); do [[ "$(default_sockets)" != "$before" ]] && break; sleep 0.25; done
  term_sock[$ws]=$(comm -13 <(echo "$before") <(default_sockets) | head -1)
  [[ -n "${term_sock[$ws]}" ]] || { fail "terminal w$ws published no default socket"; exit 1; }
  omni_sock[$ws]="$TMP/omni$ws.sock"
  setsid ghostty --class=omnigent.stub -e nvim -u NONE --listen "${omni_sock[$ws]}" \
    --cmd "set title titlestring=Session\\ $ws" >/dev/null 2>&1 &
  wait_for_app "$ws" omnigent.stub || exit 1
  wait_for_name "$ws" omnigent.stub "Session $ws" || exit 1
done
swaymsg "workspace 1" >/dev/null
echo "editors: ws1=${term_sock[1]} ws2=${term_sock[2]}"

# ── the fake debug endpoint ────────────────────────────────────────────
spec="$TMP/pages.json"
set_pages() { printf '%s\n' "$1" > "$spec"; }
set_pages "[{\"id\":\"P1\",\"url\":\"http://localhost:6767/c/sessionA\",\"nvim\":\"${omni_sock[1]}\"},
            {\"id\":\"P2\",\"url\":\"http://localhost:6767/c/sessionB\",\"nvim\":\"${omni_sock[2]}\"}]"
python3 "$ROOT/tests/nvim-show-fake-devtools.py" "$TMP/ud" "$spec" > "$TMP/fake.port" 2>"$TMP/fake.log" &
FAKE_PID=$!
for _ in $(seq 1 40); do [[ -s "$TMP/fake.port" ]] && break; sleep 0.25; done
[[ -s "$TMP/fake.port" ]] || { fail "fake devtools endpoint did not start"; cat "$TMP/fake.log"; exit 1; }

# Both run the way an agent would: no SWAYSOCK, no CC_SESSION_ID, only the
# runner's session id.
resolver() { # SESSION
  env -u SWAYSOCK OMNIGENT_RUNNER_PRIMARY_SESSION_ID="$1" OMNIGENT_DESKTOP_USER_DATA_DIR="$TMP/ud" \
    NVIM_SHOW_OMNIGENT_IDENTITY=omnigent.stub "$ROOT/bin-linux/nvim-show-resolver"
}
show() { # SESSION args...
  env -u SWAYSOCK HOME="$TMP/home" XDG_STATE_HOME="$TMP/state" AGENT=claude_code \
    OMNIGENT_RUNNER_PRIMARY_SESSION_ID="$1" OMNIGENT_DESKTOP_USER_DATA_DIR="$TMP/ud" \
    NVIM_SHOW_OMNIGENT_IDENTITY=omnigent.stub NVIM_SHOW_RESOLVER="$ROOT/bin-linux/nvim-show-resolver" \
    "$ROOT/bin/nvim-show" "${@:2}"
}
probe() { # SOCK AGENT -> "file|line" in that agent's tab, or "missing"
  nvim --server "$1" --remote-expr "luaeval(\"(function(a) for _, t in ipairs(vim.api.nvim_list_tabpages()) do local ok, v = pcall(vim.api.nvim_tabpage_get_var, t, 'show_in_nvim_agent'); if ok and v == a then local w = vim.api.nvim_tabpage_get_win(t); return vim.fn.fnamemodify(vim.api.nvim_buf_get_name(vim.api.nvim_win_get_buf(w)), ':t') .. '|' .. vim.api.nvim_win_get_cursor(w)[1] end end return 'missing' end)(_A)\", \"$2\")"
}

echo "== missing identity errors explain how to recover =="
out=$(resolver '' 2>"$TMP/err"); rc=$?
check "missing conversation identity passes" 1 "$rc"
identity_error="nvim-show-resolver: Omnigent conversation identity is unavailable (OMNIGENT_SESSION_ID and OMNIGENT_RUNNER_PRIMARY_SESSION_ID are unset)"
check "missing conversation identity is explicit" "$identity_error" "$(cat "$TMP/err")"
out=$(show '' "$TMP/work/a.txt" 1 2>&1); rc=$?
check "missing annotation identity fails before routing" 1 "$rc"
check "missing annotation identity is explicit" \
  "nvim-show: cannot determine the calling agent's session id — pass --agent KEY" "$out"
out=$(CC_SESSION_ID=claude-without-omnigent show '' "$TMP/work/a.txt" 1 2>&1); rc=$?
check "annotation identity alone cannot choose between desktop editors" 1 "$rc"
grep -Fq "cannot determine which Neovim to use" <<<"$out" && pass "generic resolution error is reported" \
  || fail "missing resolution error: $out"
grep -Fq "  $identity_error" <<<"$out" && pass "resolver identity note reaches the caller" \
  || fail "missing recovery note: $out"

echo "== the session's window picks the workspace, the workspace picks the editor =="
out=$(resolver sessionA 2>"$TMP/err"); rc=$?
check "session A resolves" 0 "$rc"
check "session A -> the ws 1 editor" "server=${term_sock[1]}" "$(grep '^server=' <<<"$out")"
check "label names the workspace and terminal" "label=ws 1: sway-ws.term.w1" "$(grep '^label=' <<<"$out")"
check "focus command targets the terminal container" \
  "focus=swaymsg -s $SWAYSOCK '[con_id=$(id_on_ws 1 sway-ws.term.w1)] focus'" "$(grep '^focus=' <<<"$out")"
out=$(resolver sessionB 2>"$TMP/err")
check "session B -> the ws 2 editor" "server=${term_sock[2]}" "$(grep '^server=' <<<"$out")"

echo "== the whole path through nvim-show, identity from the runner, --focus =="
swaymsg "[con_id=$(id_on_ws 1 omnigent.stub)] focus" >/dev/null
out=$(show sessionA "$TMP/work/a.txt" 3 --focus 2>&1); rc=$?
check "nvim-show succeeds without CC_SESSION_ID or SWAYSOCK" 0 "$rc"
check "the jump landed in the ws 1 editor" "a.txt|3" "$(probe "${term_sock[1]}" sessionA)"
check "the ws 2 editor is untouched" "missing" "$(probe "${term_sock[2]}" sessionA)"
check "--focus brought the terminal tab forward" "$(id_on_ws 1 sway-ws.term.w1)" "$(focused_id)"
check "the target and its focus command are remembered" \
  "ws 1: sway-ws.term.w1|${term_sock[1]}|swaymsg -s $SWAYSOCK '[con_id=$(id_on_ws 1 sway-ws.term.w1)] focus'" \
  "$(paste -sd'|' "$TMP/state/nvim-show/sessionA")"

echo "== owning conversation identity is independent of agent type and runner =="
out=$(OMNIGENT_SESSION_ID=sessionB show sessionA "$TMP/work/a.txt" 4 --focus 2>&1); rc=$?
check "owning conversation overrides the runner's primary conversation" 0 "$rc"
check "the jump landed in the owning conversation's editor" "a.txt|4" "$(probe "${term_sock[2]}" sessionB)"
check "the primary conversation's editor is untouched" "missing" "$(probe "${term_sock[1]}" sessionB)"
check "focus follows the owning conversation" "$(id_on_ws 2 sway-ws.term.w2)" "$(focused_id)"
out=$(OMNIGENT_SESSION_ID=sessionB show '' "$TMP/work/a.txt" 2 2>&1); rc=$?
check "canonical identity works without legacy identity" 0 "$rc"
check "canonical-only jump landed" "a.txt|2" "$(probe "${term_sock[2]}" sessionB)"
out=$(OMNIGENT_SESSION_ID=sessionB NVIM_SHOW_ANCESTOR_SESSION_IDS='' resolver '' 2>"$TMP/err"); rc=$?
check "empty ancestry is treated as absent" 0 "$rc"
check "empty ancestry preserves caller routing" "server=${term_sock[2]}" "$(grep '^server=' <<<"$out")"
out=$(OMNIGENT_SESSION_ID=sessionZ-hidden resolver sessionA 2>"$TMP/err"); rc=$?
check "a hidden owning conversation does not resolve as the primary conversation" 1 "$rc"
check "hidden owning conversation reports its own identity" \
  "nvim-show-resolver: session sessionZ is not open in any Omnigent window" "$(cat "$TMP/err")"

echo "== verified ancestors select windows without changing annotation ownership =="
out=$(OMNIGENT_SESSION_ID=sessionB NVIM_SHOW_ANCESTOR_SESSION_IDS='["sessionA"]' resolver '' 2>"$TMP/err"); rc=$?
check "a visible caller wins over its ancestor" 0 "$rc"
check "the caller's own editor is selected" "server=${term_sock[2]}" "$(grep '^server=' <<<"$out")"
out=$(OMNIGENT_SESSION_ID=child-session NVIM_SHOW_ANCESTOR_SESSION_IDS='["sessionB","sessionA"]' \
  show sessionA "$TMP/work/a.txt" 4 --focus 2>&1); rc=$?
check "a hidden child can use its nearest displayed ancestor" 0 "$rc"
check "the child owns its tab in the parent's editor" "a.txt|4" "$(probe "${term_sock[2]}" child-session)"
check "the parent's tab is unchanged" "a.txt|2" "$(probe "${term_sock[2]}" sessionB)"
check "the more distant ancestor is untouched" "missing" "$(probe "${term_sock[1]}" child-session)"
check "focus follows the nearest displayed ancestor" "$(id_on_ws 2 sway-ws.term.w2)" "$(focused_id)"
set_pages "[{\"id\":\"P1\",\"url\":\"http://localhost:6767/c/sessionA\",\"nvim\":\"${omni_sock[1]}\"}]"
out=$(OMNIGENT_SESSION_ID=child-session NVIM_SHOW_ANCESTOR_SESSION_IDS='["sessionB","sessionA"]' \
  show '' "$TMP/work/a.txt" 2 2>&1); rc=$?
check "window visibility is rechecked on subsequent calls" 0 "$rc"
check "a hidden parent allows the displayed grandparent" "a.txt|2" "$(probe "${term_sock[1]}" child-session)"
check "the grandparent's annotations are unchanged" "a.txt|3" "$(probe "${term_sock[1]}" sessionA)"
out=$(OMNIGENT_SESSION_ID=child-session NVIM_SHOW_ANCESTOR_SESSION_IDS='["hidden-parent"]' \
  resolver sessionA 2>"$TMP/err"); rc=$?
check "an undisplayed family does not use an unrelated primary session" 1 "$rc"
check "the unavailable family is explained" \
  "nvim-show-resolver: session child-se is not open in any Omnigent window (nor are its supplied ancestors)" "$(cat "$TMP/err")"
for invalid in 'null' '"sessionA"' '[""]' '[1]' 'not-json'; do
  out=$(OMNIGENT_SESSION_ID=sessionA NVIM_SHOW_ANCESTOR_SESSION_IDS="$invalid" resolver '' 2>"$TMP/err"); rc=$?
  check "malformed ancestry is rejected: $invalid" 2 "$rc"
done
set_pages "[{\"id\":\"P1\",\"url\":\"http://localhost:6767/c/sessionA\",\"nvim\":\"${omni_sock[1]}\"},
            {\"id\":\"P2\",\"url\":\"http://localhost:6767/c/sessionB\",\"nvim\":\"${omni_sock[2]}\"}]"

term2_id=$(id_on_ws 2 sway-ws.term.w2)
swaymsg "[con_id=$term2_id] move container to workspace 3" >/dev/null
out=$(OMNIGENT_SESSION_ID=child-session NVIM_SHOW_ANCESTOR_SESSION_IDS='["sessionB","sessionA"]' \
  resolver '' 2>"$TMP/err"); rc=$?
check "a displayed ancestor without an editor is not skipped" 1 "$rc"
check "the unavailable editor is reported for the nearest displayed ancestor" \
  "nvim-show-resolver: no Neovim is running on workspace 2, where session sessionB is shown" "$(cat "$TMP/err")"
swaymsg "[con_id=$term2_id] move container to workspace 2" >/dev/null

echo "== duplicate titles fall back to the nonce probe, and titles are restored =="
for ws in 1 2; do set_title "$ws" Same; wait_for_name "$ws" omnigent.stub Same; done
out=$(resolver sessionB 2>"$TMP/err"); rc=$?
check "ambiguous title still resolves" 0 "$rc"
check "nonce probe found session B's window on ws 2" "server=${term_sock[2]}" "$(grep '^server=' <<<"$out")"
sleep 0.5
check "session B's title was put back" "Same" "$(name_on_ws 2 omnigent.stub)"
check "the other window was never touched" "Same" "$(name_on_ws 1 omnigent.stub)"

echo "== a session open in two windows follows the focused workspace =="
set_pages "[{\"id\":\"P1\",\"url\":\"http://localhost:6767/c/sessionA\",\"nvim\":\"${omni_sock[1]}\"},
            {\"id\":\"P2\",\"url\":\"http://localhost:6767/c/sessionA\",\"nvim\":\"${omni_sock[2]}\"}]"
for ws in 1 2; do set_title "$ws" "Session\\ $ws"; wait_for_name "$ws" omnigent.stub "Session $ws"; done
swaymsg "workspace 2" >/dev/null; sleep 0.3
out=$(resolver sessionA 2>"$TMP/err")
check "focused workspace wins (ws 2)" "server=${term_sock[2]}" "$(grep '^server=' <<<"$out")"
swaymsg "workspace 1" >/dev/null; sleep 0.3
out=$(resolver sessionA 2>"$TMP/err")
check "focused workspace wins (ws 1)" "server=${term_sock[1]}" "$(grep '^server=' <<<"$out")"

echo "== a session that is not shown anywhere =="
out=$(resolver sessionZ 2>"$TMP/err"); rc=$?
check "resolver passes (exit 1)" 1 "$rc"
check "and says why" "nvim-show-resolver: session sessionZ is not open in any Omnigent window" "$(cat "$TMP/err")"
# nvim-show then falls back to the editor this agent used before (the agent
# key is session A's; the runner now reports a session no window shows) ...
out=$(show sessionZ --agent sessionA "$TMP/work/a.txt" 2 2>&1); rc=$?
check "a remembered editor still serves a hidden session" 0 "$rc"
check "the remembered jump landed" "a.txt|2" "$(probe "${term_sock[1]}" sessionA)"
# ... and a fresh agent gets the resolver's reason in the error.
out=$(show sessionZ "$TMP/work/a.txt" 1 2>&1); rc=$?
check "a fresh hidden session fails" 1 "$rc"
grep -q "not open in any Omnigent window" <<<"$out" && pass "the error carries the resolver's reason" \
  || fail "error lacks the resolver's reason: $out"
grep -q "Several are running" <<<"$out" && pass "and lists the running editors" \
  || fail "error does not list the editors: $out"

echo "== no debug endpoint =="
mv "$TMP/ud/DevToolsActivePort" "$TMP/ud/DevToolsActivePort.off"
out=$(resolver sessionA 2>"$TMP/err"); rc=$?
check "resolver passes when the port file is missing" 1 "$rc"
out=$(NVIM_SHOW_ANCESTOR_SESSION_IDS='not-json' resolver sessionA 2>"$TMP/err"); rc=$?
check "malformed desktop ancestry cannot block non-desktop routing" 1 "$rc"
grep -q "remote-debugging-port=0" "$TMP/err" && pass "and explains the launcher flag" \
  || fail "unhelpful message: $(cat "$TMP/err")"
mv "$TMP/ud/DevToolsActivePort.off" "$TMP/ud/DevToolsActivePort"

if (( failures > 0 )); then
  echo "test-nvim-show-resolver-e2e: $failures failure(s)" >&2
  exit 1
fi
echo "test-nvim-show-resolver-e2e: ok"
