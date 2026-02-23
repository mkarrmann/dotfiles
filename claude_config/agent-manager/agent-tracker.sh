#!/bin/bash
# Agent Tracker — manages AGENTS.md
# Called by Claude Code hooks (SessionStart, Stop) and shell functions
# Location of AGENTS.md is controlled by CLAUDE_AGENTS_FILE env var.

set -euo pipefail

AGENTS_FILE="${CLAUDE_AGENTS_FILE:-}"
if [ -z "$AGENTS_FILE" ]; then
  if [ -f "$HOME/gdrive/AGENTS.md" ]; then
    AGENTS_FILE="$HOME/gdrive/AGENTS.md"
  else
    AGENTS_FILE="$HOME/.claude/agents.md"
  fi
fi
LOCK_FILE="$(dirname "$AGENTS_FILE")/.agents.lock"
MAX_ENTRIES=15
LOG_DIR="$HOME/.claude/agent-manager/logs"
LOG_FILE="$LOG_DIR/agent-tracker.log"

# Column layout: | Name | Status | OD | Session ID | Description | Started | Updated |
# Field indices:   $2     $3       $4   $5           $6            $7        $8

_log() {
  mkdir -p "$LOG_DIR" 2>/dev/null
  if [ -f "$LOG_FILE" ] && [ "$(stat -c%s "$LOG_FILE" 2>/dev/null || stat -f%z "$LOG_FILE" 2>/dev/null || echo 0)" -gt 102400 ]; then
    mv "$LOG_FILE" "${LOG_FILE}.prev" 2>/dev/null || true
  fi
  echo "$(date '+%m-%d %H:%M:%S') $*" >> "$LOG_FILE"
}

ensure_agents_file() {
  mkdir -p "$(dirname "$AGENTS_FILE")" 2>/dev/null
  if [ ! -f "$AGENTS_FILE" ]; then
    cat > "$AGENTS_FILE" <<'HEADER'
# Claude Agents

| Name | Status | OD | Session ID | Description | Started | Updated |
|------|--------|----|------------|-------------|---------|---------|
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
      *"⚡ active"*) pri=0 ;;
      *"🟡 idle"*|*"🟢 interactive"*|*"🔄 resumed"*) pri=1 ;;
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

