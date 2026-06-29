---
name: presto-query
author: oncall+datamate
description: Query Presto/Hive data, explore table metadata, lint SQL, and search for functions. Use when users ask to run SQL queries, get table schemas, validate queries, or find Presto functions. Uses the same utilities that empower Datamate.
allowed-tools: Bash(meta presto*), Bash(meta daiquery*), Bash(meta data.semantic-model*)
hooks:
  PreToolUse:
    - matcher: "Bash"
      hooks:
        - type: command
          command: "python3 /usr/local/claude-templates-cli/components/helpers/track_datamate_tool_events.py --plugin-root /home/mkarrmann/.claude/agent-market/plugins/datamate || true"
          async: true
          timeout: 5
  PostToolUse:
    - matcher: "Bash"
      hooks:
        - type: command
          command: "python3 /usr/local/claude-templates-cli/components/helpers/track_datamate_tool_events.py --plugin-root /home/mkarrmann/.claude/agent-market/plugins/datamate || true"
          async: true
          timeout: 5
---

# Presto/Hive Query Skill

## Overview

Execute Presto/Hive queries and explore table metadata using the `meta presto.*` CLI. If you do not yet know a SPECIFIC table and namespace to use, you must use the data discovery skill to find them first.

## When to Use

- User asks to "run a SQL query" or "execute a presto query"
- User asks about table metadata, columns, or partitions
- User asks to "lint" or "validate" SQL queries
- User mentions table names like `dim_all_users`, `fact_*`, etc.
- User asks about Presto functions or SQL syntax
- User asks for distinct/sample values from a column

## Critical Rules

1. **ALWAYS check for and fetch the semantic model before writing queries** - This is the single most-skipped step and the #1 source of wrong results. As soon as you know the table (even before `table info`), run `meta data.semantic-model check --table=namespace/table_name` to see if a semantic model exists, then `meta data.semantic-model fetch --xid="asset://semantic.model/..."` (using the XID from the check result) to load the content. The semantic model defines which columns to use, correct filter values, required joins, and known gotchas. If a model is returned, use it to guide column selection, filters, and aggregations, and cite it in your response. If no model exists, proceed without one. **Do NOT skip this step** — queries written without the semantic model frequently return wrong results because column names are ambiguous. (This command is pre-approved for this skill — there is no reason to skip it.)
2. **ALWAYS get table info** - Understand the schema before writing queries via `meta presto.table info`
3. **Search function registry for non-trivial computations** - Before writing custom logic for computations like median, percentile, statistics, or array operations, use `meta presto.function search` to check if a built-in or UDF function already exists. Do not assume a function is unavailable — verify first.
4. **ALWAYS validate SQL before executing** - Use `meta presto.query lint`
5. **ALWAYS include ds filter** - Most tables are partitioned by `ds`
6. **Include explicit LIMIT unless the query is structurally bounded** - Default to an explicit `LIMIT` sized to your use case (e.g., `LIMIT 10` for sampling, `LIMIT 1000` for inspection). There is no auto-cap — queries without `LIMIT` scan the full table. Omit `LIMIT` only for the cases listed in **When LIMIT is Optional** below.
7. **ALWAYS specify namespace** when querying tables
8. **ALWAYS invoke the `accessmate` skill on permission errors** - Resolve permission errors

Failing to do any of these critical rules will make your query INVALID even if it runs successfully.

## Workflow

### Step 1: Get Table Info

```bash
meta presto.table info --table=dim_all_users --namespace=di
```

Learn: columns, partitions, ds filtering requirements.

### Step 2: Fetch Semantic Model

**Before writing any query**, look up and fetch the semantic model for the table:

```bash
# Check if a semantic model exists for this table
meta data.semantic-model check --table=namespace/table_name

# Fetch the full semantic model content (using XID from check result)
meta data.semantic-model fetch --xid="asset://semantic.model/pillar/domain/model"
```

If a semantic model is returned, use it to guide column selection, filters, joins, and aggregations. If no model exists, proceed to the next step.

### Step 3 (Optional): Search Function Registry (for non-trivial computations)

