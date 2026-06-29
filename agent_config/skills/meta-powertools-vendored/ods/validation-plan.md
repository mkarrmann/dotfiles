# ODS Skill Validation Plan

## Background

We built a new `ods` skill for Claude Code that uses `meta ods` CLI commands (Meta CLI) instead of the bare `ods` binary. The skill covers ODS1 only (not ODS3). It is installed at `~/.claude/skills/ods/` and uses `allowed-tools: Bash(meta:*)`.

The skill has 13 commands across two object types:
- **`ods.metric`** (8 commands): `query`, `load-url`, `keys`, `resolve`, `suggest-entities`, `suggest-keys`, `related`, `take-screenshot`
- **`ods.category`** (5 commands): `query`, `list`, `metadata`, `schema`, `attribution`

Your job is to validate that every command works correctly and that the skill covers the use cases that matter for ODS monitoring.

## Instructions

Run each test group below sequentially. For each test:
1. Execute the command exactly as shown
2. Record: **PASS** (command succeeded and returned meaningful output), **PASS (empty)** (command succeeded but returned no data — acceptable in sandbox), or **FAIL** (command errored or returned unexpected behavior)
3. If a test fails, note the error message
4. At the end, produce a summary table of all results

Use `--output=json` variants only where marked — default table output is fine for most tests.

**Important**: Some commands may return "No data" in sandcastle environments due to limited ODS data access. That's OK — we're validating that the CLI accepts the syntax and calls the API correctly (no argument errors, no crashes). A "No data returned" result is a PASS, not a FAIL.

---

## Test Group 1: Discovery & Entity Resolution

These tests validate that we can find entities and keys — the foundation for all queries.

### T1.1 — Fuzzy entity search
```bash
meta ods.metric suggest-entities -q "ods" --limit=5
```
**Expected**: Table of entity name suggestions. Should return at least 1 result.

### T1.2 — Fuzzy key search (unscoped)
```bash
meta ods.metric suggest-keys -q "cpu" --limit=5
```
**Expected**: Table of key name suggestions.

### T1.3 — Fuzzy key search (scoped to entity)
Pick an entity from T1.1 results and substitute it:
```bash
meta ods.metric suggest-keys -q "cpu" -e "<entity_from_T1.1>" --limit=5
```
**Expected**: Key suggestions scoped to that entity.

### T1.4 — Entity resolve (SMC tier)
```bash
meta ods.metric resolve -e "smc(ods.router)" --limit=5
```
**Expected**: List of resolved entity names, or a valid "no entities found" message.

### T1.5 — Entity resolve (regex)
```bash
meta ods.metric resolve -e "regex(ods\\..*)" --limit=5
```
**Expected**: Entities matching the regex pattern.

### T1.6 — Key discovery (prefix filter)
Pick an entity from T1.1 and substitute:
```bash
meta ods.metric keys -e "<entity_from_T1.1>" --prefix=system --limit=10
```
**Expected**: List of keys starting with "system".

### T1.7 — Key discovery (regex filter)
```bash
meta ods.metric keys -e "<entity_from_T1.1>" --regex="cpu" --limit=10
```
**Expected**: Keys matching the regex.

### T1.8 — Schema browse (entity keys)
```bash
meta ods.category schema -e "<entity_from_T1.1>" --limit=10
```
**Expected**: EKI schema listing of keys.

### T1.9 — Schema browse (entities-only mode)
```bash
meta ods.category schema -e "ods" --entities-only --limit=10
```
**Expected**: List of entities matching the prefix "ods".

### T1.10 — Related metrics (by keys)
```bash
meta ods.metric related --keys="system.cpu-util-pct" --limit=5
```
**Expected**: Entities that report this key.

---

## Test Group 2: Core Queries (ods.metric query)

These test the primary query path via the Rapido API with transforms and reductions.

### T2.1 — Basic entity/key query
Use a real entity and key from T1.6 results:
```bash
meta ods.metric query -e "<entity>" -k "<key>" --stime=1_h
```
**Expected**: Time-series data or "No data returned".

### T2.2 — Query with transform (latest)
```bash
meta ods.metric query -e "<entity>" -k "<key>" -t latest --stime=1_h
```
**Expected**: Single latest value per entity.

