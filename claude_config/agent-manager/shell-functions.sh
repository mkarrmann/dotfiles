#!/bin/bash
# Claude Agent Manager — Shell Functions
# Sourced from ~/.shellrc (portable across machines)
# Provides: claude wrapper, cn, cr, cclean, cbk, cbp, cba, cbls, agents

AGENT_TRACKER="$HOME/.claude/agent-manager/bin/agent-tracker.sh"
CODEX_SESSIONS_INDEX="$HOME/.codex/agents.tsv"

_AGENT_HOST=$(hostname -s)
CLAUDE_BG_LOGDIR="$HOME/claude-logs"

if [ -n "${CLAUDE_AGENTS_FILE:-}" ]; then
  AGENTS_FILE_LOCAL="$CLAUDE_AGENTS_FILE"
  AGENTS_DIR="$(dirname "$AGENTS_FILE_LOCAL")"
else
  _conf="$HOME/.claude/obsidian-vault.conf"
  [ -f "$_conf" ] && . "$_conf"
  AGENTS_DIR="${OBSIDIAN_VAULT:-$HOME/obsidian}"
  unset _conf
  AGENTS_FILE_LOCAL="${AGENTS_DIR}/AGENTS-${_AGENT_HOST}.md"
fi

mkdir -p "$CLAUDE_BG_LOGDIR" 2>/dev/null

# ============================================================
# claude() wrapper — replaces any .bashrc alias
#   Adds --dangerously-skip-permissions automatically.
#   On exit, marks session as stopped via agent-tracker.sh.
#   Skips stop-tracking for -p/--print mode and CLAUDE_BG_ACTIVE.
# ============================================================
_claude_session_exit() {
  if [ -f ~/.claude-last-session ]; then
    local sid
    sid=$(cat ~/.claude-last-session)
    echo "{\"session_id\": \"$sid\", \"transcript_path\": \"\"}" | bash "$AGENT_TRACKER" stop 2>/dev/null
  fi
}

unalias claude 2>/dev/null
claude() {
  # Install HUP trap so exit handler fires even if terminal is killed
  local _old_hup_trap
  _old_hup_trap=$(trap -p HUP)
  trap '_claude_session_exit' HUP

  command claude --dangerously-skip-permissions "$@"
  local exit_code=$?

  # Restore previous HUP trap
  if [ -n "$_old_hup_trap" ]; then
    eval "$_old_hup_trap"
  else
    trap - HUP
  fi

  if [[ -z "${CLAUDE_BG_ACTIVE:-}" ]]; then
    local is_print=false
    for arg in "$@"; do
      case "$arg" in
        -p|--print) is_print=true; break ;;
      esac
    done
    if ! $is_print; then
      _claude_session_exit
    fi
  fi
  return $exit_code
}

# ============================================================
# Internal helpers
# ============================================================

# _nvim_tab_handle — get the current Neovim tab handle (integer)
_nvim_tab_handle() {
  [ -z "$NVIM" ] && return 0
  nvim --headless --server "$NVIM" --remote-expr "luaeval('vim.api.nvim_get_current_tabpage()')" 2>/dev/null
}

# _nvim_exec_lua <lua-code> — execute Lua in the parent Neovim instance
_nvim_exec_lua() {
  [ -z "$NVIM" ] || nvim --server "$NVIM" --remote-expr "execute('lua $1')" >/dev/null 2>&1
}

# _lookup_sid <name> — resolve session name to full session ID (searches all per-host files)
_lookup_sid() {
  local name="$1"
  local sid
  for f in "${AGENTS_DIR}"/AGENTS-*.md "${AGENTS_DIR}/AGENTS.md"; do
    [ -f "$f" ] || continue
    sid=$(grep "| ${name} |" "$f" 2>/dev/null | head -1 | awk -F'|' '{print $5}' | tr -d ' ')
    [ -n "$sid" ] && break
  done
  echo "$sid"
}