**Before writing custom logic** for computations like median, percentile, statistics, or complex array/math operations, search the function registry:

```bash
meta presto.function search --query="median"
```

If a matching function exists, prefer it over custom SQL logic. This is especially important for `py.*` Python UDF builtins (e.g. `py.numpy.median`, `py.re.findall`, `py.stats.detect_spikes`).

### Step 4: Validate SQL

```bash
meta presto.query lint --sql="SELECT * FROM dim_all_users WHERE ds = '2024-01-15' LIMIT 100" --namespace=di
```

### Step 5: Execute Query

**Use `--output=csv` to save tokens on data payloads:**

```bash
meta presto.query execute --sql="SELECT * FROM dim_all_users WHERE ds = '2024-01-15' LIMIT 100" --namespace=di --output=csv
```

The CLI handles authentication internally — no manual CAT token minting is needed.

For long queries that exceed shell argument limits, use shell substitution:

```bash
meta presto.query execute --sql="$(cat /tmp/query.sql)" --namespace=di --output=csv
```

If the above does not work, use the script to execute the query instead (requires www checkout):
```bash
phps PrestoQueryScriptController \
  --query "SELECT * FROM dim_all_users WHERE ds = '2024-01-15' LIMIT 100" \
  --namespace "di"
```

**Output**: With `--output=csv`, results are returned as CSV with a header row. With `--output=json`, results are returned as structured JSON. SQL `NULL` values are represented as unquoted `null`; a literal string value `null` is quoted as `"null"`. Present results as a markdown table only when the user asks to see the data. Otherwise, summarize key findings.

### Step 6: Offer to Save or Share the Results

After a successful query that returns data rows, **offer to save or share** — one short sentence after presenting the results. The user opts in; this is not automatic.

**Skip the offer** if any of these are true:
- Query returned zero rows
- Query failed
- Query was schema exploration (`SHOW COLUMNS`, `DESCRIBE`, `SHOW PARTITIONS`)
- Query was a simple preview (`SELECT * FROM table LIMIT 5` with no meaningful filters)

Ask in one sentence: *"Want me to save this — as a Phabricator paste (a shareable web link), a DaiQuery notebook, a Google Doc, or a shareable analysis page?"*

Pick the path based on the user's reply:

#### Option A — Phabricator paste ("paste")

**Re-run the same query** with `--upload-to-paste` (and optionally `--paste-title`):

```bash
meta presto.query execute --sql="<THE SQL QUERY>" --namespace=<NAMESPACE> --output=csv --upload-to-paste --paste-title="<short description>"
```

The CLI creates a private paste (author-only access) and prints the paste URL. Report the paste link inline.

**If paste creation fails** (network error, CLI not available), silently skip — never block the query results.

#### Option B — DaiQuery notebook ("notebook", "daiquery", "save the query")

Produces a re-runnable notebook the user can send to teammates or build on.

1. **Create the notebook** (DaiQuery notebooks are private to the author by default — only accessible when explicitly shared):
   ```bash
   meta daiquery.notebook create --title="<short description of the question>" --output=json
   ```
   Extract the notebook URL from the output.

2. **Edit the default cell with the SQL** (notebooks are created with one empty cell — overwrite it):
   ```bash
   meta daiquery.notebook add-cell --url="<notebook_url>" --sql="<THE SQL QUERY>" --namespace=<NAMESPACE> --name=main_query --cell-index=0
   ```

3. **Add a markdown context cell at the top:**
   ```bash
   meta daiquery.notebook add-cell --url="<notebook_url>" --text="## <Question the query answers>\n\nGenerated from a Datamate session. Run the query below to reproduce the results." --cell-index=0
   ```

4. **Report the notebook URL** inline with the query results:
   ```
   DaiQuery notebook: https://www.internalfb.com/intern/daiquery/<notebook_id>/
   ```
   Keep the report to one line — the notebook URL is supplementary to the query results, not the main output.

**If notebook creation fails** (network error, CLI not available), silently skip — never block the query results. The query result is the primary output; the notebook is a bonus artifact.

