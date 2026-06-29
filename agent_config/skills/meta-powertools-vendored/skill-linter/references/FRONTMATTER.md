# Frontmatter Validation

This document explains frontmatter requirements and WHY each matters.

## Valid Frontmatter Fields

**Claude Code:** `name`, `description`, `author`, `allowed-tools`, `model`, `context`, `agent`, `hooks`, `disable-model-invocation`, `user-invocable`, `argument-hint`, `version`

**Devmate (additional):** `oncalls`, `llms-gk`, `tools`, `apply_to_regex`, `apply_to_content`, `apply_to_user_prompt`

Always check against this list before flagging unknown fields.

## `name` Field

### Requirements (🔴 Errors)

- **Present:** Optional. Defaults to directory name if omitted. If present, validate normally (format, length, directory match)
- **Matches directory:** `name: foo` must be in directory `foo/`
- **Hyphen-case:** Lowercase letters, digits, and hyphens only
- **≤64 characters:** Prevents truncation in UIs and logs

### Why It Matters

- **Matches directory:** Skills are loaded by directory name. Mismatch causes confusion.
- **Hyphen-case:** Consistent naming across all skills. Avoids filesystem issues.
- **≤64 chars:** Prevents truncation in UIs and logs.

### Edge Cases (Use Judgment)

- Name too generic? (`utils`, `helper`, `tool`)
- Name doesn't reflect purpose? (e.g., `my-skill` vs `pdf-merger`)

## `description` Field

### Requirements (🔴 Errors)

- **Present:** Must exist in frontmatter
- **≤1024 characters:** Keeps skill loading fast
- **No angle brackets:** `<` and `>` break XML parsing

### Requirements (🟡 Warnings)

- **Has WHEN trigger:** Should include "Use when...", "trigger", "if the user..."

### Why It Matters

The description determines **when Claude invokes** the skill. A vague description means:
- Skill triggers when it shouldn't
- Skill doesn't trigger when it should
- User confusion about what the skill does

### Good Pattern

```yaml
description: [WHAT it does]. Use when [WHEN to trigger].
```

### Examples

**Good:**
```yaml
description: Convert CSV files to JSON with schema validation. Use when the user asks to convert, transform, or validate CSV data.
```

**Bad:**
```yaml
description: Handles files  # Too vague, no trigger
description: Process <filename>  # Angle brackets break parsing
```

### Edge Cases (Use Judgment)

- Description sounds like another skill? (duplication)
- Keywords too broad? ("helps with code" matches everything)
- Keywords too narrow? (only triggers on exact phrase)

## Common Frontmatter Errors

| Error | Severity | Impact | Example |
|-------|----------|--------|---------|
| Name mismatch | 🔴 | Confusing, may break references | `name: foo` in directory `bar/` |
| Uppercase in name | 🔴 | Inconsistent, may break on case-sensitive systems | `name: MySkill` |
| Angle brackets | 🔴 | Parsed as XML, breaks skill loading | `description: Fix <issue>` |
| No WHEN clause | 🟡 | Skill triggers unpredictably | `description: PDF tools` |
