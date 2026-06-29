# Hook Configuration Examples

## Auto-allow Specific Tools

```json
{
  "hooks": {
    "PreToolUse": [{
      "matcher": "Read|Glob|Grep",
      "hooks": [{
        "type": "command",
        "command": "echo '{\"hookSpecificOutput\": {\"hookEventName\": \"PreToolUse\", \"permissionDecision\": \"allow\"}}'"
      }]
    }]
  }
}
```

## Inject Context on Session Start

```json
{
  "hooks": {
    "SessionStart": [{
      "hooks": [{
        "type": "command",
        "command": "echo '{\"hookSpecificOutput\": {\"hookEventName\": \"SessionStart\", \"additionalContext\": \"Remember: Always run tests before committing.\"}}'"
      }]
    }]
  }
}
```

## Block Dangerous Commands

```json
{
  "hooks": {
    "PreToolUse": [{
      "matcher": "Bash",
      "hooks": [{
        "type": "command",
        "command": "/path/to/validate-command.sh"
      }]
    }]
  }
}
```

Where `validate-command.sh`:
```bash
#!/bin/bash
input=$(cat)
command=$(echo "$input" | jq -r '.tool_input.command // empty')

if echo "$command" | grep -qE "rm -rf|sudo|dd if="; then
  echo '{"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": "Dangerous command blocked"}}'
fi
exit 0
```

## macOS Notification on Tool Use

```json
{
  "hooks": {
    "PreToolUse": [{
      "matcher": "*",
      "hooks": [{
        "type": "command",
        "command": "osascript -e 'display notification \"Tool used\" with title \"Claude Code\"'"
      }]
    }]
  }
}
```

## Block Prompts Matching Pattern

```json
{
  "hooks": {
    "UserPromptSubmit": [{
      "hooks": [{
        "type": "command",
        "command": "/path/to/check-prompt.sh"
      }]
    }]
  }
}
```

Where `check-prompt.sh`:
```bash
#!/bin/bash
input=$(cat)
prompt=$(echo "$input" | jq -r '.prompt // empty')

if echo "$prompt" | grep -qi "delete all"; then
  echo '{"decision": "block", "reason": "Prompt contains dangerous keywords"}'
fi
exit 0
```

## Plugin Marketplace.json Inline Hooks

```json
{
  "name": "my-plugin",
  "hooks": {
    "SessionStart": [{
      "matcher": "startup|resume|clear|compact",
      "hooks": [{
        "type": "command",
        "command": "${CLAUDE_PLUGIN_ROOT}/hooks/session-start.sh"
      }]
    }],
    "PreToolUse": [{
      "matcher": "mcp__plugin_myplugin_.*",
      "hooks": [{
        "type": "command",
        "command": "echo '{\"hookSpecificOutput\": {\"hookEventName\": \"PreToolUse\", \"permissionDecision\": \"allow\"}}'"
      }]
    }]
  }
}
```

## Stop Hook with LLM Review

```json
{
  "hooks": {
    "Stop": [{
      "hooks": [{
        "type": "prompt",
        "prompt": "Review the response above. If there are any issues or improvements needed, suggest them."
      }]
    }]
  }
}
```

## Multi-File Hook Setup Example

```
~/.claude/settings.json:
  SessionStart → load-global-context.sh
  PreToolUse "Bash" → global-validator.sh

.claude/settings.json:
  SessionStart → load-project-context.sh
  PreToolUse "Bash" → project-validator.sh
```

**Result on session start:** Both context scripts run in parallel.
**Result on Bash use:** Both validators run in parallel.

## Python Hook for PreToolUse Auto-Allow

```python
#!/usr/bin/env python3
import json
import sys

def main():
    try:
        input_data = json.load(sys.stdin)
    except json.JSONDecodeError:
        sys.exit(1)

    tool_name = input_data.get("tool_name", "")

    # Auto-allow MCP tools from this plugin
    if tool_name.startswith("mcp__plugin_myplugin_"):
        output = {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "allow",
                "permissionDecisionReason": f"Auto-allowed: {tool_name}"
            }
        }
        json.dump(output, sys.stdout)

    sys.exit(0)

if __name__ == "__main__":
    main()
```

## Session Start with Skill Injection

```bash
#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
PLUGIN_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

# Read skill content
content=$(cat "${PLUGIN_ROOT}/skills/my-skill/SKILL.md" 2>&1 || echo "Error reading skill")

# Escape for JSON
escape_for_json() {
    local input="$1"
    printf '%s' "$input" | python3 -c 'import sys,json; print(json.dumps(sys.stdin.read()))'
}

escaped=$(escape_for_json "$content")

cat <<EOF
{
  "hookSpecificOutput": {
    "hookEventName": "SessionStart",
    "additionalContext": ${escaped}
  }
}
EOF
```
