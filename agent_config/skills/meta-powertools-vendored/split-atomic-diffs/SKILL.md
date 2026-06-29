---
name: split-atomic-diffs
description: Split uncommitted changes into atomic diffs and submit them as drafts. Analyzes changed files, groups related changes, uses interactive commit or shelving to create focused commits, and submits each as a draft. Triggers on split changes, atomic diffs, split diff, break up changes, multiple diffs, separate commits
---

# Splitting Uncommitted Changes into Atomic Diffs

## Overview

This skill helps you take a large set of uncommitted changes and split them into smaller, focused, atomic diffs. Each atomic diff should:
- Do ONE thing well
- Be reviewable independently
- Have a clear, single purpose
- Build successfully on its own

**Use this when you have many changes that should be separate diffs.**

---

## Mandatory Workflow (Strict Order)

### 1. Create and Submit a Backup Diff (FIRST - CRITICAL SAFETY STEP)

**Before doing ANYTHING else, create a backup diff in Phabricator.**

#### First, determine your starting state:

```bash
hg status
```

- If output is **empty** → you're in a **COMMITTED state**
- If output shows **modified/added files** → you're in an **UNCOMMITTED state**

#### If starting from a COMMITTED state (no uncommitted changes):

```bash
# Simply submit the current commit as a draft backup
# DO NOT change the commit title or metadata!
jf submit --draft
```

**Important:** Do NOT modify the commit message or title. Submit it exactly as-is.

#### If starting from UNCOMMITTED changes:

```bash
# Stage all new files first
hg add .

# Create a backup commit with all changes
hg commit -m "[BACKUP] All changes before splitting - DO NOT LAND"

# Submit backup as a draft diff to Phabricator
jf submit --draft
```

**The output will show the diff number. Save it:**
```
Created/updated diff D123456789
```

**Record the backup diff number:**
```bash
# Example: BACKUP_DIFF=D123456789
echo "Backup diff: D123456789"  # Replace with actual number from output
```

**Why submit the backup as a draft FIRST:**
- Remote backup in Phabricator - survives local issues
- Easy to reference and recover from
- Can be abandoned after successful split
- Provides a clear audit trail
- **Protects against any mistakes in subsequent steps**

**After backup is submitted, uncommit to restore working directory:**
```bash
hg uncommit
```

Now your changes are back in the working directory, but you have a backup diff in Phabricator you can recover from if needed.

**Recovery (if something goes wrong at any point):**
```bash
# Download the backup diff and restore
jf download D123456789
hg uncommit
# All your original changes are restored!
```

### 2. Check Status and Understand Changes

**Now that your backup is safe, analyze what you're working with:**

```bash
hg status
```

**Then review the actual changes:**
```bash
hg diff
```

**Group files mentally by purpose:**
- Refactoring changes
- New feature code
- Bug fixes
- Test additions
- Config/build changes

### 3. Plan Your Atomic Diffs

Before splitting, create a plan. Each diff should be:

| Criteria | Good | Bad |
|----------|------|-----|
| Scope | Single feature/fix | Multiple unrelated changes |
| Reviewability | Easy to understand | Requires context from other diffs |
| Build | Passes independently | Depends on uncommitted code |
| Revertability | Can be reverted cleanly | Would break other changes |

**Example split plan:**
```
Backup: D123456789 (DO NOT LAND - for recovery only)
Diff 1: [MyApp][Refactor] Extract utility function
Diff 2: [MyApp][Feature] Add new API endpoint (depends on Diff 1)
Diff 3: [MyApp][Test] Add unit tests for new endpoint (depends on Diff 2)
Diff 4: [MyApp][Config] Update feature flags (depends on Diff 3)
         ↑
         Same first prefix tag across all atomic diffs!
```

### 4. Choose Your Splitting Strategy

#### Strategy A: Interactive Commit (Recommended for Mixed File Changes)

Use when changes within the same file need to be split:

```bash
hg commit -i
```

This opens an interactive interface where you can:
- Select specific files to include
- Select specific hunks (code chunks) within files
- Skip changes to include in later commits

**Navigation:**
- `y` = include this hunk
- `n` = skip this hunk
- `s` = split this hunk into smaller pieces
- `q` = quit and commit selected hunks
- `?` = help

#### Strategy B: Shelving (Recommended for Separating by File)

Use when you want to commit some files now, others later:

**Step 1: Shelve changes you want to commit later:**
```bash
hg shelve --name "remaining-changes" <file1> <file2> ...
```

**Step 2: Commit current changes:**
```bash
hg commit -m "First atomic change"
```

**Step 3: Unshelve and repeat:**
```bash
hg unshelve "remaining-changes"
```

#### Strategy C: Manual File Selection

For simple cases with distinct file groups:

```bash
# Commit only specific files
hg commit <file1> <file2> -m "First atomic change"

# Then commit remaining files
hg commit <file3> <file4> -m "Second atomic change"
```

