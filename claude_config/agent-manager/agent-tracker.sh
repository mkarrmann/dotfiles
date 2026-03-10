#!/bin/bash
# Agent Tracker — manages AGENTS.md
# Called by Claude Code hooks (SessionStart, Stop) and shell functions.
# Also runs as a long-lived heartbeat daemon (spawned by SessionStart hook).
# Location of AGENTS.md is controlled by CLAUDE_AGENTS_FILE env var
# or derived from the Obsidian vault root in obsidian-vault.conf.

SELF="$(readlink -f "${BASH_SOURCE[0]}" 2>/dev/null || echo "$0")"

set -euo pipefail

THIS_HOST=$(hostname -s)

if [ -n "${CLAUDE_AGENTS_FILE:-}" ]; then
  AGENTS_FILE_LOCAL="$CLAUDE_AGENTS_FILE"
  AGENTS_DIR="$(dirname "$AGENTS_FILE_LOCAL")"
  LOCK_FILE="${AGENTS_DIR}/.agents-${THIS_HOST}.lock"
else
  _conf="$HOME/.claude/obsidian-vault.conf"
  [ -f "$_conf" ] && . "$_conf"
  AGENTS_DIR="${OBSIDIAN_VAULT_ROOT:-$HOME/obsidian}"
  unset _conf
  AGENTS_FILE_LOCAL="${AGENTS_DIR}/AGENTS-${THIS_HOST}.md"
  LOCK_FILE="${AGENTS_DIR}/.agents-${THIS_HOST}.lock"
fi
MAX_ENTRIES=50

# Cross-machine lock using mkdir (atomic on most filesystems)
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
  # NOTE: .transcript is intentionally preserved — watcher needs it for LLM classification
  # Kill heartbeat daemon if running
  if [ -f "${PID_DIR}/${sid}.heartbeat" ]; then
    kill "$(cat "${PID_DIR}/${sid}.heartbeat")" 2>/dev/null || true
    rm -f "${PID_DIR}/${sid}.heartbeat"
  fi
}

# Spawn a background daemon that periodically updates the AGENTS.md timestamp,
# proving to other machines that this session is still alive.  The daemon
# monitors the Claude Code PID and exits when the process dies.
HEARTBEAT_INTERVAL=15

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

WATCHER_SCRIPT="$(dirname "$SELF")/agent-watcher.py"
WATCHER_PID_FILE="$HOME/.claude/agent-manager/watcher.pid"

