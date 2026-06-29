---
name: scuba-url-parser
description: Parse Scuba query URLs and generate SQL, or build Scuba UI URLs from query components. Use when the user provides a Scuba URL to analyze, wants to extract SQL from a Scuba query, or needs to generate a Scuba URL programmatically.
allowed-tools: Bash(python3:*), Bash(scuba:*), Bash(fburl:*), Skill(fburl-cli), Skill(scuba)
context: fork
agent: general-purpose
---

# Scuba URL Parser and Generator Skill

## Dependencies

This skill depends on two other skills. Before proceeding, check if they are
available and install them if not:

1. **fburl-cli** — Used for expanding shortened URLs (fburl.com, fb.me)
2. **scuba** — Used for dataset discovery and column value lookups

### Checking and installing dependencies

Check if each skill is installed by attempting to invoke it. If invocation fails
with a "skill not found" error, install them:

```bash
# Install fburl-cli skill
claude-templates skill fburl-cli install

# Install scuba skill
claude-templates skill scuba install
```

### Expanding shortened URLs

**MANDATORY: For shortened URLs (fburl.com, fb.me), you MUST use the Skill tool
to invoke `fburl-cli` FIRST. Only fall back to Bash if the skill invocation
fails.**

```
Skill(skill="fburl-cli", args="<shortened_url>")
```

If the `fburl-cli` skill is not available, use the `fburl` CLI directly as a
fallback (available on all Meta devservers):
```bash
fburl -r <shortened_url>
```

### Dataset Discovery

When the user doesn't know the exact dataset name or needs to explore available
columns, use the **scuba** skill:

```
Skill(skill="scuba", args="find dataset for <user's description>")
```

If the `scuba` skill is not available, install it with
`claude-templates skill scuba install`.

You are a specialized skill for:
1. **Parsing** Scuba query URLs and executing them using the scuba command
2. **Generating** Scuba query URLs from query components (dimensions, derived columns, filters)

**IMPORTANT:**
- After parsing a URL and generating SQL, execute it using scuba.
- **MANDATORY: After generating a Scuba URL, you MUST shorten it using `fburl <url>` before presenting it to the user.** Never show the user a long Scuba URL - always shorten it first.
- If the generated SQL fails, analyze the error and fix it intelligently (e.g., adjust time formats, fix syntax issues).

## Quick Start

### Parsing URLs (URL → SQL)

To parse a Scuba query URL and execute it:

```bash
python3 scripts/parse_scuba_url.py \
  'https://www.internalfb.com/intern/scuba/query/?dataset=my_dataset&drillstate=...'

# Or use the --quiet flag to only output the SQL:
python3 scripts/parse_scuba_url.py \
  --quiet 'https://www.internalfb.com/intern/scuba/query/?dataset=my_dataset&drillstate=...'

# Save the generated SQL to a file:
python3 scripts/parse_scuba_url.py \
  --save query.sql 'https://www.internalfb.com/intern/scuba/query/?dataset=my_dataset&drillstate=...'
```

### Generating URLs (Query Components → URL)

To generate a Scuba UI URL from query components:

```bash
# Use the request_size_bucket template (pre-built for analyzing request performance by size)
python3 scripts/generate_scuba_url.py \
  --dataset mgp_data_service_app_logs \
  --template request_size_bucket \
  --filter "data_solution=eq:P2P"

# Generate a custom query URL
python3 scripts/generate_scuba_url.py \
  --dataset my_dataset \
  --dimension user_id \
  --dimension country \
  --derived-column "error_count:SUM(IF(is_error, 1, 0)):Aggregated" \
  --filter "status=eq:success"

# Generate a Samples view URL (note: view types are case-sensitive)
python3 scripts/generate_scuba_url.py \
  --dataset my_dataset \
  --dimension user_id \
  --view Samples

# Shorten the generated URL
python3 scripts/generate_scuba_url.py \
  --dataset my_dataset --template request_size_bucket --shorten
```

## Input

You will receive a Scuba query URL in this format:

```
${{ scuba_url:str }}
```

The URL can be either:

- A full Scuba query URL:
  `https://www.internalfb.com/intern/scuba/query/?dataset=...`
- A shortened URL: `https://fburl.com/scuba/dataset_name/abc123`

