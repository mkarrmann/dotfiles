#!/bin/bash
# Statusline extension: agent tracking rows from AGENTS.md
# Location of AGENTS.md is controlled by CLAUDE_AGENTS_FILE env var.

AGENTS_FILE="${CLAUDE_AGENTS_FILE:-$HOME/.claude/agents.md}"
CACHE_FILE="$HOME/.claude/agent-manager/.agents-cache"
CACHE_TTL=10

if [ ! -f "$AGENTS_FILE" ]; then
  exit 0
fi

# Consume stdin (not used, but prevents broken pipe)
cat > /dev/null

# Use cached copy if fresh enough to avoid FUSE latency
mkdir -p "$(dirname "$CACHE_FILE")" 2>/dev/null
if [ -f "$CACHE_FILE" ] && \
   [ $(($(date +%s) - $(stat -c%Y "$CACHE_FILE" 2>/dev/null || stat -f%z "$CACHE_FILE" 2>/dev/null || echo 0))) -lt $CACHE_TTL ]; then
  agents_source="$CACHE_FILE"
else
  cp "$AGENTS_FILE" "$CACHE_FILE" 2>/dev/null
  agents_source="$CACHE_FILE"
fi

# Get current session's ID
current_sid=""
if [ -f ~/.claude-last-session ]; then
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

NOW_EPOCH=$(date +%s)
YEAR=$(date +%Y)

# Compute relative age string from an Updated timestamp (format: MM-DD HH:MM)
format_age() {
  local updated_ts="$1"
  [ -z "$updated_ts" ] && return

  local updated_epoch
  updated_epoch=$(date -d "${YEAR}-${updated_ts}" +%s 2>/dev/null)
  [ -z "$updated_epoch" ] && return

  local age_seconds=$(( NOW_EPOCH - updated_epoch ))
  if [ "$age_seconds" -lt 0 ]; then
    age_seconds=$(( age_seconds + 31557600 ))
  fi

  local age_minutes=$(( age_seconds / 60 ))

  if [ "$age_minutes" -lt 5 ]; then
    return
  elif [ "$age_minutes" -lt 30 ]; then
    printf "${DIM}(%dm)${RESET}" "$age_minutes"
  elif [ "$age_minutes" -lt 120 ]; then
    printf "${YELLOW}(%dm)${RESET}" "$age_minutes"
  elif [ "$age_minutes" -lt 1440 ]; then
    local hours=$(( age_minutes / 60 ))
    printf "${GRAY}(%dh)${RESET}" "$hours"
  else
    local days=$(( age_minutes / 1440 ))
    printf "${GRAY}(%dd)${RESET}" "$days"
  fi
}

age_minutes_from_ts() {
  local updated_ts="$1"
  [ -z "$updated_ts" ] && echo "" && return

  local updated_epoch
  updated_epoch=$(date -d "${YEAR}-${updated_ts}" +%s 2>/dev/null)
  [ -z "$updated_epoch" ] && echo "" && return

  local age_seconds=$(( NOW_EPOCH - updated_epoch ))
  if [ "$age_seconds" -lt 0 ]; then
    age_seconds=$(( age_seconds + 31557600 ))
  fi
  echo $(( age_seconds / 60 ))
}

color_status() {
  local status="$1"
  local age_minutes="$2"

  # Override active-like statuses if stale (>30 min without update)
  if [ -n "$age_minutes" ] && [ "$age_minutes" -ge 30 ]; then
    case "$status" in
      "⚡ active"|"🟡 idle"|"🟢 interactive"|"🔄 resumed")
        printf "${YELLOW}💤 stale${RESET}"
        return
        ;;
    esac
  fi

  case "$status" in
    *"(this session)"*)  printf "${CYAN}(this session)${RESET}" ;;
    *"interactive"*)     printf "${GREEN}$1${RESET}" ;;
    *"bg:running"*)      printf "${BLUE}$1${RESET}" ;;
    *"resumed"*)         printf "${GREEN}$1${RESET}" ;;
    *"idle"*)            printf "${GREEN}$1${RESET}" ;;
    *"active"*)          printf "${CYAN}$1${RESET}" ;;
    *"stopped"*|*"done"*) printf "${GRAY}$1${RESET}" ;;
    *)                   printf "${DIM}%s${RESET}" "$1" ;;
  esac
}

format_line() {
  local marker="$1" name="$2" status="$3" od="$4" desc="$5" updated="$6"

  local age_min=""
  age_min=$(age_minutes_from_ts "$updated")

  local status_colored
  status_colored=$(color_status "$status" "$age_min")

  local age_str=""
  if [ "$status" != "(this session)" ]; then
    age_str=$(format_age "$updated")
  fi

  if [ "$marker" = ">" ]; then
    printf "${BOLD}${CYAN}>${RESET} ${BOLD}%-16s${RESET} %b  ${DIM}%s${RESET}  ${DIM}%s${RESET} %b\n" "$name" "$status_colored" "$od" "$desc" "$age_str"
  else
    printf "  ${DIM}%-16s${RESET} %b  ${DIM}%s${RESET}  ${DIM}%s${RESET} %b\n" "$name" "$status_colored" "$od" "$desc" "$age_str"
  fi
}

current_line=""
other_lines=()

while IFS='|' read -r _ name status od sid desc started updated _; do
  name=$(echo "$name" | xargs)
  status=$(echo "$status" | xargs)
  od=$(echo "$od" | xargs)
  sid=$(echo "$sid" | xargs)
  desc=$(echo "$desc" | xargs)
  updated=$(echo "$updated" | xargs)

  [ -z "$name" ] && continue

  if [ -n "$current_sid" ] && [[ "$current_sid" == "$sid"* || "$sid" == "$current_sid"* ]]; then
    if [ -n "$current_line" ]; then
      other_lines+=("$current_line")
    fi
    current_line="$name|(this session)|$od|$desc|$updated"
  else
    other_lines+=("$name|$status|$od|$desc|$updated")
  fi
done < <(tail -n +5 "$agents_source")

if [ -n "$current_line" ]; then
  IFS='|' read -r name status od desc updated <<< "$current_line"
  format_line ">" "$name" "$status" "$od" "$desc" "$updated"
fi

for line in "${other_lines[@]}"; do
  IFS='|' read -r name status od desc updated <<< "$line"
  format_line " " "$name" "$status" "$od" "$desc" "$updated"
done
