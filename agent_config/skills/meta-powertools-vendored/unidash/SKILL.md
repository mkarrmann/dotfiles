---
name: unidash
author: oncall+datamate
description: Fetch Unidash dashboard and widget raw data and metadata like the backing M360 metrics, presto SQL queries, scuba queries, etc. Use when users ask about Unidash dashboards, widget contents, metric values, or you need to analyze dashboard data with custom filters. Trigger this skill whenever a user pastes a Unidash or Data Insights dashboard link — including `fburl.com/datainsights/...` short links and `internalfb.com/unidash/...` URLs — even if they don't mention Unidash or ask an explicit question; resolve such links with `meta unidash.dashboard view` instead of knowledge_load. Uses the same utilities that empower Datamate.
allowed-tools: Bash(meta unidash*), Bash(meta data.semantic-model*)
---

# Unidash Dashboard Data Skill

## Overview

Fetch comprehensive data from Unidash dashboards — including backing M360 metrics, Presto SQL queries, and Scuba queries — using `meta unidash.dashboard view` via `Bash`. This skill enables retrieving Unidash widget metadata and raw data after applying custom filters/selector overrides. It supports **deterministic data fetching** — pulling the exact data displayed on the dashboard with the same formatting and filter selections, without generating new SQL. If you do not yet know a SPECIFIC unidash dashboard id or url to use, you must use the data discovery capability to find it first.

No special setup is needed — the `meta` CLI is available in all environments.

## When to Use

- User asks about Unidash dashboards (e.g., "What's in this dashboard?")
- User provides a Unidash URL or tab ID
- User wants widget data, metric values, or ticker values
- User asks about dashboard filters or selectors
- User wants to apply custom filters (country, region, date range)
- User needs SQL queries or raw data from dashboard widgets
- User asks about metric boxes, charts, or tables in a dashboard
- User wants to compare dashboard data across different filter selections

**Always ask the user for the dashboard or widget URL first** if they describe a dashboard but do not provide a URL or tab ID. Do not guess identifiers — this skill operates only on a known dashboard URL, tab ID, or widget ID.

### Tool Selection Guide

| User Need | Command |
|-----------|------|
| **Find a dashboard by name** | `meta unidash.dashboard list --vanity-url=<name>` |
| **Look up dashboard metadata** (title, tabs, description) | `meta unidash.dashboard describe --vanity-url=<name>` |
| **Get live data** (metric values, ticker values, chart data, SQL results) | `meta unidash.dashboard view --tab=<url_or_id>` |
| **Understand dashboard structure** (layout, widgets, filters, query configs) | `meta unidash.tab config --tab=<url_or_id>` |
| **Preview widget metadata** (types, titles, metrics — no data) | `meta unidash.dashboard view --tab=<url_or_id> --preview` |
| **Inspect a single widget's structural config** | `meta unidash.widget metadata --widget=<id>` |
| **Attach a semantic model for a tab** | `meta data.semantic-model check` then `meta data.semantic-model fetch` |

## Dashboard Discovery

This skill requires a known dashboard URL or tab ID. If you need to **search for or discover dashboards** by keyword, use your agent's data discovery capability first — for example, the `data_discovery` skill if available, or fall back to the `meta search.data search` / `meta search.data deep-search` CLI commands which can find Unidash dashboards, Hive tables, and M360 metrics by keyword. Once you have the dashboard URL, return to this skill to fetch the actual widget data.

## Critical Rules

