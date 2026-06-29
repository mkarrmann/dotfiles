---
name: scuba
author: oncall+obx_metrics
description: 'Official Scuba skill. Load Scuba UI URLs, discover datasets, fetch table/column metadata, and craft queries with structured queries or Scuba SQL. Also use to discover Scuba UDFs, derived columns, and query table JOINs.'
allowed-tools:
  - Bash(meta scuba.dataset query*), Bash(meta scuba.dataset info*), Bash(meta scuba.dataset search*), Bash(meta scuba.dataset read-url*), Bash(meta scuba.dataset metadata*), Bash(meta scuba.column values*), Bash(meta scuba.column validate*), Bash(meta scuba.udf list*), Bash(meta scuba.udf usage*), Bash(scuba *), Bash(meta graphql.query execute*), head, grep, wc, python3, jq
---

# Scuba Natural Language Query Skill

## Prerequisites

This skill requires the `meta` CLI. If `meta` is not installed, run the install script:

```bash
bash scripts/install-meta-cli.sh
```

## Quick Reference

```bash
# Find the right dataset
meta scuba.dataset search -q "DESCRIPTION OF DATA YOU NEED" -r "REASONING"

# Get dataset metadata and top columns
meta scuba.dataset info -d DATASET -r "REASONING"

# Validate column names exist
meta scuba.dataset info -d DATASET --columns=col1,col2 -r "REASONING"

# Get dataset info with fewer top columns (faster)
meta scuba.dataset info -d DATASET --top-columns-limit=20 -r "REASONING"

# Metadata + derived columns only (skip top column fetching)
meta scuba.dataset info -d DATASET --top-columns-limit=0 -r "REASONING"

# Get ALL columns (large datasets)
meta scuba.dataset info -d DATASET --include-all-columns --output=json -r "REASONING"

# Prevent truncation of derived column lists in table output
meta scuba.dataset info -d DATASET --no-truncate -r "REASONING"

# Get dataset retention, queryability, oncall, and ownership
meta scuba.dataset metadata -d DATASET -r "REASONING"

# Get column values (REQUIRED before filtering on string columns)
meta scuba.column values -d DATASET -c COLUMN -r "REASONING"

# Filtered column values
meta scuba.column values -d DATASET -c COLUMN --filter-value='["scuba"]' --operator=substr -r "REASONING"

# Execute table query (default: 24h lookback, limit 100)
meta scuba.dataset query -d DATASET -a count -g col -r "REASONING"

# Samples query (requires --view=samples)
meta scuba.dataset query -d DATASET --view=samples -c col1,col2,col3 -l 20 -r "REASONING"

# Time series
meta scuba.dataset query -d DATASET -a p95 -c latency --time-bucket="1 hour" --hours=24 -r "REASONING"

# Week-over-week comparison
meta scuba.dataset query -d DATASET -g col -a count --compare="-7 days" -r "REASONING"

# Primary metric + additional per-column aggregations
meta scuba.dataset query -d DATASET -a sum -c col1 --aggregate-list='[{"column":"col2","op":"avg"},{"column":"col3","op":"p95"}]' -g dim -r "REASONING"

# Weighted aggregation (two-argument form)
meta scuba.dataset query -d DATASET -g col --derived-cols='[{"isUsed":true,"name":"weighted_sum","sql":"SUM(value_col, weight_col)","type":"Aggregated"}]' -r "REASONING"

# SQL mode (JOINs, HAVING, complex SQL)
meta scuba.dataset query -d DATASET --sql "SELECT col, COUNT(*) AS cnt FROM DATASET WHERE time >= NOW()-3600 GROUP BY col ORDER BY cnt DESC LIMIT 50" -r "REASONING"

# Parse Scuba URL to extract parameters
meta scuba.dataset read-url -u "SCUBA_URL" -r "REASONING"

# Execute AND return a shareable URL (STRUCTURED MODE ONLY — silently ignored with --sql)
meta scuba.dataset query -d DATASET -g col -a count --show-url -r "REASONING"

# URL only, no execution (STRUCTURED MODE ONLY — silently ignored with --sql)
meta scuba.dataset query -d DATASET -g col -a count --dry-run -r "REASONING"

# Discover UDFs by keyword (PREFERRED — returns rich metadata)
meta scuba.udf list --search=approx,count -r "REASONING"

# List ALL UDF names (lightweight, no details)
meta scuba.udf list -r "REASONING"

# Find real-world UDF usage examples
meta scuba.udf usage --udfs=APPROX_COUNT_DISTINCT_HLL -r "REASONING"

# Validate a derived column expression
meta scuba.column validate -d DATASET --sql "APPROX_COUNT_DISTINCT_HLL(col, 16)" -t Aggregated -r "REASONING"
```