# _lookup_dir <name> — resolve session name to its working directory (searches all per-host files)
_lookup_dir() {
  local name="$1"
  for f in "${AGENTS_DIR}"/AGENTS-*.md "${AGENTS_DIR}/AGENTS.md"; do
    [ -f "$f" ] || continue
    local dir
    dir=$(grep "| ${name} |" "$f" 2>/dev/null | head -1 | awk -F'|' '{print $9}' | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')
    [ -n "$dir" ] && { echo "$dir"; return; }
  done
}

# _start_bg_session <name> <prompt> [sid]
#   If sid is provided, resumes that session (cr -b).
#   If sid is empty, starts a new session (cn -b).
_start_bg_session() {
  local name="$1" prompt="$2" sid="${3:-}"
  local tab_name="cb-${name}"
  local logfile="${CLAUDE_BG_LOGDIR}/${name}.log"

  sudo ondemand-idle-checks disable 2>/dev/null

  # Kill any existing Neovim tab for this bg session
  _nvim_exec_lua "_G._claude_kill_tab_by_name('${tab_name}')"

  local tmpscript
  tmpscript=$(mktemp /tmp/claude-bg-XXXXXX.sh)
  chmod +x "$tmpscript"
  mkdir -p "$(dirname "$logfile")"

  if [ -n "$sid" ]; then
    # Resume mode (cr -b): has session ID, use --resume
    bash "$AGENT_TRACKER" background "$name" "$sid" "$prompt"
    cat > "$tmpscript" <<SCRIPT
#!/bin/bash
command claude --dangerously-skip-permissions -p --resume '${sid}' --fork-session '${prompt}' 2>&1 | tee '${logfile}'
bash '${AGENT_TRACKER}' bg-done '${sid}'
echo ''
echo '=== DONE (exit to close) ==='
rm -f "\$0"
exec bash
SCRIPT
  else
    # New mode (cn -b): no session ID yet, no --resume
    echo "$name" > ~/.claude-next-name
    touch ~/.claude-next-bg
    cat > "$tmpscript" <<SCRIPT
#!/bin/bash
command claude --dangerously-skip-permissions -p '${prompt}' 2>&1 | tee '${logfile}'
_sid=\$(cat ~/.claude-last-session 2>/dev/null)
[ -n "\$_sid" ] && bash '${AGENT_TRACKER}' bg-done "\$_sid"
echo ''
echo '=== DONE (exit to close) ==='
rm -f "\$0"
exec bash
SCRIPT
  fi

  _nvim_exec_lua "_G._claude_open_bg_session('${tab_name}', '${tmpscript}')"

  echo "Background session '${name}' started in Neovim tab."
  echo "  cbp ${name}   # peek at output"
  echo "  cba ${name}   # focus tab"
  echo "  cr ${name}    # resume interactively"
}

# ============================================================
# cn [-b] <name> [prompt...] — Start a named Claude session
#   cn <name>              → interactive session tracked in AGENTS.md
#   cn -b <name> <prompt>  → new background session in Neovim tab
# ============================================================
cn() {
  local bg_mode=false
  if [ "$1" = "-b" ]; then
    bg_mode=true
    shift
  fi
  local name="$1"
  [ -z "$name" ] && { echo "Usage: cn [-b] <name> [prompt...]"; return 1; }

  # Warn if name already exists in any agents file
  local _found_file=""
  for f in "${AGENTS_DIR}"/AGENTS-*.md "${AGENTS_DIR}/AGENTS.md"; do
    [ -f "$f" ] || continue
    if grep -q "| ${name} |" "$f" 2>/dev/null; then
      _found_file="$f"
      break
    fi
  done
  if [ -n "$_found_file" ]; then
    local existing_status
    existing_status=$(grep "| ${name} |" "$_found_file" | tail -1 | awk -F'|' '{print $3}' | xargs)
    echo "Warning: '${name}' already exists (${existing_status})"
    read -rp "Replace it with a new session? [y/N] " reply
    if [[ "$reply" != [yY] ]]; then
      echo "Cancelled. Use 'cr ${name}' to resume the existing session."
      return 0
    fi
    # Remove old row from local file only
    if [ -f "$AGENTS_FILE_LOCAL" ] && grep -q "| ${name} |" "$AGENTS_FILE_LOCAL" 2>/dev/null; then
      local tmpfile="${AGENTS_FILE_LOCAL}.tmp"
      grep -v "| ${name} |" "$AGENTS_FILE_LOCAL" > "$tmpfile"
      mv "$tmpfile" "$AGENTS_FILE_LOCAL"
    fi
    echo "Replaced. Starting new session '${name}'..."
  fi

  if $bg_mode; then
    shift
    local prompt="$*"
    [ -z "$prompt" ] && { echo "Error: background mode requires a prompt"; return 1; }
    _start_bg_session "$name" "$prompt" ""
  else
    echo "$name" > ~/.claude-next-name
    export NVIM_TAB_HANDLE
    NVIM_TAB_HANDLE=$(_nvim_tab_handle)
    _nvim_exec_lua "_G._nvim_rename_current_tab('${name}')"
    claude
  fi
}

# ============================================================
# cr [-b] <name> [prompt...] — Resume an existing session
#   cr <name>              → resume interactively (kills bg Neovim tab first)
#   cr -b <name> <prompt>  → resume in background in a new Neovim tab
# ============================================================
cr() {
  local bg_mode=false
  if [ "$1" = "-b" ]; then
    bg_mode=true
    shift
  fi
  local name="$1"
  [ -z "$name" ] && { echo "Usage: cr [-b] <name> [prompt...]"; return 1; }

  local sid
  sid=$(_lookup_sid "$name")
  [ -z "$sid" ] && { echo "No session ID found for '${name}'."; return 1; }

  if $bg_mode; then
    shift
    local prompt="$*"
    [ -z "$prompt" ] && { echo "Error: background mode requires a prompt"; return 1; }
    _start_bg_session "$name" "$prompt" "$sid"
  else
    local session_dir
    session_dir=$(_lookup_dir "$name")
    if [ -n "$session_dir" ] && [ -d "$session_dir" ] && [ "$session_dir" != "$PWD" ]; then
      echo "cd $session_dir"
      cd "$session_dir" || { echo "Cannot cd to $session_dir"; return 1; }
    fi
    _nvim_exec_lua "_G._claude_kill_tab_by_name('cb-${name}')"
    echo "Resuming session ${sid:0:8}... (${name}) interactively..."
    # Pre-set name so if session was compacted (new UUID), the hook inherits the name
    echo "$name" > ~/.claude-next-name
    export NVIM_TAB_HANDLE
    NVIM_TAB_HANDLE=$(_nvim_tab_handle)
    _nvim_exec_lua "_G._nvim_rename_current_tab('${name}')"
    claude --resume "$sid" --fork-session
    local exit_code=$?
    # Clean up: if compacted, hook already consumed it; if normal resume, it's orphaned
    rm -f ~/.claude-next-name
    if [ $exit_code -ne 0 ]; then
      echo ""
      echo "Session '${name}' (${sid:0:8}...) could not be resumed."
      echo "The session may have expired or been compacted."
      echo "  cn ${name}    # start a fresh session with this name"
      echo "  cclean ${name}  # remove the stale entry"
    fi
    return $exit_code
  fi
}

# ============================================================
# cwatch — Start/stop/status the agent watcher daemon
# ============================================================
cwatch() {
  local watcher="$HOME/.claude/agent-manager/bin/agent-watcher.py"
  local pidfile="$HOME/.claude/agent-manager/watcher.pid"

  case "${1:-status}" in
    start)
      if [ -f "$pidfile" ] && kill -0 "$(cat "$pidfile" 2>/dev/null)" 2>/dev/null; then
        echo "Watcher already running (pid=$(cat "$pidfile"))."
        return 0
      fi
      setsid python3 "$watcher" </dev/null >/dev/null 2>&1 &
      sleep 0.5
      if [ -f "$pidfile" ]; then
        echo "Watcher started (pid=$(cat "$pidfile"))."
      else
        echo "Watcher failed to start. Check ~/.claude/agent-manager/logs/watcher.log"
      fi
      ;;
    stop)
      if [ -f "$pidfile" ]; then
        local wpid
        wpid=$(cat "$pidfile")
        kill "$wpid" 2>/dev/null && echo "Watcher stopped (pid=$wpid)." || echo "Watcher not running."
        rm -f "$pidfile"
      else
        echo "Watcher not running."
      fi
      ;;
    status|"")
      if [ -f "$pidfile" ] && kill -0 "$(cat "$pidfile" 2>/dev/null)" 2>/dev/null; then
        echo "Watcher running (pid=$(cat "$pidfile"))."
      else
        echo "Watcher not running."
      fi
      local state="$HOME/.claude/agent-manager/watcher-state.json"
      if [ -f "$state" ]; then
        python3 -c "
