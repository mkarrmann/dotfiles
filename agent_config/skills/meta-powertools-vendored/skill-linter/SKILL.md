---
name: skill-linter
description: Review Claude Code skills for quality, best practices, and common mistakes. Use when reviewing SKILL.md files, auditing skill directories, or when a diff modifies skill files. Also use when asked "should we update skill-linter" to self-reflect on this skill's own quality.
---

# Skill Linter

Review Claude Code skills for quality issues, applying tiered strictness based on location.

## How to Review a Skill

### Step 1: Gather Context

Read all files in the skill directory:
- `SKILL.md` (required)
- `references/*.md` (if present)
- `scripts/*` (if present)
- `assets/*` (if present)

### Step 2: Determine Tier

Infer strictness from the skill's file path:

| Location Pattern | Tier | Report |
|------------------|------|--------|
| `claude-templates/components/skills/` | STRICT | Errors, warnings, suggestions |
| `claude-templates/components/plugins/*/skills/` | STRICT | Errors, warnings, suggestions |
| `.claude/skills/` or `.llms/skills/` | MODERATE | Errors and warnings only |
| `confucius/analects/*/skills/` | MODERATE | Errors and warnings only |
| `scripts/**/skills/` | LENIENT | Errors only |
| Other locations | MODERATE | Errors and warnings only |

### Step 3: Check Quality Criteria

Apply the checks below. See reference docs for detailed criteria:
- [Frontmatter Validation](references/FRONTMATTER.md)
- [Content Quality](references/CONTENT.md)
- [Resource Validation](references/RESOURCES.md)
- [Duplicate Detection](references/DUPLICATES.md)
- [Code Review Quality](references/CODE-REVIEW.md) - Technical accuracy, actionable triggers, persona vs procedure, script design, workflow quality

Also review against skill-creator documentation:
- [COMMON-MISTAKES.md](../skill-creator/references/COMMON-MISTAKES.md)
- [BEST-PRACTICES.md](../skill-creator/references/BEST-PRACTICES.md)

### Step 4: Report Findings

Format your review as:

```markdown
## Skill Linter Review

> 🔍 **Automated review** by the `skill-linter` skill ([source](https://fburl.com/claude-skill-linter))
>
> This is advisory, not land-blocking. Questions? Ping Jason Yanowitz <yanowitz@meta.com>

---

**Skill:** `skill-name`
**Tier:** STRICT | MODERATE | LENIENT
**Files reviewed:** N

### Issues Found

🔴 **Error:** [description]
- File: `path/to/file`
- Line: N
- Details: [specific issue]

🟡 **Warning:** [description]
- File: `path/to/file`
- Line: N
- Details: [specific issue]

🔵 **Suggestion:** [description]
- File: `path/to/file`
- Line: N (if applicable)
- Details: [specific issue]

### Summary

- Errors: N
- Warnings: N
- Suggestions: N
```

**IMPORTANT: Always include line numbers** when reporting issues. This enables:
1. More precise feedback for authors
2. Inline signals that appear directly on the affected lines in Phabricator

If no issues found, omit the "### Issues Found" heading and its contents entirely. Instead, output:

```markdown
✅ No issues found. Skill follows best practices.

### Summary

- Errors: 0
- Warnings: 0
- Suggestions: 0
```

## Quality Criteria

### Frontmatter (🔴 Errors)

| Check | Criteria |
|-------|----------|
| `name` present | Optional — defaults to directory name if omitted. If present, validate normally |
| `name` matches directory | `name: foo` must be in directory `foo/` (skip if `name` absent) |
| `name` format | Lowercase hyphen-case only (`my-skill`, not `MySkill`) (skip if `name` absent) |
| `name` length | ≤64 characters (skip if `name` absent) |
| `description` present | Must exist in frontmatter |
| `description` length | ≤1024 characters |
| `description` no angle brackets | No `<` or `>` characters (breaks parsing) |

**Valid Claude Code frontmatter fields**: `name`, `description`, `author`, `allowed-tools`, `model`, `context`, `agent`, `hooks`, `disable-model-invocation`, `user-invocable`, `argument-hint`, `version`

**Note on `version`**: Optional field for tracking skill versions (semver format, e.g., `version: 1.0.0`). Not enforced by Claude Code but useful for skill distribution and changelog tracking.

**Valid Devmate frontmatter fields** (in addition to above): `oncalls`, `llms-gk`, `tools`, `apply_to_regex`, `apply_to_content`, `apply_to_user_prompt`

**Devmate skill exceptions (`.llms/skills/`):** The `name` field is NOT required for Devmate skills. Devmate loads skills by directory name, so a missing `name` is not an error. If `name` is present, validate it normally (format, length, directory match). All other checks (description, content, security, etc.) still apply.

### Frontmatter (🟡 Warnings)