1. **Always use `--output=json`** — structured JSON output is required for reliable parsing; the default table format omits detailed widget data.
2. **ALWAYS use the two-step preview-then-fetch approach** — fetching all widgets with full data will likely timeout or OOM on large dashboards. First call with `--preview` to get widget metadata, then select only the relevant widgets (3-5 max) and fetch their full data with `--selected-widget-ids`. Never skip the preview step on an unfamiliar dashboard.
3. **Never fetch more than ~5 widgets at once** — use `--selected-widget-ids` to fetch specific widgets. Fetching too many widgets with full data causes timeouts.
4. **Validate selector keys before using** — ensure the channel names or param keys exist on the dashboard before applying overrides. Use `--preview` to discover them.
5. **Use correct URL format** — accept both tab IDs (numeric, preferred for reliability) and full Unidash URLs.
6. **Widget data is ground truth** — once you fetch widget data, use it directly. Do NOT extract M360 metric IDs from widget metadata just to re-query M360 separately — the widget already has the authoritative values.
7. **Widget values are NOT equivalent to raw M360 or Hive values** — a widget may apply filters, date ranges, smoothing (7-day avg, 28-day rolling), custom SQL, or dimension breakdowns that change the value significantly. A raw M360 query for the same metric ID will often return a different number. Never assume they match. If you answered a dashboard question using M360 or Hive instead of the widget, verify against the actual widget data before presenting it.
8. **Permission errors: report, don't pivot** — if a widget returns a permission error, tell the user. Do not silently switch to a different data source.
9. **ALWAYS check for semantic models** — when you identify a Unidash tab, run `meta data.semantic-model check --xid="asset://unidash.tab/<tab_id>"` (or pass a Hive table XID for table assets) to find mapped semantic models, then `meta data.semantic-model fetch --xid="asset://semantic.model/..."` to load the content. This is a SEPARATE step — it is NOT baked into `unidash.dashboard view`. Semantic models override widget metric definitions if they conflict — a widget may visualize a metric that the SM warns is deprecated or scoped wrong. If you use the fetched semantic model, you MUST cite it explicitly so the user can verify your reasoning and explore the model. Include:
   - **A clickable link** to the model in the Admin Portal, using the full XID with no URL-encoding
     (e.g., `https://www.internalfb.com/data/admin/context?semantic_model=asset://semantic.model/risk/accessibility/accessibility`)
   - **What the model told you** — which specific section guided your query
     (e.g., "The `critical_agent_guidance` section specifies that remediation rate = `closed_issues_rate`")

## Dashboard Analysis Decision Tree

When analyzing dashboard data, follow this order — stop at the first step that answers the question:

| Step | Condition | Action |
|------|-----------|--------|
| 1 | Preview metadata is sufficient | Use it directly |
| 2 | Question involves selector values or filter comparisons | Fetch widget data with selector overrides |
| 3 | Widget data fetched in step 2 | Inspect it fully before going elsewhere |
| 4 | Question answerable via M360 tools (trends, contribution analysis) | Use the `metric360` skill/tools |
| 5 | None of the above suffice | Query underlying data sources (Presto, Scuba) |

After resolving the question via any step above, create appropriate visualizations if the data supports it and the runtime allows it.

## Workflow

**IMPORTANT: Always follow this two-step approach on an unfamiliar dashboard. Skipping the preview step and fetching all widgets directly will likely timeout or cause OOM errors.**

### Step 1: Fetch Dashboard Overview (Preview Mode) — REQUIRED

Use `--preview` to get a quick overview of the dashboard structure without fetching query data. This is fast and returns widget titles, types, metric configs, and IDs without executing any queries:

```bash
meta unidash.dashboard view --tab="https://www.internalfb.com/unidash/dashboard/my_team/my_dashboard/overview" --preview --output=json
```

This returns:
- Widget IDs, types, and titles
- M360 metric IDs and aliases
- Presto query keys and dimensions
- Available group-bys and applied filters

### Step 2: Get Specific Widget Data

Based on the preview, pick the 3-5 most relevant widget IDs and fetch their full data with `--selected-widget-ids`:

```bash
meta unidash.dashboard view --tab=1234567890 --selected-widget-ids=694937543480714,977915087509239 --output=json
```

This returns:
- Full widget data including raw data values
- SQL queries for presto-backed widgets
- Ticker values and delta comparisons for metric boxes
- Annotations and date ranges

### Step 3: Apply Filters (Optional — Deterministic)

Use selector overrides to fetch dashboard data with specific filter selections. This produces **deterministic results** — pulling exactly the data that would be shown with those filters applied, without generating new SQL.

Use `--widget-selectors` for filter widget selectors (dropdowns, radio buttons). Maps a selector widget ID to its underlying value (not the display text):

```bash
meta unidash.dashboard view --tab=1234567890 --widget-selectors='{"727375365955348":"7 AVG"}' --selected-widget-ids=311054618528901 --output=json
```

Use `--global-selectors` for dashboard-level param filters (region, country, time period):