_start_watcher() {
  # Only start if the watcher script exists
  [ -f "$WATCHER_SCRIPT" ] || return

  # Check if already running
  if [ -f "$WATCHER_PID_FILE" ]; then
    local wpid
    wpid=$(cat "$WATCHER_PID_FILE" 2>/dev/null)
    if [ -n "$wpid" ] && [ -d "/proc/$wpid" ]; then
      return
    fi
  fi

  TMUX="" TMUX_PANE="" setsid python3 "$WATCHER_SCRIPT" </dev/null >/dev/null 2>&1 &
  _log "  watcher: started (pid=$!)"
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

# Check if AGENTS.md parent directory is accessible
_check_agents_dir() {
  local dir
  dir="$(dirname "$AGENTS_FILE_LOCAL")"
  mkdir -p "$dir" 2>/dev/null
  [ -d "$dir" ]
}

ensure_agents_file() {
  mkdir -p "$(dirname "$AGENTS_FILE_LOCAL")" 2>/dev/null
  if [ ! -f "$AGENTS_FILE_LOCAL" ]; then
    cat > "$AGENTS_FILE_LOCAL" <<'HEADER'
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
  local tmpfile="${AGENTS_FILE_LOCAL}.tmp"
  head -4 "$AGENTS_FILE_LOCAL" > "$tmpfile"
  tail -n +5 "$AGENTS_FILE_LOCAL" | while IFS= read -r line; do
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
  mv "$tmpfile" "$AGENTS_FILE_LOCAL"
}

prune() {
  local total
  total=$(grep -c "^|" "$AGENTS_FILE_LOCAL" 2>/dev/null || echo 0)
  local data_rows=$((total - 2))
  if [ "$data_rows" -le "$MAX_ENTRIES" ]; then
    return
  fi
  local to_remove=$((data_rows - MAX_ENTRIES))
  local tmpfile="${AGENTS_FILE_LOCAL}.tmp"
  head -4 "$AGENTS_FILE_LOCAL" > "$tmpfile"
  local total_data
  total_data=$(tail -n +5 "$AGENTS_FILE_LOCAL" | wc -l)
  local keep=$((total_data - to_remove))
  tail -n +5 "$AGENTS_FILE_LOCAL" | head -n "$keep" >> "$tmpfile"
  mv "$tmpfile" "$AGENTS_FILE_LOCAL"
}

STALE_THRESHOLD_MINUTES=60

# Mark stale sessions: any active-like session not updated in >STALE_THRESHOLD_MINUTES gets stopped.
# Also stops sessions whose PID is no longer alive (same-host only).
mark_stale() {
  local now_epoch
  now_epoch=$(date +%s)
  local year
  year=$(date +%Y)
  local changed=false
  local tmpfile="${AGENTS_FILE_LOCAL}.tmp"
  local name status od sid desc started updated
  local status_trimmed updated_trimmed sid_trimmed od_trimmed

  cp "$AGENTS_FILE_LOCAL" "$tmpfile"

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
    if [ "$od_trimmed" = "$THIS_HOST" ] && [ -f "${PID_DIR}/${sid_trimmed}" ]; then
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

    # Use longer thresholds for done/waiting — they're less urgent but still stale eventually
    local stale_limit="$STALE_THRESHOLD_MINUTES"
    case "$status_trimmed" in
      "🟡 done") stale_limit=120 ;;       # 2 hours
      "❓ waiting") stale_limit=240 ;;     # 4 hours
    esac

    local updated_epoch
    updated_epoch=$(date -d "${year}-${updated_trimmed}" +%s 2>/dev/null)
    [ -z "$updated_epoch" ] && continue

    local age_minutes=$(( (now_epoch - updated_epoch) / 60 ))

    if [ "$age_minutes" -lt 0 ]; then
      age_minutes=$(( age_minutes + 525960 ))
    fi

    if [ "$age_minutes" -ge "$stale_limit" ]; then
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
  done < <(tail -n +5 "$AGENTS_FILE_LOCAL")

  if $changed; then
    mv "$tmpfile" "$AGENTS_FILE_LOCAL"
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

  local host="$THIS_HOST"
  local ts
  ts=$(now)

  _log "register sid=${sid:0:8} source=$source next-name=$(cat ~/.claude-next-name 2>/dev/null || echo NONE)"

  # Track previous session in this pane for compaction detection.
  # Must read before overwriting ~/.claude-last-session.
  local prev_pane_sid=""
  if [ -n "${TMUX_PANE:-}" ]; then
    local safe_pane="${TMUX_PANE//[^a-zA-Z0-9_]/_}"
    local pane_file="${PID_DIR}/pane-${safe_pane}"
    prev_pane_sid=$(cat "$pane_file" 2>/dev/null || true)
    mkdir -p "$PID_DIR" 2>/dev/null
    echo "$sid" > "$pane_file"
  elif [ -f ~/.claude-last-session ]; then
    prev_pane_sid=$(cat ~/.claude-last-session)
  fi

  # Always track session locally, regardless of vault sync state.
  # This is critical: rename_session() and other local consumers read
  # ~/.claude-last-session to identify the current session.
  echo "$sid" > ~/.claude-last-session

  # Record Claude Code's PID for liveness checks
  _save_pid "$sid"

  # Save transcript path for watcher's LLM classification
  if [ -n "$tp" ]; then
    mkdir -p "$PID_DIR" 2>/dev/null
    echo "$tp" > "${PID_DIR}/${sid}.transcript"
  fi

  # Capture tmux context (session:window) for display
  local tmux_ctx
  tmux_ctx=$(_tmux_context)

  # Start heartbeat daemon (updates AGENTS.md timestamp periodically)
  local claude_pid
  claude_pid=$(cat "${PID_DIR}/${sid}" 2>/dev/null)
  _start_heartbeat "$sid" "$claude_pid"

  # Auto-start watcher daemon if not running
  _start_watcher

  # ── RESUME PATH ──
  if [ "$source" = "resume" ]; then
    echo "$sid" > ~/.claude-resuming
    rm -f ~/.claude-next-name 2>/dev/null

    if ! _check_agents_dir; then
      ( sleep 2 && rm -f ~/.claude-resuming ) &
      exit 0
    fi
    ensure_agents_file

    (
      _acquire_lock || exit 0

      if grep -q "| ${sid} |" "$AGENTS_FILE_LOCAL" 2>/dev/null; then
        local current_status
        current_status=$(grep "| ${sid} |" "$AGENTS_FILE_LOCAL" | awk -F'|' '{print $3}')

        if [[ "$current_status" == *"bg:running"* ]]; then
          _log "  resume: sid found, status=bg:running — updating OD+ts only"
          local tmpfile="${AGENTS_FILE_LOCAL}.tmp"
          awk -v sid="$sid" -v ts="$ts" -v host="$host" -v dir="$PWD" -F'|' 'BEGIN{OFS="|"} {
            if (NR > 4 && index($5, sid) > 0) {
              $4 = " " host " "
              $8 = " " ts " "
              $9 = " " dir " "
            }
            print
          }' "$AGENTS_FILE_LOCAL" > "$tmpfile"
          mv "$tmpfile" "$AGENTS_FILE_LOCAL"
        else
          _log "  resume: sid found, marking as resumed"
          local tmpfile="${AGENTS_FILE_LOCAL}.tmp"
          awk -v sid="$sid" -v ts="$ts" -v host="$host" -v dir="$PWD" -v desc="$tmux_ctx" -F'|' 'BEGIN{OFS="|"} {
            if (NR > 4 && index($5, sid) > 0) {
              $3 = " 🔄 resumed "
              $4 = " " host " "
              if (desc != "") $6 = " " desc " "
              $8 = " " ts " "
              $9 = " " dir " "
            }
            print
          }' "$AGENTS_FILE_LOCAL" > "$tmpfile"
          mv "$tmpfile" "$AGENTS_FILE_LOCAL"
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

  if ! _check_agents_dir; then
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

    if grep -q "| ${sid} |" "$AGENTS_FILE_LOCAL" 2>/dev/null; then
      _log "  startup: sid already exists — updating existing row"
      local current_status
      current_status=$(grep "| ${sid} |" "$AGENTS_FILE_LOCAL" | awk -F'|' '{print $3}')
      if [[ "$current_status" != *"bg:running"* ]]; then
        local tmpfile="${AGENTS_FILE_LOCAL}.tmp"
        awk -v sid="$sid" -v ts="$ts" -v host="$host" -v dir="$PWD" -v desc="$tmux_ctx" -F'|' 'BEGIN{OFS="|"} {
          if (NR > 4 && index($5, sid) > 0) {
            $3 = " 🟢 interactive "
            $4 = " " host " "
            if (desc != "") $6 = " " desc " "
            $8 = " " ts " "
            $9 = " " dir " "
          }
          print
        }' "$AGENTS_FILE_LOCAL" > "$tmpfile"
        mv "$tmpfile" "$AGENTS_FILE_LOCAL"
      fi
    else
      local name=""
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
      fi

      # Compaction detection: if still unnamed, check if previous session in
      # this pane was recently active.  A new session appearing in the same
      # pane within seconds strongly indicates context compaction.
      if [ -z "$name" ] && [ -n "${prev_pane_sid:-}" ] && [ "$prev_pane_sid" != "$sid" ]; then
        local prev_row
        prev_row=$(grep "| ${prev_pane_sid} |" "$AGENTS_FILE_LOCAL" 2>/dev/null || true)
        if [ -n "$prev_row" ]; then
          local prev_updated
          prev_updated=$(echo "$prev_row" | awk -F'|' '{gsub(/^ +| +$/, "", $8); print $8}')
          local now_epoch prev_epoch age
          now_epoch=$(date +%s)
          prev_epoch=$(date -d "$prev_updated" +%s 2>/dev/null || echo 0)
          age=$(( now_epoch - prev_epoch ))
          if [ "$age" -lt 120 ]; then
            local compaction_confirmed=true
            # For non-tmux, require same directory as additional guard
            if [ -z "${TMUX_PANE:-}" ]; then
              local prev_dir
              prev_dir=$(echo "$prev_row" | awk -F'|' '{gsub(/^ +| +$/, "", $9); print $9}')
              [ "$prev_dir" != "$PWD" ] && compaction_confirmed=false
            fi
            if [ "$compaction_confirmed" = true ]; then
              name=$(echo "$prev_row" | awk -F'|' '{gsub(/^ +| +$/, "", $2); print $2}')
              old_desc=$(echo "$prev_row" | awk -F'|' '{gsub(/^ +| +$/, "", $6); print $6}')
              old_started=$(echo "$prev_row" | awk -F'|' '{gsub(/^ +| +$/, "", $7); print $7}')
              _log "  startup: compaction detected (prev=${prev_pane_sid:0:8}, age=${age}s) — inheriting name='${name}'"
              # Mark old session as stopped (Stop hook may not fire during compaction)
              local tmpfile="${AGENTS_FILE_LOCAL}.tmp"
              awk -v sid="$prev_pane_sid" -F'|' 'BEGIN{OFS="|"} {
                if (NR > 4 && index($5, sid) > 0) { $3 = " ⏹️ stopped " }
                print
              }' "$AGENTS_FILE_LOCAL" > "$tmpfile"
              mv "$tmpfile" "$AGENTS_FILE_LOCAL"
            fi
          fi
        fi
      fi

      # Fallback: tmux window name or auto-generate
      if [ -z "$name" ]; then
        if [ -n "${TMUX:-}" ]; then
          local tmux_name
          tmux_name=$(tmux display-message -p '#W' 2>/dev/null)
          if [ -n "$tmux_name" ] && ! echo "$tmux_name" | grep -qxE "nvim|bash|zsh|sh|fish|tmux|systemd"; then
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
      fi

      if grep -q "| ${name} |" "$AGENTS_FILE_LOCAL" 2>/dev/null; then
        local existing_status
        existing_status=$(grep "| ${name} |" "$AGENTS_FILE_LOCAL" | awk -F'|' '{print $3}')
        if [[ "$existing_status" != *"stopped"* ]] && [[ "$existing_status" != *"bg:done"* ]]; then
          # Active row with same name but different sid — update sid in place.
          # This handles restarts, setup-triggered reloads, and rapid retries
          # without creating phantom auto-named entries.
          _log "  startup: name '${name}' active with different sid — updating sid in place"
          local tmpfile="${AGENTS_FILE_LOCAL}.tmp"
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
          }' "$AGENTS_FILE_LOCAL" > "$tmpfile"
          mv "$tmpfile" "$AGENTS_FILE_LOCAL"
          sort_agents
          exit 0
        fi
        # Stopped/done — replace with new session (preserve desc and started)
        old_desc=$(grep "| ${name} |" "$AGENTS_FILE_LOCAL" | awk -F'|' '{print $6}' | xargs)
        old_started=$(grep "| ${name} |" "$AGENTS_FILE_LOCAL" | awk -F'|' '{print $7}' | xargs)
        local tmpfile="${AGENTS_FILE_LOCAL}.tmp"
        grep -v "| ${name} |" "$AGENTS_FILE_LOCAL" > "$tmpfile"
        mv "$tmpfile" "$AGENTS_FILE_LOCAL"
      fi

      local initial_status="🟢 interactive"
      if [ -f ~/.claude-next-bg ]; then
        initial_status="🔵 bg:running"
        rm -f ~/.claude-next-bg
      fi
      local desc="${old_desc:-${tmux_ctx}}"
      local started="${old_started:-$ts}"
      echo "| ${name} | ${initial_status} | ${host} | ${sid} | ${desc} | ${started} | ${ts} | ${PWD} |" >> "$AGENTS_FILE_LOCAL"
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

  _check_agents_dir || exit 0
  ensure_agents_file

  (
    _acquire_lock || exit 0

    if grep -q "| ${sid} |" "$AGENTS_FILE_LOCAL" 2>/dev/null; then
      local tmpfile="${AGENTS_FILE_LOCAL}.tmp"
      awk -v sid="$sid" -v ts="$ts" -F'|' 'BEGIN{OFS="|"} {
        if (NR > 4 && index($5, sid) > 0) {
          $3 = " ⏹️ stopped "
          $8 = " " ts " "
        }
        print
      }' "$AGENTS_FILE_LOCAL" > "$tmpfile"
      mv "$tmpfile" "$AGENTS_FILE_LOCAL"
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
  local host="$THIS_HOST"
  local ts
  ts=$(now)

  _check_agents_dir || exit 0
  ensure_agents_file

  (
    _acquire_lock || exit 0

    if grep -q "| ${sid} |" "$AGENTS_FILE_LOCAL" 2>/dev/null; then
      local tmpfile="${AGENTS_FILE_LOCAL}.tmp"
      awk -v sid="$sid" -v ts="$ts" -v name="$name" -v desc="${desc:0:60}" -F'|' 'BEGIN{OFS="|"} {
        if (NR > 4 && index($5, sid) > 0) {
          $2 = " " name " "
          $3 = " 🔵 bg:running "
          $6 = " " desc " "
          $8 = " " ts " "
        }
        print
      }' "$AGENTS_FILE_LOCAL" > "$tmpfile"
      mv "$tmpfile" "$AGENTS_FILE_LOCAL"
    else
      echo "| ${name} | 🔵 bg:running | ${host} | ${sid} | ${desc:0:60} | ${ts} | ${ts} | ${PWD} |" >> "$AGENTS_FILE_LOCAL"
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

  _check_agents_dir || exit 0
  ensure_agents_file

  (
    _acquire_lock || exit 0

    if grep -q "| ${sid} |" "$AGENTS_FILE_LOCAL" 2>/dev/null; then
      local tmpfile="${AGENTS_FILE_LOCAL}.tmp"
      awk -v sid="$sid" -v ts="$ts" -F'|' 'BEGIN{OFS="|"} {
        if (NR > 4 && index($5, sid) > 0) {
          $3 = " ✅ bg:done "
          $8 = " " ts " "
        }
        print
      }' "$AGENTS_FILE_LOCAL" > "$tmpfile"
      mv "$tmpfile" "$AGENTS_FILE_LOCAL"
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

  local tmux_ctx
  tmux_ctx=$(_tmux_context)

  local ts
  ts=$(now)

  _check_agents_dir || exit 0
  ensure_agents_file

  (
    _acquire_lock || exit 0

    if grep -q "| ${sid} |" "$AGENTS_FILE_LOCAL" 2>/dev/null; then
      local current_status
      current_status=$(grep "| ${sid} |" "$AGENTS_FILE_LOCAL" | awk -F'|' '{print $3}')
      if [[ "$current_status" != *"⚡"* ]]; then
        local tmpfile="${AGENTS_FILE_LOCAL}.tmp"
        awk -v sid="$sid" -v ts="$ts" -v desc="$tmux_ctx" -F'|' 'BEGIN{OFS="|"} {
          if (NR > 4 && index($5, sid) > 0) {
            $3 = " ⚡ active "
            if (desc != "") $6 = " " desc " "
            $8 = " " ts " "
          }
          print
        }' "$AGENTS_FILE_LOCAL" > "$tmpfile"
        mv "$tmpfile" "$AGENTS_FILE_LOCAL"
        sort_agents
      else
        # Already active — refresh tmux context without touching timestamp
        if [ -n "$tmux_ctx" ]; then
          local current_desc
          current_desc=$(grep "| ${sid} |" "$AGENTS_FILE_LOCAL" | awk -F'|' '{print $6}' | xargs)
          if [ "$current_desc" != "$tmux_ctx" ]; then
            local tmpfile="${AGENTS_FILE_LOCAL}.tmp"
            awk -v sid="$sid" -v desc="$tmux_ctx" -F'|' 'BEGIN{OFS="|"} {
              if (NR > 4 && index($5, sid) > 0) {
                $6 = " " desc " "
              }
              print
            }' "$AGENTS_FILE_LOCAL" > "$tmpfile"
            mv "$tmpfile" "$AGENTS_FILE_LOCAL"
          fi
        fi
      fi
    else
      # Fallback registration: session not in AGENTS.md — startup registration
      # likely failed (e.g., vault dir was unavailable when SessionStart hook fired).
      local host="$THIS_HOST"
      local name

      if [ -f ~/.claude-next-name ]; then
        name=$(cat ~/.claude-next-name)
        rm -f ~/.claude-next-name
        _log "  active: fallback registration, name='${name}' (from claude-next-name)"
      else
        name="${host}-${sid:0:4}"
        _log "  active: fallback registration, auto-name='${name}'"
      fi

      if grep -q "| ${name} |" "$AGENTS_FILE_LOCAL" 2>/dev/null; then
        local existing_status
        existing_status=$(grep "| ${name} |" "$AGENTS_FILE_LOCAL" | awk -F'|' '{print $3}')
        if [[ "$existing_status" != *"stopped"* ]] && [[ "$existing_status" != *"bg:done"* ]]; then
          _log "  active: fallback — name '${name}' active, updating sid"
          local tmpfile="${AGENTS_FILE_LOCAL}.tmp"
          awk -v sid="$sid" -v name="$name" -v ts="$ts" -v host="$host" -v dir="$PWD" -F'|' 'BEGIN{OFS="|"} {
            if (NR > 4 && index($0, "| " name " |") > 0) {
              $3 = " ⚡ active "
              $4 = " " host " "
              $5 = " " sid " "
              $8 = " " ts " "
              $9 = " " dir " "
            }
            print
          }' "$AGENTS_FILE_LOCAL" > "$tmpfile"
          mv "$tmpfile" "$AGENTS_FILE_LOCAL"
          echo "$sid" > ~/.claude-last-session
          if [ -n "${TMUX:-}" ]; then
            tmux rename-window "$name" 2>/dev/null
          fi
          sort_agents
          exit 0
        fi
        local tmpfile="${AGENTS_FILE_LOCAL}.tmp"
        grep -v "| ${name} |" "$AGENTS_FILE_LOCAL" > "$tmpfile"
        mv "$tmpfile" "$AGENTS_FILE_LOCAL"
      fi

      echo "$sid" > ~/.claude-last-session
      local tmux_ctx
      tmux_ctx=$(_tmux_context)
      echo "| ${name} | ⚡ active | ${host} | ${sid} | ${tmux_ctx} | ${ts} | ${ts} | ${PWD} |" >> "$AGENTS_FILE_LOCAL"
      if [ -n "${TMUX:-}" ]; then
        tmux rename-window "$name" 2>/dev/null
      fi
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

  _check_agents_dir || exit 0
  ensure_agents_file

  (
    _acquire_lock || exit 0

    if grep -q "| ${sid} |" "$AGENTS_FILE_LOCAL" 2>/dev/null; then
      local current_status
      current_status=$(grep "| ${sid} |" "$AGENTS_FILE_LOCAL" | awk -F'|' '{print $3}')
      # Only transition from active/interactive/resumed — don't downgrade "waiting"
      if [[ "$current_status" == *"⚡"* ]] || [[ "$current_status" == *"🟢"* ]] || [[ "$current_status" == *"🔄"* ]]; then
        local tmpfile="${AGENTS_FILE_LOCAL}.tmp"
        awk -v sid="$sid" -v ts="$ts" -F'|' 'BEGIN{OFS="|"} {
          if (NR > 4 && index($5, sid) > 0) {
            $3 = " 🟡 done "
            $8 = " " ts " "
          }
          print
        }' "$AGENTS_FILE_LOCAL" > "$tmpfile"
        mv "$tmpfile" "$AGENTS_FILE_LOCAL"
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

  _check_agents_dir || exit 0
  ensure_agents_file

  (
    _acquire_lock || exit 0

    if grep -q "| ${sid} |" "$AGENTS_FILE_LOCAL" 2>/dev/null; then
      local current_status
      current_status=$(grep "| ${sid} |" "$AGENTS_FILE_LOCAL" | awk -F'|' '{print $3}')
      # Transition from active/interactive/resumed/done — anything except bg:running, stopped, waiting itself
      if [[ "$current_status" == *"⚡"* ]] || [[ "$current_status" == *"🟢"* ]] || [[ "$current_status" == *"🔄"* ]] || [[ "$current_status" == *"🟡"* ]]; then
        local tmpfile="${AGENTS_FILE_LOCAL}.tmp"
        awk -v sid="$sid" -v ts="$ts" -F'|' 'BEGIN{OFS="|"} {
          if (NR > 4 && index($5, sid) > 0) {
            $3 = " ❓ waiting "
            $8 = " " ts " "
          }
          print
        }' "$AGENTS_FILE_LOCAL" > "$tmpfile"
        mv "$tmpfile" "$AGENTS_FILE_LOCAL"
        sort_agents
      elif [[ "$current_status" == *"❓"* ]]; then
        # Already waiting — just update timestamp
        local tmpfile="${AGENTS_FILE_LOCAL}.tmp"
        awk -v sid="$sid" -v ts="$ts" -F'|' 'BEGIN{OFS="|"} {
          if (NR > 4 && index($5, sid) > 0) {
            $8 = " " ts " "
          }
          print
        }' "$AGENTS_FILE_LOCAL" > "$tmpfile"
        mv "$tmpfile" "$AGENTS_FILE_LOCAL"
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

  while true; do
    # Check PID liveness every 10s, but only do file I/O every HEARTBEAT_INTERVAL
    local elapsed=0
    while [ "$elapsed" -lt "$HEARTBEAT_INTERVAL" ]; do
      sleep 10
      [ -d "/proc/$claude_pid" ] || break 2
      elapsed=$((elapsed + 10))
    done

    # Skip this cycle if AGENTS.md parent dir is not accessible
    [ -d "$(dirname "$AGENTS_FILE_LOCAL")" ] || continue

    # Update timestamp only for active sessions — for other states (done, waiting),
    # the timestamp should reflect when the status was set, not heartbeat time.
    local lock_dir="${LOCK_FILE}.d"
    if mkdir "$lock_dir" 2>/dev/null; then
      local current_status
      current_status=$(grep "| ${sid} |" "$AGENTS_FILE_LOCAL" 2>/dev/null | awk -F'|' '{print $3}')
      if [[ "$current_status" == *"⚡"* ]]; then
        local ts
        ts=$(date '+%m-%d %H:%M')
        local tmpfile="${AGENTS_FILE_LOCAL}.tmp"
        if awk -v sid="$sid" -v ts="$ts" -F'|' 'BEGIN{OFS="|"} {
          if (NR > 4 && index($5, sid) > 0) { $8 = " " ts " " }
          print
        }' "$AGENTS_FILE_LOCAL" > "$tmpfile"; then
          mv "$tmpfile" "$AGENTS_FILE_LOCAL"
        else
          rm -f "$tmpfile"
        fi
      fi
      rm -rf "$lock_dir" 2>/dev/null
    fi
  done

  # Claude process died without Stop hook firing (e.g. tmux window killed).
  # Mark the session as stopped so the dashboard reflects reality.
  # Update AGENTS.md BEFORE removing the PID file — if the update fails,
  # the PID file must remain so the statusline's _check_pid can detect the
  # dead process and classify it as inactive.
  _log "  heartbeat: sid=${sid:0:8} claude pid $claude_pid dead — marking stopped"
  local agents_updated=false
  if _check_agents_dir 2>/dev/null; then
    local lock_dir="${LOCK_FILE}.d"
    if mkdir "$lock_dir" 2>/dev/null; then
      local ts
      ts=$(date '+%m-%d %H:%M')
      local tmpfile="${AGENTS_FILE_LOCAL}.tmp"
      if awk -v sid="$sid" -v ts="$ts" -F'|' 'BEGIN{OFS="|"} {
        if (NR > 4 && index($5, sid) > 0) {
          $3 = " ⏹️ stopped "
          $8 = " " ts " "
        }
        print
      }' "$AGENTS_FILE_LOCAL" > "$tmpfile"; then
        mv "$tmpfile" "$AGENTS_FILE_LOCAL"
        agents_updated=true
      else
        rm -f "$tmpfile"
      fi
      rm -rf "$lock_dir" 2>/dev/null
    fi
  fi
  if $agents_updated; then
    rm -f "${PID_DIR}/${sid}"
  fi
  rm -f "${PID_DIR}/${sid}.heartbeat"
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