# Mark stale sessions: any active-like session not updated in >STALE_THRESHOLD_MINUTES gets stopped
mark_stale() {
  local now_epoch
  now_epoch=$(date +%s)
  local year
  year=$(date +%Y)
  local changed=false
  local tmpfile="${AGENTS_FILE}.tmp"
  local name status od sid desc started updated
  local status_trimmed updated_trimmed sid_trimmed

  cp "$AGENTS_FILE" "$tmpfile"

  while IFS='|' read -r _ name status od sid desc started updated _; do
    status_trimmed=$(echo "$status" | xargs)
    updated_trimmed=$(echo "$updated" | xargs)
    sid_trimmed=$(echo "$sid" | xargs)

    [ -z "$sid_trimmed" ] && continue
    [ -z "$updated_trimmed" ] && continue

    case "$status_trimmed" in
      "⚡ active"|"🟡 idle"|"🟢 interactive"|"🔄 resumed") ;;
      *) continue ;;
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
        if (index($5, sid) > 0) {
          $3 = " ⏹️ stopped "
          $8 = " " ts " "
        }
        print
      }' "$tmpfile" > "${tmpfile}.2"
      mv "${tmpfile}.2" "$tmpfile"
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

  # ── RESUME PATH ──
  if [ "$source" = "resume" ]; then
    echo "$sid" > ~/.claude-resuming

    ensure_agents_file

    (
      flock -w 5 200 || exit 0

      if grep -q "| ${sid} |" "$AGENTS_FILE" 2>/dev/null; then
        local current_status
        current_status=$(grep "| ${sid} |" "$AGENTS_FILE" | awk -F'|' '{print $3}')

        if [[ "$current_status" == *"bg:running"* ]]; then
          _log "  resume: sid found, status=bg:running — updating OD+ts only"
          local tmpfile="${AGENTS_FILE}.tmp"
          awk -v sid="$sid" -v ts="$ts" -v host="$host" -F'|' 'BEGIN{OFS="|"} {
            if (index($5, sid) > 0) {
              $4 = " " host " "
              $8 = " " ts " "
            }
            print
          }' "$AGENTS_FILE" > "$tmpfile"
          mv "$tmpfile" "$AGENTS_FILE"
        else
          _log "  resume: sid found, marking as resumed"
          local tmpfile="${AGENTS_FILE}.tmp"
          awk -v sid="$sid" -v ts="$ts" -v host="$host" -F'|' 'BEGIN{OFS="|"} {
            if (index($5, sid) > 0) {
              $3 = " 🔄 resumed "
              $4 = " " host " "
              $8 = " " ts " "
            }
            print
          }' "$AGENTS_FILE" > "$tmpfile"
          mv "$tmpfile" "$AGENTS_FILE"
        fi
        echo "$sid" > ~/.claude-last-session
      else
        _log "  resume: sid NOT found in AGENTS.md — skipping (expired session)"
      fi

      sort_agents

    ) 200>"$LOCK_FILE"

    ( sleep 2 && rm -f ~/.claude-resuming ) &
    return
  fi

  # ── STARTUP PATH ──
  sleep 1
  if [ -f ~/.claude-resuming ]; then
    _log "  startup: phantom detected (resume flag exists) — skipping"
    exit 0
  fi

  ensure_agents_file

  (
    flock -w 5 200 || exit 0

    # Clean up stale sessions while we hold the lock
    mark_stale

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
        awk -v sid="$sid" -v ts="$ts" -v host="$host" -F'|' 'BEGIN{OFS="|"} {
          if (index($5, sid) > 0) {
            $3 = " 🟢 interactive "
            $4 = " " host " "
            $8 = " " ts " "
          }
          print
        }' "$AGENTS_FILE" > "$tmpfile"
        mv "$tmpfile" "$AGENTS_FILE"
      fi
      echo "$sid" > ~/.claude-last-session
    else
      local name
      local old_desc=""
      local old_started=""
      if [ -f ~/.claude-next-name ]; then
        name=$(cat ~/.claude-next-name)
        rm -f ~/.claude-next-name
        _log "  startup: new UUID, claude-next-name='${name}'"
        if grep -q "| ${name} |" "$AGENTS_FILE" 2>/dev/null; then
          old_desc=$(grep "| ${name} |" "$AGENTS_FILE" | awk -F'|' '{print $6}' | xargs)
          old_started=$(grep "| ${name} |" "$AGENTS_FILE" | awk -F'|' '{print $7}' | xargs)
          local tmpfile="${AGENTS_FILE}.tmp"
          grep -v "| ${name} |" "$AGENTS_FILE" > "$tmpfile"
          mv "$tmpfile" "$AGENTS_FILE"
        fi
      elif [ -n "${TMUX:-}" ]; then
        local tmux_name
        tmux_name=$(tmux display-message -p '#W' 2>/dev/null)
        if [ -n "$tmux_name" ] && [[ "$tmux_name" != "bash" && "$tmux_name" != "zsh" && "$tmux_name" != "fish" ]]; then
          name="$tmux_name"
          _log "  startup: using tmux window name='${name}'"
          if grep -q "| ${name} |" "$AGENTS_FILE" 2>/dev/null; then
            old_desc=$(grep "| ${name} |" "$AGENTS_FILE" | awk -F'|' '{print $6}' | xargs)
            old_started=$(grep "| ${name} |" "$AGENTS_FILE" | awk -F'|' '{print $7}' | xargs)
            local tmpfile="${AGENTS_FILE}.tmp"
            grep -v "| ${name} |" "$AGENTS_FILE" > "$tmpfile"
            mv "$tmpfile" "$AGENTS_FILE"
          fi
        else
          name="${host}-${sid:0:4}"
          _log "  startup: genuine new session, auto-name='${name}'"
        fi
      else
        name="${host}-${sid:0:4}"
        _log "  startup: genuine new session, auto-name='${name}'"
      fi

      echo "$sid" > ~/.claude-last-session

      local initial_status="🟢 interactive"
      if [ -f ~/.claude-next-bg ]; then
        initial_status="🔵 bg:running"
        rm -f ~/.claude-next-bg
      fi
      local desc="${old_desc:-(new session)}"
      local started="${old_started:-$ts}"
      echo "| ${name} | ${initial_status} | ${host} | ${sid} | ${desc} | ${started} | ${ts} |" >> "$AGENTS_FILE"
      _log "  startup: created row name='${name}' sid=${sid:0:8}"

      prune
    fi

    sort_agents

  ) 200>"$LOCK_FILE"
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

  local ts
  ts=$(now)

  ensure_agents_file

  (
    flock -w 5 200 || exit 0

    if grep -q "| ${sid} |" "$AGENTS_FILE" 2>/dev/null; then
      local tmpfile="${AGENTS_FILE}.tmp"
      awk -v sid="$sid" -v ts="$ts" -F'|' 'BEGIN{OFS="|"} {
        if (index($5, sid) > 0) {
          $3 = " ⏹️ stopped "
          $8 = " " ts " "
        }
        print
      }' "$AGENTS_FILE" > "$tmpfile"
      mv "$tmpfile" "$AGENTS_FILE"
    fi

    sort_agents

  ) 200>"$LOCK_FILE"
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

  ensure_agents_file

  (
    flock -w 5 200 || exit 0

    if grep -q "| ${sid} |" "$AGENTS_FILE" 2>/dev/null; then
      local tmpfile="${AGENTS_FILE}.tmp"
      awk -v sid="$sid" -v ts="$ts" -v name="$name" -v desc="${desc:0:60}" -F'|' 'BEGIN{OFS="|"} {
        if (index($5, sid) > 0) {
          $2 = " " name " "
          $3 = " 🔵 bg:running "
          $6 = " " desc " "
          $8 = " " ts " "
        }
        print
      }' "$AGENTS_FILE" > "$tmpfile"
      mv "$tmpfile" "$AGENTS_FILE"
    else
      echo "| ${name} | 🔵 bg:running | ${host} | ${sid} | ${desc:0:60} | ${ts} | ${ts} |" >> "$AGENTS_FILE"
    fi

    sort_agents

  ) 200>"$LOCK_FILE"
}

