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
AGENTS_FILE="${CLAUDE_AGENTS_FILE:-}"
if [ -z "$AGENTS_FILE" ]; then
    _gdrive_mount="/data/users/${USER}/gdrive"
    if grep -q "gdrive" /proc/mounts 2>/dev/null && [ -f "${_gdrive_mount}/AGENTS.md" ]; then
        AGENTS_FILE="${_gdrive_mount}/AGENTS.md"
    else
        AGENTS_FILE="$HOME/.claude/agents.md"
    fi
fi

# Quick check if file exists
[ ! -f "$AGENTS_FILE" ] && exit 0

# Count active agents (simple grep)
active_count=$(grep -E '\| (⚡|💭|🔧|✏️|📖|🔍|⚠️|🔐) ' "$AGENTS_FILE" 2>/dev/null | wc -l)

if [ "$active_count" -gt 0 ]; then
    # Get first active agent name and status
    first_agent=$(grep -E '\| (⚡|💭|🔧|✏️|📖|🔍|⚠️|🔐) ' "$AGENTS_FILE" 2>/dev/null | head -1)

    # Extract name and emoji
    name=$(echo "$first_agent" | awk -F'|' '{print $2}' | xargs)
    emoji=$(echo "$first_agent" | awk -F'|' '{print $3}' | awk '{print $1}')

    # Check for alerts (permission, high context, errors)
    alerts=""
    if grep -q '🔐' "$AGENTS_FILE" 2>/dev/null; then
        alerts="${alerts}🔐"
    fi
    if grep -q '❌' "$AGENTS_FILE" 2>/dev/null; then
        alerts="${alerts}❌"
    fi

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