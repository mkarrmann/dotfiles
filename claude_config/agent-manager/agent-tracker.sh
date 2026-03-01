#!/bin/bash
# Agent Tracker — manages AGENTS.md
# Called by Claude Code hooks (SessionStart, Stop) and shell functions.
# Also runs as a long-lived heartbeat daemon (spawned by SessionStart hook).
# Location of AGENTS.md is controlled by CLAUDE_AGENTS_FILE env var.

SELF="$(readlink -f "${BASH_SOURCE[0]}" 2>/dev/null || echo "$0")"

set -euo pipefail

AGENTS_FILE="${CLAUDE_AGENTS_FILE:-}"
if [ -z "$AGENTS_FILE" ]; then
  # Check gdrive at the non-home mount point. Use /proc/mounts first (instant,
  # no FUSE) to avoid hanging on a stale mount.
  _gdrive_mount="/data/users/${USER}/gdrive"
  if grep -q "gdrive" /proc/mounts 2>/dev/null && [ -f "${_gdrive_mount}/AGENTS.md" ]; then
    AGENTS_FILE="${_gdrive_mount}/AGENTS.md"
  else
    AGENTS_FILE="$HOME/.claude/agents.md"
  fi
  unset _gdrive_mount
fi
LOCK_FILE="$(dirname "$AGENTS_FILE")/.agents.lock"
MAX_ENTRIES=50

# Cross-machine lock using mkdir (atomic on FUSE/NFS, unlike flock)
_acquire_lock() {
  local lock_dir="${LOCK_FILE}.d"
  local i=0
  while ! mkdir "$lock_dir" 2>/dev/null; do
    # Break stale locks older than 30 seconds
    local lock_age
    lock_age=$(( $(date +%s) - $(stat -c%Y "$lock_dir" 2>/dev/null || echo 0) ))
    if [ "$lock_age" -gt 30 ]; then
      _log "  lock: breaking stale lock (${lock_age}s old)"
      rm -rf "$lock_dir" 2>/dev/null
      continue
    fi
    i=$((i + 1))
    [ "$i" -ge 10 ] && return 1
    sleep 0.5
  done
  trap '_release_lock' EXIT
  return 0
}

_release_lock() {
  rm -rf "${LOCK_FILE}.d" 2>/dev/null
}

LOG_DIR="$HOME/.claude/agent-manager/logs"
LOG_FILE="$LOG_DIR/agent-tracker.log"
PID_DIR="$HOME/.claude/agent-manager/pids"

# Column layout: | Name | Status | OD | Session ID | Description | Started | Updated | Dir |
# Field indices:   $2     $3       $4   $5           $6            $7        $8         $9

_log() {
  mkdir -p "$LOG_DIR" 2>/dev/null
  if [ -f "$LOG_FILE" ] && [ "$(stat -c%s "$LOG_FILE" 2>/dev/null || stat -f%z "$LOG_FILE" 2>/dev/null || echo 0)" -gt 102400 ]; then
    mv "$LOG_FILE" "${LOG_FILE}.prev" 2>/dev/null || true
  fi
  echo "$(date '+%m-%d %H:%M:%S') $*" >> "$LOG_FILE"
}

# Record the Claude Code process's PID for liveness checks.
# Hook process tree: Claude Code → sh -c "..." ($PPID) → bash agent-tracker.sh ($$)
# So Claude Code's PID is our grandparent.
_save_pid() {
  local sid="$1"
  local claude_pid
  claude_pid=$(awk '/PPid/{print $2}' /proc/$PPID/status 2>/dev/null)
  if [ -n "$claude_pid" ] && [ "$claude_pid" -gt 1 ] && [ -d "/proc/$claude_pid" ]; then
    mkdir -p "$PID_DIR" 2>/dev/null
    echo "$claude_pid" > "${PID_DIR}/${sid}"
  fi
}

_cleanup_pid() {
  local sid="$1"
  rm -f "${PID_DIR}/${sid}" 2>/dev/null
  # Kill heartbeat daemon if running
  if [ -f "${PID_DIR}/${sid}.heartbeat" ]; then
    kill "$(cat "${PID_DIR}/${sid}.heartbeat")" 2>/dev/null || true
    rm -f "${PID_DIR}/${sid}.heartbeat"
  fi
}