```bash
meta unidash.dashboard view --tab=1234567890 --global-selectors='[{"key":"region","value":"US","operator":"IN"}]' --output=json
```

### Step 4: Analyze Results

Use the returned data to answer the user's question. For metric boxes, read `selectWidgetContents.tickerValue` (the topline number) and `selectWidgetContents.computedMetricDeltas` (d/d, w/w, m/m, etc. comparisons). For charts/tables, read `data`. Cross-check `appliedFilters` against the question's constraints.

## Scanning Multiple Dashboards

When you have several candidate dashboards and need to find the most relevant one, preview each in turn rather than fully fetching any of them:

```bash
# Preview each candidate dashboard's structure (metadata only, no query data)
meta unidash.dashboard view --tab=<candidate_1> --preview --output=json
meta unidash.dashboard view --tab=<candidate_2> --preview --output=json
```

Compare widget titles, types, and M360 metric aliases across previews to identify which dashboard (and which widgets) match the user's question, then fetch full data only for the selected widgets.

## Selecting the Right Widget on Multi-Metric Dashboards

When a dashboard has multiple widgets with similar titles:

1. **Match the M360 metric name to the question** — preview metadata includes the M360 metric ID and alias for each widget. Select the widget whose metric name semantically matches the question.
2. **Check `appliedFilters`** — this field shows active filters. Match them to the question's constraints (e.g., "Messenger Android" should have filters for app_family and device_os).
3. **Check the widget type** — a `metric_box` shows a single topline number; a `line_chart` shows trends. Use the type that matches the question's intent.
4. **When in doubt, fetch 2-3 candidate widgets** and compare their data before answering. This is faster than guessing wrong and re-fetching.

## Generate Analysis Report (If the Runtime Supports It, On Request)

When the user asks to save or share the analysis, surface a report summarizing the findings. Adapt to your runtime:

- **Non-interactive runtimes** (e.g., a backend agent that returns results via its final response): embed the report content directly in your final response — dashboard title, widget titles, date range and filters applied, key findings, the data, and source links. Do not attempt interactive artifact-creation flows.
- **Interactive runtimes** (e.g., Claude Code chat): only when the user explicitly requests a report, offer to create a shareable artifact. Do not generate one automatically.
  - **Google Doc** (default for team sharing): use the `google-docs` skill to create a doc with the analysis.
  - **HTML visualization** (for quick sharing / polished view): use the `visualize` skill to produce a styled one-pager uploaded to collab-files (`https://www.internalfb.com/collab-files/view/`).

**Report contents:**

1. **Header** — dashboard title, widget title, date range, applied filters
2. **Analysis** — key findings, trends, anomalies, or answers to the user's question
3. **Data** — relevant metric values, tables, or query results
4. **Sources** — dashboard tab URL (`https://www.internalfb.com/unidash/dashboard/<vanity_url>/<tab>`), plus any backing data links:
   - Existing DaiQuery notebook link if `daiquery_notebook_id` is present
   - M360 metric link if `mainM360MetricID` is present (`https://www.internalfb.com/intern/metric360/metric/?metric_id=<metric_id>`)

**Graceful degradation:** if report generation fails, present the analysis inline with source links. Do not block the response on report creation failures.

## CLI Command Reference

### `meta unidash.dashboard view`

Primary command for fetching Unidash dashboard/widget data with selector overrides.

**CLI Flags:**

| CLI Flag | Type | Required | Description |
|----------|------|----------|-------------|
| `--tab` / `-t` | string | Yes | Dashboard tab ID (numeric) or full Unidash URL |
| `--selected-widget-ids` / `-w` | string | No | Comma-separated widget IDs to fetch (fetches all if omitted) |
| `--preview` / `-p` | flag | No | Metadata-only mode — returns widget types, titles, and metrics without fetching query data (much faster) |
| `--global-selectors` / `-g` | JSON string | No | Global selector overrides as JSON array |
| `--widget-selectors` | JSON string | No | Widget selector overrides as JSON map (widget ID → value) |
| `--max-tokens` | int | No | Max tokens for context serialization (default: 30000) |
| `--output` / `-o` | string | No | Output format: `json`, `yaml`, `csv`, `toon`, or table (default) |

**Global Selector Override Format:**

```json
[{"key": "region", "value": "US", "operator": "IN"}]
```