#### Option C — Shareable analysis page ("share", "analysis page", "publish", "export")

Produces a narrated artifact (chart + query + methodology + follow-ups) at a URL the user can paste into a doc, chat, or wiki.

Invoke the `share-analysis` skill with these inputs:

- `title`: one-line observation including a headline number from the result set
- `hook_kind`: `presto`
- `query_config`: `{ "sql": "<THE SQL>", "namespace": "<NAMESPACE>" }`
- `chart_kind`: `line` for time series, `bar` for grouped counts, `table` for raw samples
- `chart_x` / `chart_y`: the result columns for the chart axes (skip for `table`)
- `visibility`: `private` (author-only access by default)
- `opportunities` (optional): 2–6 follow-ups as `{category, subcategory, title, description, action_type, priority}`. Categories: `investigate`, `build`, `fix_data`. Anchor each to a real number from the result.

The skill returns a fullscreen artifact URL — print it inline.

#### Option D — Google Doc ("google doc", "doc", "document", "report")

Produces a formatted Google Doc with the query, results, and context — suitable for sharing in docs, tasks, or email threads.

Invoke the `google-docs` skill to create a new document with restricted sharing (author-only access by default):

- **Title**: `Presto Query Results: <short description of the question>`
- **Sharing**: Restricted — only the author can access. Do not share with "anyone with the link" or broader audiences.
- **Content** (in this order):
  1. A heading with the question the query answers
  2. The SQL query in a code block
  3. Namespace and row count
  4. Results as a formatted table
  5. A "Generated by Datamate" note at the bottom with the current date

Print the doc URL inline:
```
Google Doc: <doc_url>
```

**If doc creation fails** (network error, skill not available), silently skip — never block the query results.

If the user says no or doesn't respond, move on.

## Available Tools

| Tool | CLI Command | Purpose |
|------|-------------|---------|
| Table Info | `meta presto.table info` | Get table metadata (columns, partitions, descriptions) |
| Lint Query | `meta presto.query lint` | Validate SQL syntax without executing |
| Execute Query | `meta presto.query execute` | Execute Presto SQL queries |
| Function Search | `meta presto.function search` | Search for Presto functions by name |
| Query Dimensions | `meta presto.query dimensions` | Analyze query to extract table/column metadata |
| Create Notebook | `meta daiquery.notebook create` | Create a new DaiQuery notebook |
| Add Cell | `meta daiquery.notebook add-cell` | Add a SQL or markdown cell to a notebook |
| Upload to Paste | `meta presto.query execute --upload-to-paste` | Upload query results as a private Phabricator paste |
| Create Google Doc | `google-docs` skill | Create a formatted Google Doc with query results |

## CLI Reference

All query operations use the `meta presto.*` CLI commands. Notebook export uses `meta daiquery.*` commands (see Available Tools above).

### table_info

Get Hive table metadata (columns, partitions, descriptions).

```bash
meta presto.table info --table=TABLE_NAME --namespace=NAMESPACE
```

**Arguments:**
- `--table`, `-t` (required): Table name. Supports `namespace:table` format (e.g. `di:dim_all_users`)
- `--namespace`, `-n` (optional): Namespace (e.g., "di", "bi"). Auto-detected if not provided.
- `--output` (optional): Output format (`json`, `yaml`, `table`). Default: `table`

**Output:** Table name, namespace, description, table type, total columns, partition columns, and standard columns (each with name, type, description).

### lint_query

Validate a Presto SQL query without executing it.

```bash
meta presto.query lint --sql="SELECT col FROM table WHERE ds = '2024-01-01'" --namespace=di
```

**Arguments:**
- `--sql`, `-s` (required): The Presto SQL query to validate. For long queries, use shell substitution: `--sql="$(cat /tmp/query.sql)"`
- `--namespace`, `-n` (optional): The namespace for the tables. Default: `di`
- `--output` (optional): Output format (`json`, `yaml`, `table`). Default: `table`

**Output:** `is_valid` (true/false), `message`, `is_lint_skipped` (true/false). Exits non-zero if query is invalid.

