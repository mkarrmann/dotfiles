# Action Menu & Apply Workflows

Action menu options, jf suggest workflow, comment posting templates, and test plan suggestions.

## Action Menu

After presenting the review report, show the action menu. The following options are **always present**:

1. **Save to paste** — save the full report to a Phabricator paste via `pastry`
2. **Apply fixes via jf suggest** — post code fix suggestions on Phabricator (non-destructive, reviewable)
3. **Suggest improved test plans** — post proposed test plan as a comment on each diff with a **Weak** or **Missing** test plan (only shown when weak/missing test plans exist)

Additional options may be added based on context (e.g., `sl amend` for the diff author, apply specific fixes, post findings as comments). Use judgment, but never omit the three above.

Note: `sl amend` is only offered when the user is the diff author — it modifies their commits directly.

## jf suggest Workflow (Non-Destructive)

**How jf suggest works:** `jf suggest` posts **working copy changes** as inline suggestions on Phabricator. It does NOT accept `--file`, `--line`, or any content arguments. The workflow is: (1) `sl goto` the target commit, (2) **edit the file** with the proposed fix using the Edit tool, (3) run `jf suggest --no-commit --diff D<number> -m "description"`, (4) `sl revert --all` to clean up. The working copy diff IS the suggestion.

**How jf action works:** `jf action` posts **text comments** on a diff. Use it for test plan suggestions, findings summaries, or any non-code feedback. It takes a `-m "message"` argument with the comment text. No working copy manipulation needed.

### Pre-flight Check

Before ANY apply operation:

1. Check working copy state:
   ```bash
   sl status
   ```
   If there are modified, added, removed, or untracked files, **STOP** and tell the user:
   "Working copy is not clean. Please commit or shelve changes before applying suggestions."

2. Record current position for restoration:
   ```bash
   sl log -r . -T '{node|short}\n'
   ```
   Save this as `original_commit`.

### Apply Loop

Parse the user's selection:
- Option 1: Select all Critical findings
- Option 2: Select all Critical + Major findings
- Option 3 ("apply 1,3,5"): Extract finding IDs [1, 3, 5]

For each selected finding, process **one at a time** (not batched):

#### Step 1: Navigate to the target commit
```bash
sl goto <commit_hash_for_finding_diff>
```

#### Step 2: Verify clean state
```bash
sl status
```
If not clean (e.g., from a failed previous suggestion), revert first:
```bash
sl revert --all
```

#### Step 3: Apply the fix

Edit the file with the proposed change using the Edit tool.

#### Step 4: Post the suggestion to Phabricator
```bash
jf suggest --no-commit --diff D<number> -m "diff-stack-review: <category> — <one-line description>"
```

#### Step 5: Revert the working copy
```bash
sl revert --all
```

#### Step 6: Verify clean
```bash
sl status
```

### Post-Apply

After all suggestions are posted:

1. Return to original position:
   ```bash
   sl goto <original_commit>
   ```

2. Print summary:
   ```text
   Suggestions posted:
     [1] D111 src/Foo.php:42 — Missing Error Handling
     [3] D333 src/Baz.php:88 — Magic Number
     [5] D222 src/Bar.php:15 — N+1 Query

   View on Phabricator: each diff's page will show the suggestions inline.
   ```

## Post as Inline Comments

Post each finding as an **inline Phabricator comment on the exact line** using `meta phabricator.diff`. Do NOT post all findings in a single top-level comment with line numbers in brackets.

### Step 1: Post top-level summary

```bash
meta phabricator.diff comment -n D<number> -m "diff-stack-review: <verdict> | <N> critical, <N> major, <N> minor | <paste_link>"
```

### Step 2: Post each finding as an inline comment

For each finding, post an inline comment on the exact line.

**Comment length rules (CRITICAL):**
- Minor/nit: max 280 characters (~3 short sentences). 1 sentence total, no fix needed.
- Major/critical: max 450 characters to allow short code fix snippets.
- Problem: 1 sentence. State what's wrong directly. No preamble, no hedging.
- Fix: 1 sentence or a short code snippet. Omit "Suggested fix:" prefix if the fix is a code snippet.
- Write in a natural, human voice — like a colleague, not a tool. No structured headers or bullet lists.

```bash
# Single-line finding
meta phabricator.diff inline-comment \
  -n D<number> \
  -f "<file_path>" \
  -l <line_number> \
  -i <intent> \
  -m "[diff-stack-review] <severity> — <category>

<1-2 sentence problem + fix>"

# Multi-line finding
meta phabricator.diff inline-comment \
  -n D<number> \
  -f "<file_path>" \
  -l <start_line> \
  --line-length=<number_of_lines> \
  -i <intent> \
  -m "[diff-stack-review] <severity> — <category>

<1-2 sentence problem + fix>"
```