**IMPORTANT**: Queries execute by default. Do NOT pass `--dry-run` unless the user explicitly asks for URL-only output. Do NOT pass `--show-url` unless the user explicitly asks for a link/URL.

**Progressive disclosure**: Run `--help` on any command for full parameter documentation.

---

## User Data Profile (data.md)

First, check if `~/.claude/.data-md-optout` exists. If it does, skip this section entirely and proceed normally with dataset search.

Otherwise, check if `~/.claude/data.md` exists and read it. This file contains the user's known Scuba datasets and data context. If the file exists and the user's question matches a dataset listed there, use it directly instead of running `meta scuba.dataset search`. Use their domain context to improve search queries. If the file does not exist, proceed normally with dataset search.

---

## Command Overview

| Command | Purpose |
|---------|---------|
| `meta scuba.dataset query` | Execute queries (table, time series, samples, SQL) |
| `meta scuba.dataset info` | Dataset metadata, column validation, filter ops |
| `meta scuba.dataset search` | Semantic dataset search by description |
| `meta scuba.dataset read-url` | Parse Scuba URL to extract parameters |
| `meta scuba.column values` | Get distinct column values for filtering |
| `meta scuba.column validate` | Validate derived column SQL expression |
| `meta scuba.udf list` | Discover UDF/UDAF functions by keyword |
| `meta scuba.dataset metadata` | Dataset retention, queryability, oncall, owners |
| `meta scuba.udf usage` | Find real-world UDF usage examples |

All commands support `--output=table|json|yaml|csv|toon` and `--help`. **Prefer `--output=toon` for query results — TOON is a columnar JSON format that is ~40-50% smaller than per-row JSON for tabular data.** Use `--output=json` only when a downstream tool needs JSON.

---

## Pre-Query Discovery with `dataset info`

**Call `dataset info` before constructing any query.** It returns everything you need to build a correct query:

| Output Field | How It Informs Your Query |
|--------------|--------------------------|
| `supported_aggregations` | Valid values for `-a` / `--aggregate` (e.g., `count`, `avg`, `p95`) |
| `filter_operations_by_column_type` | Valid `op` values for `--where` per column type (Int, Normals, Tagsets, etc.) |
| Per-column `column_type` | Determines which filter operators apply (see above) |
| Per-column `groupable` / `aggregable` / `filterable` | Which columns can be used with `-g`, `-c` (for aggregation via `-a`), or `-w` |
| Per-column `format` | How to interpret numeric results (`%M` = ms, `%U` = μs — see Critical Rule 2) |
| `derived_columns_available` | All derived column names (always returned, even without `--include-all-columns`) |
| Per-column `sql` (derived columns) | The SQL expression behind each derived column |
| `default_view` / `default_query_params` | Default constraints, group-by, metric, and time range — use to construct queries matching the Scuba UI defaults |

**Targeted column validation**: Pass `--columns=col1,col2` to check specific columns before using them. The JSON response includes a `column_search_results` object with `searched_columns`, `found_columns`, and `not_found_columns`.

**Speed tip**: Use `--top-columns-limit=0` when you only need metadata (aggregations, filter ops, derived column names) without fetching top column statistics.

