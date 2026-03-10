#!/bin/bash
# Enhanced Statusline with Context Tracking and Alerts
# Shows active agents with context percentage and permission alerts

set -euo pipefail

# Import the Python enhanced detection if available
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_SCRIPT="${SCRIPT_DIR}/statusline_enhanced.py"

# If Python script exists, use it for rich status
if [ -f "$PYTHON_SCRIPT" ]; then
    exec python3 "$PYTHON_SCRIPT" "$@"
fi

# Otherwise fall back to basic shell implementation
if [ -n "${CLAUDE_AGENTS_FILE:-}" ]; then
    AGENTS_DIR="$(dirname "$CLAUDE_AGENTS_FILE")"
else
    _conf="$HOME/.claude/obsidian-vault.conf"
    [ -f "$_conf" ] && . "$_conf"
    AGENTS_DIR="${OBSIDIAN_VAULT_ROOT:-$HOME/obsidian}"
    unset _conf
fi

# Quick check if any agents files exist
_agents_files=()
for _f in "${AGENTS_DIR}"/AGENTS-*.md "${AGENTS_DIR}/AGENTS.md"; do
    [ -f "$_f" ] && _agents_files+=("$_f")
done
[ ${#_agents_files[@]} -eq 0 ] && exit 0

# Count active agents across all files
active_count=0
first_agent=""
for _f in "${_agents_files[@]}"; do
    while IFS= read -r line; do
        active_count=$((active_count + 1))
        [ -z "$first_agent" ] && first_agent="$line"
    done < <(grep -E '\| (⚡|💭|🔧|✏️|📖|🔍|⚠️|🔐) ' "$_f" 2>/dev/null)
done

if [ "$active_count" -gt 0 ]; then
    # Extract name and emoji from first_agent (already captured in loop above)
    name=$(echo "$first_agent" | awk -F'|' '{print $2}' | xargs)
    emoji=$(echo "$first_agent" | awk -F'|' '{print $3}' | awk '{print $1}')

    # Check for alerts across all files
    alerts=""
    for _f in "${_agents_files[@]}"; do
        grep -q '🔐' "$_f" 2>/dev/null && alerts="${alerts}🔐" && break
    done
    for _f in "${_agents_files[@]}"; do
        grep -q '❌' "$_f" 2>/dev/null && alerts="${alerts}❌" && break
    done

    # Build status line
    if [ "$active_count" -eq 1 ]; then
        echo -n "$emoji $name"
    else
        echo -n "$emoji $name +$((active_count - 1))"
    fi

    # Add alerts if any
    [ -n "$alerts" ] && echo -n " $alerts"

    echo ""  # Final newline
fi