Valid operators: `IN`, `NOT_IN`, `IS_NULL`, `IS_NOT_NULL`, `IS_AFTER`, `IS_BEFORE`

**Widget Selector Override Format:**

```json
{"<widget_id>": "<value>"}
```

Map the selector widget's ID to the underlying value (not the display text shown in the dropdown). Use `--preview` to discover selector widget IDs and their current values.

**Returns (with `--output=json`):**

A top-level object with the widget data nested under a `widgets` dict, plus dashboard-wide metadata:

```json
{
  "widgets": {"<widget_id>": { ... UnidashWidgetSerializedInfo ... }, ...},
  "global_selectors": <dashboard-wide selectors> | null,
  "sections": <section-level filters> | null,
  "data_truncation_warning": <string> | null
}
```

Iterate widget entries under the nested `widgets` dict. Each `widgets[<widget_id>]` is a `UnidashWidgetSerializedInfo`:

| Field | Description |
|-------|-------------|
| `widgetType` | Type: `metric_box`, `line_chart_client`, `bar_chart_client`, `table_client`, etc. |
| `widgetTitle` | Display title of the widget |
| `m360metrics` | List of M360 metrics with `metricID`, `alias`, `smoothing`, `aggregation` |
| `mainM360MetricID` | Primary metric ID for metric boxes |
| `prestoMetrics` | Presto query metrics with `queryKey`, `metrics`, `daiquery_notebook_id` |
| `scubaQueries` | Scuba query configs (dataset, view, metric, constraints, group-bys) |
| `odsQueries` | ODS query configs (entity, key, time range) |
| `sql_queries` | Resolved SQL queries for presto-backed widgets |
| `data` | Raw widget data (when available) |
| `startDate` / `endDate` | Date range in `Y-m-d` format |
| `groupBys` / `appliedGroupBys` | Dimensions used for grouping |
| `appliedFilters` | Filters formatted as "column OPERATOR (values)" |
| `selectWidgetContents` | For metric boxes: `tickerValue` and `computedMetricDeltas` (each delta has `deltaLabel` like `d/d`/`w/w`/`m/m`/`target`, `deltaValue`, `deltaMode`, and `positivity`) |
| `annotations` | Widget annotations with text and date |

**Field/format notes:**
- **Metric deltas** (d/d, w/w, etc.) surface under `selectWidgetContents.computedMetricDeltas`, computed server-side. This is independent of any UI state and is the authoritative delta for a metric box.
- **M360 metric IDs** returned in `m360metrics[].metricID` / `mainM360MetricID` are `ME`-prefixed in CLI output (e.g., `ME409483`). Pass them as-is to the `metric360` skill; never prepend `ME` to a value that is already prefixed or to a 12+ digit dimensional series ID.
- **`*_ref` handles** (e.g., `charta_flat_table_ref`, `vizirConfigRef`, `quartzConfigRef`) seen in some serialized payloads are session-scoped references used by other surfaces; the CLI returns raw payloads in `data` / `sql_queries` directly.

**Example Commands:**

```bash
# Preview the dashboard structure (metadata only, fast) — ALWAYS start here
meta unidash.dashboard view --tab="https://www.internalfb.com/unidash/dashboard/ig_creator_relevance/ig_creators_exec_dashboard/creators_exec_overview" --preview --output=json

# Fetch specific widgets only (after picking them from the preview)
meta unidash.dashboard view --tab=1234567890 --selected-widget-ids=694937543480714,977915087509239 --output=json

# With global selector filter (e.g., Country = US)
meta unidash.dashboard view --tab=1234567890 --global-selectors='[{"key":"region","value":"US","operator":"IN"}]' --output=json

# With widget selector override
meta unidash.dashboard view --tab=1234567890 --widget-selectors='{"727375365955348":"7 AVG"}' --selected-widget-ids=311054618528901 --output=json
```

### `meta unidash.widget metadata`

View the structural metadata of a single widget. Use when the user shares a specific widget (rather than a whole dashboard tab) or when you only need one widget's structural config.

**CLI Flags:**

| CLI Flag | Type | Required | Description |
|----------|------|----------|-------------|
| `--widget` | string | Yes | Widget identifier: numeric ID, fburl short link, or a full Unidash URL with a `widget_id` query param |
| `--output` / `-o` | string | No | Output format: `json`, `yaml`, `csv`, `toon`, or table (default) |

