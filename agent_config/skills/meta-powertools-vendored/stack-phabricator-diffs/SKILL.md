---
name: stack-phabricator-diffs
description: Pull and stack multiple Phabricator diffs in a dependency chain for efficient testing and landing. Handles diff queries, intelligent conflict resolution, progress tracking, and provides testing guidance. Use when managing multiple diffs that need to be tested together or landed in sequence.
---

## Version History
- v1.0.0 (2025-12-11): Initial release, supports querying, pulling, stacking with conflict resolution

# Stack Phabricator Diffs

## Overview

This skill provides a proven workflow for pulling multiple Phabricator diffs and stacking them in a dependency chain within a Sapling repository. This enables:
- **Batch Testing**: Test multiple related diffs together
- **Efficient Landing**: Land diffs in sequence without rebasing each time
- **Conflict Resolution**: Automatically resolve simple conflicts, get help for complex ones
- **Progress Tracking**: Clear visibility into what's been stacked

**Key Use Cases:**
- Stack your accepted diffs for testing before landing
- Stack someone else's diffs to test their changes
- Stack specific diffs to create a feature branch
- Rebase a set of diffs onto latest master

## Mandatory Workflow

### Step 1: Query for Diffs

Use `jf list` to query for diffs matching criteria:

**Common Queries:**
```bash
# Get a user's accepted diffs
jf list --status ACCEPTED --author <username>

# Get diffs needing revision
jf list --status NEEDS_REVISION --author <username>

# Get diffs under review
jf list --status NEEDS_REVIEW --author <username>
```