### 5. Stage New/Deleted Files First

**For new files (? in status):**
```bash
hg add <file1> <file2> ...
```

**For deleted files (! in status):**
```bash
hg rm <file1> <file2> ...
```

---

## Verification Strategies for Atomic Diffs (Multi-Codebase)

### General Principles
- **Verify each atomic diff independently.**
- **Choose verification commands based on the codebase, language, and required codegen/tools.**
- **Document the exact verification steps in your test plan for each diff.**

### Common Verification Workflows

#### 1. Standard Buck/Arc Workflow (fbsource, most C++/Python/Hack)
- Format and lint: `arc lint --apply-patches`
- Build: `buck build <target>`
- Test: `buck test <target>`
- Type check (Python/Hack): `arc pyre check-changed-targets` or `hh`

#### 2. Codegen-Dependent Workflows (e.g. Logger, WWW, Hack)
- **Run codegen tools before building/testing:**
    - Logger: `phps CodegenLogger <LoggerConfig>`
    - Meerkat: `meerkat`
    - Hack: `hackfmt -i <file.php>`
- **If local codegen fails or is unavailable:**
    - Validate syntax and types locally (`hh`, `hackfmt`, etc.)
    - Submit as draft; let CI (Sandcastle) run codegen and verify generated files.
- **Always check CI results for codegen artifacts and build/test status.**

#### 3. Multi-Repository/Stacked Diff Workflows
- **Validate each repo independently:**
    - Configerator: `conf build`, `arc lint --apply-patches`
    - FBSource: `buck build <target>`, `arc lint --apply-patches`
- **Document cross-repo dependencies and expected build failures.**
- **Deploy foundational changes first, then dependent diffs.**

#### 4. RACER/UTC-Generated Diffs
- **Python:**
    - Format: `pyfmt <file.py>`
    - Type check: `arc pyre check-changed-targets`
    - Lint: `arc lint --apply-patches`
    - Test: `buck test <test_target>`
- **JavaScript/Flow:**
    - Format: `prettier --write <file.js>`
    - Type check: `flow`
    - Lint: `arc lint --apply-patches`
    - Test: `jest <test_file_path>`
- **Hack:**
    - Format: `hackfmt -i <file.php>`
    - Type check: `hh`
    - Lint: `arc lint --apply-patches`
    - Test: `t <TestClassName> --timeout 400`
- **Run test quality graders if modifying test files.**

#### 5. Diff Signals and Stale Codegen
- **Rebase to latest base revision before verification.**
- **Run verification and codegen (e.g. Meerkat) to catch stale artifacts.**
- **Sandcastle CI jobs (arc rebuild) will run verification and codegen, and provide patches if needed.**

### Best Practices
- **Always run the full verification sequence for your codebase before submitting.**
- **If codegen or build fails locally, document what you verified and rely on CI for the rest.**
- **Monitor CI jobs for codegen, build, and test failures after submission.**
- **Update your test plan with the actual commands and results.**
- **For complex stacks, document dependency order and expected build/test status for each diff.**

### Example Test Plan (Multi-Codebase)
```markdown
Test Plan:
- Ran `arc lint --apply-patches` and `buck build fbsource//path/to/target` (fbsource)
- Ran `phps CodegenLogger <LoggerConfig>` and `meerkat` (logger codegen)
- Ran `conf build` and `arc lint --apply-patches` (configerator)
- Ran `pyfmt` and `arc pyre check-changed-targets` (Python)
- Submitted as draft; verified Sandcastle CI jobs passed for codegen, build, and tests
```

### References
- [Devserver Codegen Limitations and CI Fallback]
- [Enhanced Diff Submission Workflow with Mandatory Codegen]
- [Multi-Repository RACER Task Coordination]
- [Diff Signals]

---

## Submit All Atomic Diffs as Drafts with Correct Dependencies

**After creating all atomic commits, submit them as a properly linked stack:**

```bash
jf submit --draft
```

This submits all local commits as draft diffs. **The dependency chain is automatically established based on commit order** - each diff depends on the one before it.

### Verifying Dependencies After Submission (CRITICAL)

**After submitting, ALWAYS verify the dependencies are correctly set:**

```bash
# View your stack and verify dependency chain
sl fssl
```

The output should show your diffs in order with their relationships. Look for the dependency indicators.

**Alternatively, check each diff in Phabricator:**
1. Go to each diff URL (e.g., https://www.internalfb.com/diff/D234567890)
2. Look for "Depends on" in the diff header
3. Verify each diff (except the first) shows the correct parent dependency

### Fixing Missing or Incorrect Dependencies

**If dependencies are NOT correctly set, fix them manually:**

#### Option 1: Using jf to set dependencies
```bash
# Re-submit the stack to fix dependencies
jf submit --draft --update-all
```

#### Option 2: Using arc to explicitly set dependencies
```bash
# Set dependency for a specific diff
arc diff --depends-on D<parent_diff_number>

