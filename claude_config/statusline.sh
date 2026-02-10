#!/bin/bash

# Read JSON input from stdin
input=$(cat)
# ANSI color codes
CYAN='\033[0;36m'
BLUE='\033[0;94m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
MAGENTA='\033[0;35m'
RED='\033[0;31m'
RESET='\033[0m'
# Extract values using jq
HOSTNAME=$(hostname)
CURRENT_DIR=$(echo "$input" | jq -r '.workspace.current_dir')
MODEL_ID=$(echo "$input" | jq -r '.model.id')
TOTAL_COST=$(echo "$input" | jq -r '.cost.total_cost_usd')
TOTAL_DURATION=$(echo "$input" | jq -r '.cost.total_duration_ms')
API_DURATION=$(echo "$input" | jq -r '.cost.total_api_duration_ms')
LINES_ADDED=$(echo "$input" | jq -r '.cost.total_lines_added')
LINES_REMOVED=$(echo "$input" | jq -r '.cost.total_lines_removed')
# Get short directory name (just the last component)
DIR_NAME=$(basename "$CURRENT_DIR")
# Format duration values (convert ms to seconds for readability)
TOTAL_DURATION_SEC=$(echo "scale=1; $TOTAL_DURATION / 1000" | bc 2>/dev/null || echo "0")
API_DURATION_SEC=$(echo "scale=1; $API_DURATION / 1000" | bc 2>/dev/null || echo "0")
# Format cost (show 4 decimal places)
COST_FORMATTED=$(printf "%.4f" "$TOTAL_COST" 2>/dev/null || echo "0.0000")
# Build the status line with colors and emojis
echo -e "${CYAN}🖥️  ${HOSTNAME}${RESET} | ${BLUE}📁 ${DIR_NAME}${RESET} | ${GREEN}🤖 ${MODEL_ID}${RESET} | ${YELLOW}💰 \$${COST_FORMATTED}${RESET} ${MAGENTA}⏱️  ${TOTAL_DURATION_SEC}s/${API_DURATION_SEC}s${RESET} ${GREEN}✏️  +${LINES_ADDED}${RESET} ${RED}❌ -${LINES_REMOVED}${RESET}"

