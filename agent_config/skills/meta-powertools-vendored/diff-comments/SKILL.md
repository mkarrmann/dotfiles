---
name: diff-comments
description: Interact with Phabricator diff comments - add comments, inline comments, edit drafts, delete drafts, resolve, and respond
allowed-tools: Bash(meta phabricator.diff:*), Bash(fbcode/claude-templates/components/skills/diff-comments/scripts/*:*), Bash(jf:*), Read
---

# Diff Comments Skill

Interact with Phabricator diff comments programmatically using the `meta` CLI.

## When to Use This Skill

Use this skill when you need to:
1. Add general comments to diffs
2. Add inline comments to specific lines of code
3. Resolve or unresolve comments on diffs
4. Query all comments on a diff to find comment IDs
5. Delete draft comments
6. Respond to review feedback
7. Submit (publish) all draft comments at once
8. Query automated tooling inline messages (lint, clang-tidy, AI reviewer)
9. Edit draft comments (Python script)
10. Query comment status by FBID (Python script)

## Available Commands

### comment

Add a general comment to a diff (not tied to a specific line).

**Usage:**
```bash
meta phabricator.diff comment -n <diff_number> -m "comment text" [-i INTENT] [--draft]
```

**Arguments:**
- `-n <diff_number>`: The diff number (e.g., D12345 or 12345) (required)
- `-m "text"`: The comment text (required)
- `-i <intent>`, `--intent <intent>`: Comment intent: `nit`, `blocking`, `aside`, `preexisting`, `code-style`, `clarify`, `context`. Note: `--intent` is not supported with `--draft`
- `--draft`: Save comment as draft for batch publishing with `submit-review`
- `--dry-run`: Preview without making changes

**Examples:**
```bash
meta phabricator.diff comment -n D12345 -m "This looks good to me"
meta phabricator.diff comment -n D12345 -m "This must be fixed" -i blocking
meta phabricator.diff comment -n D12345 -m "Need to think about this more" --draft
```

**Notes:**
- By default, comments are published immediately
- Use `--draft` to save the comment as a draft visible only to you until you run `submit-review`
- Do not duplicate update summaries: if `jf submit --message` or `submit-review -m` already records the same context, do not add a separate top-level comment unless the user explicitly asks
- Informational comments should not leave actionable review state. If Phabricator makes a non-actionable comment resolvable, publish it when useful and then resolve it

### inline-comment

Add inline comments to specific lines in changed files.

**Usage:**
```bash
meta phabricator.diff inline-comment -n <diff_number> -f <file_path> -l <line_number> -m "comment text" [--intent INTENT] [--line-length N] [--on-old-file] [--draft]
```

**Arguments:**
- `-n <diff_number>`: Diff number (e.g., D12345 or 12345) (required)
- `-f <file_path>`: Path to the file within the diff (required)
- `-l <line_number>`: Line number, 1-based (required)
- `-m "text"`: The comment message (required)
- `-i <intent>`, `--intent <intent>`: Comment intent: `nit`, `blocking`, `aside`, `preexisting`, `code-style`, `clarify`, `context`
- `--line-length <N>`: Number of lines the comment spans (default 0 = single line)
- `--on-old-file`: Comment on the old/left side of the file instead of new/right
- `--draft`: Save as draft for batch publishing with `submit-review`
- `--dry-run`: Preview without making changes

**Examples:**

Single-line comment:
```bash
meta phabricator.diff inline-comment -n D12345 -f action.js -l 42 -m "Fix this typo"
```

Single-line comment with intent:
```bash
meta phabricator.diff inline-comment -n D12345 -f action.js -l 42 --intent nit -m "Consider using const instead of let"
```

Multi-line comment (5 lines starting at line 10):
```bash
meta phabricator.diff inline-comment -n D12345 -f file.js -l 10 --line-length 5 --intent blocking -m "This entire block needs refactoring"
```

Comment on old version of file:
```bash
meta phabricator.diff inline-comment -n D12345 -f file.js -l 20 --on-old-file --intent aside -m "This was wrong before too"
```

Draft comment (not published immediately):
```bash
meta phabricator.diff inline-comment -n D12345 -f file.js -l 42 --draft -m "Consider refactoring this"
```

**Notes:**
- The file path must match exactly as it appears in the diff
- Line numbers refer to the new version of the file unless `--on-old-file` is specified
- By default, comments are published immediately
- Use `--draft` to keep the comment as a draft that you can review before publishing

**Code suggestions:** The meta CLI does not yet support `--suggested` / `--suggested-file`. To attach a code suggestion to an inline comment, use the Python script instead:
```bash
inline_comment.py D12345 file.js 42 --draft --message "Use const" --suggested "const x = 42;"
inline_comment.py D12345 file.js 42 50 --draft --message "Refactor this block" --suggested-file /tmp/suggestion.txt
```

### comments

Query all comments (both general and inline) from a diff.

**Usage:**
```bash
meta phabricator.diff comments -n <diff_number> [--drafts] [--all] [--signals-only] [--output FORMAT]
```

**Arguments:**
- `-n <diff_number>`: The diff number (e.g., D12345 or 12345) (required)
- `--drafts`: Show only your draft comments (default: published only)
- `--all`: Show all comments including drafts
- `--signals-only`: Show only inline signals (automated tooling messages like lint, clang-tidy, AI reviewer)
- `-o <format>`, `--output <format>`: Output format: `table` (default), `json`, `yaml`, `csv`

**Examples:**
```bash
meta phabricator.diff comments -n D12345
meta phabricator.diff comments -n D12345 --output json
meta phabricator.diff comments -n D12345 --drafts
meta phabricator.diff comments -n D12345 --all
meta phabricator.diff comments -n D12345 --signals-only
```

**Output:**
- Lists all comments (general and inline) with their comment IDs
- Shows resolution status
- Draft comments are shown when using `--drafts` or `--all`
- Includes author, content, and `location`; newer JSON/YAML output also includes structured inline range fields: `file`, `line_start`, `line_end`, `is_new_file`, and `side`
- Useful for finding comment IDs to pass to `resolve-comments`, `delete-comment`, or `reply-comment`
- With `--signals-only`: shows automated tooling inline messages (lint warnings, clang-tidy, Devmate Reviewer, etc.) with author, source (includes severity), resolution status, file path and line number (combined in `location`), and message content

**Compatibility guidance:**
- Treat `location` as the backward-compatible anchor for older CLI versions, and prefer structured range fields when present for precise source attribution.
- If structured range fields are absent or null, fall back to `location` and verify the selected source in Phabricator before making precise claims about targeted code.
- Automated signals such as Devmate Reviewer comments use the same structured fields when present, though they may be backed by signal attachments rather than human transaction comments.

### resolve-comments

Resolve unresolved inline comments on a diff.

**Usage:**
```bash
meta phabricator.diff resolve-comments -n <diff_number> [-c <comment_id>] [-u] [--dry-run]
```

**Arguments:**
- `-n <diff_number>`: Diff number (required)
- `-c <comment_id>`, `--comment-id <comment_id>`: Specific comment ID to resolve (resolves all if omitted)
- `-u`, `--unresolve`: Unresolve instead of resolve (undo a previous resolve)
- `--dry-run`: Preview without making changes

**Examples:**
```bash
# Resolve all unresolved comments
meta phabricator.diff resolve-comments -n D12345

# Resolve a specific comment
meta phabricator.diff resolve-comments -n D12345 -c 100027866894629

# Unresolve a previously resolved comment
meta phabricator.diff resolve-comments -n D12345 -c 100027866894629 --unresolve

# Preview what would be resolved
meta phabricator.diff resolve-comments -n D12345 --dry-run
```

**Finding comment IDs:**
- Use `meta phabricator.diff comments -n D12345` to list all comments with their IDs
- Look at the comment URL on Phabricator

### delete-comment

Delete a specific comment or all your draft comments on a diff.

**Usage:**
```bash
meta phabricator.diff delete-comment -n <diff_number> -c <comment_id>
meta phabricator.diff delete-comment -n <diff_number> --drafts
```

**Arguments:**
- `-n <diff_number>`: Diff number (required)
- `-c <comment_id>`, `--comment-id <comment_id>`: Specific comment ID to delete
- `--drafts`: Delete all your draft comments (inline and general)
- `--dry-run`: Preview what would be deleted without making changes

Note: Exactly one of `--comment-id` or `--drafts` must be provided.

**Examples:**
```bash
# Delete a specific comment
meta phabricator.diff delete-comment -n D12345 -c 100027866894629

# Delete all your draft comments on a diff
meta phabricator.diff delete-comment -n D12345 --drafts

# Preview what would be deleted
meta phabricator.diff delete-comment -n D12345 --drafts --dry-run
```

### reply-comment

Reply to a specific comment on a diff.

**Usage:**
```bash
meta phabricator.diff reply-comment -n <diff_number> -c <comment_id> -m "reply text" [--draft]
```

**Arguments:**
- `-n <diff_number>`: The diff number (required)
- `-c <comment_id>`, `--comment-id <comment_id>`: Comment ID to reply to (required)
- `-m "text"`: Reply message (required)
- `--draft`: Save reply as draft for batch publishing with `submit-review`
- `--dry-run`: Preview without making changes

**Examples:**
```bash
meta phabricator.diff reply-comment -n D12345 -c 100027866894629 -m "Fixed in the latest version"
meta phabricator.diff reply-comment -n D12345 -c 100027866894629 -m "Will address" --draft
```

### submit-review

Submit (publish) all pending draft comments on a diff as a single review activity.

**Usage:**
```bash
meta phabricator.diff submit-review -n <diff_number> [-m "overall comment"]
```

**Arguments:**
- `-n <diff_number>`: Diff number (required)
- `-m "text"`: Optional overall comment to include with the submission
- `--dry-run`: Preview without making changes

**Examples:**
```bash
# Publish all drafts
meta phabricator.diff submit-review -n D12345

# Publish with an overall comment
meta phabricator.diff submit-review -n D12345 -m "Overall looks good, minor nits"
```

**Typical workflow:**
```bash
# 1. Post draft inline comments
meta phabricator.diff inline-comment -n D12345 -f file.py -l 42 -m "Fix this" --draft

# 2. Optionally draft an overall comment
meta phabricator.diff comment -n D12345 -m "Summary of review" --draft

# 3. Submit all drafts at once
meta phabricator.diff submit-review -n D12345
```

## Python-Only Commands

The following commands are only available via the bundled Python scripts (no meta CLI equivalent yet).

### top_level_comment

Post a top-level (overall) comment on a diff. Works on both laptop and devserver via `jf graphql`.

**Usage:**
```bash
top_level_comment.py <diff_number> (--message "text" | --message-file path) [--draft] [--attach-inlines] [--ai-signature]
```

**Arguments:**
- `<diff_number>`: Diff number (e.g., D12345) (required)
- `--message "text"`: Comment text (mutually exclusive with `--message-file`)
- `--message-file <path>`: Path to file containing comment text (mutually exclusive with `--message`). Avoids shell emoji-escaping issues
- `--draft`: Save as draft instead of publishing immediately
- `--attach-inlines`: Also publish any pending inline draft comments. Cannot be combined with `--draft`
- `--ai-signature`: Append "Sent from Claude Code" signature

**Examples:**
```bash
# Publish a comment immediately
top_level_comment.py D12345 --message "Looks good overall"

# Publish verdict with inlines from a temp file
top_level_comment.py D12345 --message-file /tmp/preflight/D12345/verdict.txt --attach-inlines

# Save a draft comment
top_level_comment.py D12345 --message-file /tmp/preflight/D12345/verdict.txt --draft
```

### edit_draft_comment

Edit the text of an existing draft inline comment, optionally publishing it.

**Usage:**
```bash
edit_draft_comment.py --fbid <fbid> --message "new text" [--publish] [--unset-suggested-change] [--suggested "code" | --suggested-file path]
```

**Arguments:**
- `--fbid <fbid>`: Draft comment FBID to edit (required)
- `--message "text"`: New comment text (required)
- `--publish`: Optional flag to publish the comment after editing (skip draft state)
- `--unset-suggested-change`: Optional flag to strip any suggested change block attached to the draft
- `[--suggested "code"]`: Optional suggested replacement code to attach to the draft. Mutually exclusive with `--suggested-file`.
- `[--suggested-file path]`: Optional path to file containing suggested replacement code. Mutually exclusive with `--suggested`.

**Examples:**
```bash
# Edit a draft comment's text
edit_draft_comment.py --fbid 100027866894629 --message "Updated review comment"

# Edit and publish in one step
edit_draft_comment.py --fbid 100027866894629 --message "Ship it" --publish

# Attach a code suggestion to an existing draft
edit_draft_comment.py --fbid 100027866894629 --message "Use const here" --suggested "const x = 42;"
```

**Finding draft FBIDs:**
- Use `meta phabricator.diff comments -n D12345 --drafts` to list draft comments with their IDs

### get_comment_status

Get the current status of comments by their FBID.

**Usage:**
```bash
get_comment_status.py <fbid> [<fbid>...]
```

**Arguments:**
- `<fbid>`: Comment FBID(s) to query (space-separated)

**Examples:**
```bash
get_comment_status.py 100027866894629
get_comment_status.py 100027866894629 100027866894630
```

**Output:**
- Shows ID, content, line number, resolution status, and author for each comment
- Useful for checking if a comment has been resolved or finding comment details

## Comment Intents

Valid intent values for meta CLI (lowercase):
- `nit`: Minor issue, not blocking
- `blocking`: Must be addressed before landing
- `aside`: Additional information or observation
- `clarify`: Request for clarification
- `code-style`: Code style suggestion
- `context`: Author-provided context, no action required
- `preexisting`: Issue that existed before this diff
- Empty or omitted: No specific intent

## Other Diff Actions

For other diff actions, use the `meta` CLI:
- Accept a diff: `meta phabricator.diff accept -n D12345`
- Request changes: `meta phabricator.diff reject -n D12345`
- Abandon a diff: `meta phabricator.diff abandon -n D12345`
- Publish a diff: `meta phabricator.diff publish -n D12345`

## Example Workflow

```bash
# 1. List all comments on a diff
meta phabricator.diff comments -n D88180688

# 2. Resolve a specific comment by ID
meta phabricator.diff resolve-comments -n D88180688 -c 2371925779897005

# 3. Reply to a comment
meta phabricator.diff reply-comment -n D88180688 -c 2371925779897005 -m "Fixed"

# 4. Delete all your drafts before re-reviewing
meta phabricator.diff delete-comment -n D88180688 --drafts

# 5. Post new draft comments
meta phabricator.diff inline-comment -n D88180688 -f src/foo.py -l 42 --intent nit -m "Consider renaming" --draft

# 6. Publish all drafts
meta phabricator.diff submit-review -n D88180688
```

## Tips and Best Practices

1. **Use intents appropriately**: `blocking` for must-fix issues, `nit` for suggestions
2. **Use `--draft` for review workflows**: Post all comments as drafts, then publish with `submit-review`
3. **Use `--dry-run`**: Preview changes before committing to them
4. **Be specific in comments**: Clearly describe what needs to be changed

## Related Tools

- **meta phabricator.diff**: The primary CLI for diff interactions
- **jf action**: Alternative CLI for diff state transitions
- **Phabricator UI**: https://www.internalfb.com/diff/D{diff_number}