**Long lookback queries**: When the user requests data beyond 24 hours, call `meta scuba.dataset metadata -d DATASET` first to check `retention_days` and `available_days`. If the requested `--hours` exceeds `available_days × 24`, warn the user and cap the lookback accordingly. Also check `is_queryable` — if `no`, the dataset is blocklisted and queries will fail.

---

## Query Key Parameters

| Flag | Short | Description | Default |
|------|-------|-------------|---------|
| `--dataset` | `-d` | Dataset name (required) | — |
| `--aggregate` | `-a` | Aggregation function: `count`, `avg`, `sum`, `p95`, etc. Use `-c` for columns. | count |
| `--group-by` | `-g` | Comma-separated dimensions | — |
| `--columns` | `-c` | Columns for the primary metric (aggregation) or columns to return (samples) | — |
| `--where` | `-w` | Constraint JSON array (see format below) | — |
| `--filter-sql` | — | Raw SQL WHERE (**CANNOT mix with `-w`** — see warning below) | — |
| `--limit` | `-l` | Row limit | 100 |
| `--hours` | — | Lookback hours (capped by dataset retention) | 24 |
| `--start-time` | — | Start time (Unix timestamp only) | — |
| `--end-time` | — | End time (Unix timestamp only) | — |
| `--time-bucket` | — | `auto`, `1 hour`, `1 day`, `1 week`, or seconds | — |
| `--compare` | — | Comparison offset: `-7 days`, `-1 week` | — |
| `--derived-cols` | — | Derived columns JSON ([references/derived-columns.md]) | — |
| `--aggregate-list` | — | Per-column aggregation JSON (combinable with `-a`) | — |
| `--pool` | `-p` | Query pool | uber |
| `--sql` | — | Full SQL query (bypasses structured params) | — |
| `--sql-file` | — | Read SQL from file | — |
| `--view` | — | View type: `table`, `samples`, `time_series` | table |
| `--asc` | — | Sort ascending (default: descending) | — |
| `--timezone` | — | IANA timezone | user's TZ |

**Aggregate syntax**: `-a` takes the function name (`count`, `avg`, `sum`, `min`, `max`, `p50`, `p95`, `p99`). Use `-c` to specify the column (e.g., `-a p95 -c latency`).

**Time ranges**: Use `--hours` for relative lookback. Use `--start-time`/`--end-time` only when you need exact Unix timestamps.

**WARNING: `--filter-sql` and `--where`/`-w` are mutually exclusive.** They CANNOT be used in the same query — the service will reject it. When using `--filter-sql` (needed for JSON extraction `IS NOT NULL`, SQL `LIKE`, etc.), put ALL filters in `--filter-sql`. When using `--where`/`-w` with constraint JSON, put ALL filters there.

---

## Constraint JSON Format

Used with `--where` / `-w`:

```json
[{"column":"col1","op":"eq","values":["val1"]},{"column":"col2","op":"gt","values":["100"]}]
```

**Operators:** `eq`, `neq`, `lt`, `gt`, `elt` (≤), `egt` (≥), `substr`, `!substr`, `any`, `all`, `none`, `regeq` (case-insensitive regex), `regneq`. Valid operators vary by column type — check via `meta scuba.dataset info -d DATASET --output=json` (see `filter_operations_by_column_type`).

**Per-Column Aggregation** (`--aggregate-list`): Can be used alone or combined with `-a` to set a primary metric alongside additional per-column aggregations.
```json
[{"column":"col1","op":"avg"},{"column":"col2","op":"p95"}]
```
Valid ops: `avg`, `sum`, `min`, `max`, `p5`, `p25`, `p50`, `p75`, `p90`, `p95`, `p99`, `p99.9`, `p99.99`.

**Derived Columns** (`--derived-cols`): See [references/derived-columns.md](references/derived-columns.md) for JSON format, type selection rules, and examples.

---

## Critical Rule 1: Always Check Column Values Before Filtering

**BEFORE filtering on ANY string/categorical column, you MUST call `column values`.**

