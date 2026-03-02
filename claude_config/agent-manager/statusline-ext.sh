#!/bin/bash
# Statusline extension: agent tracking rows from AGENTS.md
# Location of AGENTS.md is controlled by CLAUDE_AGENTS_FILE env var.

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
CACHE_FILE="$HOME/.claude/agent-manager/.agents-cache"
CACHE_TTL=10
PID_DIR="$HOME/.claude/agent-manager/pids"
THIS_HOST=$(hostname -s)

if [ ! -f "$AGENTS_FILE" ]; then
  exit 0
fi

# Read JSON from stdin (piped by main statusline.sh) to get the real session ID
input=$(cat)

# Use cached copy if fresh enough to avoid FUSE latency
mkdir -p "$(dirname "$CACHE_FILE")" 2>/dev/null
if [ -f "$CACHE_FILE" ] && \
   [ $(($(date +%s) - $(stat -c%Y "$CACHE_FILE" 2>/dev/null || stat -f%z "$CACHE_FILE" 2>/dev/null || echo 0))) -lt $CACHE_TTL ]; then
  agents_source="$CACHE_FILE"
else
  cp "$AGENTS_FILE" "$CACHE_FILE" 2>/dev/null
  agents_source="$CACHE_FILE"
fi

# Get current session's ID from JSON (authoritative), fall back to file
current_sid=""
if [ -n "$input" ]; then
  current_sid=$(echo "$input" | jq -r '.session_id // empty' 2>/dev/null)
fi
if [ -z "$current_sid" ] && [ -f ~/.claude-last-session ]; then
  current_sid=$(cat ~/.claude-last-session)
fi

# Colors
DIM='\033[2m'
BOLD='\033[1m'
RESET='\033[0m'
CYAN='\033[0;36m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BLUE='\033[0;34m'
GRAY='\033[0;90m'
RED='\033[0;31m'

NOW_EPOCH=$(date +%s)
YEAR=$(date +%Y)
TODAY=$(date '+%m-%d')

# Trim whitespace without spawning xargs subprocess
_trim() { local s="${1#"${1%%[! ]*}"}"; echo "${s%"${s##*[! ]}"}"; }

# Check if a session's Claude Code process is still alive (same-host only).
# Returns: 0=alive, 1=dead, 2=unknown (cross-machine or no PID file)
_check_pid() {
  local sid="$1" od="$2"
  [ "$od" != "$THIS_HOST" ] && return 2
  local pidfile="${PID_DIR}/${sid}"
  [ ! -f "$pidfile" ] && return 2
  local pid
  pid=$(cat "$pidfile" 2>/dev/null)
  [ -z "$pid" ] && return 2
  [ -d "/proc/$pid" ] && return 0
  return 1
}

# Compute age in minutes from an Updated timestamp (format: MM-DD HH:MM)
# Returns via _AGE_MIN variable (avoids subprocess)
_compute_age() {
  _AGE_MIN=""
  local updated_ts="$1"
  [ -z "$updated_ts" ] && return

  local updated_epoch
  updated_epoch=$(date -d "${YEAR}-${updated_ts}" +%s 2>/dev/null)
  [ -z "$updated_epoch" ] && return

  local age_seconds=$(( NOW_EPOCH - updated_epoch ))
  if [ "$age_seconds" -lt 0 ]; then
    age_seconds=$(( age_seconds + 31557600 ))
  fi
  _AGE_MIN=$(( age_seconds / 60 ))
}

# Format age string with color (uses _AGE_MIN from _compute_age)
_format_age_str() {
  local age_minutes="${_AGE_MIN:-0}"
  if [ "$age_minutes" -lt 5 ]; then
    echo ""
  elif [ "$age_minutes" -lt 30 ]; then
    printf "${DIM}(%dm)${RESET}" "$age_minutes"
  elif [ "$age_minutes" -lt 120 ]; then
    printf "${YELLOW}(%dm)${RESET}" "$age_minutes"
  elif [ "$age_minutes" -lt 1440 ]; then
    printf "${GRAY}(%dh)${RESET}" "$(( age_minutes / 60 ))"
  else
    printf "${GRAY}(%dd)${RESET}" "$(( age_minutes / 1440 ))"
  fi
}

color_status() {
  local status="$1"
  case "$status" in
    *"(this session)"*)  printf "${CYAN}(this session)${RESET}" ;;
    *"stuck"*)           printf "${RED}$1${RESET}" ;;
    *"waiting"*)         printf "${YELLOW}$1${RESET}" ;;
    *"interactive"*)     printf "${GREEN}$1${RESET}" ;;
    *"bg:running"*)      printf "${BLUE}$1${RESET}" ;;
    *"resumed"*)         printf "${GREEN}$1${RESET}" ;;
    *"done"*)            printf "${GREEN}$1${RESET}" ;;
    *"active"*)          printf "${CYAN}$1${RESET}" ;;
    *"stopped"*|*"bg:done"*) printf "${GRAY}$1${RESET}" ;;
    *)                   printf "${DIM}%s${RESET}" "$1" ;;
  esac
}