# Spawn a background daemon that periodically updates the AGENTS.md timestamp,
# proving to other machines that this session is still alive.  The daemon
# monitors the Claude Code PID and exits when the process dies.
HEARTBEAT_INTERVAL=300

_start_heartbeat() {
  local sid="$1" claude_pid="$2"
  [ -z "$claude_pid" ] && return

  # Kill any stale heartbeat for this session
  if [ -f "${PID_DIR}/${sid}.heartbeat" ]; then
    kill "$(cat "${PID_DIR}/${sid}.heartbeat")" 2>/dev/null || true
    rm -f "${PID_DIR}/${sid}.heartbeat"
  fi

  # Launch in a new session so it survives the hook's process group
  setsid bash "$SELF" heartbeat "$sid" "$claude_pid" </dev/null >/dev/null 2>&1 &
}

# Get tmux session:window context for the current pane.
_tmux_context() {
  if [ -n "${TMUX:-}" ]; then
    local s w
    s=$(tmux display-message -p '#S' 2>/dev/null)
    w=$(tmux display-message -p '#I' 2>/dev/null)
    [ -n "$s" ] && echo "${s}:${w}" && return
  fi
}

# Check if gdrive mount is healthy (avoid hanging on stale FUSE mounts)
_check_gdrive() {
  if ! grep -q "gdrive" /proc/mounts 2>/dev/null; then
    _log "  gdrive: not mounted (not in /proc/mounts)"
    return 1
  fi
  if ! timeout 3 ls "$(dirname "$AGENTS_FILE")" &>/dev/null; then
    _log "  gdrive: mounted but unresponsive (stale?)"
    return 1
  fi
  return 0
}

ensure_agents_file() {
  mkdir -p "$(dirname "$AGENTS_FILE")" 2>/dev/null
  if [ ! -f "$AGENTS_FILE" ]; then
    cat > "$AGENTS_FILE" <<'HEADER'
# Claude Agents

| Name | Status | OD | Session ID | Description | Started | Updated | Dir |
|------|--------|----|------------|-------------|---------|---------|-----|
HEADER
  fi
}

now() {
  date '+%m-%d %H:%M'
}

sort_agents() {
  local tmpfile="${AGENTS_FILE}.tmp"
  head -4 "$AGENTS_FILE" > "$tmpfile"
  tail -n +5 "$AGENTS_FILE" | while IFS= read -r line; do
    [ -z "$line" ] && continue
    local pri=3
    case "$line" in
      *"❓ waiting"*|*"⚡ active"*) pri=0 ;;
      *"🟡 done"*|*"🟢 interactive"*|*"🔄 resumed"*) pri=1 ;;
      *"🔵 bg:running"*) pri=2 ;;
    esac
    local updated
    updated=$(echo "$line" | awk -F'|' '{print $8}' | tr -d ' ')
    echo "${pri} ${updated} ${line}"
  done | sort -k1,1n -k2,2r | sed 's/^[0-9] [^ ]* //' >> "$tmpfile"
  mv "$tmpfile" "$AGENTS_FILE"
}

prune() {
  local total
  total=$(grep -c "^|" "$AGENTS_FILE" 2>/dev/null || echo 0)
  local data_rows=$((total - 2))
  if [ "$data_rows" -le "$MAX_ENTRIES" ]; then
    return
  fi
  local to_remove=$((data_rows - MAX_ENTRIES))
  local tmpfile="${AGENTS_FILE}.tmp"
  head -4 "$AGENTS_FILE" > "$tmpfile"
  local total_data
  total_data=$(tail -n +5 "$AGENTS_FILE" | wc -l)
  local keep=$((total_data - to_remove))
  tail -n +5 "$AGENTS_FILE" | head -n "$keep" >> "$tmpfile"
  mv "$tmpfile" "$AGENTS_FILE"
}

STALE_THRESHOLD_MINUTES=60