```bash
# STEP 1: Get actual column values
meta scuba.column values -d DATASET -c status

# STEP 2: Use exact value from step 1
meta scuba.dataset query -d DATASET -w '[{"column":"status","op":"eq","values":["VALUE"]}]' -a count
```

Column values are case-sensitive and often have unexpected formats. Guessing values returns zero results.

---

## Critical Rule 2: Always Check Column Format Before Presenting Results

**BEFORE presenting numeric results, check the column's `format` field from `dataset info`.**

```bash
meta scuba.dataset info -d DATASET --columns=cpu_time --output=json
```

| Format | Meaning | Conversion |
|--------|---------|------------|
| `%M` | Duration (ms) | **divide by 1,000 = seconds** |
| `%U` | Duration (us) | divide by 1,000,000 = seconds |
| `%T` | Timestamp | epoch seconds to datetime |
| `%P` | Percentage | check `scale` field |
| `%d` / `%C` | Integer/Count | display as-is |
| `%s` | String | display as-is |

---

## Critical Rule 3: Validate Column Names Before Using Them

```bash
meta scuba.dataset info -d DATASET --columns=col1,col2
```

Check `not_found_columns` in the response. Only use columns from `found_columns`. If columns are not found, inform the user and suggest similar columns.

The per-column details also tell you:
- `column_type` — determines which filter operators are valid (see `filter_operations_by_column_type`)
- `groupable` / `aggregable` / `filterable` — whether the column can be used with `-g`, `-a`, or `-w`
- `format` — how to interpret numeric values (see Critical Rule 2)

---

## Critical Rule 4: Validate Derived Column Expressions Before Executing

**NEVER execute a query with `--derived-cols` without first validating the expression with `column validate`.** All three steps below are mandatory — do NOT skip step 2.

1. Confirm the function exists: `meta scuba.udf list --search=sum,conditional`
2. **Validate the expression (REQUIRED)**: `meta scuba.column validate -d DATASET --sql "SUM(IF(col = 'val', 1, 0))" -t Aggregated`
3. Only after validation succeeds, execute the full query with `--derived-cols`

Skipping step 2 risks executing queries with invalid expressions, wrong types, or syntax errors that produce confusing failures.

---

## Critical Rule 5: No String Literals in `--derived-cols`

**Single-quoted string values in `--derived-cols` SQL are silently dropped** — the query returns `null` for all rows with no error. This affects `CASE WHEN ... THEN 'value'` and similar patterns. Numeric values are unaffected.

**Workarounds (preferred order):**

1. **`--sql-file`** — bypasses all shell quoting:
   ```bash
   cat > /tmp/q.sql << 'EOF'
   SELECT CASE WHEN amount < 500 THEN 'LOW' ELSE 'HIGH' END AS bucket,
     COUNT(*) AS cnt FROM my_dataset WHERE time >= NOW()-604800
   GROUP BY bucket ORDER BY cnt DESC
   EOF
   meta scuba.dataset query -d my_dataset --sql-file file:///tmp/q.sql -r "reason"
   ```

2. **`--sql` with double-quoted shell string** — single quotes survive inside double quotes:
   ```bash
   meta scuba.dataset query -d my_dataset \
     --sql "SELECT CASE WHEN amount < 500 THEN 'LOW' ELSE 'HIGH' END AS bucket, COUNT(*) AS cnt FROM my_dataset WHERE time >= NOW()-604800 GROUP BY bucket LIMIT 100" -r "reason"
   ```

3. **Numeric encoding** — map categories to integers:
   ```bash
   meta scuba.dataset query -d my_dataset -a count -g bucket \
     --derived-cols='[{"isUsed":true,"name":"bucket","sql":"IF(amount < 500, 0, 1)","type":"Numeric"}]' -r "reason"
   ```

4. **Separate queries per bucket** using `--where` constraints.

---

## Structured Mode vs SQL Mode