import json
s = json.load(open('$state'))
u = s.get('_usage', {})
if u:
    print(f\"  Classifications: {u.get('total_classifications', 0)}\")
    print(f\"  Input tokens:    ~{u.get('total_input_tokens_est', 0)}\")
    print(f\"  Output tokens:   ~{u.get('total_output_tokens_est', 0)}\")
    print(f\"  Est. cost:       \${u.get('total_cost_est', 0):.4f}\")
sessions = {k: v for k, v in s.items() if k != '_usage' and isinstance(v, dict) and v.get('classified')}
if sessions:
    print(f\"  Sessions classified: {len(sessions)}\")
    for sid, v in sessions.items():
        print(f\"    {sid[:8]}  {v.get('verdict','?'):8s}  {v.get('reason','')[:60]}\")
" 2>/dev/null
      fi
      ;;
    *)
      echo "Usage: cwatch [start|stop|status]"
      ;;
  esac
}

# ============================================================
# agents — Display AGENTS.md (all sessions)
# ============================================================
agents() {
  local header_printed=false
  for f in "${AGENTS_DIR}"/AGENTS-*.md "${AGENTS_DIR}/AGENTS.md"; do
    [ -f "$f" ] || continue
    if ! $header_printed; then
      head -4 "$f"
      header_printed=true
    fi
    tail -n +5 "$f" 2>/dev/null
  done
  if ! $header_printed; then
    echo "No agents files in $AGENTS_DIR"
  fi
}

# ============================================================
# cbls — List active Neovim Claude tabs + agents table
# ============================================================
cbls() {
  echo "=== Active Claude tabs ==="
  if [ -n "$NVIM" ]; then
    local tmpfile
    tmpfile=$(mktemp)
    _nvim_exec_lua "_G._claude_list_tabs_to_file('${tmpfile}')"
    local has_tabs=false
    if [ -f "$tmpfile" ]; then
      while IFS=$'\t' read -r _handle name state; do
        if [ -n "$name" ]; then
          has_tabs=true
          echo "  ${name}${state:+ [${state}]}"
        fi
      done < "$tmpfile"
      rm -f "$tmpfile"
    fi
    if ! $has_tabs; then
      echo "  (none)"
    fi
  else
    echo "  (not in Neovim)"
  fi
  echo ""
  agents
}

# ============================================================
# cbp <name> — Peek at a background session's log (last 50 lines)
# ============================================================
cbp() {
  if [ -z "$1" ]; then
    echo "Usage: cbp <name>"
    echo "Run cbls to see active Claude tabs."
    return 1
  fi
  local logfile="${CLAUDE_BG_LOGDIR}/${1}.log"
  if [ -f "$logfile" ]; then
    tail -50 "$logfile"
  else
    echo "No log file found: $logfile"
  fi
}

# ============================================================
# cba <name> — Focus a background session's Neovim tab
# ============================================================
cba() {
  if [ -z "$1" ]; then
    echo "Usage: cba <name>"
    echo "Run cbls to see active Claude tabs."
    return 1
  fi
  if [ -z "$NVIM" ]; then
    echo "Not in a Neovim terminal session."
    return 1
  fi
  _nvim_exec_lua "_G._claude_focus_tab_by_name('cb-${1}')"
}

# ============================================================
# cbk <name> — Kill a running background session
#   Kills the Neovim tab and marks it stopped in AGENTS.md
# ============================================================
cbk() {
  if [ -z "$1" ]; then
    echo "Usage: cbk <name>"
    echo "Run cbls to see active Claude tabs."
    return 1
  fi
  local name="$1"
  _nvim_exec_lua "_G._claude_kill_tab_by_name('cb-${name}')"
  # Look up session ID and mark as stopped
  local sid
  sid=$(_lookup_sid "$name")
  if [ -n "$sid" ]; then
    echo "{\"session_id\": \"$sid\", \"transcript_path\": \"\"}" | bash "$AGENT_TRACKER" stop 2>/dev/null
  fi
  echo "Killed background session '${name}'."
}

# ============================================================
# cclean — Clean sessions from AGENTS.md
#   cclean <name>      → remove a specific session by name
#   cclean --stopped   → remove all stopped and bg:done sessions
#   cclean --all       → clear everything (reset AGENTS.md)
# ============================================================
cclean() {
  case "$1" in
    --all)
      echo "# Claude Agents" > "$AGENTS_FILE_LOCAL"
      echo "" >> "$AGENTS_FILE_LOCAL"
      echo "| Name | Status | OD | Session ID | Description | Started | Updated | Dir |" >> "$AGENTS_FILE_LOCAL"
      echo "|------|--------|----|------------|-------------|---------|---------|-----|" >> "$AGENTS_FILE_LOCAL"
      echo "All local sessions cleared."
      ;;
    --stopped)
      if [ ! -f "$AGENTS_FILE_LOCAL" ]; then
        echo "No local agents file at $AGENTS_FILE_LOCAL"
        return 1
      fi
      local count
      count=$(grep -cE '\| ⏹️ stopped \||\| ✅ bg:done \|' "$AGENTS_FILE_LOCAL" 2>/dev/null || echo 0)
      if [ "$count" -eq 0 ]; then
        echo "No stopped or bg:done sessions to clean."
        return 0
      fi
      local tmpfile="${AGENTS_FILE_LOCAL}.tmp"
      grep -vE '\| ⏹️ stopped \||\| ✅ bg:done \|' "$AGENTS_FILE_LOCAL" > "$tmpfile"
      mv "$tmpfile" "$AGENTS_FILE_LOCAL"
      echo "Removed ${count} stopped/done session(s)."
      ;;
    --stale)
      if [ ! -f "$AGENTS_FILE_LOCAL" ]; then
        echo "No local agents file at $AGENTS_FILE_LOCAL"
        return 1
      fi
      local threshold_minutes="${2:-60}"
      local now_epoch
      now_epoch=$(date +%s)
      local year
      year=$(date +%Y)
      local stale_sids=()

      while IFS='|' read -r _ name status od sid desc started updated _; do
        local s_trimmed
        s_trimmed=$(echo "$status" | xargs)
        local u_trimmed
        u_trimmed=$(echo "$updated" | xargs)
        local sid_trimmed
        sid_trimmed=$(echo "$sid" | xargs)

        [ -z "$sid_trimmed" ] && continue
        [ -z "$u_trimmed" ] && continue

        case "$s_trimmed" in
          "⚡ active"|"🟡 done"|"🟢 interactive"|"🔄 resumed") ;;
          *) continue ;;
        esac

        local updated_epoch
        updated_epoch=$(date -d "${year}-${u_trimmed}" +%s 2>/dev/null)
        [ -z "$updated_epoch" ] && continue

        local age_minutes=$(( (now_epoch - updated_epoch) / 60 ))
        if [ "$age_minutes" -lt 0 ]; then
          age_minutes=$(( age_minutes + 525960 ))
        fi

        if [ "$age_minutes" -ge "$threshold_minutes" ]; then
          stale_sids+=("$sid_trimmed")
          local name_trimmed
          name_trimmed=$(echo "$name" | xargs)
          echo "  Removing: ${name_trimmed} (${s_trimmed}, ${age_minutes}m old)"
        fi
      done < <(tail -n +5 "$AGENTS_FILE_LOCAL")

      if [ ${#stale_sids[@]} -eq 0 ]; then
        echo "No stale sessions older than ${threshold_minutes}m found."
        return 0
      fi

      local tmpfile="${AGENTS_FILE_LOCAL}.tmp"
      cp "$AGENTS_FILE_LOCAL" "$tmpfile"
      for sid_to_remove in "${stale_sids[@]}"; do
        grep -v "| ${sid_to_remove} |" "$tmpfile" > "${tmpfile}.2"
        mv "${tmpfile}.2" "$tmpfile"
      done
      mv "$tmpfile" "$AGENTS_FILE_LOCAL"
      echo "Removed ${#stale_sids[@]} stale session(s)."
      ;;
    ""|--help|-h)
      echo "Usage:"
      echo "  cclean <name>          Remove a session by name"
      echo "  cclean --stopped       Remove all stopped and bg:done sessions"
      echo "  cclean --stale [min]   Remove active-like sessions not updated in [min] minutes (default: 60)"
      echo "  cclean --all           Clear everything"
      ;;
    *)
      local name="$1"
      if [ ! -f "$AGENTS_FILE_LOCAL" ]; then
        echo "No local agents file at $AGENTS_FILE_LOCAL"
        return 1
      fi
      if ! grep -q "| ${name} |" "$AGENTS_FILE_LOCAL" 2>/dev/null; then
        # Check if it exists on a remote host
        local _remote_file=""
        for f in "${AGENTS_DIR}"/AGENTS-*.md; do
          [ -f "$f" ] || continue
          [ "$f" = "$AGENTS_FILE_LOCAL" ] && continue
          if grep -q "| ${name} |" "$f" 2>/dev/null; then
            _remote_file="$(basename "$f")"
            break
          fi
        done
        if [ -n "$_remote_file" ]; then
          echo "Session '${name}' exists on remote host ($_remote_file), not in local file."
        else
          echo "No session named '${name}' found."
        fi
        return 1
      fi
      local tmpfile="${AGENTS_FILE_LOCAL}.tmp"
      grep -v "| ${name} |" "$AGENTS_FILE_LOCAL" > "$tmpfile"
      mv "$tmpfile" "$AGENTS_FILE_LOCAL"
      echo "Removed session '${name}'."
      ;;
  esac
}