# Example: Make D234567891 depend on D234567890
cd <repo>
hg update <commit_for_D234567891>
arc diff --depends-on D234567890 --update
```

#### Option 3: Set dependencies in Phabricator UI
1. Go to the child diff (e.g., https://www.internalfb.com/diff/D234567891)
2. Click "Edit Diff" or find the dependency section
3. Add the parent diff number in "Depends On" field
4. Save changes

#### Option 4: Edit diff summary to include dependency
```bash
# Update the diff with dependency in summary
jf submit --draft -r <rev> --message "Summary text

Depends on D<parent_diff_number>"
```

### Submitting One at a Time with Dependencies

**For more control, submit diffs individually with explicit dependencies:**

```bash
# Check your commits
hg log -r 'draft()'

# Submit the first (base) diff - no dependency needed
hg update <first_commit>
jf submit --draft
# Note the diff number: D234567890

# Submit the second diff with dependency on first
hg update <second_commit>
jf submit --draft --depends-on D234567890
# Note the diff number: D234567891

# Submit the third diff with dependency on second
hg update <third_commit>
jf submit --draft --depends-on D234567891
# And so on...
```

---

## Stacked Diffs

When creating diff on top of existing diff:

**Check parent first:**
```bash
sl fssl
```

**Copy from parent:**
- Prefix tags (keep consistent)
- Task ID
- Reviewers

**Example:**
- Parent: `[IG4A][MVVM][Homecoming] Add UiState - T12345`
- Child: `[IG4A][MVVM][Homecoming] Add ViewModel - T12345` (Depends on parent)

### First Prefix Tag Rule (IMPORTANT)

**All atomic diffs (except the backup) MUST share the same first prefix tag.**

This ensures:
- Diffs are easily identifiable as related
- Consistent filtering/searching in Phabricator
- Clear ownership and area identification

**Good example:**
```
[MyApp][Refactor] Extract utility function
[MyApp][Feature] Add new endpoint (Depends on D1)
[MyApp][Test] Add unit tests (Depends on D2)
  ↑
  Same first prefix tag!
```

**Bad example:**
```
[Utils][Refactor] Extract utility function   ← Different first tag!
[API][Feature] Add new endpoint              ← Different first tag!
[Test] Add unit tests                        ← Different first tag!
```

### Finding Prefix Tags When No Parent Diff Exists

If there's no parent diff in your current stack, search your previous diff history for consistent prefix tags:

**Search your recent diffs:**
```bash
# View your recent diff history
sl log --limit 20

# Or search Phabricator for your diffs in the same area
# https://www.internalfb.com/intern/diffs/?authors=<your_username>
```

**Search by file path to find related diffs:**
```bash
# Find diffs that touched the same files/directories
hg log --template '{desc|firstline}\n' <path/to/file_or_directory> | head -20
```

**Check blame for existing conventions:**
```bash
# See who modified the file and their commit messages
hg blame <file> | head -20

# View recent commits in the directory
hg log --template '{desc|firstline}\n' -l 10 <directory/>
```

**Common prefix tag patterns to look for:**
- Feature tags: `[IG4A]`, `[FBLite]`, `[Messenger]`, `[WhatsApp]`
- Architecture tags: `[MVVM]`, `[MVI]`, `[Clean]`
- Area tags: `[Homecoming]`, `[Feed]`, `[Stories]`, `[Reels]`
- Type tags: `[Refactor]`, `[Feature]`, `[BugFix]`, `[Test]`

**If no existing convention found:**
- Create a sensible prefix based on the feature/area
- **Use this SAME first prefix for ALL atomic diffs**
- Be consistent across all diffs in your split
- Document the new convention in your team's wiki

### Consistency rules for stacked diffs:
1. **Use the SAME first prefix tag across ALL atomic diffs**
2. Use the same additional prefix tags across all diffs in the stack
3. Reference the same Task ID
4. Add the same reviewers (they need context on the full stack)
5. **Ensure each diff explicitly depends on its parent diff**
6. Document dependencies in the summary: `Depends on D123456789`

**Submitting a stack with proper dependencies:**
```bash
# Submit all commits in the stack as drafts (dependencies auto-set)
jf submit --draft

# Verify the dependency chain
sl fssl

# If dependencies are wrong, re-submit with update
jf submit --draft --update-all

# View your stack
sl fssl

# Update a specific diff in the stack
jf submit --draft -r <rev>
```

---

## Diff Dependency Management (CRITICAL SECTION)

### Understanding Diff Dependencies

When you create a stack of diffs, each diff (except the first) should **depend on** the previous diff. This:
- Ensures diffs are reviewed and landed in correct order
- Prevents landing a child diff before its parent
- Makes the relationship between diffs clear to reviewers
- Allows Phabricator to track the stack properly

### Dependency Chain Visualization

```
D234567890: [MyApp][Refactor] Extract utility function
    ↓ (D234567891 depends on this)