# Mark stale sessions: any active-like session not updated in >STALE_THRESHOLD_MINUTES gets stopped.
# Also stops sessions whose PID is no longer alive (same-host only).
mark_stale() {
  local now_epoch
  now_epoch=$(date +%s)
  local year
  year=$(date +%Y)
  local this_host
  this_host=$(hostname -s)
  local changed=false
  local tmpfile="${AGENTS_FILE}.tmp"
  local name status od sid desc started updated
  local status_trimmed updated_trimmed sid_trimmed od_trimmed

  cp "$AGENTS_FILE" "$tmpfile"

  while IFS='|' read -r _ name status od sid desc started updated _; do
    status_trimmed=$(echo "$status" | xargs)
    updated_trimmed=$(echo "$updated" | xargs)
    sid_trimmed=$(echo "$sid" | xargs)
    od_trimmed=$(echo "$od" | xargs)

    [ -z "$sid_trimmed" ] && continue

    case "$status_trimmed" in
      "⚡ active"|"🟡 done"|"❓ waiting"|"🟢 interactive"|"🔄 resumed") ;;
      *) continue ;;
    esac

    # PID-based liveness: if we have a PID file for a same-host session, check it
    local pid_dead=false
    if [ "$od_trimmed" = "$this_host" ] && [ -f "${PID_DIR}/${sid_trimmed}" ]; then
      local stored_pid
      stored_pid=$(cat "${PID_DIR}/${sid_trimmed}" 2>/dev/null)
      if [ -n "$stored_pid" ] && ! [ -d "/proc/$stored_pid" ]; then
        pid_dead=true
      fi
    fi

    if $pid_dead; then
      local ts
      ts=$(now)
      local name_trimmed
      name_trimmed=$(echo "$name" | xargs)
      _log "  mark_stale: sid=${sid_trimmed:0:8} pid dead — marking stopped"
      awk -v sid="$sid_trimmed" -v ts="$ts" -F'|' 'BEGIN{OFS="|"} {
        if (NR > 4 && index($5, sid) > 0) {
          $3 = " ⏹️ stopped "
          $8 = " " ts " "
        }
        print
      }' "$tmpfile" > "${tmpfile}.2"
      mv "${tmpfile}.2" "$tmpfile"
      _cleanup_pid "$sid_trimmed"
      changed=true
      continue
    fi

    # Timestamp-based fallback (cross-machine or no PID file)
    [ -z "$updated_trimmed" ] && continue

    # Don't auto-stop "waiting" sessions — the question still needs an answer
    case "$status_trimmed" in
      "❓ waiting") continue ;;
    esac

    local updated_epoch
    updated_epoch=$(date -d "${year}-${updated_trimmed}" +%s 2>/dev/null)
    [ -z "$updated_epoch" ] && continue

    local age_minutes=$(( (now_epoch - updated_epoch) / 60 ))

    if [ "$age_minutes" -lt 0 ]; then
      age_minutes=$(( age_minutes + 525960 ))
    fi

    if [ "$age_minutes" -ge "$STALE_THRESHOLD_MINUTES" ]; then
      local ts
      ts=$(now)
      _log "  mark_stale: sid=${sid_trimmed:0:8} age=${age_minutes}m — marking stopped"
      awk -v sid="$sid_trimmed" -v ts="$ts" -F'|' 'BEGIN{OFS="|"} {
        if (NR > 4 && index($5, sid) > 0) {
          $3 = " ⏹️ stopped "
          $8 = " " ts " "
        }
        print
      }' "$tmpfile" > "${tmpfile}.2"
      mv "${tmpfile}.2" "$tmpfile"
      _cleanup_pid "$sid_trimmed"
      changed=true
    fi
  done < <(tail -n +5 "$AGENTS_FILE")

  if $changed; then
    mv "$tmpfile" "$AGENTS_FILE"
    sort_agents
  else
    rm -f "$tmpfile"
  fi
}