# ============================================================
# bg-done <sid> — called when tmux claude process exits
# ============================================================
cmd_bg_done() {
  local sid="$1"
  local ts
  ts=$(now)

  ensure_agents_file

  (
    flock -w 5 200 || exit 0

    if grep -q "| ${sid} |" "$AGENTS_FILE" 2>/dev/null; then
      local tmpfile="${AGENTS_FILE}.tmp"
      awk -v sid="$sid" -v ts="$ts" -F'|' 'BEGIN{OFS="|"} {
        if (index($5, sid) > 0) {
          $3 = " ✅ bg:done "
          $8 = " " ts " "
        }
        print
      }' "$AGENTS_FILE" > "$tmpfile"
      mv "$tmpfile" "$AGENTS_FILE"
    fi

    sort_agents

  ) 200>"$LOCK_FILE"
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

  local ts
  ts=$(now)

  ensure_agents_file

  (
    flock -w 5 200 || exit 0

    if grep -q "| ${sid} |" "$AGENTS_FILE" 2>/dev/null; then
      local current_status
      current_status=$(grep "| ${sid} |" "$AGENTS_FILE" | awk -F'|' '{print $3}')
      if [[ "$current_status" != *"⚡"* ]]; then
        local tmpfile="${AGENTS_FILE}.tmp"
        awk -v sid="$sid" -v ts="$ts" -F'|' 'BEGIN{OFS="|"} {
          if (index($5, sid) > 0) {
            $3 = " ⚡ active "
            $8 = " " ts " "
          }
          print
        }' "$AGENTS_FILE" > "$tmpfile"
        mv "$tmpfile" "$AGENTS_FILE"
        sort_agents
      fi
    fi

  ) 200>"$LOCK_FILE"
}

# ============================================================
# idle — called by Stop hook and Notification hook (stdin = hook JSON)
# ============================================================
cmd_idle() {
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

  ensure_agents_file

  (
    flock -w 5 200 || exit 0

    if grep -q "| ${sid} |" "$AGENTS_FILE" 2>/dev/null; then
      local current_status
      current_status=$(grep "| ${sid} |" "$AGENTS_FILE" | awk -F'|' '{print $3}')
      if [[ "$current_status" == *"⚡"* ]] || [[ "$current_status" == *"🟢"* ]] || [[ "$current_status" == *"🔄"* ]]; then
        local tmpfile="${AGENTS_FILE}.tmp"
        awk -v sid="$sid" -v ts="$ts" -F'|' 'BEGIN{OFS="|"} {
          if (index($5, sid) > 0) {
            $3 = " 🟡 idle "
            $8 = " " ts " "
          }
          print
        }' "$AGENTS_FILE" > "$tmpfile"
        mv "$tmpfile" "$AGENTS_FILE"
        sort_agents
      fi
    fi

  ) 200>"$LOCK_FILE"
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
  idle)       cmd_idle ;;
  background) cmd_background "$@" ;;
  bg-done)    cmd_bg_done "$@" ;;
  *)
    echo "Usage: agent-tracker.sh {register|stop|active|idle|background|bg-done}" >&2
    exit 1
    ;;
esac