D234567891: [MyApp][Feature] Add new API endpoint
    ↓ (D234567892 depends on this)
D234567892: [MyApp][Test] Add unit tests for new endpoint
```

### How Dependencies Are Set

#### Automatic (Preferred)
When you use `jf submit --draft` on a stack of commits, dependencies are automatically set based on commit ancestry:

```bash
# Creates commits in order: commit1 <- commit2 <- commit3
hg commit -m "[MyApp][Refactor] First change"
hg commit -m "[MyApp][Feature] Second change"
hg commit -m "[MyApp][Test] Third change"

# Submit all - dependencies auto-set based on commit order
jf submit --draft
```

#### Manual (When Auto Fails)
Sometimes automatic dependency detection fails. Set dependencies manually:

```bash
# Using --depends-on flag
jf submit --draft --depends-on D<parent_diff>

# Using arc diff
arc diff --depends-on D<parent_diff> --update

# Adding to diff summary
# Include "Depends on D<parent_diff>" in your summary
```

### Verifying Dependencies (ALWAYS DO THIS)

**After every stack submission, verify dependencies:**

```bash
# Method 1: Use sl fssl to see stack structure
sl fssl

# Method 2: Check each diff URL in Phabricator
# Look for "Depends on Dxxxxxxx" in the diff header
```

**What to look for in `sl fssl` output:**
```
Stack:
  D234567892 [MyApp][Test] Add unit tests...
    ↳ depends on D234567891
  D234567891 [MyApp][Feature] Add new API...
    ↳ depends on D234567890
  D234567890 [MyApp][Refactor] Extract utility...
    ↳ (base)
```

### Fixing Broken Dependencies

#### Scenario 1: Missing Dependency
A diff should depend on another but doesn't show the relationship.

```bash
# Fix by re-submitting with explicit dependency
hg update <commit_for_child_diff>
jf submit --draft --depends-on D<parent_diff_number>

# Or use arc
arc diff --depends-on D<parent_diff_number> --update
```

#### Scenario 2: Wrong Dependency
A diff depends on the wrong parent.

```bash
# Go to Phabricator UI
# 1. Open the diff: https://www.internalfb.com/diff/D<child_diff>
# 2. Edit the diff metadata
# 3. Remove incorrect dependency
# 4. Add correct dependency
# 5. Save

# Or re-submit the stack
jf submit --draft --update-all
```

#### Scenario 3: Circular Dependency
Two diffs incorrectly depend on each other.

```bash
# This should never happen - fix by:
# 1. Identify which diff should be the parent
# 2. Remove the incorrect reverse dependency in Phabricator UI
# 3. Ensure only child -> parent dependency exists
```

### Dependency Best Practices

| Practice | Why |
|----------|-----|
| Always verify after submit | Catch dependency issues early |
| Use `jf submit` for stacks | Auto-sets dependencies correctly |
| Check `sl fssl` output | Shows visual dependency chain |
| Include "Depends on" in summary | Documentation and backup |
| Land diffs in order | Respect the dependency chain |
| Don't skip diffs when landing | Breaks the chain for reviewers |

### Common Dependency Issues Table

| Issue | Symptom | Fix |
|-------|---------|-----|
| No dependency set | Child diff shows no "Depends on" | Re-submit with `--depends-on` |
| Wrong parent | Diff depends on unrelated diff | Edit in Phabricator UI |
| Broken chain | Middle diff missing dependency | Re-submit entire stack with `--update-all` |
| Orphaned diff | Diff has no parent when it should | Add dependency via `arc diff --depends-on` |
| Stale dependency | Parent diff was abandoned | Update to depend on new parent |

---

## Final Output Format

**After successful submission, provide a summary referencing the backup diff and dependency chain:**

```
## Split Complete

**Backup Diff (DO NOT LAND):** D123456789
  - Contains all original changes for recovery
  - Abandon after atomic diffs are landed

**Atomic Diffs Created (with dependencies):**
1. D234567890: [MyApp][Refactor] Extract utility function (base - no dependency)
2. D234567891: [MyApp][Feature] Add new API endpoint
   └── Depends on: D234567890 ✓
3. D234567892: [MyApp][Test] Add unit tests for new endpoint
   └── Depends on: D234567891 ✓
                ↑
                Same first prefix tag [MyApp] across all diffs!

**Dependency Chain Verified:** ✓
  D234567890 ← D234567891 ← D234567892

**Next Steps:**
1. Review and land atomic diffs in order (D234567890 first, then D234567891, then D234567892)
2. After all atomic diffs are landed, abandon backup diff D123456789
```

---

## Cleanup After Successful Landing

**Once all atomic diffs are landed, abandon the backup diff:**

```bash
# Abandon the backup diff in Phabricator
arc diff-abandon D123456789