| Use Case | Mode | Why |
|----------|------|-----|
| Simple count/group/filter | Structured (`-a`, `-g`, `-w`) | Easier, validates columns |
| Percentiles, time series | Structured (`-a p95`, `--time-bucket`) | Built-in support |
| Derived columns, weighted aggs | Structured (`--derived-cols`) | Handles type system |
| Week-over-week comparison | Structured (`--compare`) | Built-in comparison columns |
| Samples query | Structured (`--view=samples`) | Simpler syntax |
| JOINs (2-table) | **SQL** (`--sql`) | Not available in structured mode |
| HAVING clause | **SQL** (`--sql`) | Not available in structured mode |
| Case-sensitive regex | **SQL** (`--sql`) | Structured `regeq` is case-insensitive |
| Complex multi-condition logic | **SQL** (`--sql`) | Nested AND/OR logic |
| CASE WHEN with string output | **SQL** (`--sql` or `--sql-file`) | String literals in `--derived-cols` are silently dropped by shell quoting |
| Subqueries / CTEs | **Not supported** | Run separate queries |

**Rule of thumb**: Start with structured mode. Switch to `--sql` only when you need JOINs, HAVING, CASE WHEN, or case-sensitive regex.

---

## Combining Scuba with a different source (ODS / ODS3)

Combining *within* Scuba is handled here, in this skill: JOINs across two Scuba tables (`--sql`), per-row formulas (`--derived-cols`), and multiple metrics in one query (`--aggregate-list`). Stay in Scuba for those.

But to combine a Scuba query with a **different source** — an ODS (`rapido`) or ODS3 metric — or to apply a formula *across* those sources (a ratio, "as a percentage of", etc.), use the **`metric-formulas` skill** (run `/metric-formulas --help` for a menu of common combinations). It combines heterogeneous sources (`scuba`, `scuba_sql`, `ods3`, `rapido`, …) with a formula and returns the combined result (or a shareable fburl). Discover/validate the Scuba columns here first, then hand the query to `metric-formulas`.

---

## SQL Mode

Use `--sql` for JOINs, HAVING, case-sensitive regex, or complex queries. When `--sql` is used, all other query params are ignored except `--dataset`, `--pool`, `--output`.

**URL generation is NOT supported in SQL mode.** `--show-url` and `--dry-run` are silently ignored when combined with `--sql`. If the user asks for a Scuba URL, you must rewrite the query in structured mode (`-a`, `-g`, `-w`, etc.) and use `--show-url` on the structured query instead.

**Read [references/sql-advanced.md](references/sql-advanced.md) before writing JOIN queries.**

```bash
# Basic SQL
meta scuba.dataset query -d DATASET --sql "SELECT col, COUNT(*) AS cnt FROM DATASET WHERE time >= NOW()-3600 GROUP BY col ORDER BY cnt DESC LIMIT 50"

# HAVING (not available in structured mode)
meta scuba.dataset query -d DATASET --sql "SELECT status, COUNT(*) AS cnt FROM DATASET WHERE time >= NOW()-86400 GROUP BY status HAVING cnt > 100"

# SQL from file (for long queries) — file:// prefix is REQUIRED for server dispatch
meta scuba.dataset query -d DATASET --sql-file file:///path/to/query.sql
```

**SQL Rules:**
- Time filter required: `WHERE time >= NOW()-<seconds>` (1h=3600, 1d=86400, 1w=604800)
- Never alias as `count` — use `cnt`, `total`, `num_rows`
- Not supported: `UNION ALL`, `COUNT(DISTINCT ...)` (use `APPROX_COUNT_DISTINCT`), window functions, subqueries, CTEs
- `LIKE` wildcards (`%`) only at the start and/or end of the pattern — use `REGEXP_MATCH` for patterns with wildcards in the middle:
    - ✅ Valid: `'prefix%'`, `'%suffix'`, `'%word%'` (wildcards at edges only, even both edges)
    - ❌ Invalid: `'foo%bar'`, `'50%3306'` (wildcard between literal chars) — rewrite as `REGEXP_MATCH(col, 'foo.*bar')`
