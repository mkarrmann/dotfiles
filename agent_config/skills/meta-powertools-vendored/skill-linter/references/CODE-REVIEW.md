# Code Review Quality

This document covers higher-level quality checks that go beyond structural validation.

## Technical Accuracy Checks

### References to Non-Existent Features (🔴 Error)

Check for references to features that don't exist in Claude Code:

**Invalid slash commands:**
- `/clear`, `/reset` - These don't exist
- `/continue` - Not a real command
- Any slash command not in the documented list

**Invalid XML blocks:**
- `<thought>` blocks as user-controllable - Internal thinking is not exposed
- `<context>` blocks - Not a real feature
- Custom XML tags for formatting - Claude doesn't process these

**Invalid language features:**
- `@format` pragma in Python - This is JS/TS only
- `@noformat` pragma in Python - This is JS/TS only
- `# format: on/off` in JavaScript - This is Python Black syntax

**Why:** Misleading information causes confusion and failures.

### Non-Actionable Triggers (🟡 Warning)

Check if the description mentions triggers Claude can't detect:

**Non-actionable triggers:**
- "when context is low" / "when running out of tokens" - No visibility into context usage
- "when performance matters" - Too vague, always matters
- "for complex tasks" - Everything could be considered complex
- "when you're confused" - Too subjective
- "if something seems wrong" - Not measurable

**Good triggers:**
- "when user asks about X" - User input is observable
- "when working with Y files" - File extensions/paths are observable
- "before submitting diffs" - Specific workflow step
- "when encountering error Z" - Specific error condition

**Why:** Skills should trigger on observable conditions, not internal state.

## Conceptual Quality Checks

### Persona vs Procedure (🟡 Warning - STRICT tier only)

Flag skills that are primarily style/tone guidance rather than workflows.

**Signs of a persona skill:**
- High ratio of "be X" vs "do Y" or "run Z"
- Keywords: "tone", "style", "communication", "personality", "respond as", "speak like"
- No concrete steps, just behavioral guidelines
- No bundled scripts or reference files
- Mostly about changing communication style

**Example of persona (bad):**
```markdown
Be concise and direct. Avoid pleasantries. Use minimal words.
```

**Example of procedure (good):**
```markdown
1. Run the validation script
2. Check for errors in the output
3. Fix issues using the remediation tool
```

**Message:** "This appears to be communication style guidance. Consider moving to project/personal CLAUDE.md instead of a skill."

**Why:** Skills are for procedural knowledge. Communication preferences belong in CLAUDE.md.

### Baseline Redundancy (🔵 Suggestion)

Flag content that likely duplicates system instructions:

**Common redundancies:**
- "be concise" / "avoid verbosity" - Already default behavior
- "ask permission for destructive actions" - Already required
- "read only what's needed" - Already standard practice
- "use Edit tool efficiently" - Tool already optimized
- "avoid hallucination" - Core safety requirement
- "be helpful" - Fundamental behavior

**Message:** "This may duplicate baseline behavior. Verify this adds specific value beyond system instructions."

**Why:** Skills should extend capabilities, not restate defaults.

### Thin Skills (🔵 Suggestion - STRICT tier only)

Identify skills that might be too lightweight:

**Indicators:**
- <500 words total in SKILL.md
- No bundled scripts, references, or assets
- >50% of content is bullet points of general advice
- Could be summarized in 2-3 sentences
- No multi-step workflows

**Message:** "This skill has minimal content. Consider whether this should be CLAUDE.md content or expanded with more substance."

**Why:** Skills have overhead. Simple guidance belongs in CLAUDE.md.

## Implementation-Specific Issues

### Incorrect Tool Usage Guidance (🔴 Error)

Check for guidance that contradicts how tools actually work:

**Common mistakes:**
- "Use Bash grep instead of Grep tool" - Grep tool is preferred
- "Read entire files to search" - Inefficient, use Grep
- "Edit by rewriting the whole file" - Use Edit tool instead
- "Run Python scripts with python3" - Should use buck run in fbcode

**Why:** Incorrect tool guidance causes failures and inefficiency.

### Overscoped Capabilities (🟡 Warning)

Flag skills that claim to do too much:

**Red flags:**
- "Universal" / "all-purpose" / "handles everything"
- Lists 10+ different use cases
- Combines unrelated workflows (e.g., "PDF processing and database migrations")
- "Expert at all aspects of X"

**Better:** Focused, specific capabilities within a domain.

**Why:** Overscoped skills are hard to maintain and trigger incorrectly.

### Broken Links (🔴 Error for internal, 🟡 Warning for external)

Check all markdown links `[text](url)` actually resolve:

**Internal links (🔴 Error):**
- `internalfb.com/*` - Must resolve
- `fburl.com/*` - Must resolve
- Relative file paths: `references/GUIDE.md` - Must exist in skill directory
- Same-repo links: `../other-skill/SKILL.md` - Must exist relative to skill

**External links (🟡 Warning):**
- `github.com`, `docs.python.org`, etc. - May go stale, but less critical
- If broken, suggest archiving or finding replacement

**Why:** Broken internal links create support burden. Skills referencing moved/deleted docs are unusable.

**Check approach:**
- For file paths: Verify file exists in repository
- For internal URLs: Note as requiring validation (can be slow)
- For external URLs: Warning only, document that they may change

## Security Checks

### Secrets & PII Detection (🔴 Error)

Check for hardcoded secrets, credentials, and sensitive data:

**Secrets patterns:**
- API keys, tokens, passwords (look for `api_key=`, `token=`, `password=`, `secret=`)
- AWS credentials (`AKIA`, `aws_access_key_id`)
- Private keys (`-----BEGIN RSA PRIVATE KEY-----`, `.pem` file contents)
- OAuth tokens, JWTs with real data
- Database connection strings with credentials

**PII & Internal Data:**
- Real employee names, usernames (except well-known examples like "Alice", "Bob")
- Employee IDs, oncall rotation names
- Real account IDs, user IDs, FBIDs (use placeholder like `123456789` instead)
- Email addresses (except generic examples like `user@example.com`)
- Phone numbers, addresses

**Internal URLs:**
- Production URLs (use `example.com` or clearly mark as examples)
- Internal tool URLs with real identifiers embedded
- Links to specific tasks, diffs, or internal docs with sensitive context

**Why:** At scale, accidental credential leaks are inevitable. Catch them before they're committed.

**Exceptions:** Well-known public examples, clearly fictional data, sanitized examples.

### Dangerous Command Patterns (🔴 Error)

Check scripts and command examples for dangerous patterns:

**Unsafe bash patterns:**
- `rm -rf` without path validation or safeguards
- Unquoted variable expansion: `rm $DIR/*` should be `rm "$DIR"/*`
- Missing `set -e` or `set -u` in bash scripts
- `eval` on user input or untrusted data
- Commands without error handling that could fail silently

**Production-risky operations:**
- Database DROP/DELETE/TRUNCATE without WHERE clause or confirmation
- `--force`, `--no-verify`, `--skip-hooks` flags without warnings
- Mass file operations without dry-run examples
- `git push --force` to main/master branches

**Why:** Copy-pasted commands can cause outages. Flag dangerous patterns explicitly.

**Good practices to recommend:**
- Always quote variables: `"$VAR"`
- Use `set -euo pipefail` in bash scripts
- Show dry-run mode first: `command --dry-run`
- Add confirmation prompts for destructive operations

## Naming & Discoverability

### Generic or Ambiguous Names (🟡 Warning - STRICT tier)

Flag skill names that are too generic or won't scale:

**Generic terms to avoid:**
- Single words: "helper", "utils", "tool", "script", "workflow"
- Vague qualifiers: "my-skill", "custom-tool", "new-helper"
- Technology names alone: "python", "react", "docker" (unless it's THE official skill for that tech)
- Common patterns: "maker", "builder", "generator", "processor" without specificity

**Red flags:**
- Name has <3 meaningful words for a specific domain
- Name could apply to 10+ different tools/workflows
- Name matches common programming terms

**Good names (specific):**
- `python-dataclass-generator` (not just "python")
- `graphql-schema-validator` (not just "validator")
- `sapling-stack-management` (not just "stack-tool")

**Message:** "Skill name is too generic. With 1000+ skills, 'helper' will trigger constantly. Make it specific to its domain."

**Why:** Generic names cause discovery problems and false triggers at scale.

### Description Optimization (🔵 Suggestion)

Assess whether the description is optimized for discovery:

**Issues to flag:**
- Buries the lede: Trigger conditions appear after >200 chars
- Missing searchable keywords: No mention of specific file types, commands, or domains
- Too long: >300 chars before stating "Use when..."
- Too vague: "Helps with development tasks" without specifics

**Good structure:**
1. What it does (1 sentence, <100 chars)
2. Use when... (trigger conditions, searchable keywords)

**Examples:**

**Bad:**
```
This skill provides comprehensive assistance for various development workflows including but not limited to file processing, data transformation, and general automation tasks. Use when you need help with development.
```

**Good:**
```
Generate Python dataclasses from JSON schemas. Use when working with .json schema files, creating type-safe data models, or the user asks to "generate dataclass from JSON".
```

**Why:** Bad descriptions mean skills won't be discovered or will trigger incorrectly.

## Script Quality

### Script Quality Checks (🟡 Warning for STRICT tier, 🔵 Suggestion for others)

Check bundled scripts for quality issues:

**Missing basics:**
- No shebang (`#!/usr/bin/env python3`, `#!/bin/bash`)
- Wrong shebang (`#!/usr/bin/python` instead of `#!/usr/bin/env python3`)
- Not executable (missing `chmod +x`)
- No usage/help output (script should explain itself when run without args)

**Poor error handling:**
- Bash scripts without `set -e` (fail on error)
- Bash scripts without `set -u` (fail on undefined variables)
- Python scripts that print stack traces instead of user-friendly errors
- Silent failures (exits with code 0 even on error)

**Usability issues:**
- No docstring or header comment explaining purpose
- No argument parsing (`argparse` in Python, `getopts` in bash)
- Hardcoded paths instead of arguments
- No validation of required dependencies

**Good script practices:**
- Has shebang and executable bit
- Shows usage when called incorrectly
- Validates inputs before running
- Fails fast with clear error messages
- Uses `set -euo pipefail` in bash

**Why:** Poorly written scripts frustrate users and cause silent failures.

### Undocumented Magic Numbers (🟡 Warning - STRICT tier, 🔵 Suggestion - others)

Flag constants without comments explaining why that value was chosen.

**Bad:**
```python
TIMEOUT = 47  # Why 47?
RETRIES = 5   # Why 5?
```

**Good:**
```python
REQUEST_TIMEOUT = 30  # HTTP requests typically complete within 30s
MAX_RETRIES = 3       # Most intermittent failures resolve by retry 2
```

**Message:** "Undocumented constant `[name] = [value]`. Add a comment explaining why this value was chosen."

### Unclear Script Intent (🔵 Suggestion)

Flag script references in SKILL.md that don't clarify whether Claude should execute the script or read it as reference.

**Good (clear intent):**
- "Run `scripts/analyze.py input.pdf` to extract fields." (execute)
- "See `scripts/algorithm.py` for the extraction logic." (read)

**Bad (ambiguous):**
- "The script `scripts/analyze.py` handles field extraction."

**Message:** "Unclear whether Claude should execute or read this script. Use 'Run X' for execution or 'See X for...' for reference."

### Unqualified MCP Tool Names (🟡 Warning)

Flag MCP tool references that lack the server prefix. Must use `ServerName:tool_name` format.

**Good:** `Use the BigQuery:bigquery_schema tool to retrieve table schemas.`
**Bad:** `Use the bigquery_schema tool to retrieve table schemas.`

**Why:** Without the server prefix, Claude may fail to locate the tool when multiple MCP servers are available.

**Message:** "MCP tool reference without server prefix. Use fully qualified `ServerName:tool_name` format."

### Complex Workflows Without Validation (🔵 Suggestion - STRICT tier only)

Flag multi-step workflows (4+ steps) that lack any validation or verification step between actions, especially when steps involve destructive or irreversible operations.

**Good (feedback loop):**
```markdown
1. Make edits
2. Validate: `python scripts/validate.py output/`
3. If validation fails, fix and re-validate
4. Only proceed when validation passes
```

**Message:** "Multi-step workflow has no validation step. Add a verification check between destructive or irreversible steps."

### Fragility Mismatch (🟡 Warning - STRICT tier, 🔵 Suggestion - others)

Flag fragile operations (database changes, deployments, destructive actions) guided by vague text instructions instead of exact scripts or commands.

**Message:** "This workflow involves [fragile operation] but uses text guidance instead of exact scripts. Provide specific commands to reduce error risk."

## How to Apply These Checks

### For each check:

1. **Scan the content** - Look for the patterns described
2. **Consider context** - Some patterns might be justified
3. **Use judgment** - Not all instances are problems
4. **Provide specifics** - Quote the problematic text
5. **Suggest fixes** - Offer concrete improvements

### Example findings:

```markdown
🔴 **Error:** Reference to non-existent feature
- File: `SKILL.md:11`
- Details: References `<thought>` blocks as user-controllable. These are internal only.
- Suggestion: Remove or clarify that thinking happens internally

🟡 **Warning:** Non-actionable trigger
- File: `SKILL.md:3` (description)
- Details: "when context is running low" - Claude cannot detect context usage
- Suggestion: Change to "when the user mentions token limits" or remove

🔵 **Suggestion:** Thin skill
- Details: Only 400 words, no scripts, mostly general advice
- Suggestion: Consider moving to CLAUDE.md or expanding with concrete workflows
```