# Or use the Phabricator UI:
# 1. Go to https://www.internalfb.com/diff/D123456789
# 2. Click "Abandon Diff"
```

**Also clean up local backup commit if still present:**
```bash
hg hide <backup-commit-hash>
```

---

## Detailed Splitting Examples

### Example 1: Splitting from UNCOMMITTED Changes

**Original status:**
```
M src/utils/helper.kt          # Refactored
M src/api/endpoint.kt          # New feature
A src/api/newfile.kt           # New feature
M tests/api_test.kt            # Tests for feature
```

**Execution:**
```bash
# STEP 1: Check starting state
hg status
# OUTPUT shows modified files → UNCOMMITTED state

# STEP 2: Create and submit backup (from uncommitted state)
hg add .
hg commit -m "[BACKUP] All changes before splitting - DO NOT LAND"
jf submit --draft
# OUTPUT: Created diff D123456789
BACKUP_DIFF="D123456789"
hg uncommit

# STEP 3: Now analyze changes
hg status
hg diff

# STEP 4: Check for existing prefix conventions in this area
hg log --template '{desc|firstline}\n' src/utils/ | head -5
# OUTPUT: [MyApp][Utils] Previous refactor...
# Use [MyApp] as first prefix for ALL atomic diffs

# STEP 5: Split plan (note: same first prefix [MyApp] for all, with dependencies)
# Diff 1: [MyApp][Utils] Refactoring (helper.kt) - base
# Diff 2: [MyApp][API] Feature + tests (endpoint.kt, newfile.kt, api_test.kt) - depends on Diff 1

# STEP 6: Add new files first
hg add src/api/newfile.kt

# STEP 7: Shelve the feature changes
hg shelve --name "feature" src/api/endpoint.kt src/api/newfile.kt tests/api_test.kt

# STEP 8: Commit refactor with discovered prefix
hg commit -m "[MyApp][Utils] Extract helper utility..."

# STEP 9: Unshelve and commit feature (SAME first prefix [MyApp])
hg unshelve feature
hg commit -m "[MyApp][API] Add new API endpoint..."

# STEP 10: Submit all atomic diffs as drafts (dependencies auto-set)
jf submit --draft

# STEP 11: VERIFY DEPENDENCIES (CRITICAL!)
sl fssl
# Verify output shows:
#   D234567891 [MyApp][API] Add new API...
#     ↳ depends on D234567890
#   D234567890 [MyApp][Utils] Extract helper...
#     ↳ (base)

# If dependencies are missing, fix them:
# jf submit --draft --update-all

echo "
## Split Complete

**Backup Diff (DO NOT LAND):** $BACKUP_DIFF

**Atomic Diffs Created (with dependencies):**
1. D234567890: [MyApp][Utils] Extract helper utility... (base)
2. D234567891: [MyApp][API] Add new API endpoint...
   └── Depends on: D234567890 ✓

**Dependency Chain Verified:** ✓
  D234567890 ← D234567891

**Prefix Convention:**
- First prefix: [MyApp] (from previous diffs in src/utils/)
- All atomic diffs share the same first prefix tag

**Next Steps:**
1. Review and land atomic diffs in order (D234567890 first)
2. After landing, abandon backup diff $BACKUP_DIFF
"
```

### Example 2: Splitting from COMMITTED State

**Starting state:** Changes are already committed locally

```bash
# STEP 1: Check starting state
hg status
# OUTPUT is empty → COMMITTED state

# Check current commit
hg log -r . --template '{desc|firstline}\n'
# OUTPUT: Big commit with multiple unrelated changes

# STEP 2: Simply submit current commit as backup
# DO NOT change the commit title or metadata!
jf submit --draft
# OUTPUT: Created diff D123456789
BACKUP_DIFF="D123456789"

# STEP 3: Uncommit to restore working directory
hg uncommit

# STEP 4: Now analyze changes
hg status
hg diff

# STEP 5: Search for first prefix convention
hg log --template '{desc|firstline}\n' src/ | head -5
# OUTPUT: [MyApp][Core] Previous change...
# Use [MyApp] as first prefix for ALL atomic diffs

# STEP 6: Now split using interactive commit or shelving
hg commit -i
# ... continue with normal splitting workflow

# STEP 7: Submit with dependencies
jf submit --draft

# STEP 8: Verify dependencies
sl fssl
```

### Example 3: Splitting Within a Single File

**Original status:**
```
M src/bigfile.kt    # Contains both bug fix AND new feature
```

**Execution:**
```bash
# STEP 1: Check starting state and create backup
hg status
# OUTPUT shows modified file → UNCOMMITTED state

hg commit -m "[BACKUP] All changes before splitting - DO NOT LAND"
jf submit --draft
BACKUP_DIFF="D123456789"
hg uncommit