- ORDER BY column must appear in SELECT list
- Regex: use POSIX classes (`[0-9]`, `[a-z]`) — `\d`/`\w`/`\s` shorthand classes are NOT supported; e.g., `'code_[0-9]+'`
- NULL handling: `IS NULL` / `IS NOT NULL` work as expected; use `COALESCE(col, default)` for defaults
- String comparison: `=` for exact match, `LIKE` for prefix/suffix, `REGEXP_MATCH` for patterns
- `REGEXP_EXTRACT` / `REGEXP_MATCH` only accept VARCHAR columns — tagset and norm-vector columns must be cast first

---

## Common Patterns

### Find Dataset Then Query
```bash
# 1. Search for the right dataset
meta scuba.dataset search -q "user activity and engagement metrics"
# 2. Learn columns, types, valid aggregations, and filter operators
meta scuba.dataset info -d FOUND_DATASET
# 3. Query using validated columns and operators from step 2
meta scuba.dataset query -d FOUND_DATASET -a count -g col
```

### Filtered Query (check values first!)
```bash
meta scuba.column values -d DATASET -c status
meta scuba.dataset query -d DATASET -w '[{"column":"status","op":"eq","values":["VALUE"]}]' -a count
```

### Time Series with Percentile
```bash
meta scuba.dataset query -d DATASET -a p95 -c latency --time-bucket="1 hour" --hours=168
```

### Weekly/Daily Active Users
```bash
# WAU over 4 weeks
meta scuba.dataset query -d DATASET -a approxcountdistinct -c userid --time-bucket="1 week" --hours=672
# DAU over 2 weeks
meta scuba.dataset query -d DATASET -a approxcountdistinct -c userid --time-bucket="1 day" --hours=336
```

### Samples Query
```bash
meta scuba.dataset query -d DATASET --view=samples -c col1,col2,col3 -l 20
```

### Parse URL
```bash
# Parse to get parameters
meta scuba.dataset read-url -u "SCUBA_URL" --output=json
```

### Multiple Constraints
```bash
meta scuba.dataset query -d DATASET -w '[{"column":"status","op":"eq","values":["500"]},{"column":"backend","op":"eq","values":["api"]}]' -a count
```

### Discovering Derived Columns

**Read [references/derived-columns.md](references/derived-columns.md)** before exploring derived columns. Search with multiple keywords.

`dataset info` always returns `derived_columns_available` — the full list of derived column names — even without `--include-all-columns`. To see the SQL expression behind a derived column, check the per-column `sql` field in the JSON output:

```bash
# See all derived column names + SQL expressions for specific columns
meta scuba.dataset info -d DATASET --columns=col_a,col_b --output=json
# Metadata-only (fast): get derived column names without top column stats
meta scuba.dataset info -d DATASET --top-columns-limit=0 --output=json
```

### JSON Field Extraction

Use `--derived-cols` with `GET_JSON_OBJECT`. **Read [references/json-field-extraction.md](references/json-field-extraction.md)** first.

```bash
meta scuba.dataset query -d DATASET -g extracted_field -a count \
  --derived-cols='[{"isUsed":true,"name":"extracted_field","sql":"GET_JSON_OBJECT(json_col, '"'"'$.field_name'"'"')","type":"String"}]' \
  --filter-sql="GET_JSON_OBJECT(json_col, '$.field_name') IS NOT NULL"
```

### UDF Discovery, Validation, and Usage

```bash
# Discover functions matching keywords
meta scuba.udf list --search=approx,distinct
# See real-world usage
meta scuba.udf usage --udfs=APPROX_COUNT_DISTINCT_HLL
# Validate expression
meta scuba.column validate -d DATASET --sql "APPROX_COUNT_DISTINCT_HLL(user_id, 16)" -t Aggregated
```

### Regex Matching

`regeq`/`regneq` constraints are case-**insensitive** (`REGEXP_IMATCH`). For case-**sensitive** matching, use `--filter-sql` with `REGEXP_MATCH` or `--sql` mode.

