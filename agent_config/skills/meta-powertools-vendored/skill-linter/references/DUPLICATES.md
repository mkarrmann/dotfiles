# Duplicate Detection

Reference guide for detecting and handling duplicate skills.

## How Detection Works

The `find_duplicates.py` script identifies similar skills via:

### Name Similarity

| Match Type | Condition | Score |
|------------|-----------|-------|
| Exact | Names are identical | +80% |
| Prefix | One name is prefix of other (e.g., `scuba` / `scuba_cli`) | +50% |
| Edit Distance | Levenshtein distance ≤2 (e.g., `pyre` / `pyre2`) | +30% |

### Keyword Overlap

Extracts keywords from name + description, calculates Jaccard similarity:

```
Jaccard = |keywords_A ∩ keywords_B| / |keywords_A ∪ keywords_B|
```

| Overlap | Score Added |
|---------|-------------|
| >25% | overlap × 50% |

### Confidence Levels

| Confidence | Score Range | Typical Cause |
|------------|-------------|---------------|
| HIGH | ≥60% | Exact name match, or prefix + high keyword overlap |
| MEDIUM | 40-59% | Similar names or moderate keyword overlap |
| LOW | 25-39% | Related topics, some keyword overlap |

## True Duplicates vs Related Skills

### True Duplicates

Skills that serve the **same purpose** and should be consolidated:

- Same name, copied to different locations
- Same functionality with minor local modifications
- Stale copies that diverged from the source

**Example:** `scuba_cli` copied to multiple `.claude/skills/` directories

### Related Skills

Skills that **overlap in domain** but serve different purposes:

- `pyre` (general type checking) vs `pyre-at-meta` (Meta-specific workflow)
- `python-testing` (general) vs `python-at-meta` (includes testing + more)
- `git` vs `sapling-workflow` (different VCS)

**Key question:** Would using both skills cause confusion or contradiction?

## When to Consolidate

Consolidate when:
1. Skills have the **same name**
2. Skills have **identical or near-identical functionality**
3. One is clearly a **copy** of the other (check git history)
4. Keeping both causes **confusion** or **contradictory instructions**

## When to Keep Separate

Keep separate when:
1. Skills serve **different use cases** despite keyword overlap
2. Skills have **different audiences** (team-specific vs general)
3. Skills provide **complementary** rather than duplicate functionality
4. One is a **wrapper/extension** of another with intentional differences

## Recommended Actions

| Finding | Action |
|---------|--------|
| Exact copy in local `.claude/` | Replace with symlink to shared skill |
| Modified copy | Evaluate if modifications are valuable; if so, upstream them |
| Similar names, different purpose | Rename to clarify distinction |
| High keyword overlap, different names | Document relationship in both skills |

## Using the Script

```bash
# From fbcode directory:

# Scan local directory
buck2 run //claude-templates/components/skills/skill-linter/scripts:find_duplicates -- /path/to/skills

# Scan all of fbsource (requires devserver with xbgs)
buck2 run //claude-templates/components/skills/skill-linter/scripts:find_duplicates -- --fbsource

# Only high-confidence matches
buck2 run //claude-templates/components/skills/skill-linter/scripts:find_duplicates -- --fbsource --min-confidence high

# Check one skill against all others
buck2 run //claude-templates/components/skills/skill-linter/scripts:find_duplicates -- --fbsource --skill my-skill-name

# JSON output for further processing
buck2 run //claude-templates/components/skills/skill-linter/scripts:find_duplicates -- --fbsource --json
```

## Common Patterns at Meta

From fbsource scans:

1. **Shared skills copied locally** - Teams copy skills like `scuba_cli`, `tupperware`, `skill-creator` to their `.claude/` instead of symlinking
2. **Stale copies** - Copies don't get updates when the source changes
3. **Divergent modifications** - Local changes that should be upstreamed

**Recommendation:** Use symlinks to shared skills rather than copies:
```bash
ln -s /path/to/shared/skill ~/.claude/skills/skill-name
```
