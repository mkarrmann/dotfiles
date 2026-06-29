---
name: hook-creator
description: Use when writing Claude Code hooks for plugins or settings. Covers hook configuration, JSON output format, common pitfalls, and debugging techniques based on extensive testing.
tags:
  - supports-claude
---

# Claude Code Hooks Best Practices Guide

## Quick Reference: What Works Where

| Feature | SessionStart | UserPromptSubmit | PreToolUse | PermissionRequest | PostToolUse | Notification | Stop | SubagentStop | PreCompact | SessionEnd |
|---------|--------------|------------------|------------|-------------------|-------------|--------------|------|--------------|------------|------------|
| Command hooks | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| `additionalContext` | ✅ | ✅ | ✅ | ❌ | ✅ | ❌ | N/A | N/A | ❌ | N/A |
| `type: "prompt"` | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ✅ | ✅ | ❌ | ❌ |
| Matchers | N/A | N/A | ✅ | ✅ | ✅ | N/A | N/A | N/A | N/A | N/A |
| `permissionDecision` | N/A | N/A | ✅ | ✅ | N/A | N/A | N/A | N/A | N/A | N/A |
| `decision: "block"` | N/A | ✅ | N/A | N/A | N/A | N/A | N/A | N/A | N/A | N/A |

### Event Descriptions

| Event | When it runs |
|-------|--------------|
| `SessionStart` | When a new session starts or resumes |
| `UserPromptSubmit` | When user submits a prompt, before Claude processes it |
| `PreToolUse` | Before tool calls (can block them) |
| `PermissionRequest` | When a permission dialog is shown (can allow or deny) |
| `PostToolUse` | After tool calls complete |
| `Notification` | When Claude Code sends notifications |
| `Stop` | When Claude Code finishes responding (per-response) |
| `SubagentStop` | When subagent tasks complete |
| `PreCompact` | Before Claude Code runs a compact operation |
| `SessionEnd` | When Claude Code session ends (use for cleanup/logging) |

## Where Hooks Can Be Defined

| File | Scope | Committed? |
|------|-------|------------|
| `~/.claude/settings.json` | User (global) | N/A |
| `~/.claude/settings.local.json` | User (local override) | N/A |
| `.claude/settings.json` | Project (shared) | Yes |
| `.claude/settings.local.json` | Project (personal) | No |
| Plugin marketplace.json | Per-plugin | Depends |

**All matching hooks from all sources run in parallel.** No guaranteed order.

### Plugin Hooks: Install-Time Merge

**CRITICAL**: Plugin hooks (from marketplace.json or hooks/hooks.json) are **copied to `~/.claude/settings.json` at install time**. They are NOT loaded dynamically from plugin directories at runtime.

```
Install Time:                          Runtime:
┌─────────────────────┐               ┌─────────────────────┐
│ marketplace.json    │               │                     │
│ (inline hooks)      │──┐            │  Claude Code reads  │
│         OR          │  ├─► merge ─► │  ~/.claude/         │
│ hooks/hooks.json    │──┘            │  settings.json      │
└─────────────────────┘               └─────────────────────┘
```

**Implications:**
- Deleting hooks from settings.json removes them, even if the plugin is still "enabled"
- Reinstalling a plugin re-merges hooks to settings.json
- `${CLAUDE_PLUGIN_ROOT}` is expanded by Claude Code at runtime when executing the command

### Settings Precedence (Highest to Lowest)

1. Project `.claude/settings.local.json` - highest
2. Project `.claude/settings.json`
3. User `~/.claude/settings.local.json`
4. User `~/.claude/settings.json` - lowest

## Hook Types

### Command Hooks (all events)
```json
{"type": "command", "command": "your-shell-command"}
```

### Prompt Hooks (Stop/SubagentStop only)
```json
{"type": "prompt", "prompt": "Your prompt for Claude"}
```

## Common Mistakes to Avoid

### 1. `type: "prompt"` with wrong events
Only works with Stop and SubagentStop. Will silently fail elsewhere.

### 2. Matchers with non-tool events
Matchers only work for PreToolUse, PostToolUse, PermissionRequest.

### 3. UserPromptSubmit + prompt = INFINITE LOOP
```json
"UserPromptSubmit": [{"hooks": [{"type": "prompt", "prompt": "..."}]}]  // CRASHES!
```

### 4. Missing hookSpecificOutput wrapper
```json
// Wrong:
{"additionalContext": "text"}

// Right:
{"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": "text"}}
```

### 5. PreToolUse stdout not in context
PreToolUse stdout only appears in verbose mode (ctrl+o), not Claude's context.

### 6. Plugin hooks.json ignored
If marketplace.json has inline hooks, hooks/hooks.json is completely ignored.

### 7. Changes not taking effect
After source changes: `claude-templates plugin <name> install --dev`, then NEW session.

### 8. Slash commands in additionalContext
Slash commands appear as plain text, not executed.

## JSON Output Quick Reference

### Context Injection (SessionStart, UserPromptSubmit, PreToolUse, PostToolUse)
```json
{"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": "Your context"}}
```

### Permission Decision (PreToolUse)
```json
{"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "allow", "permissionDecisionReason": "Auto-approved"}}
```

Valid values: `"allow"`, `"deny"`, `"ask"`

### Block Prompt (UserPromptSubmit)
```json
{"decision": "block", "reason": "Blocked by policy"}
```

## Matchers

Matchers are regex patterns. **Only for PreToolUse, PostToolUse, PermissionRequest.**

```json
{"matcher": "Bash", ...}           // Exact tool name
{"matcher": "Edit|Write", ...}     // Regex: Edit OR Write
{"matcher": "*", ...}              // All tools
```

## Debugging

1. Check registered hooks: `/hooks`
2. View debug logs: `tail -f ~/.claude/debug/[session-id].txt`
3. Search logs: `grep "hookSpecificOutput" ~/.claude/debug/*.txt`
4. Capture stdin: `"command": "cat > /tmp/hook-debug.json"`
5. Log to file: `echo "fired at $(date)" >> /tmp/hook.log`

## Key Reminders

1. **Restart session** after settings changes
2. **Use hookSpecificOutput wrapper** for all JSON output
3. **Match features to events** - check compatibility matrix
4. **Plain stdout works** for SessionStart/UserPromptSubmit context
5. **Hooks run in parallel** - no guaranteed execution order

## Detailed References

- [stdin-schemas.md](references/stdin-schemas.md) - JSON input your hooks receive
- [examples.md](references/examples.md) - Complete configuration examples
- [event-reference.md](references/event-reference.md) - All events with use cases