### execute_query

Execute a Presto SQL query.

```bash
meta presto.query execute --sql="SELECT col FROM table WHERE ds = '2024-01-01' LIMIT 10" --namespace=di --output=csv
```

**Arguments:**
- `--sql`, `-s` (required): The Presto SQL query to execute. For long queries that exceed shell argument limits, use shell substitution: `--sql="$(cat /path/to/query.sql)"`
- `--namespace`, `-n` (optional): The namespace for the tables. Default: `di`
- `--limit`, `-l` (optional): Maximum rows to return. No default — pass `--limit` explicitly or include `LIMIT` in your SQL (limit in SQL takes priority over limit arg).
- `--output` (optional): Output format (`json`, `csv`, `table`). Default: `table`
- `--show-url` (optional): Show the Presto query ID for debugging
- `--upload-to-paste` (optional): Upload query results to a private Phabricator paste and print the URL
- `--paste-title` (optional): Title for the paste (default: auto-generated from SQL). Only used with `--upload-to-paste`

**Output:** Tabular result rows with column headers. Shows row count. SQL with no `LIMIT` runs as written (no auto-cap).

**Security:** Queries are validated via AccessMate and DPAS before execution.

### function_search

Search for Presto functions by name or keyword.

```bash
meta presto.function search --query="json_extract"
```

**Arguments:**
- `--query`, `-q` (required): Function name or keyword to search for
- `--limit`, `-l` (optional): Maximum number of results. Default: `20`
- `--output` (optional): Output format (`json`, `yaml`, `table`). Default: `table`

**Output:** Matching functions with `name`, `signature`, and `description`. Also shows `functions_found` count and optional `additional_instructions`.

### query_dimensions

Analyze a SQL query to extract table and column metadata.

```bash
meta presto.query dimensions --sql="SELECT a.col FROM table_a a JOIN table_b b ON a.id = b.id" --namespace=di
```

**Arguments:**
- `--sql`, `-s` (required): The Presto SQL query to analyze. For long queries, use shell substitution: `--sql="$(cat /path/to/query.sql)"`
- `--namespace`, `-n` (optional): The namespace for the tables. Default: `di`
- `--output` (optional): Output format (`json`, `yaml`, `table`). Default: `table`

**Output:** Three sections: referenced tables, table metadata (with referenced columns), and query output columns.

## Environment Detection

The `meta presto.*` CLI commands work from any environment (fbcode, www, laptop). For legacy `phps` ScriptController commands (www checkout only), see [references/phps-scriptcontrollers.md](references/phps-scriptcontrollers.md).

## Timezone

Meta's Presto defaults to `US/Pacific` (PST/PDT), NOT UTC. Date functions like `NOW()`, `CURRENT_DATE`, `CURRENT_TIMESTAMP`, and timestamp comparisons use Pacific time. If your analysis requires UTC, cast explicitly: `your_ts_col AT TIME ZONE 'UTC'`, or set the session timezone: `SET SESSION timezone = 'UTC'` before your query. For hour-level or finer time columns, include timezone in the alias (e.g., `AS hour_of_day_pacific`, `AS created_at_utc`) so labels propagate correctly to charts and DataFrames.

## When LIMIT is Optional

Omit `LIMIT` only when the result set is bounded by the query's structure, not by the input table's size:

- **Aggregations that collapse the row count** — scalar aggregates (`COUNT(*)`, `AVG`, `MIN`/`MAX`, `COUNT(DISTINCT ...)`) or `GROUP BY` on a low-cardinality column (e.g., `status`, `country`). High-cardinality `GROUP BY` (e.g., `user_id`) still needs `LIMIT`.
- **Inherently bounded `WHERE`** — primary-key / unique-key lookup, `IN` with a small explicit list, or a partition you've already sized with `COUNT(*)`.
- **Full-data correctness** — exact percentiles/distributions and audit queries where `LIMIT` would silently drop matches (e.g., `SELECT user_id, email FROM ... WHERE email IS NULL`). Use `LIMIT 1` for `EXISTS`-style "any row?" checks.
- **Window functions / joins requiring full coverage** — apply `LIMIT N` (or filter on the row number) in the outer query if you only want top-N.