| Check | Criteria |
|-------|----------|
| `description` has WHEN trigger | Should include "Use when...", "trigger", "if the user...", "helpful for" |
| `description` too short | Description under 50 characters likely lacks enough context for Claude to decide when to invoke |
| Unknown frontmatter field | Check EACH field individually before flagging — do not batch-flag. Valid Claude Code fields: `name`, `description`, `author`, `allowed-tools`, `model`, `context`, `agent`, `hooks`, `disable-model-invocation`, `user-invocable`, `argument-hint`, `version`. Devmate adds: `oncalls`, `llms-gk`, `tools`, `apply_to_regex`, `apply_to_content`, `apply_to_user_prompt`. |

**Description quality matters**: The description is how Claude decides whether to auto-invoke a skill. Too short or vague = skill won't trigger when it should.

### Frontmatter (🔵 Suggestions)

| Check | Criteria |
|-------|----------|
| Missing `allowed-tools` | Consider adding `allowed-tools` to restrict tool permissions. Skills with unrestricted tools may trigger unexpected permission prompts. |
| Unrestricted Bash access | `Bash` in `allowed-tools` without restrictions grants full shell access. Consider `Bash(cmd:*)` patterns (e.g., `Bash(git:*)`, `Bash(sl:*)`) to limit to specific commands. |

**Examples of restricted Bash patterns:**
- `Bash(git:*)` - Only git commands
- `Bash(sl:*, git:*)` - Sapling and git commands
- `Bash(buck:*, buck2:*)` - Buck build commands

### Content (🔴 Errors)

| Check | Criteria |
|-------|----------|
| No `[TODO:` markers | Check all `.md` files (ignore code blocks) |
| No template sections | No `## Structuring This Skill` heading |

### Content (🟡 Warnings)

| Check | Criteria |
|-------|----------|
| No placeholder files | `scripts/example.py`, `references/api_reference.md`, `assets/example_asset.txt` with placeholder content |
| `[TODO:` in non-SKILL.md files | Warning instead of error for reference docs |
| Over-explaining known concepts | Paragraphs explaining what PDFs, JSON, or libraries are. Only project-specific context. |
| Too many options without default | Listing 3+ approaches without recommending one. Pick a default. |
| Inconsistent terminology | 3+ different terms for the same concept across files |
| Time-sensitive information | Date-conditional logic like "Before August 2025". Use "Current method" / "Old patterns" sections. |

### Content (🔵 Suggestions)

| Check | Criteria |
|-------|----------|
| Skill-to-skill invocation without fork | If skill content mentions "use the X skill", "invoke X skill", or "call the X skill", suggest adding `context: fork` to prevent context bloat |
**Why fork for skill composition?** When Skill A invokes Skill B, forking B saves ~40% tokens because only the summary returns to main context. Without fork, all of B's intermediate work stays in context.

### Paths & Links (🔴 Errors)

| Check | Criteria |
|-------|----------|
| No hardcoded absolute paths | No `/Users/`, `/home/`, `/data/users/` in scripts |
| Internal links resolve | `[text](internal_url)` must work for internalfb.com, fburl.com, relative paths |

### Paths & Links (🟡 Warnings)

| Check | Criteria |
|-------|----------|
| External links resolve | `[text](external_url)` for github.com, etc. should work but may go stale |
| No deep reference chains | References one level deep from SKILL.md. No A→B→C chains. |

### Paths & Links (🔵 Suggestions)

| Check | Criteria |
|-------|----------|
| No empty directories | Delete empty `scripts/`, `references/`, `assets/` |
| Long reference files have TOC | Reference files >100 lines need a table of contents at top |

### Duplicates (🟡 Warnings)

| Check | Criteria |
|-------|----------|
| High-confidence duplicate | Exact name match with another skill (likely copy, recommend symlink) |
| Stale copy | Copy that has diverged from source (recommend updating or symlinking) |

### Duplicates (🔵 Suggestions)

| Check | Criteria |
|-------|----------|
| Medium-confidence duplicate | Similar name or high keyword overlap (use Claude judgment) |
| Related skill exists | Consider documenting relationship or consolidating |

### Build (🔴 Errors) - STRICT and MODERATE tiers

| Check | Criteria |
|-------|----------|
| Scripts have Buck targets | Each `scripts/*.py` must have a corresponding `python_library` or `python_binary` in TARGETS |
| Scripts are buildable | `buck2 build` succeeds for script targets |

