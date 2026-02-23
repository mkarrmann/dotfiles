#!/bin/bash

# Read JSON input from stdin
input=$(cat)
# ANSI color codes
CYAN='\033[0;36m'
BLUE='\033[0;94m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
MAGENTA='\033[0;35m'
ORANGE='\033[38;5;208m'
DARK_RED='\033[0;31m'
RESET='\033[0m'
# Extract values using jq
CURRENT_DIR=$(echo "$input" | jq -r '.workspace.current_dir')
MODEL_ID=$(echo "$input" | jq -r '.model.id')
TOTAL_COST=$(echo "$input" | jq -r '.cost.total_cost_usd')
TOTAL_DURATION=$(echo "$input" | jq -r '.cost.total_duration_ms')
API_DURATION=$(echo "$input" | jq -r '.cost.total_api_duration_ms')
LINES_ADDED=$(echo "$input" | jq -r '.cost.total_lines_added')
LINES_REMOVED=$(echo "$input" | jq -r '.cost.total_lines_removed')
CONTEXT_WINDOW_SIZE=$(echo "$input" | jq -r '.context_window.context_window_size')
OUTPUT_TOKENS=$(echo "$input" | jq -r '.context_window.total_output_tokens')
USED_PCT=$(echo "$input" | jq -r '.context_window.used_percentage')
CTX_TOKENS=$(echo "$input" | jq -r '.context_window.current_usage | .input_tokens + .cache_creation_input_tokens + .cache_read_input_tokens')
SESSION_ID=$(echo "$input" | jq -r '.session_id // empty')
# Get short directory name (just the last component)
DIR_NAME=$(basename "$CURRENT_DIR")
# Format duration values (convert ms to seconds for readability)
TOTAL_DURATION_SEC=$(echo "scale=1; $TOTAL_DURATION / 1000" | bc 2>/dev/null || echo "0")
API_DURATION_SEC=$(echo "scale=1; $API_DURATION / 1000" | bc 2>/dev/null || echo "0")
# Format cost (show 4 decimal places)
COST_FORMATTED=$(printf "%.4f" "$TOTAL_COST" 2>/dev/null || echo "0.0000")
# Format token counts for readability (e.g., 200000 -> 200k)
format_tokens() {
  echo "$1" | awk '{if ($1 >= 1000) printf "%.1fk", $1/1000; else printf "%d", $1}'
}
CTX_SIZE_FMT=$(format_tokens "$CONTEXT_WINDOW_SIZE")
OUTPUT_FMT=$(format_tokens "$OUTPUT_TOKENS")
CTX_TOKENS_FMT=$(format_tokens "$CTX_TOKENS")
# Color the percentage based on context window usage
if [ "$USED_PCT" -ge 85 ] 2>/dev/null; then
  PCT_COLOR=$DARK_RED
elif [ "$USED_PCT" -ge 70 ] 2>/dev/null; then
  PCT_COLOR=$ORANGE
elif [ "$USED_PCT" -ge 50 ] 2>/dev/null; then
  PCT_COLOR=$YELLOW
else
  PCT_COLOR=$GREEN
fi
# Build the status line with colors and emojis
echo -e "${BLUE}📁 ${DIR_NAME}${RESET} | ${GREEN}🤖 ${MODEL_ID}${RESET} | ${MAGENTA}🔑 ${SESSION_ID}${RESET} | ${YELLOW}💰 \$${COST_FORMATTED}${RESET} ${MAGENTA}⏱️  ${TOTAL_DURATION_SEC}s/${API_DURATION_SEC}s${RESET} ${GREEN}✏️  +${LINES_ADDED}${RESET} ${DARK_RED}❌ -${LINES_REMOVED}${RESET} | ${CYAN}📊 ${PCT_COLOR}${USED_PCT}%${RESET} ${CYAN}ctx:${CTX_TOKENS_FMT}/${CTX_SIZE_FMT} out:${OUTPUT_FMT}${RESET}"

# Run statusline extensions (each receives the JSON input via stdin)
STATUSLINE_EXT_DIR="$HOME/.claude/statusline.d"
if [ -d "$STATUSLINE_EXT_DIR" ]; then
  for ext in "$STATUSLINE_EXT_DIR"/*.sh; do
    [ -f "$ext" ] && echo "$input" | bash "$ext"
  done
fi

