---
name: diff-review
description: Review code changes locally - either from a specific Phabricator diff or the current commit in your stack. Use when asked to review code, provide feedback, or analyze changes.
allowed-tools: mcp__plugin_meta_www__get_phabricator_diff_details, Bash(sl:*), Bash(meta:*), Read
---

# Diff Review Skill

Review code changes locally and provide feedback in the chat.

## Precondition: Paladin Plugin Required

Before doing anything else, verify the Paladin plugin is installed by checking whether `paladin:review-code` appears in your available skills.

**If Paladin is NOT installed**, output the following message to the user verbatim and STOP — do not run any of the steps below, do not fetch the diff, do not analyze anything:

> This skill requires the Paladin plugin to provide cross-model review consensus. Please install it first by running the following command **in a separate shell / terminal** (not inside Claude Code — this is not a slash command):
>
> ```
> agent-market plugin paladin install
> ```
>
> Then start a new Claude Code session and re-invoke this skill.

**If Paladin IS installed**, proceed with the workflow below. After completing your local review, also invoke `Skill('paladin:review-code')` against the same target (diff number or current changes) and merge its findings with your own consumer/caller and library-migration analysis (deduplicate by file:line + category) before presenting the final unified output.

## Workflow

### Option 1: Review a Specific Diff (when diff number is provided)

If the user specifies a diff number (e.g., "review D12345678"):

1. Use `mcp__plugin_meta_www__get_phabricator_diff_details` with the diff number to fetch the diff content (set `include_raw_diff: true`)
2. Analyze the changes for issues
3. Present findings in the chat

### Option 2: Review Current Commit (when no diff number is provided)

If no diff number is specified (e.g., "review my changes" or just "review"):

1. Run `sl status` to check for uncommitted changes
2. Run `sl diff` for uncommitted changes, or `sl show .` for the current commit
3. Analyze the diff output for issues
4. Present findings in the chat

For larger diffs, use `Read` to examine specific files in detail.

## Mandatory: Review Beyond the Diff

The raw diff alone is never sufficient. You MUST read surrounding context for changed code:

### Consumer/Caller Analysis
When a diff changes how data is **produced** (return values, output formats, library swaps):
- Read every function that **consumes the output** of the changed code
- Search for call sites of modified functions and check how return values are used
- Look for implicit behavioral contracts: null representation, type semantics, error signaling

### Library/API Migration Diffs
When a diff replaces one library with another:
- Identify **behavioral differences** between old and new: return types, null handling, error modes, default settings
- Scan downstream code for patterns that depend on the **old library's behavior**
- Flag **missing tests**: a library migration with zero test changes is a red flag
- Check for **inconsistencies**: if surrounding code uses a defensive pattern but the changed area doesn't, flag it

### Data Schema Awareness
When reviewing code that processes structured data (query results, API responses, parsed files):
- Read the **query or schema** that produces the data to understand field types
- Verify that null-check and type-check patterns match the actual data types
- Flag bare truthiness checks on values that could be null/empty in ambiguous ways

## Review Focus Areas

Focus on medium and high priority issues:

| Priority | Examples |
|----------|----------|
| **High** | Bugs, security vulnerabilities, data races, memory leaks |
| **Medium** | Logic errors, missing error handling, performance issues |

## What NOT to Flag

- Low priority issues
- Style preferences (unless clearly wrong)
- "Consider adding a comment" suggestions
- Trivial nitpicks
- Minor formatting issues

## Output Format

Jump straight to findings — do NOT summarize what the diff does (the author knows).

1. **Issues Found**: List each issue with:
   - File and line number(s)
   - Severity (High/Medium)
   - Description (1-2 sentences max) and suggested fix (1 sentence or code snippet)
2. If no issues: "Looks good — no critical issues." (Do not elaborate or add positive notes.)

**Conciseness rules:**
- Each finding: max 3 sentences total. If the fix is obvious, omit it.
- No "Positive Notes" section — praise wastes reviewer attention.
- No multi-paragraph explanations or hedging ("this could potentially...").
- Minor issues: one sentence max.

## Posting Review as Comments

After presenting your findings in the chat, if the user asks to post the review as comments on the diff, use the Meta CLI to publish your feedback:

### Diff-Level Comments (General Feedback)

For overall assessment and summary:

```bash
meta phabricator.diff comment --number=D12345678 --message='Your review summary'
```

**With intent** (optional):
- `--intent=blocking` - For issues that must be fixed
- `--intent=nit` - For minor suggestions
- `--intent=aside` - For informational comments

### Inline Comments (Specific Code)

For feedback on specific lines:

```bash
meta phabricator.diff inline-comment \
  --number=D12345678 \
  --file=path/to/file.py \
  --line=42 \
  --message='Your specific feedback'
```

**For multi-line comments:**
```bash
meta phabricator.diff inline-comment \
  --number=D12345678 \
  --file=path/to/file.py \
  --line=42 \
  --length=5 \
  --message='Feedback on lines 42-46'
```

### Draft Comments

To save as draft (not published immediately):
```bash
meta phabricator.diff comment --number=D12345678 --message='Draft review' --draft
```

### Verifying Comments

To check if comments were posted:
```bash
# List comments on a diff
meta phabricator.diff comments --number=D12345678 --output=json
```

Or use `mcp__plugin_meta_www__get_phabricator_diff_details` with `include_diff_comments=true` to fetch comments.

## Example Usage

**User**: "Review D87244684"
→ Use `mcp__plugin_meta_www__get_phabricator_diff_details` with `phabricator_diff_number="D87244684"` and `include_raw_diff=true`, analyze, and present findings

**User**: "Review my current changes"
→ Run `sl status` and `sl diff` (or `sl show .`), analyze the output, and present findings

**User**: "Do a thorough review of my diff"
→ Fetch the diff, use `Read` to examine changed files in full context, and provide comprehensive analysis
