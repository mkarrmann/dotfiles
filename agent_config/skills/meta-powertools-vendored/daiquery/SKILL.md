---
name: daiquery
author: oncall+datamate
description: Search saved queries, create and update saved queries, manage notebooks, delete notebook cells, delete queries, and fetch query details from DaiQuery URLs in DaiQuery (Meta's interactive query and visualization tool). Use when users want to search for existing saved queries, create a new saved query (clone a template or from scratch), update query SQL, list workspaces, get query details, create/read/edit/publish notebooks, delete one or more cells from a notebook, extract notebook cell UUIDs for Unidash integration, read notebook macros/parameters, delete or restore queries, or fetch query details from DaiQuery URLs, fburl links, or notebook links. At the end of a data conversation, automatically creates a DaiQuery notebook by default. For sessions combining SQL and Python code, offers to create a Bento notebook. For actually executing SQL queries or viewing results, delegate to the presto-query skill instead. Uses the same utilities that empower Datamate.
allowed-tools: Bash, Bash(bento-engine *)
---

# DaiQuery Skill

Search for saved queries, create and update saved queries, manage notebooks, delete notebook cells, delete queries, and fetch query details from URLs in DaiQuery, Meta's interactive query and visualization tool.

## When to Use

- User wants to search for existing saved queries by name or content
- User wants to create a new saved query (clone an existing query as a template, or from scratch)
- User wants to update an existing query with new SQL
- User wants to list their DaiQuery workspaces
- User wants to get details about a specific saved query
- User provides a DaiQuery URL, notebook URL, or fburl link and wants to view query details
- User mentions "daiquery", "saved queries", "notebook", or shares a daiquery link
- User wants to create, read, edit, or publish a DaiQuery notebook
- User wants to extract notebook cell UUIDs (e.g., for Unidash widget source tokens)
- User wants to read notebook macro/parameter definitions
- User wants to delete queries from a workspace
- User wants to manage notebook cells (SQL or markdown)
- User wants to delete one or more cells from a DaiQuery notebook
- User wants to restore deleted queries

### When NOT to Use (Use `presto-query` Instead)

- User wants to **execute** a SQL query and see results — use the `/presto-query` skill
- User wants to run a query against Presto, Scuba, MySQL, or Raptor and see output in terminal
- User says "run this query", "execute this SQL", "show me the results"

## Using Without fbsource (Meta CLI)

If you don't have an fbsource checkout (e.g., XFN devservers), use the `meta` CLI instead of `buck2 run`. The `meta daiquery` commands talk directly to backend services and require no local repo.

### Available Commands

| Meta CLI Command | Equivalent buck2 Skill Command |
|-----------------|-------------------------------|
| `meta daiquery.workspace list` | `buck2 run ...scripts:search_queries -- --list-workspaces` |
| `meta daiquery.workspace list --creator=myunixname` | (filter by creator) |
| `meta daiquery.workspace metadata --id=123456` | `buck2 run ...scripts:search_queries -- --query-id 123456` |
| `meta daiquery.notebook cells --url=<URL>` | `buck2 run ...scripts:fetch_query -- --url <URL>` |
| `meta daiquery.notebook add-cell --url=<URL> --sql="SELECT ..."` | (notebook cell creation) |
| `meta daiquery.notebook add-cell --url=<URL> --text="## Summary"` | (markdown cell creation) |

### Check Availability

```bash
which meta && meta daiquery 2>&1 | head -5
```

If `meta daiquery` is available, you do not need fbsource or `buck2` for basic DaiQuery operations.

## Prerequisites

### Core Features (Always Available)

These features use scripts bundled with the skill at `fbcode//claude-templates/components/skills/daiquery/scripts:*` and work in any fbsource checkout:
- Search saved queries, create queries, update queries, fetch query details from URLs

### Advanced Features (Require Additional fbsource Paths)

These features depend on targets outside the skill bundle. **Before using them, verify the required paths exist.** If they don't (e.g., sparse checkout or no fbsource), direct the user to the DaiQuery web UI fallback.

| Feature | Required fbsource Path | Fallback |
|---------|----------------------|----------|
| Notebook management (create, read, edit, workspace) | `fbcode/dataswarm-pipelines/tasks/ad_metrics/aep/modeled_aggrid/daiquery_helpers/` | Create/edit notebooks at https://www.internalfb.com/intern/daiquery/ |
| Delete notebook cells | Same as above | Edit the notebook manually in DaiQuery web UI |
| Notebook programmatic API | Same as above | Use the CLI or web UI instead |
| Notebook introspection (cell UUIDs, macros) | `fbcode/scripts/graphql/graphql_curl` | Inspect cells manually in the DaiQuery notebook UI |
| Query deletion & restoration | `fbcode/scripts/graphql/graphql_curl` | Delete queries manually in DaiQuery web UI (right-click → Delete) |

### Detection Before Running Advanced Commands

Before attempting notebook CLI or GraphQL commands, run a quick check:

```bash
# Check for notebook CLI target
buck2 targets fbcode//dataswarm-pipelines/tasks/ad_metrics/aep/modeled_aggrid/daiquery_helpers:modeled_attribution_daq_cli 2>/dev/null && echo "Notebook CLI available" || echo "Notebook CLI not available"

# Check for graphql_curl
test -f fbcode/scripts/graphql/graphql_curl && echo "graphql_curl available" || echo "graphql_curl not available"
```

If a tool is unavailable, inform the user and provide the web UI link as an alternative. Do not attempt to run the command — it will fail with a confusing build error.

## Quick Start

### Search for Saved Queries

```bash
buck2 run fbcode//claude-templates/components/skills/daiquery/scripts:search_queries -- \
  --search "revenue metrics"
```

### Fetch Query Details from URL

```bash
buck2 run fbcode//claude-templates/components/skills/daiquery/scripts:fetch_query -- \
  --url "https://www.internalfb.com/intern/daiquery/workspace/?queryid=123"
```

### Update an Existing Query

```bash
buck2 run fbcode//claude-templates/components/skills/daiquery/scripts:update_query -- \
  --query-id 12345 --sql "SELECT * FROM my_table LIMIT 10"
```

### Update a Query from a SQL File

```bash
buck2 run fbcode//claude-templates/components/skills/daiquery/scripts:update_query -- \
  --query-id 12345 --sql-file /path/to/query.sql
```

### Create a Saved Query

Clone an existing query as a template (inherits its macros / namespace), overriding the SQL + name:

```bash
buck2 run fbcode//claude-templates/components/skills/daiquery/scripts:create_query -- \
  --from-query 1779784203187506 --name "my_new_query" --sql-file /path/to/query.sql
```

Or create from scratch:

```bash
buck2 run fbcode//claude-templates/components/skills/daiquery/scripts:create_query -- \
  --workspace 1509774830529896 --namespace infrastructure \
  --name "my_new_query" --sql "SELECT 1"
```

### Create a DaiQuery Notebook

```bash
buck2 run fbcode//dataswarm-pipelines/tasks/ad_metrics/aep/modeled_aggrid/daiquery_helpers:modeled_attribution_daq_cli -- \
  create-notebook --title "My Investigation Notebook"
```

Create directly in a workspace:

```bash
buck2 run fbcode//dataswarm-pipelines/tasks/ad_metrics/aep/modeled_aggrid/daiquery_helpers:modeled_attribution_daq_cli -- \
  create-notebook --title "My Investigation Notebook" --workspace <workspace_id>
```

### Move Notebook to Workspace

```bash
buck2 run fbcode//dataswarm-pipelines/tasks/ad_metrics/aep/modeled_aggrid/daiquery_helpers:modeled_attribution_daq_cli -- \
  move-to-workspace --notebook-id <notebook_id> --workspace-id <workspace_id>
```

### Delete Queries from a Workspace

```bash
fbcode/scripts/graphql/graphql_curl --query 'mutation ($input: DaiqueryQueryMarkForDeletionData!) {
  daiquery_query_mark_for_deletion(data: $input) { deleted_query_ids }
}' --variables '{"input": {"client_mutation_id": "cleanup_1", "actor_id": "<USER_FBID>", "query_ids": ["<QUERY_ID>"]}}'
```

### Inspect Notebook Cells (Get Cell UUIDs)

```bash
fbcode/scripts/graphql/graphql_curl --query 'query {
  daiquery_notebook(id: "<NOTEBOOK_ID>") {
    id
    name
    main_revision { id source_content }
  }
}'
```

Parse the `source_content` JSON to extract cell UUIDs from `cells[].metadata.originalKey`. See [Notebook Introspection via GraphQL](#notebook-introspection-via-graphql) for full details.

## Execute SQL Queries

**Use the `/presto-query` skill for query execution.** This skill focuses on DaiQuery management (search, update, notebooks, deletion). To actually run SQL and see results, invoke the `presto-query` skill which handles Presto/Hive query execution, table metadata, SQL linting, and function search.

## Search Saved Queries

Search for existing saved queries across workspaces by name or SQL content.

### Usage

```bash
buck2 run fbcode//claude-templates/components/skills/daiquery/scripts:search_queries -- [OPTIONS]
```

### Actions (mutually exclusive)

| Parameter | Description |
|-----------|-------------|
| `--search`, `-s` | Search term to find in query names or SQL content |
| `--list-workspaces`, `-l` | List all accessible workspaces |
| `--query-id`, `-q` | Get details for a specific query by ID |

### Optional Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `--workspace-id`, `-w` | All workspaces | Limit search to a specific workspace |
| `--limit` | 10 | Maximum number of results to return |
| `--format`, `-f` | "text" | Output format: `json` or `text` |

### Examples

**Search for Queries by Keyword:**
```bash
buck2 run fbcode//claude-templates/components/skills/daiquery/scripts:search_queries -- \
  --search "revenue"
```

**Search Within Specific Workspace:**
```bash
buck2 run fbcode//claude-templates/components/skills/daiquery/scripts:search_queries -- \
  --search "user metrics" \
  --workspace-id 12345
```

**List All Workspaces:**
```bash
buck2 run fbcode//claude-templates/components/skills/daiquery/scripts:search_queries -- \
  --list-workspaces
```

**Get Query Details:**
```bash
buck2 run fbcode//claude-templates/components/skills/daiquery/scripts:search_queries -- \
  --query-id 67890
```

**JSON Output with More Results:**
```bash
buck2 run fbcode//claude-templates/components/skills/daiquery/scripts:search_queries -- \
  --search "metrics" \
  --limit 25 \
  --format json
```

## Workflow Examples

### Investigate Data for Incident Response

1. Search for existing relevant queries:
   ```bash
   buck2 run fbcode//claude-templates/components/skills/daiquery/scripts:search_queries -- \
     --search "error rate"
   ```

2. If a suitable query exists, get its SQL and reuse or modify it.

3. To execute a query and see results, use the `/presto-query` skill.

### Iterate on an Existing Query

1. Get the current SQL of a query:
   ```bash
   buck2 run fbcode//claude-templates/components/skills/daiquery/scripts:search_queries -- \
     --query-id 12345678
   ```

2. Modify the SQL locally, then update the query:
   ```bash
   buck2 run fbcode//claude-templates/components/skills/daiquery/scripts:update_query -- \
     --query-id 12345678 --sql-file /path/to/updated_query.sql
   ```

3. View the updated query at the returned URL. To execute it, use the `/presto-query` skill.

### Reuse Existing Query

1. Find the query:
   ```bash
   buck2 run fbcode//claude-templates/components/skills/daiquery/scripts:search_queries -- \
     --search "daily active users"
   ```

2. Get full query details including SQL:
   ```bash
   buck2 run fbcode//claude-templates/components/skills/daiquery/scripts:search_queries -- \
     --query-id 12345678
   ```

3. To execute the SQL, use the `/presto-query` skill.

## Update Existing Queries

Update the SQL content of an existing DaiQuery query by creating a new version. Supports both inline SQL and reading from a file.

**Note:** This works with regular SQL queries. DaiQuery notebooks (Python code) use a different format and should be updated via the `daiquerycli push` command instead.

### Usage

```bash
buck2 run fbcode//claude-templates/components/skills/daiquery/scripts:update_query -- [OPTIONS]
```

### Required Parameters

| Parameter | Description |
|-----------|-------------|
| `--query-id`, `-q` | DaiQuery query ID to update |
| `--sql`, `-s` OR `--sql-file`, `-f` | New SQL content (inline or from file, mutually exclusive) |

### Optional Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `--name`, `-n` | Unchanged | New name for the query |
| `--namespace` | Unchanged | New namespace for the query |
| `--format` | "text" | Output format: `json` or `text` |

### Examples

**Update with Inline SQL:**
```bash
buck2 run fbcode//claude-templates/components/skills/daiquery/scripts:update_query -- \
  --query-id 12345 --sql "SELECT * FROM my_table LIMIT 10"
```

**Update from a SQL File:**
```bash
buck2 run fbcode//claude-templates/components/skills/daiquery/scripts:update_query -- \
  --query-id 12345 --sql-file /path/to/query.sql
```

**Update SQL and Rename:**
```bash
buck2 run fbcode//claude-templates/components/skills/daiquery/scripts:update_query -- \
  --query-id 12345 --sql-file /path/to/query.sql --name "My Updated Query"
```

**Update with Namespace Change:**
```bash
buck2 run fbcode//claude-templates/components/skills/daiquery/scripts:update_query -- \
  --query-id 12345 --sql "SELECT 1" --namespace infrastructure
```

**JSON Output:**
```bash
buck2 run fbcode//claude-templates/components/skills/daiquery/scripts:update_query -- \
  --query-id 12345 --sql "SELECT 1" --format json
```

Returns:
```json
{
  "success": true,
  "query_id": 12345,
  "workspace_id": 1234567,
  "url": "https://www.internalfb.com/intern/daiquery/workspace/1234567/12345/",
  "query_name": "My Query",
  "previous_name": "My Query",
  "namespace": "ad_metrics",
  "previous_namespace": "ad_metrics",
  "version_id": 9876543
}
```

## Create Saved Queries

Create a new classic DaiQuery saved query (referenced by `query_id`, e.g. for a Unidash widget) — the create counterpart to `update_query`. Wraps the DaiQuery thrift API (`DaiqueryApi.create_report`). This is the supported flag-driven path; `meta daiquery.query` has no create action (only `execute` / `metadata` / `search`), and notebook creation is separate — see *Manage DaiQuery Notebooks*.

**Two modes:**
- **Clone a template** (`--from-query <id>`): inherits the template query's macros, namespace, schema, and tier — you only override the SQL and name. Use this when the new query reuses a template's macros, e.g. the `date_start` / `date_end` macros a Unidash period picker subscribes to. Most query-backed dashboard widgets want this mode.
- **From scratch** (no `--from-query`): requires `--workspace` and `--namespace`.

> **When to use `daiquerycli push` instead.** `create_query` is a one-shot, flag-driven create (optionally cloning a template), and returns the new `query_id` as JSON. If instead you maintain a *set* of queries **as code** — a Python module of `Report` objects you edit and re-apply over time — use `buck2 run fbcode//daiquery/daiquerycli:daiquerycli -- push <file.py>`. `push` reconciles the whole file against remote (creating new queries and updating existing ones in one pass), and supports `--dryrun`, name filtering, and jinja2 templating; but it has no clone mode and doesn't emit the new `query_id` for scripting. Rule of thumb: `create_query` for a quick single create or template-clone; `daiquerycli push` for declaratively managing queries-as-code.

> **Tip — selector option queries.** A handy use of `create_query` is making a small `SELECT DISTINCT <col> ...` query (with a relative `ds` window so it stays fresh) to back a **live Unidash selector dropdown** — point the selector's `values_col` / `labels_col` at the query's column so the filter options auto-refresh from the data instead of a hardcoded list.

### Usage

```bash
buck2 run fbcode//claude-templates/components/skills/daiquery/scripts:create_query -- [OPTIONS]
```

### Required Parameters

| Parameter | Description |
|-----------|-------------|
| `--name`, `-n` | Name for the new query |
| `--sql`, `-s` OR `--sql-file`, `-f` | SQL content (inline or from file, mutually exclusive) |
| `--from-query`, `-F` *(or)* `--workspace` + `--namespace` | Either clone a template id, or specify the target workspace + namespace directly |

### Optional Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `--from-query`, `-F` | None | Template query id to clone macros / namespace / schema / tier from |
| `--workspace`, `-w` | Template's workspace | Target workspace/container id (required without `--from-query`) |
| `--namespace` | Template's namespace | Data namespace, e.g. `infrastructure` (required without `--from-query`) |
| `--description`, `-d` | "" | Query description |
| `--macros-json` | Inherit / none | JSON array of macro objects (thrift `Macro` fields); overrides inherited macros in clone mode |
| `--version-type` | `presto` | Override the version data-source type |
| `--format` | "text" | Output format: `json` or `text` |

### Examples

**Clone a template (inherit its macros), override SQL + name:**
```bash
buck2 run fbcode//claude-templates/components/skills/daiquery/scripts:create_query -- \
  --from-query 1779784203187506 --name "scheduler_exits_bpf_trace" \
  --sql-file /path/to/query.sql
```

**Create from scratch:**
```bash
buck2 run fbcode//claude-templates/components/skills/daiquery/scripts:create_query -- \
  --workspace 1509774830529896 --namespace infrastructure \
  --name "my_new_query" --sql "SELECT 1"
```

**From scratch with macros:**
```bash
buck2 run fbcode//claude-templates/components/skills/daiquery/scripts:create_query -- \
  --workspace 1509774830529896 --namespace infrastructure \
  --name "my_new_query" --sql "SELECT * WHERE ds = '\$date\$'" \
  --macros-json '[{"key": "date", "value": "<DATEID-0>", "type": "free_text"}]'
```

Returns:
```json
{
  "success": true,
  "query_id": 1981140619432066,
  "workspace_id": 1509774830529896,
  "url": "https://www.internalfb.com/intern/daiquery/workspace/1509774830529896/1981140619432066/",
  "query_name": "scheduler_exits_bpf_trace",
  "version_id": 1012504281731578
}
```

## Fetch Query Details from URLs

Fetch query details from DaiQuery URLs, notebook URLs, or fburl short links. Supports both regular queries and DaiQuery notebooks.

> **Note:** This script fetches query metadata and SQL content. To actually **execute** a query, use the `/presto-query` skill.

### Usage

```bash
buck2 run fbcode//claude-templates/components/skills/daiquery/scripts:fetch_query -- [OPTIONS]
```

### Required Parameters

| Parameter | Description |
|-----------|-------------|
| `--url`, `-u` | DaiQuery URL, notebook URL, or fburl to fetch query from |

### Optional Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `--format`, `-f` | "text" | Output format: `json` or `text` |

### Supported URL Formats

- `https://www.internalfb.com/intern/daiquery/workspace/?queryid=123`
- `https://www.internalfb.com/intern/daiquery/workspace/456/123/` (queries and notebooks)
- `https://www.internalfb.com/intern/daiquery/query/123/`
- `https://fburl.com/daiquery/abc123` (fburl short links)

### Examples

**Get Query Details from URL:**
```bash
buck2 run fbcode//claude-templates/components/skills/daiquery/scripts:fetch_query -- \
  --url "https://www.internalfb.com/intern/daiquery/workspace/?queryid=123"
```

**Get Query from fburl:**
```bash
buck2 run fbcode//claude-templates/components/skills/daiquery/scripts:fetch_query -- \
  --url "https://fburl.com/daiquery/abc123"
```

**JSON Output:**
```bash
buck2 run fbcode//claude-templates/components/skills/daiquery/scripts:fetch_query -- \
  --url "https://..." --format json
```

Returns:
```json
{
  "success": true,
  "query_id": 12345678,
  "name": "Original Query Name",
  "sql": "SELECT * FROM table...",
  "data_source": "presto",
  "namespace": "ad_metrics",
  "workspace_id": 1234567,
  "url": "https://www.internalfb.com/intern/daiquery/workspace/1234567/12345678/"
}
```

### Notebook Support

When a DaiQuery notebook URL is detected by the `fetch_query` script, it provides query details but cannot directly execute notebook cells. Notebooks can be fully managed programmatically — see the [Manage DaiQuery Notebooks](#manage-daiquery-notebooks) section below.

For regular SQL queries fetched via CLI fallback, the response includes `"fetched_via": "cli"` to indicate the CLI was used.

## Manage DaiQuery Notebooks

Create, read, edit, and publish DaiQuery notebooks programmatically using the CLI tool.

> **Requires fbsource:** This section uses the `modeled_attribution_daq_cli` buck2 target from `dataswarm-pipelines`. Before running these commands, verify availability (see [Prerequisites](#prerequisites)). If the target is not available, use the DaiQuery web UI at https://www.internalfb.com/intern/daiquery/ to manage notebooks manually.

### CLI Base Command

All notebook commands use this base path:
```bash
buck2 run fbcode//dataswarm-pipelines/tasks/ad_metrics/aep/modeled_aggrid/daiquery_helpers:modeled_attribution_daq_cli -- <command>
```

### Create Notebook

Create a new empty notebook:

```bash
buck2 run fbcode//dataswarm-pipelines/tasks/ad_metrics/aep/modeled_aggrid/daiquery_helpers:modeled_attribution_daq_cli -- \
  create-notebook --title "My Investigation"
```

Create a notebook directly in a workspace:

```bash
buck2 run fbcode//dataswarm-pipelines/tasks/ad_metrics/aep/modeled_aggrid/daiquery_helpers:modeled_attribution_daq_cli -- \
  create-notebook --title "My Investigation" --workspace <workspace_id>
```

**Output (with `--workspace`):**
```json
{
  "notebook_id": "1234567890",
  "notebook_number": "9001234",
  "url": "https://our.intern.facebook.com/intern/anp/view/?id=9001234",
  "workspace": {
    "status": "success",
    "notebook_id": "1234567890",
    "workspace_id": "9876543210",
    "workspace_url": "https://www.internalfb.com/intern/daiquery/workspace/9876543210/"
  }
}
```

### Move Notebook to Workspace

Move an existing notebook into a workspace (or move it between workspaces):

```bash
buck2 run fbcode//dataswarm-pipelines/tasks/ad_metrics/aep/modeled_aggrid/daiquery_helpers:modeled_attribution_daq_cli -- \
  move-to-workspace --notebook-id <notebook_id> --workspace-id <workspace_id>
```

This atomically adds the notebook to the target workspace. If the notebook was already in another workspace, it is removed from there first.

### List Cells

View all cells in a notebook:

```bash
buck2 run fbcode//dataswarm-pipelines/tasks/ad_metrics/aep/modeled_aggrid/daiquery_helpers:modeled_attribution_daq_cli -- \
  list -n <notebook_id>
```

**Extracting notebook ID from URL:** For `https://www.internalfb.com/intern/daiquery/workspace/965492022193583/1136764448572418/`, the notebook ID is the last segment (`1136764448572418`).

### Create Cell (SQL)

Add a new SQL query cell:

```bash
buck2 run fbcode//dataswarm-pipelines/tasks/ad_metrics/aep/modeled_aggrid/daiquery_helpers:modeled_attribution_daq_cli -- \
  create -n <notebook_id> --cell-id my_query --sql "SELECT * FROM my_table LIMIT 10"
```

### Create Cell (Markdown)

Add a markdown cell for documentation or section headers:

```bash
buck2 run fbcode//dataswarm-pipelines/tasks/ad_metrics/aep/modeled_aggrid/daiquery_helpers:modeled_attribution_daq_cli -- \
  create -n <notebook_id> --cell-id section_header --cell-type markdown --sql "## My Section

This section contains analysis for XYZ."
```

Use `--index N` to insert at a specific position (0-indexed). Without `--index`, cells are appended at the end.

### Edit Cell

Update an existing cell's SQL:

```bash
buck2 run fbcode//dataswarm-pipelines/tasks/ad_metrics/aep/modeled_aggrid/daiquery_helpers:modeled_attribution_daq_cli -- \
  edit -n <notebook_id> --cell-id my_query --sql "SELECT * FROM my_table WHERE ds = '<DATEID>'"
```

Use `--new-name` to rename a cell during edit.

### Delete Cells

Delete one or more cells from a notebook in a single operation:

```bash
# Delete a single cell by ID
buck2 run fbcode//dataswarm-pipelines/tasks/ad_metrics/aep/modeled_aggrid/daiquery_helpers:modeled_attribution_daq_cli -- \
  delete -n <notebook_id> --cell-id <cell_id>

# Delete multiple cells by ID
buck2 run fbcode//dataswarm-pipelines/tasks/ad_metrics/aep/modeled_aggrid/daiquery_helpers:modeled_attribution_daq_cli -- \
  delete -n <notebook_id> --cell-id <id1> --cell-id <id2>

# Delete the last N cells
buck2 run fbcode//dataswarm-pipelines/tasks/ad_metrics/aep/modeled_aggrid/daiquery_helpers:modeled_attribution_daq_cli -- \
  delete -n <notebook_id> --last 2
```

`--cell-id` and `--last` are mutually exclusive. Use `--last` when you want to trim cells from the end without looking up their IDs.

### Notebook Workflow

1. **Create a notebook** using `create-notebook` (use `--workspace <id>` to place it directly in a workspace)
2. **List cells** — a freshly created notebook contains a default starter cell at index 0 (a blank/placeholder cell; don't assume its exact content). Check for it.
3. **Reuse the starter cell if you can** — `edit` it into your first real query (rename via `--new-name`) instead of appending. If you appended your cells instead, it's fine to leave the blank starter cell (harmless), or `delete` it if you prefer a clean notebook.
4. **Add additional queries** by creating new cells for subsequent queries
5. **Move to workspace** if not done at creation — use `move-to-workspace`
6. **Share the notebook URL** for user to review
7. **Iterate** based on feedback until the notebook is complete

**Always pass pretty-printed, multi-line SQL** when creating or editing cells. The CLI stores `--sql` verbatim and DaiQuery does not auto-format — a single-line query forces the user to click "Format" before it will run.

### Important Notes

- Cells can reference other cells by `cell_id` in SQL (e.g., `SELECT * FROM baseline`)
- Default namespace is `ad_metrics`. Use `--namespace` to specify a different one
- The notebook must be published in DaiQuery UI for CLI to see changes made in the browser
- Always prefer the CLI over MCP tools (`create_daiquery_query`) for notebook creation — the CLI supports meaningful cell IDs, while MCP tools generate UUIDs

## Notebook SQL Cell Format

SQL cells in DaiQuery notebooks use a specific JSON structure for their source content:

```json
{
  "subquery": {
    "type": "presto",
    "params": {
      "namespace": "ad_metrics",
      "sql": "SELECT * FROM my_table LIMIT 10"
    },
    "daiqueryParams": {
      "isNamespaceInferred": false,
      "tableDependencies": ["table_name:namespace", "dim_all_carbon_v3:infrastructure"]
    }
  }
}
```

### Key Points

- **Table dependencies format:** `table_name:namespace` (e.g., `dim_all_carbon_v3:infrastructure`)
- **Each cell must contain exactly ONE SQL statement.** Multi-statement SQL must be split into separate cells.
- The CLI's `--sql` flag handles encoding automatically — you only need this JSON format when using the programmatic API directly.

## Notebook Programmatic API (Advanced)

For bulk operations or scripting beyond the CLI, use the `notebook_helpers.py` library.

> **Requires fbsource:** This library is in `dataswarm-pipelines` and is not bundled with the skill. For most use cases, the CLI (above) is sufficient.

### Library Location

```
fbcode/dataswarm-pipelines/tasks/ad_metrics/aep/modeled_aggrid/daiquery_helpers/notebook_helpers.py
```

### Available Functions

| Function | Description |
|----------|-------------|
| `load_notebook(notebook_id)` | Download and parse a notebook with full metadata. Returns dict. |
| `publish_notebook(notebook, notebook_id, title=...)` | Publish updated notebook content. Returns dict. |
| `encode_sql(sql, namespace)` | Encode SQL into DaiQuery cell source JSON format. Returns str. |
| `extract_sql(source)` | Extract raw SQL from cell source. Returns str. |
| `find_cell(cells, cell_id)` | Find a cell by its `originalKey`. Returns dict or None. |
| `create_new_notebook(title)` | Create a brand new empty notebook. Returns dict with `notebook_id`, `url`. |
| `move_notebook_to_workspace(notebook_id, workspace_id)` | Move a notebook into a DaiQuery workspace via `phps DaiqueryAddNotebookToWorkspace`. Returns dict with status and workspace URL. |

### Execution via PAR Binary

For environments without `buck2` available, the library can be executed via the pre-built PAR binary's link-tree:

```bash
LT="$(buck2 build --show-full-output fbcode//dataswarm-pipelines/tasks/ad_metrics/aep/modeled_aggrid/daiquery_helpers:modeled_attribution_daq_cli#link-tree 2>&1 | awk '{print $2}')"
PYTHON_BIN="$LT/runtime/bin/python3.12"
PYTHONPATH="$LT" LD_LIBRARY_PATH="$LT/runtime/lib:$LT/." "$PYTHON_BIN" my_script.py
```

## Notebook Introspection via GraphQL

Read notebook content, extract cell UUIDs, and retrieve macro definitions using the GraphQL API. This is the **only reliable way** to get notebook cell UUIDs — the thrift API (`DaiqueryApi`, `client.getQuery()`, `client.getQueryVersionsByQueries()`) can fetch basic query metadata but cannot access notebook cell details.

> **Requires fbsource:** Uses `graphql_curl` at `fbcode/scripts/graphql/graphql_curl`. See [Prerequisites](#prerequisites) for availability check.

### Why GraphQL (Not Thrift)

The DaiQuery thrift API treats notebooks as opaque queries — it returns the query ID, name, and workspace but not the internal cell structure. The GraphQL API exposes the `AnalyticsNotebook` type which contains the full Jupyter-format notebook content including cell UUIDs, SQL source, and macro definitions.

This was discovered by inspecting browser network requests (F12 → Network tab) when loading a notebook in the DaiQuery UI.

### Fetch Notebook Content

```bash
fbcode/scripts/graphql/graphql_curl --query 'query {
  daiquery_notebook(id: "<NOTEBOOK_ID>") {
    id
    name
    main_revision {
      id
      source_content
    }
  }
}'
```

- **Root field:** `daiquery_notebook(id: "...")`
- **Type:** `AnalyticsNotebook` (not `EntAnalyticsNotebook`)
- **`source_content`:** JSON string containing the full Jupyter-format notebook (cells, metadata, macros)
- **`main_revision.id`:** The revision ID (useful for versioning)

### Extract Cell UUIDs

The `source_content` field is a JSON string. Parse it to access cells:

```python
import json

# source_content is a JSON string
notebook = json.loads(source_content)
cells = notebook.get('cells', [])

for cell in cells:
    cell_type = cell.get('cell_type', '?')           # 'code' or 'markdown'
    cell_uuid = cell['metadata'].get('originalKey')    # UUID for this cell
    source = cell.get('source', '')                    # Cell content (SQL JSON or markdown)
    if isinstance(source, list):
        source = ''.join(source)
    print(f"Cell UUID: {cell_uuid}, Type: {cell_type}")
```

### Cell UUID → Unidash Source Token

To reference a notebook cell in a Unidash widget, use the source token format:

```
AnalyticsNotebookCell/<cell_uuid>:<notebook_id>
```

**Example:** For cell UUID `aa65eb9c-bcfe-4963-82a1-cf186c6a9f71` in notebook `915669977478090`:
```
AnalyticsNotebookCell/aa65eb9c-bcfe-4963-82a1-cf186c6a9f71:915669977478090
```

This token goes in the widget config's `source_tokens` array:
```json
{
  "queries": {
    "Query 1": {
      "params": {
        "source_tokens": ["AnalyticsNotebookCell/<cell_uuid>:<notebook_id>"]
      },
      "source": "dimensional"
    }
  }
}
```

### Extract Macro Definitions

Macros (query parameters / filters) are in the notebook metadata:

```python
notebook = json.loads(source_content)
macros = notebook.get('metadata', {}).get('macros', [])

for macro in macros:
    print(f"Key: {macro['key']}")
    print(f"  Display Name: {macro.get('displayName', '')}")
    print(f"  Type: {macro.get('type', '')}")  # e.g., 'list_of_values', 'free_text'
    print(f"  Default: {macro.get('value', '')}")
    print(f"  Options: {macro.get('macroValues', [])}")
```

Common macro types:
- `list_of_values` — dropdown with predefined options
- `free_text` — text input
- `can_support_multiple_options` — if `true`, multi-select is supported

### Important Notes

- **Thrift API limitation:** `DaiqueryApi` methods like `getQuery()`, `getQueryVersionsByQueries()`, and `report_by_id()` return query-level metadata (name, workspace, SQL for regular queries) but NOT notebook cell structure. Always use GraphQL for cell introspection.
- **Cell types:** SQL cells have `cell_type: "code"` with JSON-encoded source containing the SQL. Markdown cells have `cell_type: "markdown"` with plain text source.
- **Empty separator cells:** Notebooks may contain empty cells used as visual separators — skip cells where `source` is empty.

## Data Usage Declaration

DaiQuery notebooks require a data usage declaration before cells can be executed.

### Setting `no_uii`

For notebooks containing infrastructure or non-user data, set the `no_uii` flag in notebook metadata:

```python
notebook["metadata"]["disseminate_notebook_info"]["no_uii"] = True
```

- **Without this setting**, DaiQuery shows: "execution is disabled until user data usage is specified"
- Set `no_uii = true` for infrastructure data, aggregated metrics, or any data that does not contain User Identifiable Information
- This can also be set manually in the DaiQuery web UI under notebook settings

## Delete Queries

Delete queries from a DaiQuery workspace using the GraphQL mutation. This performs a **soft delete** — queries are moved to a recycle bin for approximately 14 days before permanent deletion.

> **Requires fbsource:** The `graphql_curl` script lives at `fbcode/scripts/graphql/graphql_curl`. If it's not available, instruct the user to delete queries manually in the DaiQuery web UI (right-click a query → Delete, or use workspace settings).

### Delete Mutation

```bash
fbcode/scripts/graphql/graphql_curl --query 'mutation ($input: DaiqueryQueryMarkForDeletionData!) {
  daiquery_query_mark_for_deletion(data: $input) {
    deleted_query_ids
  }
}' --variables '{
  "input": {
    "client_mutation_id": "cleanup_1",
    "actor_id": "<USER_FBID>",
    "query_ids": ["<QUERY_ID_1>", "<QUERY_ID_2>"]
  }
}'
```

### Parameters

| Field | Description |
|-------|-------------|
| `client_mutation_id` | Arbitrary string identifier for the mutation |
| `actor_id` | Your employee FBID (see below for how to obtain) |
| `query_ids` | Array of query ID strings to delete (supports bulk deletion) |

### Get Your Actor ID (FBID)

```bash
fbcode/scripts/graphql/graphql_curl --query 'query {
  employees_by_unixname_or_email(unixnames_or_emails: ["YOUR_USERNAME"]) {
    fbid: unencoded_id
  }
}'
```

## Restore Deleted Queries

Restore previously deleted queries from the recycle bin. Requires `graphql_curl` (see [Delete Queries](#delete-queries) prerequisite note).

```bash
fbcode/scripts/graphql/graphql_curl --query 'mutation ($input: DaiqueryQueryRestoreData!) {
  daiquery_query_restore(data: $input) {
    restored_query_ids
  }
}' --variables '{
  "input": {
    "client_mutation_id": "restore_1",
    "actor_id": "<USER_FBID>",
    "query_ids": ["<QUERY_ID_1>", "<QUERY_ID_2>"]
  }
}'
```

The restore mutation uses the same parameter format as the delete mutation. Queries can only be restored while they remain in the recycle bin (~14 days).

## DaiQuery URL Parsing

Extract IDs from DaiQuery URLs:

| URL Format | Workspace ID | Query/Notebook ID |
|------------|-------------|-------------------|
| `.../workspace/1193435249443731/915669977478090/` | `1193435249443731` | `915669977478090` |
| `.../workspace/?queryid=915669977478090` | (not in URL) | `915669977478090` |
| `.../query/915669977478090/` | (not in URL) | `915669977478090` |

The workspace ID is the first numeric segment after `/workspace/`, and the query/notebook ID is the second segment (or the `queryid` parameter). Both regular queries and notebooks use the same URL pattern — you can distinguish them by fetching query details (notebooks have type `notebook`).

**`create` output vs. a browseable URL — `id` is not `notebook_number`.** `meta daiquery.notebook create` returns `url` as an ANP viewer link (`/intern/anp/view/?id=<notebook_number>`), plus separate `id` (the notebook FBID) and `notebook_number` fields. Report the `url` field (or `N<notebook_number>`) — **never** put `notebook_number` into a `/intern/daiquery/<...>` path. The `/intern/daiquery/` browse route needs the notebook **id** in the workspace form `/intern/daiquery/workspace/<workspace_id>/<notebook_id>/`; `notebook_number` only resolves via the `N<number>` bunnylol redirect or the ANP viewer link. A bare `/intern/daiquery/<notebook_number>` is accepted by the `add-cell`/`edit-cell` CLI (its parser resolves the number) but 404s in the browser — do not report it.

## Thrift API for Query and Macro Management (Advanced)

For programmatic bulk operations on regular queries — listing workspace contents, reading/writing macros, publishing new versions — use the DaiQuery thrift API directly via Python scripts.

### BUCK Target Dependencies

Create a `python_binary` target with these deps:

```python
python_binary(
    name = "my_daiquery_script",
    srcs = ["my_script.py"],
    main_function = ".my_script.main",
    deps = [
        "//daiquery:thrift-python-types",
        "//daiquery/daiquerycli:daiqueryapi",
        "//daiquery/daiquerycli:helpers",
    ],
)
```

Then run with: `buck2 run fbcode//path/to:my_daiquery_script`

### Core API Usage

```python
from daiquery.daiquerycli.daiqueryapi import DaiqueryApi
from daiquery.daiquerycli.helpers import Report, Macro

def main():
    with DaiqueryApi() as api:
        client = api.client

        # List queries in a workspace
        queries = client.getQueriesByContainer(WORKSPACE_ID, 0, 200)
        for q in queries:
            print(f"ID: {q.query_id}, Name: '{q.name}'")

        # Get query details
        q = client.getQuery(QUERY_ID)
        print(f"Name: {q.name}, Container: {q.container_id}")

        # Get report with config (includes SQL, macros)
        report = api.report_by_id(QUERY_ID)
        config = report._config
        print(f"SQL: {config.sql}")
```

### Reading Macros from a Query

```python
report = api.report_by_id(QUERY_ID)
config = report._config

if hasattr(config, 'macros') and config.macros:
    for macro in config.macros:
        print(f"Key: {macro.key}")
        print(f"  Display Name: {macro.display_name}")
        print(f"  Type: {macro.type}")
        print(f"  Default Value: {macro.value}")
        print(f"  Options: {list(macro.macro_values)}")
        print(f"  Multi-select: {macro.can_support_multiple_options}")
```

### Adding Macros to a Query

```python
from daiquery.daiquerycli.helpers import Report, Macro

# Build the Report object with macros
report = Report(
    query_id=QUERY_ID,
    container_id=WORKSPACE_ID,
    name='My Query',
    sql='SELECT * FROM my_table WHERE vendor IN ($vendor$)',
    namespace_name='infrastructure',
    macros=[
        Macro(
            key='vendor',
            value='NVIDIA,AMD',                    # Default value
            macro_values=['NVIDIA', 'AMD'],         # Available options
            macro_map={'NVIDIA': 'NVIDIA', 'AMD': 'AMD'},
            display_name='Vendor',
            description='Filter by vendor',
            type='list_of_values',                  # or 'free_text'
            array_publish_format='csv_no_delimiter',
            can_support_multiple_options=True,
        ),
    ],
)
```

### Macro Type Reference

| Field | Description | Example Values |
|-------|-------------|---------------|
| `key` | Parameter name used in SQL as `$key$` | `'vendor'`, `'lookback_period'` |
| `value` | Default value (comma-separated for multi) | `'NVIDIA,AMD'`, `'7'` |
| `macro_values` | List of available options | `['NVIDIA', 'AMD']` |
| `macro_map` | Display label → value mapping | `{'NVIDIA': 'NVIDIA'}` |
| `display_name` | Human-readable label in UI | `'Vendor'` |
| `description` | Tooltip text in UI | `'Filter by vendor'` |
| `type` | Selector type | `'list_of_values'`, `'free_text'` |
| `array_publish_format` | How multi-values are joined | `'csv_no_delimiter'` |
| `can_support_multiple_options` | Allow multi-select | `True` / `False` |

### Important Notes

- **Macros in SQL:** Reference macros in SQL with `$key$` syntax (e.g., `WHERE vendor IN ($vendor$)`)
- **`Report` is a helper class** that wraps the thrift types — it's not the raw thrift struct
- **Publishing:** After building a `Report` object with macros, use the DaiQuery API to publish a new version. The `update_query` CLI script handles this for SQL-only updates, but macro changes require the thrift API directly.
- **Thrift vs GraphQL for macros:** The thrift API is for managing macros on **regular queries**. For **notebook** macros, use GraphQL introspection (see [Notebook Introspection via GraphQL](#notebook-introspection-via-graphql)) to read them, and edit notebooks via the notebook CLI or web UI to modify them.

## Troubleshooting

### Notebook CLI or graphql_curl Not Found

If `buck2 run fbcode//dataswarm-pipelines/...` fails with a build error or `graphql_curl` is not found, the user's environment likely doesn't include these paths (sparse checkout or no fbsource). Do not retry — instead:
1. **Try `meta daiquery`** — run `meta daiquery.notebook --help` to check if Meta CLI is available. It works without fbsource and supports notebook cell management.
2. If Meta CLI is also unavailable, provide the DaiQuery web UI link: https://www.internalfb.com/intern/daiquery/
3. For notebooks, the user can create/edit directly in the web UI
4. For query deletion, the user can right-click queries in the workspace to delete them

### "No workspaces available"

Create a workspace in DaiQuery first: https://www.internalfb.com/intern/daiquery/

### Query Execution Errors

- Verify SQL syntax is correct for the data source
- Check that the namespace is valid for your query
- Ensure you have access to the tables referenced

### Authentication Issues

The DaiQuery API uses your Meta credentials. Ensure you're authenticated:
```bash
# Re-authenticate if needed
kinit
```

## End-of-Conversation: Create DaiQuery Notebook by Default

At the end of a data conversation — after queries have been executed and results discussed — **automatically create a DaiQuery notebook** containing the queries from the session. This produces a shareable artifact the user can send to teammates, re-run, or build on, without them having to ask for it.

**Skip notebook creation** if any of these are true:
- The session only involved schema exploration (`SHOW COLUMNS`, `DESCRIBE`, `SHOW PARTITIONS`)
- The session only involved simple previews (`SELECT * FROM table LIMIT 5` with no meaningful filters)
- All queries failed or returned zero rows
- A notebook was already created during the session
- The session involved both SQL and Python code — in this case, skip the DaiQuery notebook entirely and offer a Bento notebook instead (see next section)

### Procedure

> **Two things to know about `create`:**
> 1. **It seeds a default starter cell** at index 0 — a blank/placeholder cell. Don't assume its exact content (it varies by version). Check the cells after creating, and **reuse the starter cell for your first query if you can** (preferred); otherwise it's fine to leave it — a blank trailing cell is harmless.
> 2. **It does not format SQL.** `add-cell`/`edit-cell` store the `--sql` string verbatim and DaiQuery has no auto-format, so a single-line query forces the user to click "Format" before running. Always pass **pretty-printed, multi-line** SQL. Write each query to its own temp file and pass it via `--sql="$(cat "$f")"` — this preserves newlines and avoids the shell mangling DaiQuery SQL is prone to (`$macros`, single quotes in literals, backticks). **Name the file per notebook + cell** — e.g. `${TMPDIR:-/tmp}/dq-cell-<notebook_number>-<cell_id>.sql` — so files never collide across cells or sessions (never a single fixed `/tmp` name reused for every cell).

1. **Create the notebook:**
   ```bash
   meta daiquery.notebook create --title="<short description of the analysis>" --output=json
   ```
   The JSON output has three fields you'll use: `url` (an ANP viewer link, `https://our.intern.facebook.com/intern/anp/view/?id=<notebook_number>` — already valid and browseable), `id` (the notebook FBID), and `notebook_number`. **Use the `url` field verbatim as `<notebook_url>`** in every `add-cell`/`edit-cell`/`cells` call below — it works as-is; do NOT rebuild it into a different form. The notebook already contains one default starter cell at index 0 (a blank/placeholder cell).

2. **Reuse the starter cell for your FIRST query** (preferred) — `edit-cell` index 0 in place instead of appending a new cell, so you don't leave an extra cell behind. Write the formatted, multi-line SQL to a per-notebook+cell temp file, then pass it via `$(cat ...)`:
   ```bash
   # NOTEBOOK_NUMBER comes from the create output above; CELL_ID is this cell's name
   f="${TMPDIR:-/tmp}/dq-cell-${NOTEBOOK_NUMBER}-${CELL_ID}.sql"   # write this query's formatted SQL into "$f"
   meta daiquery.notebook edit-cell --url="<notebook_url>" --cell-index=0 \
     --sql="$(cat "$f")" --namespace=<NAMESPACE> --name="${CELL_ID}"
   ```

3. **Add a markdown context cell at the top:**
   ```bash
   meta daiquery.notebook add-cell --url="<notebook_url>" --text="## <Question the analysis answers>\n\nGenerated from a Datamate session." --cell-index=0
   ```

4. **Add the remaining SQL cells** (query #2 onward), one per analytical query, each multi-line and with the correct namespace. Use a distinct per-notebook+cell temp file for each (unique `CELL_ID`):
   ```bash
   f="${TMPDIR:-/tmp}/dq-cell-${NOTEBOOK_NUMBER}-${CELL_ID}.sql"   # write this query's formatted SQL into "$f"
   meta daiquery.notebook add-cell --url="<notebook_url>" --sql="$(cat "$f")" \
     --namespace=<NAMESPACE> --name="${CELL_ID}"
   ```

5. **Optional — tidy the starter cell.** If you reused it in step 2, there's nothing to do. If you appended your queries instead, the blank starter cell is still at index 0; leaving it is fine (a blank cell is harmless), or remove it by listing cells and deleting the empty one:
   ```bash
   meta daiquery.notebook cells --url="<notebook_url>"
   meta daiquery.notebook delete-cell --url="<notebook_url>" --cell-id=<blank_cell_id>
   ```

6. **Report the notebook URL** inline — use the `url` field from the `create` output **verbatim** (the ANP `/intern/anp/view/?id=…` link), or the bunnylol form `N<notebook_number>`:
   ```
   DaiQuery notebook: <notebook_url>   # the create `url` field, or N<notebook_number>
   ```
   Keep the report to one line — the notebook URL is supplementary, not the main output.

   **Do NOT hand-build `https://www.internalfb.com/intern/daiquery/<notebook_number>`.** `notebook_number` is not a valid path segment for that route — the link 404s in the browser even though `add-cell` accepts it (the CLI resolves the number; the browser route does not). The browseable daiquery form is `https://www.internalfb.com/intern/daiquery/workspace/<workspace_id>/<notebook_id>/`, which needs the notebook **id** (not the number) **and** a workspace — neither of which `create` returns when run without `--workspace`. So report the `url` field `create` already gave you, or `N<notebook_number>`.

**If notebook creation fails** (network error, CLI not available), silently skip — never block the conversation. The analysis results are the primary output; the notebook is a bonus artifact.

## Bento Notebook for SQL + Code Combinations

When the conversation involves **both SQL queries and Python code** (e.g., post-processing query results with pandas, computing derived metrics, or generating visualizations), **do not auto-create a DaiQuery notebook**. Instead, **offer to create a Bento notebook**. Bento notebooks are stateful and let the user chain SQL query cells with Python code cells, which DaiQuery notebooks cannot do — making them the right artifact for mixed workflows.

**Offer a Bento notebook when any of these are true:**
- Python code was written to transform, analyze, or visualize query results
- The user asked for statistical analysis, ML, or charting that required Python
- Multiple queries feed into a combined analysis (e.g., joining results in pandas)

Ask in one sentence: "This analysis combined SQL and Python — want me to save it as a Bento notebook?" Only proceed if the user says yes. If the user declines, do not fall back to creating a DaiQuery notebook.

### Procedure

1. **Create a Bento session:**
   ```bash
   bento-engine create --json
   ```

2. **Add a markdown cell describing the analysis:**
   ```bash
   bento-engine cell create "## <Question the analysis answers>\n\nGenerated from a Datamate session." --cell-type markdown --json
   ```

3. **Add SQL query cells** — for each SQL query, create a code cell that loads the data via `bamboo`:
   ```bash
   bento-engine cell create "from analytics.bamboo import Bamboo as bb\ndf = bb.query_presto(sql=\"\"\"<SQL QUERY>\"\"\", namespace=\"<NAMESPACE>\")" --json
   ```

4. **Add Python analysis cells** — add the Python code used for post-processing, visualization, or further analysis:
   ```bash
   bento-engine cell create "<PYTHON CODE>" --json
   ```

5. **Publish the notebook:**
   ```bash
   bento-engine notebook publish "<short description of the analysis>" --json
   ```

6. **Report the notebook URL** inline:
   ```
   Bento notebook: <notebook_url>
   ```

**If Bento is unavailable** (`bento-engine` not found or server not running), fall back to creating a DaiQuery notebook with the SQL queries only.

## Reference

- **DaiQuery Web UI**: https://www.internalfb.com/intern/daiquery/
- **DaiQuery Documentation**: https://www.internalfb.com/wiki/Daiquery/
- **Meta CLI (no fbsource required)**: `meta daiquery.notebook --help` and `meta daiquery.workspace --help`

## Important: SQL Engine in DaiQuery

**Spark SQL is deprecated in DaiQuery.** Always use **Presto** as the query engine for DaiQuery notebooks and saved queries. Spark SQL queries may fail or produce unexpected results in DaiQuery.

Note: `TRANSFORM FAST` queries cannot run in DaiQuery — they can only run in Dataswarm pipelines via Chronos/Spark.
