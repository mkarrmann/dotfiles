#!/bin/bash
# Claude Agent Manager — Shell Functions
# Sourced from ~/.shellrc (portable across machines)
# Provides: claude wrapper, cn, cr, cclean, cbk, cbp, cba, cbls, agents

AGENT_TRACKER="$HOME/.claude/agent-manager/bin/agent-tracker.sh"

CLAUDE_BG_LOGDIR="$HOME/claude-logs"
AGENTS_FILE="${CLAUDE_AGENTS_FILE:-}"
if [ -z "$AGENTS_FILE" ]; then
  _gdrive_mount="/data/users/${USER}/gdrive"
  if grep -q "gdrive" /proc/mounts 2>/dev/null && [ -f "${_gdrive_mount}/AGENTS.md" ]; then
    AGENTS_FILE="${_gdrive_mount}/AGENTS.md"
  else
    AGENTS_FILE="$HOME/.claude/agents.md"
  fi
  unset _gdrive_mount
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

# _lookup_sid <name> — resolve session name to full session ID
_lookup_sid() {
  local name="$1"
  local sid
  sid=$(grep "| ${name} |" "$AGENTS_FILE" 2>/dev/null | head -1 | awk -F'|' '{print $5}' | tr -d ' ')
  echo "$sid"
}

# _lookup_dir <name> — resolve session name to its working directory
_lookup_dir() {
  local name="$1"
  grep "| ${name} |" "$AGENTS_FILE" 2>/dev/null | head -1 | awk -F'|' '{print $9}' | sed 's/^[[:space:]]*//;s/[[:space:]]*$//'
}

# _start_bg_session <name> <prompt> [sid]
#   If sid is provided, resumes that session (cr -b).
#   If sid is empty, starts a new session (cn -b).
_start_bg_session() {
  local name="$1" prompt="$2" sid="${3:-}"
  local tmux_name="cb-${name}"
  local logfile="${CLAUDE_BG_LOGDIR}/${name}.log"

  sudo ondemand-idle-checks disable 2>/dev/null
  tmux kill-session -t "$tmux_name" 2>/dev/null

  local claude_cmd=""
  local done_cmd=""

  if [ -n "$sid" ]; then
    # Resume mode (cr -b): has session ID, use --resume
    bash "$AGENT_TRACKER" background "$name" "$sid" "$prompt"
    claude_cmd="command claude --dangerously-skip-permissions -p --resume '${sid}' '${prompt}'"
    done_cmd="bash '${AGENT_TRACKER}' bg-done '${sid}'"
  else
    # New mode (cn -b): no session ID yet, no --resume
    echo "$name" > ~/.claude-next-name
    touch ~/.claude-next-bg
    claude_cmd="command claude --dangerously-skip-permissions -p '${prompt}'"
    # Read session ID after claude finishes (SessionStart hook writes it)
    done_cmd="sid=\$(cat ~/.claude-last-session 2>/dev/null); [ -n \"\$sid\" ] && bash '${AGENT_TRACKER}' bg-done \"\$sid\""
  fi

  mkdir -p "$(dirname "$logfile")"
  tmux new-session -d -s "$tmux_name" \
    "CLAUDE_BG_ACTIVE=1 ${claude_cmd} 2>&1 | tee '${logfile}'; ${done_cmd}; echo ''; echo '=== DONE (exit to close) ==='; bash"

  echo "Background session '${name}' started in tmux."
  echo "  cbp ${name}   # peek at output"
  echo "  cba ${name}   # attach to tmux"
  echo "  cr ${name}    # resume interactively"
}

# ============================================================
# cn [-b] <name> [prompt...] — Start a named Claude session
#   cn <name>              → interactive session tracked in AGENTS.md
#   cn -b <name> <prompt>  → new background session in tmux
# ============================================================
cn() {
  local bg_mode=false
  if [ "$1" = "-b" ]; then
    bg_mode=true
    shift
  fi
  local name="$1"
  [ -z "$name" ] && { echo "Usage: cn [-b] <name> [prompt...]"; return 1; }

  # Warn if name already exists in AGENTS.md
  if [ -f "$AGENTS_FILE" ] && grep -q "| ${name} |" "$AGENTS_FILE" 2>/dev/null; then
    local existing_status
    existing_status=$(grep "| ${name} |" "$AGENTS_FILE" | tail -1 | awk -F'|' '{print $3}' | xargs)
    echo "Warning: '${name}' already exists (${existing_status})"
    read -rp "Replace it with a new session? [y/N] " reply
    if [[ "$reply" != [yY] ]]; then
      echo "Cancelled. Use 'cr ${name}' to resume the existing session."
      return 0
    fi
    # Remove old row so the new session takes its place
    local tmpfile="${AGENTS_FILE}.tmp"
    grep -v "| ${name} |" "$AGENTS_FILE" > "$tmpfile"
    mv "$tmpfile" "$AGENTS_FILE"
    echo "Replaced. Starting new session '${name}'..."
  fi

  if $bg_mode; then
    shift
    local prompt="$*"
    [ -z "$prompt" ] && { echo "Error: background mode requires a prompt"; return 1; }
    _start_bg_session "$name" "$prompt" ""
  else
    echo "$name" > ~/.claude-next-name
    [ -n "$TMUX" ] && tmux rename-window "$name" 2>/dev/null
    claude
  fi
}