shorten_path() {
  local p="$1"
  [ -z "$p" ] && return

  local home="$HOME"
  if [[ "$p" == "$home"* ]]; then
    p="~${p#$home}"
  elif [[ "$p" == "/home/$USER"* ]]; then
    p="~${p#/home/$USER}"
  elif [[ "$p" == "/data/users/$USER"* ]]; then
    p="~${p#/data/users/$USER}"
  fi

  if [ "${#p}" -gt 35 ]; then
    # Extract last 2 path components using bash string ops
    local tail="${p##*/}"
    local rest="${p%/*}"
    local parent="${rest##*/}"
    p="…/${parent}/${tail}"
  fi

  echo "$p"
}

format_line() {
  local marker="$1" name="$2" status="$3" od="$4" sid="$5" desc="$6" started="$7" updated="$8" dir="$9"

  _compute_age "$updated"

  local status_colored
  status_colored=$(color_status "$status")

  local age_str=""
  if [ "$status" != "(this session)" ]; then
    age_str=$(_format_age_str)
  fi

  local dir_str=""
  if [ -n "$dir" ]; then
    dir_str=$(shorten_path "$dir")
  fi

  local time_str=""
  if [ -n "$started" ]; then
    local s_date="${started%% *}"
    if [ "$s_date" = "$TODAY" ]; then
      time_str="${started##* }"
    else
      time_str="$started"
    fi
    if [ -n "$updated" ] && [ "$updated" != "$started" ]; then
      local u_date="${updated%% *}"
      local u_time
      if [ "$u_date" = "$TODAY" ]; then
        u_time="${updated##* }"
      else
        u_time="$updated"
      fi
      time_str="${time_str}→${u_time}"
    fi
  fi

  local desc_display="$desc"
  case "$desc" in
    "(new session)"|"-"|"") desc_display="" ;;
  esac

  if [ "$marker" = ">" ]; then
    printf "${BOLD}${CYAN}>${RESET} ${BOLD}%-16s${RESET} %b" "$name" "$status_colored"
  else
    printf "  ${DIM}%-16s${RESET} %b" "$name" "$status_colored"
  fi
  [ -n "$desc_display" ] && printf "  ${DIM}%s${RESET}" "$desc_display"
  printf "  ${DIM}%s${RESET}" "$od"
  [ -n "$time_str" ] && printf "  ${DIM}%s${RESET}" "$time_str"
  [ -n "$age_str" ] && printf " %b" "$age_str"
  [ -n "$dir_str" ] && printf "  ${DIM}%s${RESET}" "$dir_str"
  printf "\n"
}

MAX_INACTIVE=3

current_line=""
live_lines=()
inactive_lines=()

is_live_status() {
  local status="$1"
  case "$status" in
    "⚡ active"|"🟡 done"|"❓ waiting"|"🟢 interactive"|"🔄 resumed"|"🔵 bg:running"|"🔴 stuck") return 0 ;;
    *) return 1 ;;
  esac
}

while IFS='|' read -r _ name status od sid desc started updated dir _; do
  name=$(_trim "$name")
  status=$(_trim "$status")
  od=$(_trim "$od")
  sid=$(_trim "$sid")
  desc=$(_trim "$desc")
  started=$(_trim "$started")
  updated=$(_trim "$updated")
  dir=$(_trim "$dir")

  [ -z "$name" ] && continue

  if [ -n "$current_sid" ] && [ "$sid" = "$current_sid" ]; then
    if [ -n "$current_line" ]; then
      live_lines+=("$current_line")
    fi
    current_line="$name|(this session)|$od|$sid|$desc|$started|$updated|$dir"
  else
    _check_pid "$sid" "$od"
    pid_result=$?
    if [ "$pid_result" -eq 1 ]; then
      inactive_lines+=("$name|⏹️ stopped|$od|$sid|$desc|$started|$updated|$dir")
    else
      if is_live_status "$status"; then
        live_lines+=("$name|$status|$od|$sid|$desc|$started|$updated|$dir")
      else
        inactive_lines+=("$name|$status|$od|$sid|$desc|$started|$updated|$dir")
      fi
    fi
  fi
done < <(tail -n +5 "$agents_source")

# Current session first
if [ -n "$current_line" ]; then
  IFS='|' read -r name status od sid desc started updated dir <<< "$current_line"
  format_line ">" "$name" "$status" "$od" "$sid" "$desc" "$started" "$updated" "$dir"
fi

# All live sessions
for line in "${live_lines[@]}"; do
  IFS='|' read -r name status od sid desc started updated dir <<< "$line"
  format_line " " "$name" "$status" "$od" "$sid" "$desc" "$started" "$updated" "$dir"
done

# Most recent inactive sessions, up to limit
inactive_shown=0
for line in "${inactive_lines[@]}"; do
  [ "$inactive_shown" -ge "$MAX_INACTIVE" ] && break
  IFS='|' read -r name status od sid desc started updated dir <<< "$line"
  format_line " " "$name" "$status" "$od" "$sid" "$desc" "$started" "$updated" "$dir"
  inactive_shown=$((inactive_shown + 1))
done

# Show count of hidden sessions if any were truncated
inactive_total=${#inactive_lines[@]}
if [ "$inactive_total" -gt "$MAX_INACTIVE" ]; then
  hidden=$((inactive_total - MAX_INACTIVE))
  printf "  ${DIM}(+%d more — run 'agents' to see all)${RESET}\n" "$hidden"
fi