Quick check before omitting: *"Is the maximum row count bounded by something other than the input table size?"* If no, add `LIMIT`. `ORDER BY` alone does not bound output — pair it with `LIMIT N`.

## Common Mistakes

| Mistake | Fix |
|---------|-----|
| Querying without fetching semantic model | Run `meta data.semantic-model check --table=namespace/table_name` after `table info` — column names are often ambiguous without the model |
| Execute without validation | Always use `meta presto.query lint` first |
| Missing ds filter | Add `WHERE ds = '2024-01-15'` for partitioned tables |
| Missing LIMIT | Add an explicit `LIMIT` — no auto-cap. See **When LIMIT is Optional** for the structurally-bounded cases where omitting it is correct. |
| Wrong namespace | Use `meta presto.table info --table=TABLE_NAME` without `--namespace` to auto-detect. See **Namespace Discovery** below for common namespaces |
| Assuming UTC timezone | Meta's Presto defaults to US/Pacific — use `AT TIME ZONE 'UTC'` or `SET SESSION timezone = 'UTC'` when UTC is needed |
| Ambiguous hour-level alias (e.g., `AS hour`) | For hour-level or finer, include timezone in alias: `AS hour_of_day_pacific` or `AS hour_utc` — labels flow to charts and DataFrames |
| Passing `-r "<reasoning>"` to `presto.query` | `presto.query` does **not** accept `-r` / `--reasoning`. The reasoning flag exists on `scuba.dataset` commands (search, query, info, metadata) for telemetry — it is not part of presto. Use `--sql=…` / `-s …` to pass the SQL and omit any reasoning flag. |
| Passing `--cats-file=…` to `presto.query` | `--cats-file` does **not** exist on any `presto.query` subcommand. The CLI handles authentication internally — do not pass CAT tokens. Valid flags for `presto.query execute` are `--sql`/`-s`, `--namespace`/`-n`, `--limit`/`-l`, `--show-url`, and `--output`. |
| Querying `information_schema.tables`/`.columns` (or `SHOW TABLES`/`SHOW SCHEMAS`) for discovery | **Never**: these enumerate Hive metastore metadata across millions of tables and cause `OVERALL_TIMEOUT`. Find tables via the `data_discovery` skill (`meta search.data deep-search`); inspect a known table via `meta presto.table info`. |

## Python UDF Builtins (`py.*`)

When a query needs regex, statistics, trend analysis, date math, or data reshaping that native Presto functions don't cover well, prefer `py.*` functions (e.g. `py.numpy.percentile`, `py.scipy.ttest`, `py.stats.trend`). See [references/python-udf-builtins.md](references/python-udf-builtins.md) for the full catalog.

## Troubleshooting

| Issue | Solution |
|-------|----------|
| "Table not found" | Verify table name, use `meta presto.table info --table=TABLE_NAME` without `--namespace` to auto-detect the correct namespace |
| "Unsupported namespace" or "Invalid namespace" | See **Namespace Discovery** below. Do NOT guess namespaces by trial and error |
| "Too many queued queries" | Quota limit hit. Wait 60-120 seconds before retrying. Use `--show-url` to monitor query status in the Presto UI. Do not retry immediately — rapid retries worsen queue congestion |
| "Query exceeded distributed user memory limit" | See **Query Memory Limit Exceeded** below |
| "Query times out" | Add tighter LIMIT, filter on ds partition, reduce columns selected |
| "Column not found" | Use `meta presto.table info` to check available columns |
| Results exceed CLI memory | See **Results Exceed CLI Memory** below |
| `sync_partition_metadata` fails with websocket URL | This operation is not supported via the Presto CLI. Use the Dataswarm pipeline or UI to sync partitions instead. Do not retry — the error is expected |
| Permission denied / ACL blocked | Follow the **Permission Denied** workflow below |

## Namespace Discovery

