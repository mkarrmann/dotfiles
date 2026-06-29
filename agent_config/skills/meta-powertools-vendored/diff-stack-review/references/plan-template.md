# Fix Plan Template

Use this template when generating the fix plan for diff stack review (sl amend mode).

For the non-destructive jf suggest mode, see [jf-suggest-workflow.md](./jf-suggest-workflow.md).

## Apply Modes

| Mode | Command | Effect | Best For |
|------|---------|--------|----------|
| **jf suggest** | `jf suggest --no-commit --diff D<num> -m "msg"` | Posts inline suggestion on Phabricator, no local changes | Non-destructive review, team collaboration |
| **sl amend** | `sl amend` | Modifies the local commit in place | When you want to fix and re-submit |

## Plan Structure (sl amend mode)

```markdown
# Fix Plan for Diff Stack

## Execution Order
Fixes applied starting from **oldest diff first** to maintain stack integrity.
Each diff validated and tested before proceeding to next.

## Pre-Requisites
- Clean working directory: `sl status`
- Pull latest: `sl pull --rebase`

---

## Diff N: DXXXXXXX - [title] (OLDEST/position)

### Issues to Fix
1. **[Type - SEVERITY]** Description
2. **[Type - SEVERITY]** Description

### Fix Steps
1. Navigate to commit:
   ```bash
   sl goto {COMMIT_HASH}
   ```

2. Fix Issue 1 - Description:
   - File: `filename.php:LINE`
   - Change: What to change
   - Before:
     ```php
     // old code
     ```
   - After:
     ```php
     // new code
     ```

3. Validate changes:
   ```bash
   arc lint -a
   buck test //path/to:test
   ```

4. Amend the commit:
   ```bash
   sl amend
   ```

5. ✅ Confirm all green before proceeding

---

[Repeat for each diff in order from oldest to newest]

---

## Post-Fix Steps

1. Return to stack head:
   ```bash
   sl goto {HEAD_COMMIT_HASH}
   ```

2. Rebase the stack:
   ```bash
   sl rebase -r "BASE::" -d master
   ```

3. Run full validation:
   ```bash
   arc lint -a
   ```

4. Review local changes:
   ```bash
   sl diff -r "BASE::"
   ```

5. **Compare Phabricator vs Local** (see comparison-template.md)

6. **WAIT FOR USER PERMISSION TO SUBMIT**

---

## Submission (REQUIRES EXPLICIT USER PERMISSION)

⚠️ **DO NOT SUBMIT WITHOUT USER SAYING "submit" OR "land"**

When user approves:
```bash
jf submit --draft --publish-when-ready -r "BASE::"
```
```

## Execution Progress Tracking

```markdown
## Execution Progress

| Diff | Status | Validation |
|------|--------|------------|
| DXXXXXXX | ✅ Fixed | ✅ All green |
| DXXXXXXX | 🔄 In Progress | - |
| DXXXXXXX | ⏳ Pending | - |

### Current: DXXXXXXX
Fixing issue N of M...
```