is_subagent() {
  local tp="$1"
  [[ "$tp" == */subagents/* ]]
}

# ============================================================
# register — called by SessionStart hook (stdin = hook JSON)
# ============================================================
cmd_register() {
  local input
  input=$(cat)
  local sid
  sid=$(echo "$input" | jq -r '.session_id // empty')
  [ -z "$sid" ] && exit 0

  local source
  source=$(echo "$input" | jq -r '.source // "startup"')

  local tp
  tp=$(echo "$input" | jq -r '.transcript_path // empty')
  if is_subagent "$tp"; then
    exit 0
  fi

  local host
  host=$(hostname -s)
  local ts
  ts=$(now)

  _log "register sid=${sid:0:8} source=$source next-name=$(cat ~/.claude-next-name 2>/dev/null || echo NONE)"

  # Always track session locally, regardless of gdrive state.
  # This is critical: rename_session() and other local consumers read
  # ~/.claude-last-session to identify the current session.  If we only
  # write it after a successful gdrive update, a stale mount causes the
  # file to point at a *previous* session, so renames and resumes silently
  # target the wrong row.
  echo "$sid" > ~/.claude-last-session

  # Record Claude Code's PID for liveness checks (local, fast — do before gdrive)
  _save_pid "$sid"

  # Capture tmux context (session:window) for display
  local tmux_ctx
  tmux_ctx=$(_tmux_context)

  # Start cross-machine heartbeat daemon (updates AGENTS.md timestamp periodically)
  local claude_pid
  claude_pid=$(cat "${PID_DIR}/${sid}" 2>/dev/null)
  _start_heartbeat "$sid" "$claude_pid"

  # ── RESUME PATH ──
  if [ "$source" = "resume" ]; then
    echo "$sid" > ~/.claude-resuming

    if ! _check_gdrive; then
      ( sleep 2 && rm -f ~/.claude-resuming ) &
      exit 0
    fi
    ensure_agents_file

    (
      _acquire_lock || exit 0

      if grep -q "| ${sid} |" "$AGENTS_FILE" 2>/dev/null; then
        local current_status
        current_status=$(grep "| ${sid} |" "$AGENTS_FILE" | awk -F'|' '{print $3}')

        if [[ "$current_status" == *"bg:running"* ]]; then
          _log "  resume: sid found, status=bg:running — updating OD+ts only"
          local tmpfile="${AGENTS_FILE}.tmp"
          awk -v sid="$sid" -v ts="$ts" -v host="$host" -v dir="$PWD" -F'|' 'BEGIN{OFS="|"} {
            if (NR > 4 && index($5, sid) > 0) {
              $4 = " " host " "
              $8 = " " ts " "
              $9 = " " dir " "
            }
            print
          }' "$AGENTS_FILE" > "$tmpfile"
          mv "$tmpfile" "$AGENTS_FILE"
        else
          _log "  resume: sid found, marking as resumed"
          local tmpfile="${AGENTS_FILE}.tmp"
          awk -v sid="$sid" -v ts="$ts" -v host="$host" -v dir="$PWD" -v desc="$tmux_ctx" -F'|' 'BEGIN{OFS="|"} {
            if (NR > 4 && index($5, sid) > 0) {
              $3 = " 🔄 resumed "
              $4 = " " host " "
              if (desc != "") $6 = " " desc " "
              $8 = " " ts " "
              $9 = " " dir " "
            }
            print
          }' "$AGENTS_FILE" > "$tmpfile"
          mv "$tmpfile" "$AGENTS_FILE"
        fi
      else
        _log "  resume: sid NOT found in AGENTS.md — skipping (expired session)"
      fi

      sort_agents

    )

    ( sleep 2 && rm -f ~/.claude-resuming ) &
    return
  fi

  # ── STARTUP PATH ──
  # Capture intended name BEFORE sleep — a concurrent startup may consume
  # ~/.claude-next-name during the sleep window.
  local intended_name=""
  if [ -f ~/.claude-next-name ]; then
    intended_name=$(cat ~/.claude-next-name)
  fi

  sleep 1
  if [ -f ~/.claude-resuming ]; then
    _log "  startup: phantom detected (resume flag exists) — skipping"
    exit 0
  fi

  if ! _check_gdrive; then
    exit 0
  fi
  ensure_agents_file

  (
    _acquire_lock || exit 0

    # Clean up stale sessions while we hold the lock
    mark_stale

    # Guard: sid may have been clobbered in subshell (observed in logs)
    [ -z "$sid" ] && exit 0

    if [ -f ~/.claude-resuming ]; then
      _log "  startup: phantom detected after flock — skipping"
      exit 0
    fi

    if grep -q "| ${sid} |" "$AGENTS_FILE" 2>/dev/null; then
      _log "  startup: sid already exists — updating existing row"
      local current_status
      current_status=$(grep "| ${sid} |" "$AGENTS_FILE" | awk -F'|' '{print $3}')
      if [[ "$current_status" != *"bg:running"* ]]; then
        local tmpfile="${AGENTS_FILE}.tmp"
        awk -v sid="$sid" -v ts="$ts" -v host="$host" -v dir="$PWD" -v desc="$tmux_ctx" -F'|' 'BEGIN{OFS="|"} {
          if (NR > 4 && index($5, sid) > 0) {
            $3 = " 🟢 interactive "
            $4 = " " host " "
            if (desc != "") $6 = " " desc " "
            $8 = " " ts " "
            $9 = " " dir " "
          }
          print
        }' "$AGENTS_FILE" > "$tmpfile"
        mv "$tmpfile" "$AGENTS_FILE"
      fi
    else
      local name
      local old_desc=""
      local old_started=""
      if [ -n "$intended_name" ]; then
        name="$intended_name"
        rm -f ~/.claude-next-name 2>/dev/null
        _log "  startup: new UUID, intended-name='${name}'"
      elif [ -f ~/.claude-next-name ]; then
        name=$(cat ~/.claude-next-name)
        rm -f ~/.claude-next-name
        _log "  startup: new UUID, claude-next-name='${name}'"
      elif [ -n "${TMUX:-}" ]; then
        local tmux_name
        tmux_name=$(tmux display-message -p '#W' 2>/dev/null)
        if [ -n "$tmux_name" ] && [[ "$tmux_name" != "bash" && "$tmux_name" != "zsh" && "$tmux_name" != "fish" ]]; then
          name="$tmux_name"
          _log "  startup: using tmux window name='${name}'"
        else
          name="${host}-${sid:0:4}"
          _log "  startup: genuine new session, auto-name='${name}'"
        fi
      else
        name="${host}-${sid:0:4}"
        _log "  startup: genuine new session, auto-name='${name}'"
      fi

      if grep -q "| ${name} |" "$AGENTS_FILE" 2>/dev/null; then
        local existing_status
        existing_status=$(grep "| ${name} |" "$AGENTS_FILE" | awk -F'|' '{print $3}')
        if [[ "$existing_status" != *"stopped"* ]] && [[ "$existing_status" != *"bg:done"* ]]; then
          # Active row with same name but different sid — update sid in place.
          # This handles restarts, setup-triggered reloads, and rapid retries
          # without creating phantom auto-named entries.
          _log "  startup: name '${name}' active with different sid — updating sid in place"
          local tmpfile="${AGENTS_FILE}.tmp"
          awk -v sid="$sid" -v name="$name" -v ts="$ts" -v host="$host" -v dir="$PWD" -v desc="$tmux_ctx" -F'|' 'BEGIN{OFS="|"} {
            if (NR > 4 && index($0, "| " name " |") > 0) {
              $3 = " 🟢 interactive "
              $4 = " " host " "
              $5 = " " sid " "
              if (desc != "") $6 = " " desc " "
              $8 = " " ts " "
              $9 = " " dir " "
            }
            print
          }' "$AGENTS_FILE" > "$tmpfile"
          mv "$tmpfile" "$AGENTS_FILE"
          sort_agents
          exit 0
        fi
        # Stopped/done — replace with new session (preserve desc and started)
        old_desc=$(grep "| ${name} |" "$AGENTS_FILE" | awk -F'|' '{print $6}' | xargs)
        old_started=$(grep "| ${name} |" "$AGENTS_FILE" | awk -F'|' '{print $7}' | xargs)
        local tmpfile="${AGENTS_FILE}.tmp"
        grep -v "| ${name} |" "$AGENTS_FILE" > "$tmpfile"
        mv "$tmpfile" "$AGENTS_FILE"
      fi

      local initial_status="🟢 interactive"
      if [ -f ~/.claude-next-bg ]; then
        initial_status="🔵 bg:running"
        rm -f ~/.claude-next-bg
      fi
      local desc="${old_desc:-${tmux_ctx}}"
      local started="${old_started:-$ts}"
      echo "| ${name} | ${initial_status} | ${host} | ${sid} | ${desc} | ${started} | ${ts} | ${PWD} |" >> "$AGENTS_FILE"
      _log "  startup: created row name='${name}' sid=${sid:0:8}"

      prune
    fi

    sort_agents

  )
}

# ============================================================
# stop — called by Stop hook (stdin = hook JSON)
# ============================================================
cmd_stop() {
  local input
  input=$(cat)
  local sid
  sid=$(echo "$input" | jq -r '.session_id // empty')
  [ -z "$sid" ] && exit 0

  local tp
  tp=$(echo "$input" | jq -r '.transcript_path // empty')
  if is_subagent "$tp"; then
    exit 0
  fi

  _cleanup_pid "$sid"

  local ts
  ts=$(now)

  _check_gdrive || exit 0
  ensure_agents_file

  (
    _acquire_lock || exit 0

    if grep -q "| ${sid} |" "$AGENTS_FILE" 2>/dev/null; then
      local tmpfile="${AGENTS_FILE}.tmp"
      awk -v sid="$sid" -v ts="$ts" -F'|' 'BEGIN{OFS="|"} {
        if (NR > 4 && index($5, sid) > 0) {
          $3 = " ⏹️ stopped "
          $8 = " " ts " "
        }
        print
      }' "$AGENTS_FILE" > "$tmpfile"
      mv "$tmpfile" "$AGENTS_FILE"
    fi

    sort_agents

  )
}

# ============================================================
# background <name> <sid> <description> — called by shell functions
# ============================================================
cmd_background() {
  local name="$1"
  local sid="$2"
  shift 2
  local desc="$*"
  local host
  host=$(hostname -s)
  local ts
  ts=$(now)

  _check_gdrive || exit 0
  ensure_agents_file

  (
    _acquire_lock || exit 0

    if grep -q "| ${sid} |" "$AGENTS_FILE" 2>/dev/null; then
      local tmpfile="${AGENTS_FILE}.tmp"
      awk -v sid="$sid" -v ts="$ts" -v name="$name" -v desc="${desc:0:60}" -F'|' 'BEGIN{OFS="|"} {
        if (NR > 4 && index($5, sid) > 0) {
          $2 = " " name " "
          $3 = " 🔵 bg:running "
          $6 = " " desc " "
          $8 = " " ts " "
        }
        print
      }' "$AGENTS_FILE" > "$tmpfile"
      mv "$tmpfile" "$AGENTS_FILE"
    else
      echo "| ${name} | 🔵 bg:running | ${host} | ${sid} | ${desc:0:60} | ${ts} | ${ts} | ${PWD} |" >> "$AGENTS_FILE"
    fi

    sort_agents

  )
}

# ============================================================
# bg-done <sid> — called when tmux claude process exits
# ============================================================
cmd_bg_done() {
  local sid="$1"
  local ts
  ts=$(now)

  _check_gdrive || exit 0
  ensure_agents_file

  (
    _acquire_lock || exit 0

    if grep -q "| ${sid} |" "$AGENTS_FILE" 2>/dev/null; then
      local tmpfile="${AGENTS_FILE}.tmp"
      awk -v sid="$sid" -v ts="$ts" -F'|' 'BEGIN{OFS="|"} {
        if (NR > 4 && index($5, sid) > 0) {
          $3 = " ✅ bg:done "
          $8 = " " ts " "
        }
        print
      }' "$AGENTS_FILE" > "$tmpfile"
      mv "$tmpfile" "$AGENTS_FILE"
    fi

    sort_agents

  )
}

# ============================================================
# active — called by UserPromptSubmit hook (stdin = hook JSON)
# ============================================================
cmd_active() {
  local input
  input=$(cat)
  local sid
  sid=$(echo "$input" | jq -r '.session_id // empty')
  [ -z "$sid" ] && exit 0

  local tp
  tp=$(echo "$input" | jq -r '.transcript_path // empty')
  if is_subagent "$tp"; then
    exit 0
  fi

  # Fallback PID capture in case SessionStart hook failed
  [ ! -f "${PID_DIR}/${sid}" ] && _save_pid "$sid"

  local ts
  ts=$(now)

  _check_gdrive || exit 0
  ensure_agents_file

  (
    _acquire_lock || exit 0

    if grep -q "| ${sid} |" "$AGENTS_FILE" 2>/dev/null; then
      local current_status
      current_status=$(grep "| ${sid} |" "$AGENTS_FILE" | awk -F'|' '{print $3}')
      if [[ "$current_status" != *"⚡"* ]]; then
        local tmpfile="${AGENTS_FILE}.tmp"
        awk -v sid="$sid" -v ts="$ts" -F'|' 'BEGIN{OFS="|"} {
          if (NR > 4 && index($5, sid) > 0) {
            $3 = " ⚡ active "
            $8 = " " ts " "
          }
          print
        }' "$AGENTS_FILE" > "$tmpfile"
        mv "$tmpfile" "$AGENTS_FILE"
        sort_agents
      fi
    else
      # Fallback registration: session not in AGENTS.md — startup registration
      # likely failed (e.g., gdrive was stale when SessionStart hook fired).
      local host
      host=$(hostname -s)
      local name

      if [ -f ~/.claude-next-name ]; then
        name=$(cat ~/.claude-next-name)
        rm -f ~/.claude-next-name
        _log "  active: fallback registration, name='${name}' (from claude-next-name)"
      else
        name="${host}-${sid:0:4}"
        _log "  active: fallback registration, auto-name='${name}'"
      fi

      if grep -q "| ${name} |" "$AGENTS_FILE" 2>/dev/null; then
        local existing_status
        existing_status=$(grep "| ${name} |" "$AGENTS_FILE" | awk -F'|' '{print $3}')
        if [[ "$existing_status" != *"stopped"* ]] && [[ "$existing_status" != *"bg:done"* ]]; then
          _log "  active: fallback — name '${name}' active, updating sid"
          local tmpfile="${AGENTS_FILE}.tmp"
          awk -v sid="$sid" -v name="$name" -v ts="$ts" -v host="$host" -v dir="$PWD" -F'|' 'BEGIN{OFS="|"} {
            if (NR > 4 && index($0, "| " name " |") > 0) {
              $3 = " ⚡ active "
              $4 = " " host " "
              $5 = " " sid " "
              $8 = " " ts " "
              $9 = " " dir " "
            }
            print
          }' "$AGENTS_FILE" > "$tmpfile"
          mv "$tmpfile" "$AGENTS_FILE"
          echo "$sid" > ~/.claude-last-session
          sort_agents
          exit 0
        fi
        local tmpfile="${AGENTS_FILE}.tmp"
        grep -v "| ${name} |" "$AGENTS_FILE" > "$tmpfile"
        mv "$tmpfile" "$AGENTS_FILE"
      fi

      echo "$sid" > ~/.claude-last-session
      local tmux_ctx
      tmux_ctx=$(_tmux_context)
      echo "| ${name} | ⚡ active | ${host} | ${sid} | ${tmux_ctx} | ${ts} | ${ts} | ${PWD} |" >> "$AGENTS_FILE"
      sort_agents
    fi

  )
}

# ============================================================
# done — called by Stop hook (stdin = hook JSON)
#   Claude finished its turn; output ready for user review.
# ============================================================
cmd_done() {
  local input
  input=$(cat)
  local sid
  sid=$(echo "$input" | jq -r '.session_id // empty')
  [ -z "$sid" ] && exit 0

  local tp
  tp=$(echo "$input" | jq -r '.transcript_path // empty')
  if is_subagent "$tp"; then
    exit 0
  fi

  local ts
  ts=$(now)

  _check_gdrive || exit 0
  ensure_agents_file

  (
    _acquire_lock || exit 0

    if grep -q "| ${sid} |" "$AGENTS_FILE" 2>/dev/null; then
      local current_status
      current_status=$(grep "| ${sid} |" "$AGENTS_FILE" | awk -F'|' '{print $3}')
      # Only transition from active/interactive/resumed — don't downgrade "waiting"
      if [[ "$current_status" == *"⚡"* ]] || [[ "$current_status" == *"🟢"* ]] || [[ "$current_status" == *"🔄"* ]]; then
        local tmpfile="${AGENTS_FILE}.tmp"
        awk -v sid="$sid" -v ts="$ts" -F'|' 'BEGIN{OFS="|"} {
          if (NR > 4 && index($5, sid) > 0) {
            $3 = " 🟡 done "
            $8 = " " ts " "
          }
          print
        }' "$AGENTS_FILE" > "$tmpfile"
        mv "$tmpfile" "$AGENTS_FILE"
        sort_agents
      fi
    fi

  )
}

# ============================================================
# waiting — called by Notification hook for permission_prompt|elicitation_dialog
#   Claude needs user input (question, permission prompt, etc.)
# ============================================================
cmd_waiting() {
  local input
  input=$(cat)
  local sid
  sid=$(echo "$input" | jq -r '.session_id // empty')
  [ -z "$sid" ] && exit 0

  local tp
  tp=$(echo "$input" | jq -r '.transcript_path // empty')
  if is_subagent "$tp"; then
    exit 0
  fi

  local ts
  ts=$(now)

  _check_gdrive || exit 0
  ensure_agents_file

  (
    _acquire_lock || exit 0

    if grep -q "| ${sid} |" "$AGENTS_FILE" 2>/dev/null; then
      local current_status
      current_status=$(grep "| ${sid} |" "$AGENTS_FILE" | awk -F'|' '{print $3}')
      # Transition from active/interactive/resumed/done — anything except bg:running, stopped, waiting itself
      if [[ "$current_status" == *"⚡"* ]] || [[ "$current_status" == *"🟢"* ]] || [[ "$current_status" == *"🔄"* ]] || [[ "$current_status" == *"🟡"* ]]; then
        local tmpfile="${AGENTS_FILE}.tmp"
        awk -v sid="$sid" -v ts="$ts" -F'|' 'BEGIN{OFS="|"} {
          if (NR > 4 && index($5, sid) > 0) {
            $3 = " ❓ waiting "
            $8 = " " ts " "
          }
          print
        }' "$AGENTS_FILE" > "$tmpfile"
        mv "$tmpfile" "$AGENTS_FILE"
        sort_agents
      elif [[ "$current_status" == *"❓"* ]]; then
        # Already waiting — just update timestamp
        local tmpfile="${AGENTS_FILE}.tmp"
        awk -v sid="$sid" -v ts="$ts" -F'|' 'BEGIN{OFS="|"} {
          if (NR > 4 && index($5, sid) > 0) {
            $8 = " " ts " "
          }
          print
        }' "$AGENTS_FILE" > "$tmpfile"
        mv "$tmpfile" "$AGENTS_FILE"
      fi
    fi

  )
}

# ============================================================
# heartbeat <sid> <claude_pid> — long-lived daemon, updates timestamp
#   Spawned by _start_heartbeat in cmd_register.  Exits when the
#   monitored Claude Code process dies.
# ============================================================
cmd_heartbeat() {
  set +e  # Don't exit on transient failures in a long-lived daemon
  local sid="$1" claude_pid="$2"
  local heartbeat_pidfile="${PID_DIR}/${sid}.heartbeat"

  mkdir -p "$PID_DIR" 2>/dev/null
  echo $$ > "$heartbeat_pidfile"
  trap 'rm -f "$heartbeat_pidfile"; exit 0' EXIT TERM

  while sleep "$HEARTBEAT_INTERVAL"; do
    # Stop if Claude process is dead
    [ -d "/proc/$claude_pid" ] || break

    # Quick gdrive health check — skip this cycle if unavailable
    grep -q "gdrive" /proc/mounts 2>/dev/null || continue
    timeout 3 ls "$(dirname "$AGENTS_FILE")" &>/dev/null || continue

    # Update timestamp (inline lock to avoid trap conflicts with _acquire_lock)
    local lock_dir="${LOCK_FILE}.d"
    if mkdir "$lock_dir" 2>/dev/null; then
      if grep -q "| ${sid} |" "$AGENTS_FILE" 2>/dev/null; then
        local ts
        ts=$(date '+%m-%d %H:%M')
        local tmpfile="${AGENTS_FILE}.tmp"
        if awk -v sid="$sid" -v ts="$ts" -F'|' 'BEGIN{OFS="|"} {
          if (NR > 4 && index($5, sid) > 0) { $8 = " " ts " " }
          print
        }' "$AGENTS_FILE" > "$tmpfile"; then
          mv "$tmpfile" "$AGENTS_FILE"
        else
          rm -f "$tmpfile"
        fi
      fi
      rm -rf "$lock_dir" 2>/dev/null
    fi
  done
}

# ============================================================
# Dispatch
# ============================================================
action="${1:-}"
shift 2>/dev/null || true

case "$action" in
  register)   cmd_register ;;
  stop)       cmd_stop ;;
  active)     cmd_active ;;
  done)       cmd_done ;;
  waiting)    cmd_waiting ;;
  heartbeat)  cmd_heartbeat "$@" ;;
  background) cmd_background "$@" ;;
  bg-done)    cmd_bg_done "$@" ;;
  *)
    echo "Usage: agent-tracker.sh {register|stop|active|done|waiting|heartbeat|background|bg-done}" >&2
    exit 1
    ;;
esac