If you encounter "Unsupported namespace" or "Invalid namespace", use `meta presto.table info --table=TABLE_NAME` **without** `--namespace` to auto-detect the correct namespace for a table.

Common namespaces (not exhaustive): `di`, `bi`, `dps`, `ad_metrics`, `ad_delivery`, `infrastructure`, `bizapps`, `messages`, `instagram`, `gen_ai`, `ai_infra`, `commerce`, `growth`, `integrity`, `marketplace`, `reels`, `video`, `groups`, `events`, `search`, `feed`, `ads`, `people_and_data`, `xfn`, `scuba`.

Do NOT guess namespaces by trial and error. Always use `meta presto.table info` to discover the correct namespace first. The `presto` CLI's `--help` output does not list valid namespaces.

## Query Memory Limit Exceeded

If a query fails with "Query exceeded distributed user memory limit", the query itself is consuming too much memory during execution on the Presto cluster. **Restructure the query before retrying** — falling back to the legacy `presto` CLI will NOT help because it hits the same cluster memory limits.

Steps to reduce query memory usage:

1. **Add tighter LIMIT** and narrower `ds` filters to reduce data scanned
2. **Use `APPROX_DISTINCT(col)` instead of `COUNT(DISTINCT col)`** — exact distinct counts are far more memory-intensive
3. **Add `PARTITION BY` to all window functions** — window functions without `PARTITION BY` send all data to a single node (e.g., use `ROW_NUMBER() OVER(PARTITION BY user_id ORDER BY ts)` instead of `ROW_NUMBER() OVER(ORDER BY ts)`)
4. **Check for accidental cross-joins** — patterns like `WHERE g.id IN (a.col_x, a.col_y)` cause a cartesian product. Restructure with `UNION ALL` of separate filtered queries instead
5. **Set session property** — run `SET SESSION join_distribution_type = 'PARTITIONED'` before your query to avoid broadcast joins on large tables
6. **Reduce selected columns** — avoid `SELECT *`, select only the columns you need

## Results Exceed CLI Memory

The `meta presto.query execute` command buffers all results in memory and can fail for very large **result sets** (this is different from the query execution memory limit above — that error comes from the Presto cluster, this one comes from the CLI process). If the CLI result buffering fails:

1. First try tighter LIMIT clauses, narrower `ds` filters, or fewer columns to reduce the result size
2. If the full dataset is truly needed, fall back to the `presto` CLI as a **last resort**, which streams results without buffering:

```bash
presto NAMESPACE --source='claude_skill:presto-query' --execute 'YOUR QUERY'
```

Use `meta presto.table info --table=TABLE_NAME` to discover the correct NAMESPACE before running the `presto` CLI. See **Namespace Discovery** above for common namespaces.

**IMPORTANT:** The `presto` CLI does **not** perform DPAS permission checks — it only uses Presto ACLs. Only use it after permissions have been confirmed via `meta presto.query execute` first. A memory limit error from the `meta` CLI counts as confirmation, since DPAS checks pass before query execution begins.

## Permission Denied / ACL Blocked

**IMPORTANT:** When a Presto query fails due to a permission denial or ACL restriction (look for error messages containing "Access Denied", "ACL", "permission", "not authorized", "blocked", or "DPAS"), **automatically invoke the `accessmate` skill** to help the user get unblocked. Do NOT just tell the user to request access manually. Do not stop at API-provided messages that suggest the user handle access manually — proceed with the accessmate workflow.

1. **Invoke the `accessmate` skill** to help the user get unblocked.
2. **CRITICAL: When running any accessmate CLI commands**, always pass `--caller <plugin-name>` as a global flag (before the subcommand), where `<plugin-name>` is the name of the plugin this skill is installed under. This ensures the correct GK gate is checked. If the CLI returns a JSON response with `"disabled": true`, inform the user that the Accessmate auto-grant skill is currently disabled. Do NOT suggest invoking `/accessmate` directly — instead, provide the Data Protection portal URL from the CLI response so the user can request access manually. Do NOT retry the command — continue the conversation.

Do NOT stop at just reporting the permission error. Proactively help the user get unblocked.