**Good examples:**
- `"[diff-stack-review] Major — Missing null check\n\nQuery result used without checking for null — will throw if the row doesn't exist. Add a null guard before line 44."`
- `"[diff-stack-review] Minor — Magic number\n\nExtract 86400 to a named constant."`
- `"[diff-stack-review] Critical — SQL injection\n\n$userId is user input concatenated into the query. Use a parameterized query instead."`

**Bad examples (too long):**
- Multi-paragraph explanations of why the issue matters
- Full code_before/code_after blocks for one-line fixes
- "This could potentially lead to issues in production because..." hedging

**Severity → Intent mapping:**
| Severity | Intent |
|----------|--------|
| Critical | `blocking` |
| Major    | `blocking` |
| Minor    | `nit` |

**Rules:**
- Use the exact file path from the diff — use `meta phabricator.diff files -n D<number>` to discover correct paths
- Valid intents: `blocking`, `nit`, `aside`, `clarify`, `context`, `preexisting`, `code-style`

### Full flag reference

| Flag | Required | Description |
|------|----------|-------------|
| `-n` | yes | Diff number (e.g. `D12345678`) |
| `-f` | yes | File path as shown in diff changeset |
| `-l` | yes | Line number (1-based, new/right side by default) |
| `-m` | yes | Comment text |
| `-i` | no | Intent: `blocking`, `nit`, `aside`, `clarify`, `context`, `preexisting`, `code-style` |
| `--line-length` | no | Number of lines the comment spans (default 0 = single line) |
| `--on-old-file` | no | Comment on old/left side instead of new/right |
| `--draft` | no | Save as draft (batch-publish with `submit-review`) |

### Supporting commands

| Command | Purpose |
|---------|---------|
| `meta phabricator.diff files -n D<number>` | List file paths available in the diff changeset |
| `meta phabricator.diff comments -n D<number>` | List existing comments |
| `meta phabricator.diff comments -n D<number> --drafts` | List draft comments |
| `meta phabricator.diff delete-comment -n D<number> --drafts` | Delete all draft comments |
| `meta phabricator.diff delete-comment -n D<number> -c <comment_id>` | Delete a specific comment |
| `meta phabricator.diff resolve-comments -n D<number>` | Resolve all unresolved inline comments |
| `meta phabricator.diff resolve-comments -n D<number> --comment-id=<id>` | Resolve a specific comment |
| `meta phabricator.diff reply-comment -n D<number> -c <comment_id> -m "reply"` | Reply to a comment |
| `meta phabricator.diff submit-review -n D<number> -m "summary"` | Publish all draft comments + summary as a single review |

No working copy manipulation needed for this option.

## Suggest Improved Test Plans

For each diff with a Weak or Missing test plan assessment, post a comment with a proposed improved test plan:

```bash
jf action D<number> -m "$(cat <<'EOF'
[diff-stack-review] Suggested test plan improvement

Current test plan assessment: <Weak/Missing>
Gap: <what's missing>

Suggested test plan:
---
Test Plan:
- <specific test command with target>
- <specific scenario tested and result>
- <additional coverage for identified gaps>
---

This is a suggestion — copy the test plan above into your commit message if it looks right.
EOF
)"
```

## jf suggest Command Reference

```bash
# From uncommitted working copy changes
jf suggest --no-commit --diff D<number> -m "description"

# From a committed change
jf suggest -r <rev> --diff D<number> -m "description"
```

Key flags:
- `--no-commit`: Use working copy changes (don't require a commit)
- `--diff D<number>`: Target Phabricator diff
- `-m "message"`: Description of the suggestion
- `--context N`: Lines of context around the change (default: 2)

## Related jf Commands

### `jf inlines`
```bash
jf inlines --latest -r <rev> --skip-author
```
View inline comments on diffs. Flags: `--latest`, `--skip-author`, `--include-resolved`, `-r <rev>`.

### `jf action`
```bash
jf action D<number> -m "comment text"
```
Post comments or take actions on diffs.

### `jf export`
```bash
jf export --diff D<number>
```
Show raw diff content from Phabricator.

## Error Recovery

If `jf suggest` fails:

1. Revert the working copy:
   ```bash
   sl revert --all
   ```

2. Check if the diff is in a submittable state:
   ```bash
   jf diff-properties D<number>
   ```

3. If the diff was closed/landed, skip it and move to the next finding.

4. If there's a transient error, retry once:
   ```bash
   jf suggest --no-commit --diff D<number> -m "description"
   ```

5. If it fails again, report the error and continue with remaining findings.
