#!/bin/bash
# tmux-resurrect post-save-all hook.
# Records which tmux windows have active Claude Code sessions so they can
# be auto-resumed after a tmux-resurrect restore.
#
# Writes ~/.claude/agent-manager/resurrect/manifest.json mapping window
# names to {sid, dir} for sessions with active-like statuses.

set -uo pipefail

RESURRECT_DIR="$HOME/.claude/agent-manager/resurrect"
MANIFEST="${RESURRECT_DIR}/manifest.json"

# ── Resolve AGENTS.md (same logic as agent-tracker.sh / shell-functions.sh) ──
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

[ ! -f "$AGENTS_FILE" ] && exit 0

# ── Collect active sessions from AGENTS.md ──
# Format: Name | Status | OD | Session ID | Description | Started | Updated | Dir
# Fields via awk -F'|': $2=Name $3=Status $4=OD $5=SID $6=Desc $7=Started $8=Updated $9=Dir
declare -A agent_sid
declare -A agent_dir

while IFS='|' read -r _ name status _ sid _ _ _ dir _; do
  name=$(echo "$name" | xargs 2>/dev/null) || continue
  status=$(echo "$status" | xargs 2>/dev/null) || continue
  sid=$(echo "$sid" | xargs 2>/dev/null) || continue
  dir=$(echo "$dir" | xargs 2>/dev/null) || true

  [ -z "$name" ] || [ -z "$sid" ] && continue

  case "$status" in
    "⚡ active"|"🟡 done"|"🟡 idle"|"❓ waiting"|"🟢 interactive"|"🔄 resumed")
      agent_sid["$name"]="$sid"
      agent_dir["$name"]="${dir:-}"
      ;;
  esac
done < <(tail -n +5 "$AGENTS_FILE" 2>/dev/null)

if [ ${#agent_sid[@]} -eq 0 ]; then
  rm -f "$MANIFEST"
  exit 0
fi

# ── Match against current tmux window names ──
mkdir -p "$RESURRECT_DIR"

manifest="{"
first=true

while IFS=$'\t' read -r _ _ window_name; do
  [ -z "$window_name" ] && continue
  if [ -n "${agent_sid[$window_name]+x}" ]; then
    $first || manifest+=","
    first=false
    sid="${agent_sid[$window_name]}"
    dir="${agent_dir[$window_name]}"
    # Escape double quotes in dir (unlikely but defensive)
    dir="${dir//\"/\\\"}"
    manifest+="\"${window_name}\":{\"sid\":\"${sid}\",\"dir\":\"${dir}\"}"
  fi
done < <(tmux list-windows -a -F "#{session_name}	#{window_index}	#{window_name}" 2>/dev/null)

manifest+="}"

if [ "$manifest" = "{}" ]; then
  rm -f "$MANIFEST"
else
  echo "$manifest" > "$MANIFEST"
fi