# STEP 2: Search for first prefix convention
hg log --template '{desc|firstline}\n' src/ | head -5
# OUTPUT: [MyApp][Core] Previous change...
# Use [MyApp] as first prefix for ALL atomic diffs

# STEP 3: Now use interactive commit
hg commit -i
# Commit bug fix with: [MyApp][BugFix] Fix null pointer exception
```

**In the interface:**
1. Review each hunk
2. Select `y` for bug fix hunks
3. Select `n` for feature hunks
4. Commit bug fix: `[MyApp][BugFix] Fix null pointer exception`
5. Run again for feature changes
6. Commit feature: `[MyApp][Feature] Add new validation logic`

```bash
# STEP 4: Submit with dependencies
jf submit --draft

# STEP 5: Verify dependencies
sl fssl
# Should show:
#   D234567891 [MyApp][Feature] Add new validation logic
#     ↳ depends on D234567890
#   D234567890 [MyApp][BugFix] Fix null pointer exception
#     ↳ (base)
```

**Final output:**
```
## Split Complete

**Backup Diff (DO NOT LAND):** D123456789

**Atomic Diffs Created (with dependencies):**
1. D234567890: [MyApp][BugFix] Fix null pointer exception (base)
2. D234567891: [MyApp][Feature] Add new validation logic
   └── Depends on: D234567890 ✓

**Dependency Chain Verified:** ✓
  D234567890 ← D234567891

**Next Steps:**
1. Review and land atomic diffs in order (D234567890 first)
2. After landing, abandon backup diff D123456789
```

### Example 4: Creating a Clean Dependency Chain (Stacked Diffs)

When later diffs depend on earlier ones:

```
Backup: D123456789 (DO NOT LAND)
Diff 1: [MyFeature] Add base interface     (base - independent)
Diff 2: [MyFeature] Implement interface    (depends on Diff 1)
Diff 3: [MyFeature] Add tests              (depends on Diff 2)
         ↑
         Same first prefix tag!
```

**Key rule:** Each diff must build on top of the previous ones AND explicitly depend on it.

```bash
# STEP 1: Check starting state and create backup
hg status
# Determine if committed or uncommitted, then backup accordingly

# If uncommitted:
hg add .
hg commit -m "[BACKUP] All changes before splitting - DO NOT LAND"
jf submit --draft
BACKUP_DIFF="D123456789"
hg uncommit

# If already committed:
jf submit --draft  # DO NOT change title!
BACKUP_DIFF="D123456789"
hg uncommit

# STEP 2: Analyze changes
hg status
hg diff

# STEP 3: Search for existing prefix conventions
hg log --template '{desc|firstline}\n' -l 10 src/data/
# OUTPUT: [MyFeature][Data] Previous data layer change...
# Use [MyFeature] as first prefix for ALL atomic diffs

# STEP 4: Commit in dependency order with SAME first prefix
hg commit <interface-files> -m "[MyFeature][Interface] Add DataProvider interface - T98765"
buck build <target>  # Verify

hg commit <impl-files> -m "[MyFeature][Impl] Implement DataProvider - T98765"
buck build <target>  # Verify

hg commit <test-files> -m "[MyFeature][Test] Add DataProvider tests - T98765"
buck build <target>  # Verify

# STEP 5: Submit the stack (dependencies auto-set based on commit order)
jf submit --draft

# STEP 6: VERIFY DEPENDENCIES (CRITICAL!)
sl fssl
# Should show:
#   D234567892 [MyFeature][Test] Add DataProvider tests
#     ↳ depends on D234567891
#   D234567891 [MyFeature][Impl] Implement DataProvider
#     ↳ depends on D234567890
#   D234567890 [MyFeature][Interface] Add DataProvider interface
#     ↳ (base)

# If dependencies are NOT correct, fix them:
jf submit --draft --update-all

# Or fix individual diffs:
# hg update <commit_for_D234567891>
# jf submit --draft --depends-on D234567890

echo "
## Split Complete

**Backup Diff (DO NOT LAND):** $BACKUP_DIFF

**Atomic Diffs Created (Stacked with dependencies):**
1. D234567890: [MyFeature][Interface] Add DataProvider interface - T98765 (base)
2. D234567891: [MyFeature][Impl] Implement DataProvider - T98765
   └── Depends on: D234567890 ✓
3. D234567892: [MyFeature][Test] Add DataProvider tests - T98765
   └── Depends on: D234567891 ✓

**Dependency Chain Verified:** ✓
  D234567890 ← D234567891 ← D234567892

**Stack Info:**
- First prefix: [MyFeature] (same across all atomic diffs)
- Source: Previous diffs in src/data/
- Shared task: T98765
- Land order: 1 -> 2 -> 3 (must respect dependency chain)