### Time-Based Comparisons
```bash
meta scuba.dataset query -d DATASET -g error_message -a count --hours=24 --compare="-7 days"
```

Output includes `Hits`, `Hits (Comparison)`, `Hits (Percent)`, `Hits (Delta)`. A `(Percent)` value of `3.4028235e+38` means the item is new (zero in comparison period).

---

## Offer to Save as a Shareable Analysis Page

After a successful analytical query, **offer to save it as a shareable analysis page** — one short sentence after the inline results. The user opts in; this is not automatic.

**Skip the offer** if any of these are true:
- Query was a schema exploration (`scuba.column values`, samples preview with no constraints)
- Query failed or returned zero rows

Ask in one sentence: *"Want me to save this as a shareable analysis page (chart + query + follow-ups)?"* If the user says yes (or says "share", "save", "publish", "export"), invoke the `share-analysis` skill with these inputs:

- `title`: one-line observation including a headline number from the result set
- `hook_kind`: `scuba`
- `query_config`: the scuba query config you just ran (`{ dataset, hours, groupBy, metric, limit, orderBy, orderDesc, constraints }`)
- `chart_kind`: `line` for time series, `bar` for grouped counts, `table` for raw samples
- `chart_x` / `chart_y`: the result columns for the chart axes (skip for `table`)
- `opportunities` (optional): 2–6 follow-ups as `{category, subcategory, title, description, action_type, priority}`. Categories: `investigate`, `build`, `fix_data`. Anchor each to a real number from the result.

The skill returns a fullscreen artifact URL — print it inline.

If the user says no or doesn't respond, move on.

---

## Troubleshooting

| Error | Solution |
|-------|----------|
| "There must be at least one column selected" | Add `--view=samples -c col1,col2` |
| Query returns zero results | Check column values with `meta scuba.column values` first |
| "Dataset does not exist" | Verify name with `meta scuba.dataset search` |
| "Invalid constraint operator" | Check valid ops via `meta scuba.dataset info --output=json` |
| Results don't match Scuba UI | Check column format; apply conversion (e.g., `%M` = divide by 1000) |
| "Column does not exist" | Validate with `meta scuba.dataset info --columns=...` |
| "Aggregate can not accept other aggregates" | Derived column type wrong — see [references/derived-columns.md](references/derived-columns.md) |
| `--show-url` / `--dry-run` produces no URL | These flags are silently ignored with `--sql`. Rewrite in structured mode or run two separate commands |
| JOIN errors | See [references/sql-advanced.md](references/sql-advanced.md) |
| Derived column returns all `null` | String literals in `--derived-cols` CASE/IF are silently lost in shell quoting — use `--sql-file`, `--sql`, or numeric encoding (see Critical Rule 5) |
| Query returns partial/no results for long lookback | Check `meta scuba.dataset metadata -d DATASET` — `available_days` may be less than requested `--hours / 24` |
| "Dataset is blocklisted" / query blocked | Run `meta scuba.dataset metadata -d DATASET` — if `is_queryable: no`, dataset is blocklisted; contact oncall |
| `DSS4_AGENT_ACCESS_CONTROL` / "columns contain sensitive data" | DSS4-sensitive columns blocked in default mode — see **DSS4 Access & Sensitive Mode** below |
| Tempted to query the Hive `_inc_archive` mirror when the user said "Scuba" | Query Scuba directly. The `_inc_archive` mirror is the Hive copy and lags by hours — it has older data, not different data |
| Query returns 0 rows — should I fall back to Hive? | **No.** Verify the time window and the dataset name first (re-check via `meta scuba.column values` and `meta scuba.dataset search`). The Hive mirror will NOT help — it lags Scuba, so it has strictly older data |

---

## DSS4 Access & Sensitive Mode

When a query fails with `DSS4_AGENT_ACCESS_CONTROL`, **automatically re-run without the sensitive columns listed in the error** — this is the shared default for all runtimes. Then surface how the user can get full access to the sensitive columns; the exact remediation is runtime-specific.