## Instructions

Follow these steps to parse and execute the Scuba query:

### Step 1: Parse the URL

**If the URL is a shortened URL (fburl.com or fb.me), expand it first:**

1. **MUST use the fburl-cli skill FIRST**:
   ```
   Skill(skill="fburl-cli", args="<shortened_url>")
   ```
2. **Fallback to Bash ONLY if the skill fails**:
   ```bash
   fburl -r <shortened_url>
   ```

Then proceed with parsing the expanded URL.

To parse the URL:

1. Use the `scripts/parse_scuba_url.py` script with the URL
2. The script will extract URL parameters from the input URL
3. Identify the `dataset` parameter
4. Extract and URL-decode the `drillstate` parameter (it's a JSON object)
5. Extract the `pool` parameter if present (defaults to 'uber')
6. Extract the `view` parameter if present

### Step 2: Parse the Drillstate JSON

From the decoded drillstate JSON, extract the following key components:

1. **Time range**:
   - `start` (e.g., "-10080 minutes")
   - `end` (e.g., "now")

2. **Dimensions**:
   - `dimensions` array (columns to group by)

3. **Derived columns**:
   - `derivedCols` array containing:
     - `name`: column name
     - `sql`: SQL expression
     - `type`: "Aggregated" or other types

4. **Constraints/Filters**:
   - `constraints` (regular filters)
   - Each constraint has:
     - `col`: column name
     - `op`: operator (e.g., "contains", "equals", "regex", ">", "<", etc.)
     - `val`: value to filter by
   - `c_constraints` (compare constraints)
   - `b_constraints` (both constraints)
   - `filterStoreData` object

5. **Aggregations**:
   - `metric` (e.g., "avg", "sum", "count")
   - `aggregateList`

6. **Other settings**:
   - `top` (limit)
   - `order` (order by column)
   - `order_desc` (descending order boolean)
   - `view` (visualization type)

### Step 3: Generate Scuba SQL

Based on the parsed drillstate, construct a valid Scuba SQL query following this
template:

```sql
SELECT
    <dimensions>,
    <derived_columns_or_aggregations>
FROM <dataset>
WHERE
    time > <start_time>
    AND time < <end_time>
    <additional_constraints>
GROUP BY <dimensions>
ORDER BY <order_column> <DESC/ASC>
LIMIT <top>
```

**Important considerations:**

- Convert time expressions (e.g., "-10080 minutes") to proper time format
- Handle derived columns by including their SQL expressions in the SELECT clause
- Apply all filters from constraints arrays
- **Supported constraint operators:**
  - `contains`: Uses `strpos(column, 'value') > 0` for substring matching
  - `equals` or `=`: Uses `column = 'value'`
  - `not_equals` or `!=`: Uses `column != 'value'`
  - `regex`: Uses `REGEXP_LIKE(column, 'pattern')`
  - `greater_than` or `>`: Uses `column > value`
  - `less_than` or `<`: Uses `column < value`
  - `greater_equals` or `>=`: Uses `column >= value`
  - `less_equals` or `<=`: Uses `column <= value`
  - `in`: Uses `column IN (value1, value2, ...)`
  - `none`: Tagset exclusion — uses `strpos(column, 'value') = 0` (aliased to `none_contains`)
- Use proper SQL syntax for Scuba queries
- Handle NULL checks and type casts as specified in derived columns
- **Note:** The `pool` parameter is extracted from the URL but not included in
  the SQL. It should be passed separately to the scuba command if needed (e.g.,
  `--pool uber`). The generated SQL shown by parse_scuba_url.py does not include
  pool information.

### Step 4: Execute the Generated SQL

After parsing and generating SQL, execute it using scuba:

```bash
scuba -e "<generated_sql>"
```

**If execution fails:**

- Analyze the error message
- Fix common issues:
  - Wrong time functions: ensure using `now()` not `unix_timestamp()`
  - Syntax errors: check for proper escaping and quotes
  - Type errors: verify column types match operations
- Retry with the corrected SQL
- Show both the original and corrected SQL to the user

The parse_scuba_url.py script shows you the exact SQL to run.

### Step 5: Output Results

1. Display the parsed query components (dataset, time range, dimensions,
   filters)
2. Show the generated SQL query
3. Optionally save the SQL to a file if requested (use --save flag)
4. Show the scuba-cli command for execution
5. Execute the query using scuba-cli if requested
6. Display the results

## Error Handling

- If the URL is malformed, explain what's wrong and ask for a valid Scuba URL
- If the drillstate JSON cannot be parsed, show the parsing error
- If the SQL generation fails, explain which component caused the issue
- If scuba execution fails:
  - Show the error message
  - Identify the issue (syntax error, wrong function, type mismatch, etc.)
  - Fix the SQL and retry
  - Common fixes:
    - `unix_timestamp()` → `now()` (UNIX_TIMESTAMP does not exist in Scuba)
    - `interval '24' hour` → `now()-86400` (interval syntax not supported)
    - `interval '1' day` → `now()-86400` (use seconds-based arithmetic)
    - Date string → Unix epoch timestamp
    - String type mismatches → proper type casting
  - Show what was corrected so the user understands the fix

**CRITICAL: Scuba Time Syntax**:
- ✅ Use `now()-86400` for 24 hours ago (seconds-based arithmetic)
- ✅ Use `now()-3600` for 1 hour ago
- ❌ NEVER use `interval '24' hour` or `now() - interval '1' day` - NOT SUPPORTED
- ❌ NEVER use `UNIX_TIMESTAMP()` - does NOT exist (use `now()` instead)

## Examples

### Example 1: Full Scuba Query URL with Filters

For a URL like:

```
https://www.internalfb.com/intern/scuba/query/?dataset=llm_service&drillstate={"constraints":[{"col":"client_id","op":"contains","val":"data_platform"}],...}&pool=uber&view=table_client
```

You should:

1. Extract dataset: `llm_service`
2. Parse the drillstate to find:
   - Constraints: `client_id` contains `data_platform`
   - Dimensions, derived columns, time range, etc.
3. Generate appropriate SQL with the filter:
   ```sql
   WHERE time > ... AND time < ... AND strpos(client_id, 'data_platform') > 0
   ```
4. Use the scuba-cli Claude skill to execute it

### Example 2: Full Scuba Query URL Without Filters

For a URL like:

```
https://www.internalfb.com/intern/scuba/query/?dataset=mgp_data_service_app_logs&drillstate={...}&pool=uber&view=table_client
```

You should:

1. Extract dataset: `mgp_data_service_app_logs`
2. Parse the drillstate to find dimensions, derived columns, time range, etc.
3. Generate appropriate SQL
4. Use the scuba-cli Claude skill to execute it

### Example 3: Shortened fburl.com URL

For a shortened URL like:

```
https://fburl.com/scuba/mgp_data_service_app_logs/68lh6eb9
```

You should:

1. **MUST use the fburl-cli skill FIRST** to expand the URL:
   ```
   Skill(skill="fburl-cli", args="https://fburl.com/scuba/mgp_data_service_app_logs/68lh6eb9")
   ```
   Fallback to Bash ONLY if skill fails: `fburl -r <url>`
2. The expansion will return the full Scuba query URL
3. Then parse the expanded URL using parse_scuba_url.py
4. Then proceed with normal parsing

### Example 4: Save SQL to File

```bash
python3 scripts/parse_scuba_url.py 'https://www.internalfb.com/intern/scuba/query/?dataset=my_dataset&drillstate=...' --save my_query.sql
```

This will parse the URL, generate SQL, and save it to `my_query.sql`.

**IMPORTANT**: Always show the generated SQL query before executing it, so the
user can verify it's correct.

### Example 5: JOIN Query URL

Scuba supports JOIN queries between two tables. JOIN URLs differ from
single-table queries in these ways:

- The `dataset` parameter is a **JSON object** (not a plain string) containing
  the join configuration (table names, join columns, join type)
- The `pool` parameter is prefixed with `join:` (e.g., `join:uber`)
- All column names are **table-prefixed**: `table_name.column_name`

The parser automatically detects JOIN URLs and generates the correct SQL with
a `FROM ... JOIN ... ON ...` clause.

For a JOIN URL like:

```
https://www.internalfb.com/intern/scuba/query/?dataset={"table1":"security_hsm_ncipher_slots","table2":"security_hsm_ncipher_modules","table1JoinColumn":"hostname","table2JoinColumn":"hostname","joinType":"INNER"}&pool=join:uber&drillstate=...
```

The parser will:

1. Detect the JSON `dataset` parameter and parse the join configuration
2. Generate SQL with a proper JOIN clause:
   ```sql
   SELECT
       security_hsm_ncipher_slots.hostname,
       security_hsm_ncipher_modules.product_name
   FROM security_hsm_ncipher_slots
   INNER JOIN security_hsm_ncipher_modules
       ON security_hsm_ncipher_slots.hostname = security_hsm_ncipher_modules.hostname
   WHERE
       time > now() - 60 * 60
       AND time < now()
   GROUP BY security_hsm_ncipher_slots.hostname, security_hsm_ncipher_modules.product_name
   ```
3. Display JOIN details (tables, join columns, join type) in the output

---

## Part 2: Generating Scuba UI URLs

The `generate_scuba_url.py` script creates Scuba query URLs that can be opened
in a browser. This is useful for:

- Sharing queries with team members
- Creating bookmarkable analysis URLs
- Building complex queries programmatically

**CRITICAL: Always shorten generated URLs with `fburl <url>` before presenting
them to the user. Long Scuba URLs should never be shown directly.**

### Derived Column Types

When adding derived columns, specify the correct type:

- **Aggregated**: For aggregate functions (SUM, COUNT, AVG, etc.). These are
  computed across rows in each group.
- **Normal**: For row-level expressions (CASE, IF, etc.) that should be included
  in GROUP BY. Use this for bucketing columns like `request_size_bucket`.
- **String**: For string expressions that don't aggregate.

**Important**: Non-aggregated expressions like CASE statements must use type
"Normal" so they are included in the GROUP BY clause.

### Pre-built Templates

#### request_size_bucket Template

Analyzes request performance by size bucket. Useful for identifying timeout
patterns related to request size.

```bash
python3 scripts/generate_scuba_url.py \
  --dataset mgp_data_service_app_logs \
  --template request_size_bucket \
  --filter "data_solution=eq:P2P" \
  --filter "tw_task_handle=substr:data_platform_online/mgp_data_platform_online_batch_data_service"
```

This template includes:
- **Dimensions**: `data_solution`, `llm_model_id`, `request_size_bucket`
- **Size buckets**: Small (<2K), Medium (2K-5K), Large (5K-10K), XLarge (10K-100K), XXLarge (100K+)
- **Metrics**: `timeout_errors`, `exception_rate_pct`, `app_server_success_rate`,
  `llm_client_success_rate`, `llm_server_success_rate`

### Custom Query Generation

Build custom queries with dimensions, derived columns, and filters:

```bash
python3 scripts/generate_scuba_url.py \
  --dataset my_dataset \
  --time-range "-24 hours" \
  --dimension user_id \
  --dimension country \
  --derived-column "error_count:SUM(IF(is_error, 1, 0)):Aggregated" \
  --derived-column "status_bucket:CASE WHEN status = 200 THEN 'success' ELSE 'error' END:Normal" \
  --filter "status=eq:200" \
  --filter "name=substr:test" \
  --view Samples \
  --limit 50 \
  --shorten
```

### View Types

The `--view` flag sets the Scuba visualization type. **View types are case-sensitive.** Common values:

| View Type | Description |
|-----------|-------------|
| `table_client` | Table view (default) |
| `Samples` | Individual sample rows |
| `timeseries` | Time series chart |

### Querying Hive-backed datasets via Presto

Some Scuba datasets have Hive table backings — the same dataset name exists as both a native Scuba dataset and a Hive/Presto table, and the two can return **different data**. To query the underlying Presto table from a Scuba URL, pass `--pool=presto:<hive_namespace>` and supply the Hive table name as the `--dataset` value (which may or may not include a `scuba_` prefix — see below).

```bash
# Hive table name matches the native Scuba dataset name (no prefix)
python3 scripts/generate_scuba_url.py \
  --dataset <table_name> \
  --dimension <col> \
  --view Samples \
  --pool presto:<hive_namespace> \
  --shorten

# Hive table name has a "scuba_" prefix that the native Scuba dataset name lacks —
# pass the full Hive name (with prefix) as --dataset
python3 scripts/generate_scuba_url.py \
  --dataset scuba_<native_name> \
  --pool presto:<hive_namespace> \
  --shorten
```

**Look up whether the Hive table has a `scuba_` prefix** via `meta presto.table info --table=<X>` (try both with and without the prefix). The `namespace:` field in the output is what goes after `presto:`, and the table name shown is what `--dataset` should be set to.

**Common gotchas:**
- `--pool=presto` (no namespace) errors with `rockfort_express.presto.root not found`. The namespace is required.
- If `--dataset` doesn't match the actual Hive table name when routed through Presto, you'll get `metastore_NoSuchObjectException: Table not found; databaseName='<ns>', tableName='<X>'`. Concrete cases at Meta: `videos.scuba_su_chop` exists but `videos.su_chop` does not — so the URL needs `--dataset=scuba_su_chop --pool=presto:videos`. Same for `videos.scuba_copyright_infra_funnel`, `videos.scuba_vi_comp_match_segment`, `scuba.scuba_su_record_node`, `scuba.scuba_reporting_service_event`. Conversely, `videos.mui_service` and `videos.su_sports_classifier` are bare in Hive — use `--dataset=mui_service` / `--dataset=su_sports_classifier` for those.
- `--pool` is ignored when `--join-table` is set — the join builder auto-prefixes with `join:` regardless.

### Filter Syntax

Filters use the format `column=operator:value`:

- `status=eq:success` - equals
- `status=neq:error` - not equals
- `name=substr:test` - contains substring
- `name=!substr:internal` - does not contain
- `count=gt:100` - greater than
- `count=lt:1000` - less than
- `count=gte:50` - greater than or equal
- `count=lte:500` - less than or equal
- `pattern=regeq:^test.*` - regex match

### JOIN Query Generation

Generate Scuba URLs for JOIN queries between two tables:

```bash
python3 scripts/generate_scuba_url.py \
  --dataset security_hsm_ncipher_slots \
  --join-table security_hsm_ncipher_modules \
  --join-column1 hostname \
  --join-column2 hostname \
  --join-type INNER \
  --dimension "security_hsm_ncipher_slots.hostname" \
  --dimension "security_hsm_ncipher_modules.product_name" \
  --filter "security_hsm_ncipher_slots.mode=eq:operational" \
  --shorten
```

**JOIN CLI arguments:**

- `--join-table` — Second table to join with. When specified, `--dataset`
  becomes table1.
- `--join-column1` — Join column in table1 (required with `--join-table`)
- `--join-column2` — Join column in table2 (required with `--join-table`)
- `--join-type` — JOIN type: `INNER`, `LEFT`, `RIGHT`, `FULL` (default: `INNER`)

**Key differences from single-table URLs:**

- The URL's `dataset` parameter becomes a JSON object with join config
- The `pool` is auto-set to `join:uber`
- Use table-prefixed column names: `table_name.column_name`

### URL Shortening (MANDATORY)

**You MUST always shorten generated Scuba URLs before presenting them to the user.**
Long Scuba URLs are unwieldy and should never be shown directly.

**How to shorten:**
```bash
fburl "<long_scuba_url>"
```

This returns a short URL like `https://fburl.com/scuba/dataset_name/abc123`.

**Workflow for generating URLs:**
1. Build the Scuba URL using `ScubaQueryBuilder` or the CLI
2. Run `fburl <url>` to get the shortened URL
3. Present ONLY the shortened URL to the user

If you need the full URL for debugging, use `--quiet` to get the raw URL,
but still shorten it before showing to the user.

---

## Issues & Feedback

This skill has been tested with real Scuba URLs and handles most common query
patterns. However, Scuba's URL format is complex and evolving.

**If you encounter a Scuba URL that fails to parse or generates incorrect SQL:**

1. Note the error message or incorrect output
2. Copy the full Scuba URL (expanded, not shortened)
3. Reach out to the skill author: **@carlbellingan**
4. or... use Claude to fix it, submit a diff, and add me as a reviewer :)

Your feedback helps improve this skill for everyone. All reported issues will be
investigated and fixed.
