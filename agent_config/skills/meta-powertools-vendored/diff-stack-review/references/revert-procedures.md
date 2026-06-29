# Revert Procedures

## Quick Commands Reference

| Scenario | Command |
|----------|---------|
| Discard all uncommitted changes | `sl revert --all` |
| Remove untracked files | `sl purge --files` |
| Undo last amend | `sl unamend` |
| Go to specific commit | `sl goto {HASH}` |
| Hide commits (soft delete) | `sl hide -r {REVSET}` |
| Re-sync diff from Phabricator | `jf sync DXXXXXXX` |

## Full Revert Procedure

When user says "revert" or "undo all changes":

```markdown
## Reverting All Changes

⚠️ This will discard ALL uncommitted changes and restore the stack.

### Step 1: Save Current Position
```bash
sl log -r . --template "{node|short}\n"
```

### Step 2: Discard Uncommitted Changes
```bash
sl revert --all
```

### Step 3: Clean Untracked Files
```bash
sl purge --files
```

### Step 4: Return to Stack Head
```bash
sl goto {ORIGINAL_HEAD_COMMIT_HASH}
```

### Step 5: Verify Clean State
```bash
sl status
sl ssl
```

### Step 6: Confirm to User
- ✅ All uncommitted changes discarded
- ✅ Stack restored to original state
- ✅ Currently at: {commit hash and title}
```

## Partial Revert (Some diffs amended)

### Option A: Unamend (Recommended)
```bash
sl goto {COMMIT_HASH}
sl unamend
```

### Option B: Manual revert
```bash
sl revert --all
sl amend
```

### Option C: Re-pull from Phabricator (Nuclear)
```bash
sl hide -r "BASE::"
jf sync DXXXXXXX  # For each diff
```

## Confirmation Before Revert

Always confirm with user:

```
⚠️ You requested to revert all changes.

Current state:
- Diffs modified: X of Y
- Uncommitted changes: N files

This will:
- Discard all uncommitted changes
- Restore amended commits to original state
- Return to the stack head

Are you sure? [Yes, revert all] [No, continue]
```