**Returns:** the structural metadata of the widget — `view_config` (visualization), `query_configs` (Quartz query), and `params` (filter/dimension setup). `view_config` and `query_configs` are JSON-encoded strings as Unidash stores them; with `--output=json`, pipe through `jq '.view_config | fromjson'` (and similarly for `query_configs`) to drill into either one.

**Note:** this returns the widget's structural config, NOT the serialized data shape (`UnidashWidgetSerializedInfo`) with resolved SQL and live data. To get a single widget's resolved data, use `meta unidash.dashboard view --tab=<tab> --selected-widget-ids=<widget_id> --output=json` instead.

**Example:**

```bash
meta unidash.widget metadata --widget=977915087509239 --output=json
```

### `meta unidash.dashboard list`

Search for dashboards by vanity URL.

**CLI Flags:**

| CLI Flag | Type | Required | Description |
|----------|------|----------|-------------|
| `--vanity-url` / `-u` | string | Yes | Vanity URL to search for (exact match) |
| `--limit` / `-l` | int | No | Maximum number of results to return |
| `--output` / `-o` | string | No | Output format: `json`, `yaml`, `csv`, `toon`, or table (default) |

**Returns:** dashboard_fbid, title, vanity_url, description.

**Example:**

```bash
meta unidash.dashboard list --vanity-url=my_dashboard --output=json
```

**Workflow tip:** Use this when you only have a dashboard name/vanity URL. Then use `meta unidash.dashboard describe` to get tab IDs.

### `meta unidash.dashboard describe`

View detailed metadata for a Unidash dashboard. (The older `meta unidash.dashboard metadata` is a deprecated alias for this command — prefer `describe`.)

**CLI Flags:**

| CLI Flag | Type | Required | Description |
|----------|------|----------|-------------|
| `--vanity-url` / `-u` | string | Yes | Dashboard vanity URL / locator |
| `--output` / `-o` | string | No | Output format: `json`, `yaml`, `csv`, `toon`, or table (default) |

**Returns:** dashboard ID, title, description, tabs (pages) with their IDs, creation and modification times.

**Example:**

```bash
meta unidash.dashboard describe --vanity-url=my_dashboard --output=json
```

**Workflow tip:** Use this to get the list of tab IDs, then use `meta unidash.dashboard view --tab=<tab_id>` to fetch widget data.

### `meta unidash.tab config`

View the full structural config of a tab including layout, widgets, query configs, and view configs.

**CLI Flags:**

| CLI Flag | Type | Required | Description |
|----------|------|----------|-------------|
| `--tab` / `-t` | string | Yes | Tab identifier: numeric ID, fburl, full Unidash URL, or asset XID |
| `--output` / `-o` | string | No | Output format: `json`, `yaml`, `csv`, `toon`, or table (default) |

**Returns:** Complete tab definition including title, description, layout, settings, sections, widgets (with query_configs and view_config), page-level filters, and tab panels.

**Example:**

```bash
meta unidash.tab config --tab=1234567890 --output=json
```

**Workflow tip:** Use this to inspect widget query configs and view configs. This returns the same shape accepted by the tab update command.

### `meta data.semantic-model` (semantic-model attachment)

Semantic models are NOT baked into `unidash.dashboard view` — fetch them separately for any tab you analyze. `check` reports whether a model exists; `fetch` loads its content.

**CLI Flags (both `check` and `fetch`):**

| CLI Flag | Type | Required | Description |
|----------|------|----------|-------------|
| `--xid` | string | One of `--xid`/`--table` | Full asset XID (e.g. `asset://unidash.tab/<tab_id>`, `asset://hive.table/<ns>/<table>`, or `asset://semantic.model/<pillar>/<domain>/<model>`) |
| `--table` | string | One of `--xid`/`--table` | Hive table as `namespace/table_name` (e.g. `bi/dim_all_users`) |
| `--output` / `-o` | string | No | Output format: `json`, `yaml`, `csv`, `toon`, or table (default) |

```bash
meta data.semantic-model check --xid="asset://unidash.tab/1234567890" --output=json
meta data.semantic-model fetch --xid="asset://semantic.model/<pillar>/<domain>/<model>" --output=json
```