**Exceptions:** Scripts with `# @noautodeps` comment are exempt (standalone scripts that don't need Buck).

**How to check:**
```bash
# Look for TARGETS file
ls scripts/TARGETS

# Verify targets build
buck2 build fbcode//path/to/skill/scripts:...
```

### Security (🔴 Errors)

| Check | Criteria |
|-------|----------|
| No secrets or credentials | No API keys, tokens, passwords, private keys in any files |
| No PII or internal data | No real employee names/IDs, account IDs, email addresses (except generic examples) |
| No dangerous commands | No unquoted variables, `rm -rf` without safeguards, `--force` without warnings |

See [Code Review Quality](references/CODE-REVIEW.md#security-checks) for details.

### Naming & Discoverability (🟡 Warning - STRICT tier, 🔵 Suggestion - others)

| Check | Criteria |
|-------|----------|
| Not generic name | Avoid "helper", "utils", "tool" without specificity; name should be specific to domain |
| Description optimized | Trigger conditions appear early (<200 chars), includes searchable keywords |

See [Code Review Quality](references/CODE-REVIEW.md#naming--discoverability) for details.

### Technical Accuracy (🔴 Errors)

| Check | Criteria |
|-------|----------|
| No invalid slash commands | No references to `/clear`, `/reset`, or other non-existent commands |
| No invalid XML blocks | No user-controllable `<thought>` blocks or other non-existent features |
| No wrong language features | No `@format` in Python, `# format:` in JS, etc. |
| MCP tools fully qualified | Tool references use `ServerName:tool_name` format, not bare tool names |

See [Code Review Quality](references/CODE-REVIEW.md) for details.

### Actionable Triggers (🟡 Warning)

| Check | Criteria |
|-------|----------|
| Triggers are observable | No "when context is low", "when performance matters", "for complex tasks" |
| Triggers reference user input or files | "when user asks X", "when working with Y files", "before Z workflow" |

See [Code Review Quality](references/CODE-REVIEW.md) for details.

### Conceptual Quality (🟡 Warning - STRICT tier, 🔵 Suggestion - others)

| Check | Criteria |
|-------|----------|
| Not a persona skill | Not primarily style/tone guidance; should have concrete workflows |
| Not baseline redundancy | Doesn't duplicate default behavior ("be concise", "ask permission", etc.) |
| Not overly thin | >500 words or has bundled resources; substantial enough to warrant a skill |
| Not overscoped | Focused capabilities, not "universal" or 10+ unrelated use cases |
| Complex workflows have validation | Multi-step workflows (4+ steps) include verification steps between actions |
| Fragile operations use exact scripts | Destructive/fragile tasks provide specific commands, not vague text guidance |

See [Code Review Quality](references/CODE-REVIEW.md) for details.

### Script Quality (🟡 Warning - STRICT tier, 🔵 Suggestion - others)

| Check | Criteria |
|-------|----------|
| Scripts have shebangs | `#!/usr/bin/env python3` or `#!/bin/bash` at top of scripts |
| Scripts are executable | Files in `scripts/` should have executable bit set |
| Scripts have error handling | Bash uses `set -e`, Python has try/catch or clear errors |
| Scripts have usage/help | Shows help when run without args or with `--help` |
| No undocumented magic numbers | Script constants have comments explaining why that value |
| Clear script intent | Each script reference in SKILL.md states whether to execute ("Run X") or read ("See X for...") |

See [Code Review Quality](references/CODE-REVIEW.md#script-quality) for details.

### Testing (🟡 Warning - STRICT tier, 🔵 Suggestion - others)

| Check | Criteria |
|-------|----------|
| Scripts have tests | Each `scripts/*.py` should have a corresponding `scripts/tests/*_test.py` |
| Tests have Buck targets | `scripts/tests/TARGETS` should include `python_unittest` for each test file |
| Tests are runnable | `buck2 test fbcode//path/to/skill/scripts/tests/...` succeeds |

**Exceptions:** Simple standalone scripts (e.g., `example.py` placeholder) may not need tests.

**How to check:**
```bash
# Look for tests directory
ls scripts/tests/

# Verify test targets exist
buck2 targets fbcode//path/to/skill/scripts/tests:

# Run tests
buck2 test fbcode//path/to/skill/scripts/tests/...
```

### Subjective Quality (Claude Judgment)

| Check | Criteria |
|-------|----------|
| Description specificity | Triggers on right queries, not too broad/narrow |
| Duplication | Not duplicating an existing shared skill |
| Code organization | Long code blocks should be scripts, not inline |
| Quick Start quality | Runnable example with realistic data |
| Documentation match | SKILL.md accurately describes what scripts do |

## Self-Reflection Mode

When asked "should we update skill-linter" or similar:

1. **Review this skill** against current best practices
2. **Check dependencies** - Read skill-creator's COMMON-MISTAKES.md and BEST-PRACTICES.md
3. **Identify gaps** - Are there new patterns not covered here?
4. **Propose updates** - List specific changes to SKILL.md or references

## Special Case: skill-creator Changes

If a diff modifies skill-creator documentation:
- `skill-creator/references/COMMON-MISTAKES.md`
- `skill-creator/references/BEST-PRACTICES.md`
- `skill-creator/SKILL.md`

Add this warning to your review:

```markdown
⚠️ **Dependency Alert:** This diff modifies skill-creator documentation.

The `skill-linter` skill references these files for quality criteria.
Please verify:
- [ ] Changes are intentional improvements to skill quality standards
- [ ] skill-linter criteria may need updates to match (e.g., you've moved or added files it references. In Claude Code, you should be able to install the skill-linter skill and then ask 'Do the changes to skill-creator require changes to skill-linter?'
```
