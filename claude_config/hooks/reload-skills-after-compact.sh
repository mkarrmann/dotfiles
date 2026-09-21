#!/usr/bin/env bash
# Ask Claude Code to re-load Skill descriptions after a compaction.
#
# Compaction drops the skill listing from context. CLAUDE.md survives (it is
# re-loaded via InstructionsLoaded, whose matchers include "compact"), but
# skills are not re-announced. Directory-scoped skills recover on their own
# because they re-announce whenever their directory is touched; user- and
# workspace-scoped skills (~/.claude/skills, ~/checkoutN/.claude/skills) have
# no such trigger, so after the first compaction they are silently invisible
# for the rest of the session -- the model cannot match on descriptions it no
# longer has, and may go looking for skill files on disk instead.
#
# Wired to SessionStart with matcher "compact" so it costs nothing on a normal
# session start, where the harness supplies the listing itself.
set -euo pipefail

cat <<'EOF'
{"hookSpecificOutput":{"hookEventName":"SessionStart","reloadSkills":true}}
EOF