See Critical Rule #9 for when to use these and how to cite the model.

## Deterministic Data Fetching

Deterministic data fetching means pulling the **exact data shown on the dashboard** with the same formatting and filter selections. No new SQL is generated, which means:

- **No hallucinations** — data comes directly from the dashboard's rendered output
- **Faster responses** — no query generation or execution overhead
- **Accurate results** — values match precisely with what's displayed on the dashboard
- **Filter support** — read dashboards with specific filter selections applied via selector overrides

| Scenario | Use Deterministic? | How |
|----------|-------------------|-----|
| "What's the DAU on this dashboard?" | Yes | `meta unidash.dashboard view` with `--selected-widget-ids` |
| "Show me this dashboard filtered by US" | Yes | Add `--global-selectors` / `--widget-selectors` |
| "Which metrics moved the most?" | Yes | Fetch the relevant metric-box widgets and read `computedMetricDeltas` |
| "Run a custom SQL query" | No | Use Presto/Scuba tools instead |

### How Selector Overrides Work

Unidash dashboards have two types of filters:

1. **Widget selectors** — dropdown/radio filter widgets that publish values on channels. Override with `--widget-selectors`, mapping the widget's FBID to the desired underlying value.
2. **Global selectors** — dashboard-level param filters (region, country, time). Override with `--global-selectors`, a JSON list of `{key, value, operator}` objects.

The CLI does not auto-resolve dependent (cascading) selectors — override all dependent selectors explicitly.

## Common Mistakes

| Mistake | Fix |
|---------|-----|
| Not using `--output=json` | Always pass `--output=json` to get structured data for parsing |
| Fetching all widgets without previewing first | Use `--preview` first, then `--selected-widget-ids` with 3-5 widget IDs |
| Fetching more than ~5 widgets at once | Limit to the 3-5 most relevant widgets via `--selected-widget-ids` |
| Fetching full data when only needing metadata | Use `--preview` for faster metadata-only fetches |
| Iterating the top-level object as a widget dict | Widget entries live under the nested `widgets` dict, not at the top level |
| Improperly quoted JSON strings | Wrap JSON values in single quotes on the command line, e.g., `--global-selectors='[...]'` |
| Invalid selector key in `--widget-selectors` | Verify the widget ID exists on the dashboard's filter widgets using `--preview` first |
| Invalid param key in `--global-selectors` | Verify the param key exists on the dashboard's global selectors |
| Using `--widget-selectors` for global params | Use `--global-selectors` for dashboard-level param filters |
| Using `--global-selectors` for filter widgets | Use `--widget-selectors` for filter widget selectors |
| Expecting cascading selector auto-resolution | The CLI does not auto-resolve dependent selectors; override all dependent selectors explicitly |
| Falling back to M360 or Hive when the question is about a dashboard | Widget values != raw M360/Hive values (filters, smoothing, custom SQL). Fetch the widget data instead. |
| Querying an M360 metric ID extracted from widget metadata | The widget already has the authoritative value. Use the widget data directly. |
| Calling this skill without a URL or widget ID | Ask the user for the dashboard/widget URL first |

## Troubleshooting

| Issue | Solution |
|-------|----------|
| 500 errors / OOM / slow response | Use the two-step approach: `--preview` first, then `--selected-widget-ids` with 3-5 widget IDs |
| "Unable to retrieve dashboard context" | Verify the dashboard URL or tab ID is correct and accessible |
| "Invalid --global-selectors JSON" | Check that the JSON is a valid array of objects with `key`, `value`, and `operator` fields |
| "Invalid --widget-selectors JSON" | Check that the JSON is a valid map of widget ID to value, e.g., `{"123":"value"}` |
| "Failed to validate widget selector overrides" | Verify the widget ID corresponds to a selector widget; use `--preview` to discover valid selector widgets |
| Empty widget data | The widget may require specific date ranges or filters to return data |
| Missing `sql_queries` | Only Presto-backed widgets have resolved SQL queries |
| "Access denied" | Report it to the user. Check if you have access to the dashboard through proper ACLs; do not silently switch data sources. |
| Command not found | Ensure the `meta` CLI is available; it is pre-installed in all standard environments |