### T2.3 — Query with transform (rate)
```bash
meta ods.metric query -e "<entity>" -k "<key>" -t rate --stime=1_h
```
**Expected**: Rate-of-change values.

### T2.4 — Query with chained transforms
```bash
meta ods.metric query -e "<entity>" -k "<key>" -t "rate,avg(300)" --stime=1_h
```
**Expected**: Rate smoothed with 5-minute average.

### T2.5 — Query with reduction (avg)
Use an SMC tier or selector that resolves to multiple entities:
```bash
meta ods.metric query -e "smc(ods.router)" -k "system.cpu-util-pct" -t latest -r avg --stime=1_h
```
**Expected**: Single aggregated value or "No data".

### T2.6 — Query with reduction (top N)
```bash
meta ods.metric query -e "smc(ods.router)" -k "system.cpu-util-pct" -t latest -r "top(5)" --stime=1_h
```
**Expected**: Top 5 entities by value or "No data".

### T2.7 — Query with different time range
```bash
meta ods.metric query -e "<entity>" -k "<key>" --stime=6_h
```
**Expected**: Data over 6 hours.

### T2.8 — Query with JSON output
```bash
meta ods.metric query -e "<entity>" -k "<key>" --stime=1_h --output=json
```
**Expected**: Valid JSON output (not table format).

### T2.9 — Query with --show-url
```bash
meta ods.metric query -e "<entity>" -k "<key>" --stime=1_h --show-url
```
**Expected**: Output includes a Canvas Fiddle URL (even if no data).

### T2.10 — Query with scale transform
```bash
meta ods.metric query -e "<entity>" -k "<key>" -t "scale(0.001)" --stime=1_h
```
**Expected**: Values multiplied by 0.001.

### T2.11 — Query with delta transform
```bash
meta ods.metric query -e "<entity>" -k "<key>" -t delta --stime=1_h
```
**Expected**: Point-to-point differences.

---

## Test Group 3: Canvas URL Queries (ods.metric load-url)