**Next Steps:**
1. Add reviewers to all diffs in stack
2. Land diffs in order (1 -> 2 -> 3)
3. After all landed, abandon backup diff $BACKUP_DIFF
"
```

---

## Managing Shelved Changes

**List all shelves:**
```bash
hg shelve --list
```

**Unshelve specific shelf:**
```bash
hg unshelve <name>
```

**Delete a shelf:**
```bash
hg shelve --delete <name>
```

**Shelve everything:**
```bash
hg shelve --name "all-changes"
```

---

## Handling Dependencies Between Changes

### Scenario: Feature Code Depends on Refactor

**Wrong approach:**
- Commit feature first → won't build without refactor
- Submit without dependencies → unclear relationship

**Right approach:**
1. Commit refactor first
2. Verify build
3. Commit feature on top
4. Submit both diffs as stack
5. **Verify dependency is set: feature depends on refactor**
6. **Use SAME first prefix tag for both**

### Scenario: Circular Dependencies

If you find changes that can't be separated cleanly:
- They might actually belong in the same diff
- Consider if the separation makes sense
- Ask: "Can a reviewer understand this diff alone?"

---

## Recovery Procedures

### If splitting goes wrong:

```bash
# Option 1: Download backup diff from Phabricator
jf download D123456789
hg uncommit
# All your original changes are restored!

# Option 2: Restore from local backup commit (if still present)
hg update --clean <backup-commit-hash>
hg uncommit

# Option 3: Abort shelve operation
hg unshelve --abort

# Option 4: Discard current changes and download backup
hg revert --all
jf download D123456789
hg uncommit
```

### If you accidentally committed wrong files:

```bash
# Uncommit but keep changes
hg uncommit

# Or amend the commit
hg amend --exclude <file-to-remove>
```

### If dependencies are wrong after submission:

```bash
# Re-submit entire stack to fix
jf submit --draft --update-all

# Or fix individual diff
hg update <commit>
jf submit --draft --depends-on D<correct_parent>

# Or edit in Phabricator UI
```

---

## TodoWrite Integration

```json
[
  {"content": "Check starting state: hg status (empty = committed, files = uncommitted)", "status": "pending"},
  {"content": "Submit backup as draft diff (DO NOT change title if committed)", "status": "pending"},
  {"content": "Record backup diff number", "status": "pending"},
  {"content": "Uncommit backup to restore working directory", "status": "pending"},
  {"content": "Run hg status and hg diff to understand all changes", "status": "pending"},
  {"content": "Search diff history for existing first prefix tag convention", "status": "pending"},
  {"content": "Create splitting plan - identify atomic units", "status": "pending"},
  {"content": "Stage new files with hg add", "status": "pending"},
  {"content": "Create first atomic commit (use consistent first prefix)", "status": "pending"},
  {"content": "Verify first commit builds", "status": "pending"},
  {"content": "Create remaining atomic commits (SAME first prefix)", "status": "pending"},
  {"content": "Verify all commits build", "status": "pending"},
  {"content": "Submit all atomic diffs with jf submit --draft", "status": "pending"},
  {"content": "VERIFY DEPENDENCIES with sl fssl (CRITICAL!)", "status": "pending"},
  {"content": "Fix any missing/incorrect dependencies", "status": "pending"},
  {"content": "Verify all atomic diffs have same first prefix tag", "status": "pending"},
  {"content": "For stacked diffs: ensure consistent tags, task ID, reviewers", "status": "pending"},
  {"content": "Output summary with backup diff reference and dependency chain", "status": "pending"},
]
```

---

## Common Mistakes Table

| Mistake | Why It's Wrong | Fix |
|---------|----------------|-----|
| Analyze changes before backup | Risk losing work if something goes wrong | ALWAYS submit backup FIRST |
| Skip backup diff | Risk losing work if splitting fails | ALWAYS submit backup first |
| Changed backup title when starting from committed state | Unnecessary modification, wastes time | Submit as-is with `jf submit --draft` |
| Don't record backup diff number | Can't reference for recovery | Save the Dxxxxxxxx number |
| Different first prefix tags | Diffs look unrelated, hard to filter | Use SAME first prefix for ALL atomic diffs |
| Skip the plan | Random splits, poor organization | Plan before splitting |
| Don't verify builds | Later diffs may not compile | Build after each commit |
| **Don't verify dependencies** | **Diffs not properly linked, can land out of order** | **ALWAYS run `sl fssl` after submit** |
| **Skip dependency check** | **Reviewers confused, landing issues** | **Verify each diff shows correct parent** |
| Too granular | 10 diffs for small change | Group related changes |
| Too coarse | "Atomic" diff with 3 features | Split by single purpose |
| Wrong order | Dependent code before dependency | Order by dependency chain |
| Forget shelved changes | Lost work | Check `hg shelve --list` |
| Split tests from code | Reviewer can't verify | Keep related tests together |
| Forget to abandon backup | Cluttered Phabricator | Abandon backup after landing |
| Land the backup diff | Duplicate changes | Backup is for recovery only |
| Inconsistent stack metadata | Confusing for reviewers | Copy tags, task ID, reviewers from parent |
| Invent new prefix tags | Inconsistent with codebase | Search diff history first |
| **Land diffs out of order** | **Breaks dependency chain** | **Always land parent before child** |

---

## Heuristics for Good Atomic Splits

### Split BY:
- Feature/functionality
- Layer (UI, logic, data)
- Type of change (refactor, feature, bugfix)
- Independent reviewability

### Keep TOGETHER:
- Code and its tests
- Interface and its implementation (if small)
- Closely related utilities
- Changes that would break each other if separated

### Prefix Tag Rules:
- **First prefix tag MUST be the same** across all atomic diffs
- Additional tags can vary based on the specific change type
- Example: `[MyApp][Refactor]`, `[MyApp][Feature]`, `[MyApp][Test]`

### Dependency Rules:
- **Every diff (except the first) MUST depend on its parent**
- Dependencies are set automatically by `jf submit` based on commit order
- **ALWAYS verify dependencies after submission with `sl fssl`**
- Fix broken dependencies immediately before requesting review

---

## Interactive Commit Cheatsheet

```
Commands during hg commit -i:

y - include this hunk in commit
n - exclude this hunk from commit
e - edit this hunk manually
s - split this hunk into smaller pieces
d - done, skip remaining hunks in this file
a - include all remaining hunks in this file
q - quit, commit selected hunks
? - show help
```

---

## When to Use This Skill

**Use when:**
- You have multiple unrelated changes mixed together
- A reviewer asks you to split a large diff
- You realize mid-work that changes should be separate
- You want cleaner git/hg history

**Don't use when:**
- Changes are truly atomic already
- Splitting would create unbuildable intermediate states
- Changes are tightly coupled and inseparable

---

## Quick Reference Commands

```bash
# STEP 1: CHECK STARTING STATE
hg status
# Empty output = COMMITTED state
# Files listed = UNCOMMITTED state

# STEP 2: CREATE AND SUBMIT BACKUP FIRST (Critical!)

# If UNCOMMITTED changes:
hg add .
hg commit -m "[BACKUP] All changes before splitting - DO NOT LAND"
jf submit --draft
# Save the diff number from output: Dxxxxxxxx
hg uncommit

# If ALREADY COMMITTED (do NOT change title!):
jf submit --draft
# Save the diff number from output: Dxxxxxxxx
hg uncommit

# STEP 3: Now analyze your changes
hg status
hg diff

# Search for existing FIRST prefix convention
hg log --template '{desc|firstline}\n' <path/to/directory> | head -10
sl log --limit 20  # View your recent diffs
# Use this SAME first prefix for ALL atomic diffs!

# Interactive commit (select hunks)
hg commit -i

# Shelve for later
hg shelve --name "later" <files>
hg unshelve "later"

# Commit specific files (use SAME first prefix!)
hg commit <file1> <file2> -m "[FirstPrefix][Type] message"

# Verify build
arc lint --apply-patches
buck build <target>

# Run codegen if needed
phps CodegenLogger <LoggerConfig>
meerkat

# Submit all atomic diffs as drafts (dependencies auto-set)
jf submit --draft

# VERIFY DEPENDENCIES (CRITICAL!)
sl fssl
# Look for "depends on" indicators for each diff

# Fix dependencies if needed
jf submit --draft --update-all
# Or for individual diff:
jf submit --draft --depends-on D<parent_diff>

# Check your commit stack
hg log -r 'draft()'

# View stack info
sl fssl

# Recovery from backup diff
jf download Dxxxxxxxx
hg uncommit

# Cleanup: Abandon backup after success
arc diff-abandon Dxxxxxxxx
```

---

## Output Template

After completing the split, always output:

```
## Split Complete

**Backup Diff (DO NOT LAND):** D<backup_number>
  - Contains all original changes for recovery
  - Abandon after atomic diffs are landed

**Atomic Diffs Created (with dependencies):**
1. D<diff1>: [FirstPrefix][Type1] <title1> (base - no dependency)
2. D<diff2>: [FirstPrefix][Type2] <title2>
   └── Depends on: D<diff1> ✓
3. D<diff3>: [FirstPrefix][Type3] <title3>
   └── Depends on: D<diff2> ✓
...
              ↑
              Same first prefix across all atomic diffs!

**Dependency Chain Verified:** ✓
  D<diff1> ← D<diff2> ← D<diff3>

**Prefix Convention:**
- First prefix: [FirstPrefix] (from <source>)
- All atomic diffs share the same first prefix tag

**Stack Info (if stacked):**
- Shared first prefix: [FirstPrefix]
- Shared task: T<task_number>
- Land order: 1 -> 2 -> 3 (must respect dependency chain!)

**Next Steps:**
1. Review and land atomic diffs in order (respect dependencies!)
2. After all atomic diffs are landed, abandon backup diff D<backup_number>
