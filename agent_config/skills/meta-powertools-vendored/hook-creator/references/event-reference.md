# Hook Event Reference

## SessionStart

**Triggers:** When a Claude Code session begins.

**Matchers:** `startup`, `resume`, `clear`, `compact`
- `startup` - Fresh session start
- `resume` - Resuming existing session
- `clear` - After `/clear` command
- `compact` - After context compaction

**Use cases:**
- Inject context or instructions into every session
- Load project-specific configuration
- Display welcome messages
- Set up environment

**Supports:**
- ✅ Command hooks
- ✅ additionalContext output
- ❌ Prompt hooks
- ❌ permissionDecision

## Stop

**Triggers:** When Claude stops responding.

**Use cases:**
- LLM-based evaluation of responses
- Quality checks
- Summary generation

**Supports:**
- ✅ Command hooks
- ✅ Prompt hooks (LLM reviews response)
- ❌ additionalContext (session ending)
- ❌ permissionDecision

## UserPromptSubmit

**Triggers:** When the user submits a prompt.

**Use cases:**
- Prompt validation
- Logging user requests
- Adding contextual information
- Blocking certain prompt patterns

**Supports:**
- ✅ Command hooks
- ✅ additionalContext output
- ✅ decision: "block" output
- ❌ Prompt hooks (CAUSES INFINITE LOOP!)
- ❌ Matchers

## PreToolUse

**Triggers:** Before each tool call.

**Supports matchers:** Yes - filter by tool name regex

**Use cases:**
- Auto-allow trusted tools
- Block dangerous operations
- Log tool usage
- Validate tool inputs

**Supports:**
- ✅ Command hooks
- ✅ permissionDecision (allow/deny/ask)
- ✅ Matchers
- ❌ additionalContext (stdout only in verbose mode)
- ❌ Prompt hooks

**Permission values:**
- `"allow"` - Auto-approve the tool
- `"deny"` - Block the tool
- `"ask"` - Prompt user for permission

## PostToolUse

**Triggers:** After each tool completes.

**Supports matchers:** Yes - filter by tool name regex

**Use cases:**
- Log tool results
- Audit file access patterns
- Add context based on results
- Track metrics

**Supports:**
- ✅ Command hooks
- ✅ Matchers
- ❌ additionalContext
- ❌ Prompt hooks
- ❌ permissionDecision

## SubagentStop

**Triggers:** When a subagent (Task tool) completes.

**Use cases:**
- LLM-based evaluation of agent output
- Quality checks on agent work

**Supports:**
- ✅ Command hooks
- ✅ Prompt hooks
- ❌ additionalContext
- ❌ permissionDecision

## Notification

**Triggers:** When Claude needs to display a permission request.

**Note:** Only fires in interactive environments where permissions aren't pre-approved.

**Use cases:**
- Custom permission UI
- Logging permission requests

## PreCompact

**Triggers:** Before context compaction occurs.

**Use cases:**
- Save important context before truncation
- Log what's being compacted
- Export session state

## SessionEnd

**Triggers:** When a Claude Code session ends.

**Use cases:**
- Cleanup temporary files
- Save session data
- Log session metrics
- Send notifications

## Event Comparison Matrix

| Event | When | Matchers | Context Output | Block | Permission |
|-------|------|----------|----------------|-------|------------|
| SessionStart | Session begins | source | ✅ | ❌ | ❌ |
| Stop | Claude stops | ❌ | ❌ | ❌ | ❌ |
| UserPromptSubmit | User sends prompt | ❌ | ✅ | ✅ | ❌ |
| PreToolUse | Before tool | tool name | ❌ | ❌ | ✅ |
| PostToolUse | After tool | tool name | ❌ | ❌ | ❌ |
| SubagentStop | Agent completes | ❌ | ❌ | ❌ | ❌ |
| Notification | Permission popup | ❌ | ❌ | ❌ | ❌ |
| PreCompact | Before compact | ❌ | ❌ | ❌ | ❌ |
| SessionEnd | Session ends | ❌ | ❌ | ❌ | ❌ |
