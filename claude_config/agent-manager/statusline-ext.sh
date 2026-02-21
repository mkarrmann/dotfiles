#!/bin/bash
# Statusline extension: agent tracking rows from AGENTS.md
# Location of AGENTS.md is controlled by CLAUDE_AGENTS_FILE env var.

AGENTS_FILE="${CLAUDE_AGENTS_FILE:-$HOME/.claude/agents.md}"

if [ ! -f "$AGENTS_FILE" ]; then
  exit 0
fi

# Consume stdin (not used, but prevents broken pipe)
cat > /dev/null

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
BLUE='\033[0;34m'
GRAY='\033[0;90m'

color_status() {
  case "$1" in
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
  local marker="$1" name="$2" status="$3" od="$4" desc="$5"
  local status_colored
  status_colored=$(color_status "$status")
  if [ "$marker" = ">" ]; then
    printf "${BOLD}${CYAN}>${RESET} ${BOLD}%-16s${RESET} %b  ${DIM}%s${RESET}  ${DIM}%s${RESET}\n" "$name" "$status_colored" "$od" "$desc"
  else
    printf "  ${DIM}%-16s${RESET} %b  ${DIM}%s${RESET}  ${DIM}%s${RESET}\n" "$name" "$status_colored" "$od" "$desc"
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

  [ -z "$name" ] && continue

  if [ -n "$current_sid" ] && [[ "$current_sid" == "$sid"* || "$sid" == "$current_sid"* ]]; then
    if [ -n "$current_line" ]; then
      other_lines+=("$current_line")
    fi
    current_line="$name|(this session)|$od|$desc"
  else
    other_lines+=("$name|$status|$od|$desc")
  fi
done < <(tail -n +5 "$AGENTS_FILE")

if [ -n "$current_line" ]; then
  IFS='|' read -r name status od desc <<< "$current_line"
  format_line ">" "$name" "$status" "$od" "$desc"
fi

for line in "${other_lines[@]}"; do
  IFS='|' read -r name status od desc <<< "$line"
  format_line " " "$name" "$status" "$od" "$desc"
done