**Critical:** Use actual username from `whoami`, NOT `@me` (which doesn't work with jf).

**Status Options:**
- `ACCEPTED` - Diffs approved and ready to land
- `NEEDS_REVISION` - Diffs requiring changes
- `NEEDS_REVIEW` - Diffs awaiting review
- `CLOSED` - Landed diffs
- `ABANDONED` - Abandoned diffs

**Other Useful Flags:**
- `--limit N` - Limit number of results
- `--repositories REPO` - Filter by repository

### Step 2: Create Todo List

Before starting, use TodoWrite to create todos for all diffs:

```json
[
  {"content": "Pull D12345 - Description", "status": "pending", "activeForm": "Pulling D12345"},
  {"content": "Pull D12346 - Description", "status": "pending", "activeForm": "Pulling D12346"},
  {"content": "Pull D12347 - Description", "status": "pending", "activeForm": "Pulling D12347"}
]
```

**Benefits:**
- Track progress across the session
- User visibility into what's happening
- Easy to see what's completed/pending/failed

### Step 3: The Stacking Algorithm

**THIS IS THE CRITICAL PART - Follow This Exact Sequence**

For each diff, in order:

```bash
# A. Pull the diff
jf get D<diff_number>

# B. Get the commit hash (will be the newest/max)
commit_hash=$(sl log -r 'max(all())' -T '{node|short}')

# C. Rebase onto current location (THIS IS KEY)
sl rebase -s $commit_hash -d .

# D. If successful, goto the new commit
new_hash=$(sl log -r 'max(all())' -T '{node|short}')
sl goto $new_hash

# E. Update todo and repeat for next diff
```

**Critical Details:**
- **Always use `-s` (source) flag**, NOT `-b` (base)
- **Always use `-d .`** (current location as destination)
- **Always `sl goto` the new hash** after successful rebase
- **Update TodoWrite** after each successful stack

**Why This Works:**
- `-s` rebases just the source commit (what we want)
- `-d .` means "onto where I am now" (builds the chain)
- `sl goto` moves us to the tip so next diff stacks on top
- This creates: diff1 → diff2 → diff3 → ... (dependency chain)

### Step 4: Conflict Resolution

When `sl rebase` hits conflicts, follow this decision tree:

#### A. Analyze the Conflict

1. **Read conflict markers:**
   ```
   <<<<<<< dest:   <hash> - <description>
   [Current code - destination]
   =======
   [Diff's changes - source]
   >>>>>>> source: <hash> - <description>
   ```

2. **Understand what the diff is trying to do:**
   - Check the diff title/description
   - Look at the changes in context
   - Identify if it's refactoring, bug fix, new feature, etc.

#### B. Auto-Resolve Simple Conflicts

**Type 1: Import Statements**

If conflict is only in imports:
- **Resolution:** Merge both import lists
- Remove duplicates
- Keep alphabetically sorted

```java
// Keep both sets of imports, merge and deduplicate
import com.facebook.A;
import com.facebook.B;  // From destination
import com.google.C;    // From source
```

**Type 2: Code Already Refactored**

If diff modifies code that no longer exists (e.g., JUnit 4 → JUnit 5 migration):
- **Resolution:** Keep destination (current code)
- The diff's changes are obsolete
- Mark todo as "SKIPPED - code already refactored"

**Example:**
```
Diff tries to change: @Before → @BeforeEach (JUnit 4 → 5)
Destination already has: @BeforeEach
Resolution: Keep destination, skip diff's changes
```

**Type 3: Formatting/Style Only**

If both sides do the same thing with different style:
- **Resolution:** Choose version matching codebase patterns
- Usually keep destination (current code style)

**Type 4: Consistent Patterns**

If diff converts `for` to `forEach` and destination already uses `forEach`:
- **Resolution:** Keep destination for consistency
- The refactoring is already applied

#### C. Ask User for Complex Conflicts

**When logic differs between versions:**
- Show both versions clearly
- Explain what each side does
- Ask which to keep or how to merge

**Example:**
```
The diff changes the calculation from X to Y, but the destination
has changed it to Z.

Destination (current):
  result = value * 2 + offset;

Source (diff wants):
  result = value * 3;

Which version should we keep, or should we merge them?
```

#### D. After Resolving Conflict

```bash
# 1. Mark file as resolved
sl resolve --mark <file_path>

# 2. Continue the rebase
sl rebase --continue

# 3. The commit may have a new hash, get it
new_hash=$(sl log -r 'max(all())' -T '{node|short}')

# 4. Goto the new commit
sl goto $new_hash

# 5. Update TodoWrite
```

**Note:** If rebase says "not rebasing ..., destination already has all changes", the diff is a duplicate. Mark todo as completed/skipped.

### Step 5: Error Handling

| Error | Meaning | Action |
|-------|---------|--------|
| `nothing to rebase` | Commit already in right place | Just `sl goto` it and continue |
| `destination already has all changes` | Changes already applied | Mark todo skipped, continue |
| `hit merge conflicts` | Need conflict resolution | Follow conflict resolution process |
| `unable to get FBID for @me` | Invalid username | Use actual username from `whoami` |

### Step 6: Final Summary

After all diffs processed:

```bash
# Show the full stack
sl log -r 'ancestors(.) and not ancestors(master)' -T '{node|short} {desc|firstline}\n'

# Count stacked commits
sl log -r 'ancestors(.) and not ancestors(master)' -T '{node|short}\n' | wc -l

# Show current position
sl whereami
```

**Provide Summary:**
- Total diffs requested: X
- Successfully stacked: Y
- Skipped (with reasons): Z
- Current position: <commit hash>
- Next steps: Testing commands

## Testing the Stack

After stacking, suggest appropriate testing:

**For Java/Buck projects:**
```bash
# Run all tests in the module
buck test fbcode//fbjava/<module>/... --all

# Run specific test class
buck test 'fbcode//fbjava/<module>:<target>_test' -- TestClassName
```

**For other projects:**
- Suggest project-specific test commands
- Reference any test commands from diff test plans

## Landing the Diffs

After successful testing:

**Individual landing:**
```bash
jf land D<diff_number>
```

**Batch landing:**
- Land from bottom to top of stack
- Wait for each to land before landing next
- Monitor for any integration issues

## Common Patterns Learned

### Pattern 1: JUnit 4 → JUnit 5 Migration

**Indicators:**
- Conflict with `@Before` vs `@BeforeEach`
- `@Test` vs `@ParameterizedTest`
- `Arrays.asList(new Object[] {})` vs modern syntax

**Resolution:** Destination already migrated. Keep destination, skip diff.

### Pattern 2: Duplicate Refactorings

**Indicators:**
- Diff converts loops to lambda style
- Destination already uses lambda style

**Resolution:** Keep destination, mark diff as "changes already applied"

### Pattern 3: Import Conflicts

**Always merge both sides** unless imports are for code that no longer exists.

## TodoWrite Integration

**Track progress throughout:**

```json
[
  {"content": "Pull D12345 - Fix bug", "status": "completed", "activeForm": "Pulling D12345"},
  {"content": "Pull D12346 - Add feature", "status": "in_progress", "activeForm": "Pulling D12346"},
  {"content": "Pull D12347 - Refactor", "status": "pending", "activeForm": "Pulling D12347"}
]
```

**Update after each diff:**
- Mark `in_progress` when starting
- Mark `completed` when successfully stacked
- Add note if skipped: "Pull D12345 - SKIPPED (code already refactored)"

## Example Invocations

Users might say:
- "Stack my accepted diffs"
- "Pull all of Alice's NEEDS_REVISION diffs and stack them"
- "Stack D12345, D12346, and D12347"
- "Get Bob's diffs with status ACCEPTED and stack them"

**Always confirm parameters if not explicit:**
- Which user?
- Which status filter?
- Which diffs specifically?

## Checklist Before Starting

- [ ] Confirmed query parameters (user, status, or specific diffs)
- [ ] Repository is clean (`sl status` shows no uncommitted changes)
- [ ] Created TodoWrite list with all diffs
- [ ] Understand conflict resolution strategy

## Checklist After Completion

- [ ] Verified stack with `sl log`
- [ ] Counted successful vs skipped diffs
- [ ] Provided testing commands
- [ ] Noted current position for user
- [ ] Updated TodoWrite with final status

## When NOT to Use This Skill

**Don't use for:**
- Landing individual diffs (just use `jf land`)
- Creating new diffs (use `submitting-diffs` skill)
- Amending existing diffs (use `jf amend`)
- Simple rebases (just use `sl rebase` directly)

## Reference Commands

```bash
# Query diffs
jf list --status ACCEPTED --author <username>

# Pull a diff
jf get D<number>

# Stack it (the critical sequence)
sl rebase -s <commit> -d .
sl goto <new_hash>

# Check stack
sl log -r 'ancestors(.) and not ancestors(master)' -T '{node|short} {desc|firstline}\n'

# Test
buck test fbcode//<path>/... --all

# Land
jf land D<number>
```

## Summary

**The Magic Formula:**
1. Query → Get list of diffs
2. Create todos → Track progress
3. For each diff: **Pull → Rebase -s onto . → Goto new hash**
4. Resolve conflicts intelligently
5. Update todos as you go
6. Provide summary and testing guidance

**Key Success Factors:**
- Use `-s` (source) not `-b` (base)
- Always rebase onto current location with `-d .`
- Always `sl goto` after successful rebase
- Auto-resolve simple conflicts (imports, obsolete code)
- Ask user for complex conflicts
- Track everything with TodoWrite