# ============================================================
# Codex session helpers (Neovim tab naming + lightweight index)
# ============================================================

_codex_index_upsert() {
  local name="$1" sid="$2" dir="${3:-$PWD}"
  [ -z "$name" ] && return 1
  [ -z "$sid" ] && return 1
  mkdir -p "$(dirname "$CODEX_SESSIONS_INDEX")" 2>/dev/null
  touch "$CODEX_SESSIONS_INDEX" 2>/dev/null
  local now
  now=$(date +%s)
  local tmpfile="${CODEX_SESSIONS_INDEX}.tmp"
  awk -F'\t' -v n="$name" -v s="$sid" '
    BEGIN { OFS="\t" }
    $1 == n || $2 == s { next }
    { print $1, $2, $3, $4 }
  ' "$CODEX_SESSIONS_INDEX" > "$tmpfile" 2>/dev/null
  printf '%s\t%s\t%s\t%s\n' "$name" "$sid" "$dir" "$now" >> "$tmpfile"
  mv "$tmpfile" "$CODEX_SESSIONS_INDEX"
}

_codex_lookup_sid() {
  local name="$1"
  [ -f "$CODEX_SESSIONS_INDEX" ] || return 1
  awk -F'\t' -v n="$name" '$1 == n { print $2; exit }' "$CODEX_SESSIONS_INDEX"
}