# ============================================================
# cr [-b] <name> [prompt...] — Resume an existing session
#   cr <name>              → resume interactively (kills tmux first)
#   cr -b <name> <prompt>  → resume in background via tmux
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
    tmux kill-session -t "cb-${name}" 2>/dev/null && echo "Killed tmux session cb-${name}"
    echo "Resuming session ${sid:0:8}... (${name}) interactively..."
    # Pre-set name so if session was compacted (new UUID), the hook inherits the name
    echo "$name" > ~/.claude-next-name
    [ -n "$TMUX" ] && tmux rename-window "$name" 2>/dev/null
    claude --resume "$sid"
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
# agents — Display AGENTS.md (all sessions)
# ============================================================
agents() {
  if [ -f "$AGENTS_FILE" ]; then
    cat "$AGENTS_FILE"
  else
    echo "No agents file at $AGENTS_FILE"
  fi
}

# ============================================================
# cbls — List active tmux sessions + agents table
# ============================================================
cbls() {
  echo "=== Active tmux sessions ==="
  local has_sessions=false
  while IFS= read -r line; do
    if [[ "$line" == cb-* ]]; then
      has_sessions=true
      echo "  $line"
    fi
  done < <(tmux ls 2>/dev/null)
  if ! $has_sessions; then
    echo "  (none running)"
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
    echo "Active sessions:"
    tmux ls 2>/dev/null | grep "^cb-" | sed 's/^cb-/  /' | cut -d: -f1
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
# cba <name> — Attach to a background session's tmux
# ============================================================
cba() {
  if [ -z "$1" ]; then
    echo "Usage: cba <name>"
    echo "Active sessions:"
    tmux ls 2>/dev/null | grep "^cb-" | sed 's/^cb-/  /' | cut -d: -f1
    return 1
  fi
  tmux attach -t "cb-${1}"
}

# ============================================================
# cbk <name> — Kill a running background session
#   Kills the tmux session and marks it stopped in AGENTS.md
# ============================================================
cbk() {
  if [ -z "$1" ]; then
    echo "Usage: cbk <name>"
    echo "Active sessions:"
    tmux ls 2>/dev/null | grep "^cb-" | sed 's/^cb-/  /' | cut -d: -f1
    return 1
  fi
  local name="$1"
  local tmux_name="cb-${name}"
  if tmux has-session -t "$tmux_name" 2>/dev/null; then
    tmux kill-session -t "$tmux_name"
    # Look up session ID and mark as stopped
    local sid
    sid=$(_lookup_sid "$name")
    if [ -n "$sid" ]; then
      echo "{\"session_id\": \"$sid\", \"transcript_path\": \"\"}" | bash "$AGENT_TRACKER" stop 2>/dev/null
    fi
    echo "Killed background session '${name}'."
  else
    echo "No running background session '${name}' found."
  fi
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
      echo "# Claude Agents" > "$AGENTS_FILE"
      echo "" >> "$AGENTS_FILE"
      echo "| Name | Status | OD | Session ID | Description | Started | Updated | Dir |" >> "$AGENTS_FILE"
      echo "|------|--------|----|------------|-------------|---------|---------|-----|" >> "$AGENTS_FILE"
      echo "All sessions cleared."
      ;;
    --stopped)
      if [ ! -f "$AGENTS_FILE" ]; then
        echo "No agents file at $AGENTS_FILE"
        return 1
      fi
      local count
      count=$(grep -cE '\| ⏹️ stopped \||\| ✅ bg:done \|' "$AGENTS_FILE" 2>/dev/null || echo 0)
      if [ "$count" -eq 0 ]; then
        echo "No stopped or bg:done sessions to clean."
        return 0
      fi
      local tmpfile="${AGENTS_FILE}.tmp"
      grep -vE '\| ⏹️ stopped \||\| ✅ bg:done \|' "$AGENTS_FILE" > "$tmpfile"
      mv "$tmpfile" "$AGENTS_FILE"
      echo "Removed ${count} stopped/done session(s)."
      ;;
    --stale)
      if [ ! -f "$AGENTS_FILE" ]; then
        echo "No agents file at $AGENTS_FILE"
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
      done < <(tail -n +5 "$AGENTS_FILE")

      if [ ${#stale_sids[@]} -eq 0 ]; then
        echo "No stale sessions older than ${threshold_minutes}m found."
        return 0
      fi

      local tmpfile="${AGENTS_FILE}.tmp"
      cp "$AGENTS_FILE" "$tmpfile"
      for sid_to_remove in "${stale_sids[@]}"; do
        grep -v "| ${sid_to_remove} |" "$tmpfile" > "${tmpfile}.2"
        mv "${tmpfile}.2" "$tmpfile"
      done
      mv "$tmpfile" "$AGENTS_FILE"
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
      if [ ! -f "$AGENTS_FILE" ]; then
        echo "No agents file at $AGENTS_FILE"
        return 1
      fi
      if ! grep -q "| ${name} |" "$AGENTS_FILE" 2>/dev/null; then
        echo "No session named '${name}' found."
        return 1
      fi
      local tmpfile="${AGENTS_FILE}.tmp"
      grep -v "| ${name} |" "$AGENTS_FILE" > "$tmpfile"
      mv "$tmpfile" "$AGENTS_FILE"
      echo "Removed session '${name}'."
      ;;
  esac
}