### T3.1 — Decode-only mode
Use any known Canvas URL (if you don't have one, skip with a note):
```bash
meta ods.metric load-url -u "https://fburl.com/canvas/<known_id>" --decode-only
```
**Expected**: Decoded query parameters displayed (no data fetched).

### T3.2 — Full URL query
```bash
meta ods.metric load-url -u "https://fburl.com/canvas/<known_id>"
```
**Expected**: Time-series data from the chart, or "No data".

### T3.3 — URL query with JSON output
```bash
meta ods.metric load-url -u "https://fburl.com/canvas/<known_id>" --output=json
```
**Expected**: JSON-formatted output.

> **Note**: If no Canvas URL is available, test with `meta ods.metric load-url --help` to confirm the command is available and document the skip.

---

## Test Group 4: Category Management (ods.category)

### T4.1 — List categories (basic)
```bash
meta ods.category list --limit=5
```
**Expected**: Table of ODS categories with name, status, owners.

### T4.2 — List with name filter
```bash
meta ods.category list --name=ods --limit=5
```
**Expected**: Categories matching "ods" substring.

### T4.3 — List with oncall filter
```bash
meta ods.category list --oncall=ods --limit=5
```
**Expected**: Categories owned by oncall matching "ods".

### T4.4 — List with defcon filter
```bash
meta ods.category list --defcon=crit --limit=5
```
**Expected**: Categories with CRIT defcon level.

### T4.5 — List with limiter-status filter
```bash
meta ods.category list --limiter-status=blocked --limit=5
```
**Expected**: Blocked categories.

### T4.6 — List with all-statuses
```bash
meta ods.category list --all-statuses --limit=5
```
**Expected**: Categories including non-ALLOWED statuses.

### T4.7 — List with custom columns
```bash
meta ods.category list --columns=name,defconLevel,limiterStatus --limit=5
```
**Expected**: Table with only the specified columns.

### T4.8 — Category metadata
Pick a category name from T4.1 results:
```bash
meta ods.category metadata -c "<category_from_T4.1>"
```
**Expected**: Detailed metadata (owner, limits, retention, defcon level).

### T4.9 — Category metadata (JSON)
```bash
meta ods.category metadata -c "<category_from_T4.1>" --output=json
```
**Expected**: JSON-formatted metadata.

### T4.10 — Category attribution
```bash
meta ods.category attribution -c "<category_from_T4.1>"
```
**Expected**: Attribution metadata or informational message.

---

## Test Group 5: Category Queries (ods.category query)

This tests the alternative query path for aggregation types like percentiles.

### T5.1 — Basic category query
Use a real entity/key discovered earlier:
```bash
meta ods.category query -e "<entity>" -k "<key>" --hours=1
```
**Expected**: Time-series data or "No data points found".

### T5.2 — Query with aggregation type (avg)
```bash
meta ods.category query -e "<entity>" -k "<key>" -a avg --hours=1
```
**Expected**: Averaged values.

### T5.3 — Query with aggregation type (p99)
```bash
meta ods.category query -e "<entity>" -k "<key>" -a p99 --hours=1
```
**Expected**: P99 aggregated values.

### T5.4 — Query with key regex
```bash
meta ods.category query -e "<entity>" -k "system\\.cpu.*" --key-regex --hours=1
```
**Expected**: Data for keys matching the pattern.

### T5.5 — Query with longer time range
```bash
meta ods.category query -e "<entity>" -k "<key>" --hours=24
```
**Expected**: 24 hours of data.

### T5.6 — Query with table granularity
```bash
meta ods.category query -e "<entity>" -k "<key>" -t week --hours=168
```
**Expected**: Weekly-granularity data.

---

## Test Group 6: Screenshot & Visualization

### T6.1 — Take screenshot (basic)
Requires a Canvas URL:
```bash
meta ods.metric take-screenshot -u "https://fburl.com/canvas/<known_id>"
```
**Expected**: PNG file saved to `/tmp/ods_screenshot_*.png` and path printed.

### T6.2 — Take screenshot with custom dimensions
```bash
meta ods.metric take-screenshot -u "https://fburl.com/canvas/<known_id>" --width=1400 --height=1000
```
**Expected**: PNG saved with custom dimensions.

### T6.3 — Take screenshot with custom date range
```bash
meta ods.metric take-screenshot -u "https://fburl.com/canvas/<known_id>" --start-date 2026-04-06 --end-date 2026-04-13
```
**Expected**: Screenshot covering specified date range.

> **Note**: If no Canvas URL is available, validate with `--help` and document the skip.

---

## Test Group 7: Output Format Variations

### T7.1 — JSON output
```bash
meta ods.metric keys -e "<entity>" --limit=3 --output=json
```
**Expected**: Valid JSON array/object.

### T7.2 — YAML output
```bash
meta ods.metric keys -e "<entity>" --limit=3 --output=yaml
```
**Expected**: Valid YAML output.

### T7.3 — CSV output
```bash
meta ods.metric keys -e "<entity>" --limit=3 --output=csv
```
**Expected**: CSV-formatted output.

### T7.4 — No-truncate flag
```bash
meta ods.category list --limit=3 --no-truncate
```
**Expected**: Full values without truncation.

### T7.5 — Verbose flag
```bash
meta ods.metric suggest-entities -q "ods" --limit=3 --verbose
```
**Expected**: Additional debug information in output.

---

## Test Group 8: Error Handling

### T8.1 — Missing required flag
```bash
meta ods.metric query -e "ods" 2>&1
```
**Expected**: Clear error message about missing `-k` flag (not a crash).

### T8.2 — Invalid entity selector
```bash
meta ods.metric resolve -e "invalid_selector(foo)" --limit=3 2>&1
```
**Expected**: Error message about invalid selector syntax.

### T8.3 — Help output
```bash
meta ods.metric query --help 2>&1 | head -5
```
**Expected**: Usage/help text displayed correctly.

---

## Test Group 9: End-to-End Workflow Validation

These simulate real user scenarios to validate the skill guides Claude through complete workflows.

### T9.1 — Service health investigation
Run the full discovery-to-query flow:
```bash
# Step 1: Find an entity
meta ods.metric suggest-entities -q "ods" --limit=3

# Step 2: Discover keys (use entity from step 1)
meta ods.metric keys -e "<entity>" --limit=10

# Step 3: Query a metric (use entity + key from steps 1-2)
meta ods.metric query -e "<entity>" -k "<key>" --stime=1_h

# Step 4: Get shareable URL
meta ods.metric query -e "<entity>" -k "<key>" --stime=1_h --show-url
```
**Expected**: Each step succeeds and feeds into the next.

### T9.2 — Category investigation workflow
```bash
# Step 1: Find a category
meta ods.category list --limit=3

# Step 2: Get its metadata
meta ods.category metadata -c "<category_from_step1>"

# Step 3: Query data for it using category query
meta ods.category query -e "<entity>" -k "<key>" --hours=1
```
**Expected**: Full workflow completes.

### T9.3 — Cross-entity discovery
```bash
# Find entities related by a common key
meta ods.metric related --keys="system.cpu-util-pct" --limit=5

# Then query one of those entities
meta ods.metric query -e "<entity_from_related>" -k "system.cpu-util-pct" -t latest --stime=30_min
```
**Expected**: Related command returns entities, query works on one of them.

---

## Summary Template

After running all tests, fill in this table:

| Test | Command | Result | Notes |
|------|---------|--------|-------|
| T1.1 | suggest-entities | | |
| T1.2 | suggest-keys (unscoped) | | |
| T1.3 | suggest-keys (scoped) | | |
| T1.4 | resolve (smc) | | |
| T1.5 | resolve (regex) | | |
| T1.6 | keys (prefix) | | |
| T1.7 | keys (regex) | | |
| T1.8 | schema (keys) | | |
| T1.9 | schema (entities-only) | | |
| T1.10 | related (by keys) | | |
| T2.1 | query (basic) | | |
| T2.2 | query (latest) | | |
| T2.3 | query (rate) | | |
| T2.4 | query (chained transforms) | | |
| T2.5 | query (reduction avg) | | |
| T2.6 | query (reduction top N) | | |
| T2.7 | query (6h time range) | | |
| T2.8 | query (JSON output) | | |
| T2.9 | query (--show-url) | | |
| T2.10 | query (scale) | | |
| T2.11 | query (delta) | | |
| T3.1 | load-url (decode-only) | | |
| T3.2 | load-url (full) | | |
| T3.3 | load-url (JSON) | | |
| T4.1 | category list | | |
| T4.2 | category list (name) | | |
| T4.3 | category list (oncall) | | |
| T4.4 | category list (defcon) | | |
| T4.5 | category list (limiter) | | |
| T4.6 | category list (all-statuses) | | |
| T4.7 | category list (columns) | | |
| T4.8 | category metadata | | |
| T4.9 | category metadata (JSON) | | |
| T4.10 | category attribution | | |
| T5.1 | category query (basic) | | |
| T5.2 | category query (avg) | | |
| T5.3 | category query (p99) | | |
| T5.4 | category query (key regex) | | |
| T5.5 | category query (24h) | | |
| T5.6 | category query (week table) | | |
| T6.1 | take-screenshot | | |
| T6.2 | take-screenshot (dimensions) | | |
| T6.3 | take-screenshot (date range) | | |
| T7.1 | output JSON | | |
| T7.2 | output YAML | | |
| T7.3 | output CSV | | |
| T7.4 | no-truncate | | |
| T7.5 | verbose | | |
| T8.1 | missing required flag | | |
| T8.2 | invalid selector | | |
| T8.3 | help output | | |
| T9.1 | E2E health investigation | | |
| T9.2 | E2E category investigation | | |
| T9.3 | E2E cross-entity discovery | | |

**Total**: __ / 46 PASS | __ FAIL | __ SKIP

## Pass Criteria

- All T1.x (discovery): Must PASS — these are foundational
- All T2.x (queries): PASS or PASS (empty) — syntax must be accepted
- T3.x (Canvas URL): PASS or SKIP if no URL available
- All T4.x (category management): Must PASS — these don't depend on data
- T5.x (category query): PASS or PASS (empty)
- T6.x (screenshot): PASS or SKIP if no URL available
- All T7.x (output formats): Must PASS
- All T8.x (error handling): Must PASS
- T9.x (E2E workflows): PASS or PASS (empty)

**Minimum to ship**: 0 FAILs in T1, T4, T7, T8 groups. All other FAILs must have documented workarounds.