_codex_lookup_dir() {
  local name="$1"
  [ -f "$CODEX_SESSIONS_INDEX" ] || return 1
  awk -F'\t' -v n="$name" '$1 == n { print $3; exit }' "$CODEX_SESSIONS_INDEX"
}

_codex_latest_sid() {
  python3 - <<'PY'
import glob
import os
import re

files = glob.glob(os.path.expanduser("~/.codex/sessions/**/*.jsonl"), recursive=True)
if not files:
    raise SystemExit(1)
latest = max(files, key=lambda p: os.path.getmtime(p))
m = re.search(r'([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\.jsonl$', latest, re.I)
if m:
    print(m.group(1).lower())
PY
}

_codex_set_name() {
  local name="$1" sid="$2" dir="${3:-$PWD}"
  [ -z "$name" ] && return 1
  _nvim_exec_lua "_G._nvim_rename_current_tab('${name}')"
  [ -n "$sid" ] && _codex_index_upsert "$name" "$sid" "$dir"
}

codex_name() {
  local name="$*"
  [ -z "$name" ] && { echo "Usage: codex_name <name>"; return 1; }
  local sid
  sid=$(_codex_latest_sid 2>/dev/null)
  _codex_set_name "$name" "$sid" "$PWD"
}

# zsh expands aliases in function definitions, which can break names like
# `con` if an alias already exists in the user's shell startup files.
unalias co con cor cof cols codex_name 2>/dev/null || true

