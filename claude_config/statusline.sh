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

# Extract all values in one jq call without eval.
if ! IFS=$'\t' read -r \
  CURRENT_DIR \
  MODEL_ID \
  TOTAL_COST \
  TOTAL_DURATION \
  API_DURATION \
  LINES_ADDED \
  LINES_REMOVED \
  CONTEXT_WINDOW_SIZE \
  OUTPUT_TOKENS \
  USED_PCT \
  CTX_TOKENS \
  SESSION_ID < <(
  printf '%s' "$input" | jq -r '[
    .workspace.current_dir // "",
    .model.id // "",
    (.cost.total_cost_usd // 0),
    (.cost.total_duration_ms // 0),
    (.cost.total_api_duration_ms // 0),
    (.cost.total_lines_added // 0),
    (.cost.total_lines_removed // 0),
    (.context_window.context_window_size // 0),
    (.context_window.total_output_tokens // 0),
    (.context_window.used_percentage // 0),
    ((.context_window.current_usage | (.input_tokens // 0) + (.cache_creation_input_tokens // 0) + (.cache_read_input_tokens // 0)) // 0),
    .session_id // ""
  ] | @tsv' 2>/dev/null
); then
  CURRENT_DIR=""
  MODEL_ID=""
  TOTAL_COST=0
  TOTAL_DURATION=0
  API_DURATION=0
  LINES_ADDED=0
  LINES_REMOVED=0
  CONTEXT_WINDOW_SIZE=0
  OUTPUT_TOKENS=0
  USED_PCT=0
  CTX_TOKENS=0
  SESSION_ID=""
fi

DIR_NAME=$(basename "${CURRENT_DIR:-.}")

# Format durations (integer arithmetic to avoid bc)
TOTAL_DURATION_SEC=$(( TOTAL_DURATION / 1000 )).$(( (TOTAL_DURATION % 1000) / 100 ))
API_DURATION_SEC=$(( API_DURATION / 1000 )).$(( (API_DURATION % 1000) / 100 ))

COST_FORMATTED=$(printf "%.4f" "$TOTAL_COST" 2>/dev/null || echo "0.0000")

# Format token counts (pure bash, no awk)
fmt_tok() {
  local v="$1"
  if [ "${v:-0}" -ge 1000 ] 2>/dev/null; then
    local k=$(( v / 1000 ))
    local d=$(( (v % 1000) / 100 ))
    echo "${k}.${d}k"
  else
    echo "${v:-0}"
  fi
}
CTX_SIZE_FMT=$(fmt_tok "$CONTEXT_WINDOW_SIZE")
OUTPUT_FMT=$(fmt_tok "$OUTPUT_TOKENS")
CTX_TOKENS_FMT=$(fmt_tok "$CTX_TOKENS")

# Color percentage based on context window usage
if [ "${USED_PCT:-0}" -ge 85 ] 2>/dev/null; then
  PCT_COLOR=$DARK_RED
elif [ "${USED_PCT:-0}" -ge 70 ] 2>/dev/null; then
  PCT_COLOR=$ORANGE
elif [ "${USED_PCT:-0}" -ge 50 ] 2>/dev/null; then
  PCT_COLOR=$YELLOW
else
  PCT_COLOR=$GREEN
fi

echo -e "${BLUE}📁 ${DIR_NAME}${RESET} | ${GREEN}🤖 ${MODEL_ID}${RESET} | ${MAGENTA}🔑 ${SESSION_ID}${RESET} | ${YELLOW}💰 \$${COST_FORMATTED}${RESET} ${MAGENTA}⏱️  ${TOTAL_DURATION_SEC}s/${API_DURATION_SEC}s${RESET} ${GREEN}✏️  +${LINES_ADDED}${RESET} ${DARK_RED}❌ -${LINES_REMOVED}${RESET} | ${CYAN}📊 ${PCT_COLOR}${USED_PCT}%${RESET} ${CYAN}ctx:${CTX_TOKENS_FMT}/${CTX_SIZE_FMT} out:${OUTPUT_FMT}${RESET}"

# Run statusline extensions (each receives the JSON input via stdin)
STATUSLINE_EXT_DIR="$HOME/.claude/statusline.d"
if [ -d "$STATUSLINE_EXT_DIR" ]; then
  for ext in "$STATUSLINE_EXT_DIR"/*.sh; do
    [ -f "$ext" ] && printf '%s\n' "$input" | bash "$ext"
  done
fi