In the **interactive Claude Code CLI**, tell the user to run `claude --meta-sensitive-mode` (+ `claude-templates skill scuba install`) for full access. Other runtimes surface this limitation differently (e.g. their own access-elevation flow), so adapt the remediation to the runtime rather than recommending the CLI flags verbatim.

Only the auto-rerun + a remediation hint apply for this specific error — other access errors (SELECT permissions, blocklists) won't be fixed by sensitive mode.

---

## Scuba UDF Reference

Common UDFs in derived columns and SQL:

| Function | Usage | Example |
|----------|-------|---------|
| `IF(cond, then, else)` | Conditional | `IF(status = 'FAILED', 1, 0)` |
| `COALESCE(a, b, ...)` | First non-null | `COALESCE(region, 'unknown')` |
| `SUBSTR(str, pos, len)` | Substring | `SUBSTR(job_name, 1, 10)` |
| `REGEXP_EXTRACT(str, pattern)` | Regex match | `REGEXP_EXTRACT(msg, '(\\w+Error)')` |
| `REGEXP_MATCH(str, pattern)` | Regex test | `REGEXP_MATCH(name, '^test_.*')` |
| `CONCAT(a, b)` | Concatenate | `CONCAT(cluster, '/', region)` |
| `REPLACE(str, old, new)` | Replace | `REPLACE(path, '/root/', '')` |
| `CAST(expr AS type)` | Type cast | `CAST(gpu_count AS VARCHAR)` |
| `GET_JSON_OBJECT(json, path)` | JSON extract | `GET_JSON_OBJECT(col, '$.key')` |
| `APPROX_COUNT_DISTINCT_HLL(col, prec)` | Unique count (agg) | `APPROX_COUNT_DISTINCT_HLL(host, 16)` |
| `APPROX_PERCENTILE(col, pct)` | Percentile (agg, SQL mode) | `APPROX_PERCENTILE(latency, 0.95)` |
| `SUM(IF(cond, 1, 0))` | Conditional count (agg) | `SUM(IF(status = 'FAILED', 1, 0))` |
| `FROM_UNIXTIME(epoch)` | Epoch → datetime string | `FROM_UNIXTIME(created_at)` |
| `FROM_UNIXTIME(epoch, tz)` | Epoch → datetime in timezone | `FROM_UNIXTIME(created_at, "America/Los_Angeles")` |

**IMPORTANT**: `FROM_UNIXTIME(epoch)` without a timezone argument returns timestamps in an implicit timezone (not necessarily UTC). When timezone matters, always use the two-argument form with an explicit IANA timezone string (e.g., `"UTC"`, `"America/Los_Angeles"`, `"America/New_York"`).

**IMPORTANT**: Aggregate functions in derived columns require `"type":"Aggregated"`. Scalar expressions use `"type":"Numeric"`.

---

## Reasoning Logging

Always include `--reasoning` (short: `-r`) with a brief explanation combining what
the user wants and why you chose this approach:

```bash
meta scuba.dataset query -d events -a count -g event_type --hours=24 \
  -r "User wants event distribution over last day. Count by event_type for overview."
```

Keep reasoning to 1-2 sentences. Include: (1) user intent, (2) why these parameters.

---

## Best Practices

1. **Critical Rules 1–4 above are mandatory**
2. Always specify `--view=samples -c` for samples queries
3. Use `--hours` for relative lookback (not `--start-time` unless you need exact timestamps)
4. Start simple — add filters/grouping incrementally
5. Do NOT pass `--dry-run` unless explicitly requested. **NEVER combine with `--sql`** — silently ignored
6. Do NOT pass `--show-url` unless user asks for a link. **NEVER combine with `--sql`** — silently ignored; rewrite to structured mode first
7. Use `--compare` for time comparisons instead of manual constraint construction
8. Use `--sql` for JOINs, HAVING, case-sensitive regex
9. Prefer `--where` over `--filter-sql` for standard filters
10. **Thoroughly search derived columns** — see [references/derived-columns.md](references/derived-columns.md)