co() {
  codex "$@"
}

con() {
  local name="$1"
  shift || true
  [ -z "$name" ] && { echo "Usage: con <name> [prompt ...]"; return 1; }
  _codex_set_name "$name" "" "$PWD"
  codex "$@"
  local sid
  sid=$(_codex_latest_sid 2>/dev/null)
  [ -n "$sid" ] && _codex_index_upsert "$name" "$sid" "$PWD"
}

cor() {
  local name="$1"
  shift || true
  [ -z "$name" ] && { echo "Usage: cor <name> [prompt ...]"; return 1; }
  local sid
  sid=$(_codex_lookup_sid "$name")
  [ -z "$sid" ] && { echo "No Codex session mapped to '${name}'."; return 1; }
  local session_dir
  session_dir=$(_codex_lookup_dir "$name")
  if [ -n "$session_dir" ] && [ -d "$session_dir" ] && [ "$session_dir" != "$PWD" ]; then
    echo "cd $session_dir"
    cd "$session_dir" || return 1
  fi
  _codex_set_name "$name" "$sid" "$PWD"
  codex resume "$sid" "$@"
}

cof() {
  local name="$1"
  shift || true
  [ -z "$name" ] && { echo "Usage: cof <name> [prompt ...]"; return 1; }
  local sid
  sid=$(_codex_lookup_sid "$name")
  [ -z "$sid" ] && { echo "No Codex session mapped to '${name}'."; return 1; }
  _codex_set_name "$name" "$sid" "$PWD"
  codex fork "$sid" "$@"
}

cols() {
  if [ ! -f "$CODEX_SESSIONS_INDEX" ]; then
    echo "No Codex session index at $CODEX_SESSIONS_INDEX"
    return 0
  fi
  awk -F'\t' 'BEGIN {
      printf "%-28s %-36s %-20s %s\n", "Name", "Session ID", "Updated", "Dir"
      printf "%-28s %-36s %-20s %s\n", "----", "----------", "-------", "---"
    }
    {
      cmd = "date -d @" $4 " \"+%Y-%m-%d %H:%M\" 2>/dev/null"
      cmd | getline ts
      close(cmd)
      if (ts == "") ts = $4
      printf "%-28s %-36s %-20s %s\n", $1, $2, ts, $3
    }' "$CODEX_SESSIONS_INDEX"
}
