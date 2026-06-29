# Pre-Submission Comparison Template

Show user what changed in each diff compared to Phabricator before submission.

## Process for Each Diff

For each diff ID in the stack:

1. **Fetch Phabricator version**:
   Use `get_phabricator_diff_details` with `include_raw_diff=true`

2. **Get local version**:
   ```bash
   sl goto {COMMIT_HASH}
   sl diff -c .
   ```

3. **Present comparison** in this format:

### D{DIFF_ID}: {COMMIT_TITLE}

**Files Modified in This Review Session:**

| File | Changes Summary |
|------|-----------------|
| `MSPManager.php` | +15 lines, -8 lines |
| `MSPException.php` | +3 lines, -1 line |

**Detailed Changes:**

#### `MSPManager.php`

**Issue Fixed:** SQL Injection vulnerability (Line 42)

```diff
- $query = "SELECT * FROM users WHERE id = " . $userId;
+ $query = "SELECT * FROM users WHERE id = %s";
+ $result = query($query, $userId);
```

## Summary Display Template

# Pre-Submission Review: Changes Made During This Session

## Summary

| Diff ID | Title | Files Changed | Lines Added | Lines Removed |
|---------|-------|---------------|-------------|---------------|
| D90939395 | [msp] Exception class | 2 | 5 | 2 |
| D90939396 | [msp] Manager class | 3 | 45 | 18 |

## Detailed Changes Per Diff

[Show detailed changes for each diff]

---

**Ready to submit?**
[Submit All] [Review Individual Diffs] [Cancel]

## Implementation Notes

1. **Use get_phabricator_diff_details** with:
   - `phabricator_diff_number`: The diff ID
   - `include_raw_diff`: true
   - `include_diff_summary`: true

2. **Compare with local state** by going to each commit and getting diff

3. **Focus on meaningful changes**: Group by issue fixed, show before/after

4. **Handle no-change scenarios**: Note "No changes made" for unmodified diffs
