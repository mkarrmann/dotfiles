# Scuba Skill Test Plan

Test prompts for validating the `scuba` skill's query routing logic. Each prompt should trigger `meta scuba.dataset query` (structured or `--sql` mode) automatically — the skill decides which execution method to use based on query complexity.

## Prerequisites

- The scuba skill must be loaded (invoke `/scuba` or ensure it's auto-loaded)
- You must be in an environment where `meta scuba.*` CLI tools are available
- No GraphQL or `jf graphql` tools are needed — the skill uses `meta scuba.*` exclusively

---

# Part 1: Test Prompts

Feed these prompts into a Claude Code session one at a time. Each test should use a fresh session.

## Test 1: JOIN

```
Join core_viz_session_actions with scuba_queries on userid. Filter core_viz_session_actions to action = 'scuba_tools_execute_query' and show the average cpu_time from scuba_queries for users who triggered execute_query actions in the last hour, grouped by query_source.
```

## Test 2: HAVING

```
Show me all available functions I can use in scuba queries. Then find which query_sources in the scuba_queries dataset had more than 1000 queries.
```

## Test 3: Discovery + Query Execution

```
I'm looking for data about error rates in web requests. First help me find the right dataset, then explore its schema, then find error types that occurred more than 500 times in the past day.
```

## Test 4: Weighted Aggregation

```
Calculate a weighted sum of cpu_time from scuba_queries over the last hour, weighted by total_time_ms, grouped by query_source.
```

## Test 5: File-Based Execution

```
I have a complex scuba query I want to save and run from a file. Write a SQL query that describes the scuba_queries dataset, save it to a temp file, and execute it.
```

## Test 6: Two-Step Aggregation

```
From scuba_queries in the last day, first compute the average query count per user, then find only users above that average.
```

## Test 7: Regex Extraction

```
From scuba_queries in the last hour, extract the first word from each query_source using a regex pattern and count queries per extracted prefix.
```

## Test 8: JSON Field Extraction

```
From core_viz_session_actions in the last hour, extract the "metric" field from the action_metadata JSON column for actions containing "scuba_tools" and count queries per metric type, grouped by the extracted value.
```

## Test 9: Conditional Aggregation

```
From scuba_queries in the last hour, count how many queries had cpu_time above 1000 vs at or below 1000, grouped by query_source.
```

## Test 10: UDF Discovery

```
Show me what aggregate functions are available in Scuba, specifically any UDAFs for approximate counting.
```

## Test 11: Expression Validation

```
Validate this derived column: UPPER(hostname) on scuba_queries.
```

## Test 12: UDF Usage Examples

```
Find examples of how APPROX_COUNT_DISTINCT_HLL is used in real queries.
```

## Test 13: End-to-End UDF Workflow

```
I want to count how many SQL-type queries vs non-SQL queries hit scuba_queries per hour over the last 24 hours. Find an aggregate function for conditional counting and execute the query
```

---

# Part 2: Evaluation Rubric

Use this section to grade the outputs from Part 1. Do not share this section with the test subject.

## Test 1: JOIN

**What it tests:** The skill uses `--sql` mode for JOINs (not available in structured mode).

**Pass criteria:**
- Uses `meta scuba.dataset query -d ... --sql` with a JOIN
- Joins `core_viz_session_actions` and `scuba_queries` on `userid`
- Filters `core_viz_session_actions` to `action = 'scuba_tools_execute_query'`
- Selects `AVG(cpu_time)` from `scuba_queries`, grouped by `query_source`
- Time filter on both tables (`WHERE ... time >= NOW()-3600` or equivalent)
- Uses full table names (not aliases) for column qualification
- Reads `references/sql-advanced.md` before writing the JOIN query

**Fail signal:** Uses structured mode for the JOIN, omits the time filter on one of the joined tables, or uses table aliases for column qualification.

---

## Test 2: HAVING

**What it tests:** The skill uses `--sql` mode for HAVING and always includes time filters.

**Pass criteria:**
- Discovers functions using `meta scuba.udf list` (with or without `--search`)
- Uses `meta scuba.dataset query --sql` with `HAVING COUNT(*) > 1000`, time filter, and non-reserved alias (e.g., `cnt`, not `count`)

**Fail signal:** HAVING query omits time filter, aliases a column as `count`, or uses structured mode for HAVING.

---

## Test 3: Discovery + Query Execution

**What it tests:** The skill uses discovery tools then routes to the appropriate query mode.

**Pass criteria:**
1. Discovery: `meta scuba.dataset search -q "..."` to find the right dataset
2. Schema: `meta scuba.dataset info -d DATASET` to explore columns
3. Query: `meta scuba.dataset query --sql` with `HAVING COUNT(*) > 500` (SQL mode required for HAVING), or structured mode with post-filtering if applicable

**Fail signal:** Skips discovery/schema steps, or uses incorrect tools.

---

## Test 4: Weighted Aggregation

**What it tests:** The skill uses `--derived-cols` with Scuba's two-argument weighted aggregation syntax and `"type":"Aggregated"`.

**Pass criteria:**
- Uses `meta scuba.dataset query` with `--derived-cols`
- Derived column uses `SUM(cpu_time, total_time_ms)` (two-arg weighted form)
- Sets `"type":"Aggregated"` in the derived column definition
- Groups by `query_source`
- Reads `references/derived-columns.md` before constructing the derived column

**Fail signal:** Uses `SUM(cpu_time * total_time_ms)` instead of weighted syntax, or sets type to `"Numeric"` instead of `"Aggregated"`.

---

## Test 5: File-Based Execution

**What it tests:** The `--sql-file` flag works.

**Pass criteria:**
- Writes a `.sql` file with a valid Scuba SQL query
- Executes with `meta scuba.dataset query -d DATASET --sql-file /path/to/file.sql`
- Not blocked by allowed-tools

**Fail signal:** `--sql-file` is rejected by tool permissions, or Claude says it can't use file-based execution.

---

## Test 6: Two-Step Aggregation

**What it tests:** The skill uses SQL mode for queries requiring filtering on aggregated results (HAVING).

**Pass criteria:**
- Uses `meta scuba.dataset query --sql` with HAVING or a two-step pipeline
- Time filter (`WHERE time >= now()-86400`)
- Does not attempt structured mode for the filtering step

**Fail signal:** Tries structured mode for the aggregation filter, or omits time filters.

---

## Test 7: Regex Extraction

**What it tests:** The skill uses `--derived-cols` with `REGEXP_EXTRACT` and `"type":"String"`.

**Pass criteria:**
- Uses `meta scuba.dataset query` with `--derived-cols`
- Derived column uses `REGEXP_EXTRACT(query_source, '^([a-z]+)')` or similar pattern
- Sets `"type":"String"` in the derived column definition
- Groups by the derived column name and counts per group
- Uses `--filter-sql` with `IS NOT NULL` to exclude non-matching rows
- Reads `references/derived-columns.md` before constructing the derived column

**Fail signal:** Sets type to `"Numeric"` instead of `"String"`, omits the NULL filter, or mixes `--filter-sql` with `--where`.

---

## Test 8: JSON Field Extraction

**What it tests:** The skill uses `--derived-cols` with `GET_JSON_OBJECT` and `"type":"String"`.

**Pass criteria:**
- Uses `meta scuba.dataset query` with `--derived-cols`
- Derived column uses `GET_JSON_OBJECT(action_metadata, '$.metric')` or similar
- Sets `"type":"String"` in the derived column definition
- Filters to actions containing `scuba_tools`
- Groups by extracted metric type and counts occurrences
- Uses `--filter-sql` with `IS NOT NULL` to exclude non-matching rows
- Does NOT mix `--filter-sql` with `--where` (all filters go in `--filter-sql`)
- Reads `references/json-field-extraction.md` and/or `references/derived-columns.md` before constructing the query

**Fail signal:** Uses an existing derived column instead of `GET_JSON_OBJECT`, omits the action filter, or mixes `--filter-sql` with `--where`.

---

## Test 9: Conditional Aggregation

**What it tests:** The skill uses `--derived-cols` with `SUM(IF(...))` / `COUNT_IF` and `"type":"Aggregated"`.

**Pass criteria:**
- Uses `meta scuba.dataset query` with `--derived-cols`
- Derived columns use `SUM(IF(cpu_time > 1000, 1, 0))` or `COUNT_IF` for conditional counting
- Sets `"type":"Aggregated"` for all conditional aggregate derived columns
- Groups by `query_source`
- Produces two columns: above-threshold and at-or-below
- Reads `references/derived-columns.md` before constructing the derived columns

**Fail signal:** Sets type to `"Numeric"` instead of `"Aggregated"`, or runs two separate queries instead of conditional expressions.

---

## Test 10: UDF Discovery

**What it tests:** The skill uses `meta scuba.udf list` with `--search` for targeted function discovery.

**Pass criteria:**
- Uses `meta scuba.udf list --search=approx,count,distinct` or similar search terms
- Presents the results (name, description, signatures) to the user
- Reads `references/derived-columns.md` (the "Discovering and Using UDFs" section) if needed

**Fail signal:** Only lists all UDFs without search filtering, or fails to present function details.

---

## Test 11: Expression Validation

**What it tests:** The skill uses `meta scuba.column validate` to check expression syntax.

**Pass criteria:**
- Uses `meta scuba.column validate -d scuba_queries --sql "UPPER(hostname)" -t String`
- Sets type to `String` (since `UPPER()` returns a string)
- Reports whether the expression is valid or shows the error

**Fail signal:** Tries to validate by running a full query instead of using `column validate`.

---

## Test 12: UDF Usage Examples

**What it tests:** The skill uses `meta scuba.udf usage` to find real-world usage patterns.

**Pass criteria:**
- Uses `meta scuba.udf usage --udfs=APPROX_COUNT_DISTINCT_HLL`
- Presents extracted usage examples from the results

**Fail signal:** Manually constructs example queries instead of using the `udf usage` tool.

---

## Test 13: End-to-End UDF Workflow (Conditional Aggregation)

**Prompt:** "I want to count how many SQL-type queries vs non-SQL queries hit scuba_queries per hour over the last 24 hours. Find an aggregate function for conditional counting and execute the query"

**What it tests:** The skill follows the discover → learn → validate → execute workflow from `references/derived-columns.md`.

**Pass criteria:**
1. **Discover:** Uses `meta scuba.udf list --search=count,conditional,if` or similar
2. **Learn:** Uses `meta scuba.udf usage --udfs=COUNT_IF` or similar for the discovered function
3. **Validate:** Uses `meta scuba.column validate -d scuba_queries --sql "..." -t Aggregated` to test the expression. If `COUNT_IF` validation fails, the skill should adapt to `SUM(IF(...))` and re-validate
4. **Execute:** Uses `meta scuba.dataset query -d scuba_queries` with `--derived-cols` containing the validated expression (e.g., `SUM(IF(query_type = 'sql', 1, 0))`) with `"type":"Aggregated"`, `--view=time_series`, `--time-bucket="1 hour"`, and `--hours=24`
- Reads `references/derived-columns.md` before starting the workflow
- Checks `meta scuba.column values -d scuba_queries -c query_type` before constructing the condition (Critical Rule 1)

**Fail signal:** Skips any of the four workflow steps, sets type to `"Numeric"` instead of `"Aggregated"` for the aggregate expression, or skips column value validation before filtering.
