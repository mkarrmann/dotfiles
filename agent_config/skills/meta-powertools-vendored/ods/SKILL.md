---
name: ods
description: Query single-source ODS1 time-series metrics using the Meta CLI. Accepts Canvas/ODS URLs to execute or inspect existing charts (including multi-source and formula charts). Supports entity/key queries, transforms (rate, avg, delta), reductions (sum, top), percentile aggregation, entity/key discovery, and category management. Use when the user asks about an ODS metric, shares a Canvas URL, or mentions entity patterns like smc(), twtasks(), regex(). Do NOT use to AUTHOR a new formula or combine multiple metrics into one chart (ratios, "as a percentage of", "combine A and B", or mixing ODS with Scuba) — use the metric-formulas skill. Do NOT use for ODS3 — use the ods3-cli skill instead.
allowed-tools: Bash(meta:*)
---

# ODS1 Metrics via Meta CLI

Query ODS (Operational Data Store) time-series metrics using `meta ods`. ODS is Meta's monitoring platform storing 120+ billion data points per minute.

**Use this skill when**:
- User shares an ODS **Canvas URL** (e.g., `fburl.com/canvas/...`, `internalfb.com/canvas/...`)
- User asks about ODS metrics, counters, or time-series data
- User mentions entity patterns like `smc(...)`, `twtasks(...)`, `regex(...)`
- User wants to check service health, capacity, or performance trends
- User asks about ODS categories (owners, limits, quota, defcon)

**Do NOT use for ODS3.** ODS1 and ODS3 are separate systems. Use the `ods3-cli` skill for ODS3 dimensional metrics and ObQL queries.

## Scope and Routing

This skill handles **single-source ODS1 queries** end to end. For anything beyond that, route as follows:

| User intent | Destination | How |
|-------------|-------------|-----|
| Query one entity/key time-series (with transforms/reductions) | **this skill** | `meta ods.metric query -e <entity> -k <key>` |
| Percentile/statistical aggregation (p95, p99, stddev) | **this skill** | `meta ods.category query -e <entity> -k <key> -a p99` |
| Execute or inspect an existing chart from a Canvas/ODS URL — including multi-source and formula charts | **this skill** | `meta ods.metric load-url -u "<url>"` (see "Beyond a single query") |
| Author a NEW formula or combine multiple metrics — any source(s), including ODS + Scuba (ratio, "as a percentage of", "combine A and B") | **`metric-formulas` skill** | redirect — combining sources is a Canvas concern, not an ODS one |
| ODS3 / OBQL dimensional metrics | **`ods3-cli` skill** | redirect — separate system |

**Key boundary:** `meta ods.metric query` is single-source only (exactly one `-e`/`-k`, no `--formula`). It can *execute* an existing formula/multi-source chart via `load-url`, but it cannot *author* a new one — that requires `metric-formulas`.

**TIP**: Run `meta ods.metric query --help` or any subcommand with `--help` to discover all available options.

## Core Principles

**Entity/Key Model**: Every ODS time-series is identified by:
- **Entity**: Where the measurement is taken (host, job, task, tier)
- **Key**: What metric is being measured

**Time-Series Data**: ODS stores numeric values over time with:
- 1-minute or 4-minute resolution
- Recent data in Gorilla (in-memory), older data in cold storage
- ~1-2 minute delay for real-time data

**Two Query Paths**: ODS1 has two query commands for different use cases:
- `meta ods.metric query` — Rapido API. Use for transforms (rate, avg, delta) and reductions (sum, avg, top). Supports entity selectors (`smc()`, `regex()`, etc.).
- `meta ods.category query` — OdsRouter API. Use for aggregation types (p10, p50, p95, p99, stddev) and regex entity/key matching. Simpler interface, no transforms.

## Quick Start

### 1. Query from Canvas URL

> **CRITICAL**: NEVER use WebFetch, knowledge_load, or any URL-fetching tool on Canvas/ODS URLs. Pass them directly to `meta ods.metric load-url`.

> **IMPORTANT**: When a user shares a Canvas URL, they want you to investigate *that specific chart*. Do NOT override the time range by default — the URL encodes the time range the user cares about. First query with just `load-url` to see what the chart shows, then adjust as needed.

```bash
# Query data from a Canvas/ODS chart URL
meta ods.metric load-url -u "https://fburl.com/canvas/xyz123"

# Inspect query parameters without executing
meta ods.metric load-url -u "https://fburl.com/canvas/xyz123" --decode-only

# Get results as JSON
meta ods.metric load-url -u "https://fburl.com/canvas/xyz123" --output=json
```

**Supported URL formats**: Canvas Fiddle short URLs (`fburl.com/canvas/...`), Canvas Fiddle full URLs (`internalfb.com/canvas/fiddle?...`), ODS Chart URLs (`internalfb.com/intern/ods/chart?...`).

### 2. Query by Entity/Key

```bash
# Basic query (last 1 hour by default)
meta ods.metric query -e "<entity>" -k "<key>"

# Query with time range
meta ods.metric query -e "<entity>" -k "<key>" --stime=1_h

# Query with transform and reduction
meta ods.metric query -e "smc(my.tier)" -k "system.cpu-util-pct" -t latest -r avg

# Chain transforms
meta ods.metric query -e "smc(my.tier)" -k "requests" -t "rate,avg(3600)" -r "top(10)"

# Get JSON output
meta ods.metric query -e my.host -k my.key --output=json
```

### 3. Discover Entities

```bash
# Resolve entity patterns to concrete names
meta ods.metric resolve -e "smc(my.service.tier)"

# Fuzzy search for entities by name
meta ods.metric suggest-entities -q "web.prod"

# Find entities that share specific keys
meta ods.metric related --keys="system.cpu-util-pct,system.mem-used"
```

### 4. Discover Keys

```bash
# Find keys for an entity (with prefix filter)
meta ods.metric keys -e "my.host.name" --prefix=system

# Find keys by regex
meta ods.metric keys -e "my.host" --regex="cpu\\..*"

# Check key space size before paginating (JSON includes "cardinality" field)
meta ods.metric keys -e "my.host" --show-cardinality --output=json

# Sample keys from a large key space without scrolling
meta ods.metric keys -e "my.host" --random-samples=10

# Fuzzy search for key names
meta ods.metric suggest-keys -q "cpu" -e "my.host.name"

# Find keys common across multiple entities
meta ods.metric related --entities="host1,host2"

# Browse entity/key schema via EKI
meta ods.category schema -e "my.entity" --filter=cpu
```

### 5. Get Shareable Chart URL

```bash
# Generate a Canvas Fiddle URL for the query
meta ods.metric query -e "smc(my.tier)" -k "system.cpu-util-pct" --stime=1_h --show-url

# Add visual annotations (vertical markers, etc.)
meta ods.metric query -e "smc(my.tier)" -k requests --show-url --view-params '{"vmarks":[{"value":"1775399728","title":"Rollout"}]}'

# Take a screenshot of a chart (saves PNG locally)
meta ods.metric take-screenshot -u "https://fburl.com/canvas/xyz123"

# Upload screenshot to Pixelcloud for sharing
meta ods.metric take-screenshot -u "https://fburl.com/canvas/xyz123" --upload
```

## Beyond a single query

`meta ods.metric query` is single-source. Two adjacent needs are handled differently:

### Executing an existing multi-source / formula chart (works here)

If the user shares a Canvas/ODS URL that already contains multiple data sources, a formula, tetrahedra, or a formulas_proxy_query, `meta ods.metric load-url` executes it transparently — it runs every leaf query (any source mix) and returns the combined table. You do NOT need another skill for this.

```bash
# Executes the full multi-source / formula chart behind the URL
meta ods.metric load-url -u "https://fburl.com/canvas/xyz123"

# Inspect the underlying leaf queries without executing
meta ods.metric load-url -u "https://fburl.com/canvas/xyz123" --decode-only
```

**Caps** (load-url bounds what it emits): 1000 rows per query config, 10000 rows total across the batch (later queries skipped with a `truncated` flag once exhausted), 8 concurrent queries. Per-query failures are isolated; if every query fails the command exits non-zero. Narrow a too-big chart with a shorter time range or fewer entities.

**`--decode-only`** inspects without executing — it unwraps `tetrahedra` / `formulas_proxy_query` down to leaf sub-queries. It re-emits a runnable `query_command` only for **ODS/rapido** leaves; Scuba, drillstate, and other leaves get `query_command: null`, and the **formula itself is not re-emitted**. To re-run the combined result, re-run `load-url` on the original URL.

### Authoring a NEW formula / combined chart (use metric-formulas)

This skill cannot create a new formula or combine metrics from scratch (no `--formula`, single `-e`/`-k`, ODS-only). Combining metrics — especially across sources like ODS + Scuba — is a Canvas concern, not an ODS one. When the user wants a ratio, a percentage, or to combine two or more metrics into one chart, use the **`metric-formulas` skill** — run `/metric-formulas --help` for a menu of common combinations (ratio, percentage-of, success rate, cross-source). It builds a `tetrahedra` widget that nests heterogeneous sub-queries with a formula. Discover the ODS entities/keys here first (`resolve`, `keys`, `suggest-*`), then hand the chosen entity/key pairs to `metric-formulas`. If `metric-formulas` isn't available, stop and ask the user to install it rather than trying to fake a formula with single-source queries.

## Entity Selectors

Entity selectors query multiple entities at once. They work in both `meta ods.metric query` and `meta ods.metric resolve`:

| Selector | Description | Example |
|----------|-------------|---------|
| `smc(tier)` | All hosts in an SMC tier | `smc(my.service.tier)` |
| `smc(tier, recurse=.*)` | Include child tiers recursively | `smc(my.tier, recurse=.*)` |
| `smc(tier, selector=twtask)` | TW task entities in a tier | `smc(my.tier, selector=twtask)` |
| `smc(tier, selector=twjob)` | TW job entities in a tier | `smc(my.tier, selector=twjob)` |
| `regex(pattern)` | Regex match on entity names | `regex(devvm.*\\.prn1)` |
| `tw(job)` | Tupperware job | `tw(my_tw_job)` |
| `twtasks(job)` | All tasks in a TW job | `twtasks(my_tw_job)` |
| `cluster(...)` | Cluster-based selection | `cluster(type=FRONTEND)` |
| `tag(...)` | Tag-based selection | `tag(dc=prn1)` |

**For comprehensive `smc()` options including `recurse`, `only_leaves`, filters, and ports, see [references/query-reference.md](references/query-reference.md).**

## Query Syntax Quick Reference

### Transforms (modify individual time series, via `-t`)

| Transform | Description | Example |
|-----------|-------------|---------|
| `latest` | Most recent value | `-t latest` |
| `avg(N)` | Average over N-second window | `-t "avg(300)"` |
| `sum(N)` | Sum over N-second window | `-t "sum(3600)"` |
| `rate` | Rate of change per second | `-t rate` |
| `delta` | Difference from previous | `-t delta` |
| `scale(N)` | Multiply by factor | `-t "scale(1000)"` |

Chain transforms with commas: `-t "rate,avg(300)"`

### Reductions (aggregate multiple time series, via `-r`)

| Reduction | Description |
|-----------|-------------|
| `sum` | Sum all values across entities |
| `avg` | Average across entities |
| `max` / `min` | Maximum / minimum value |
| `count` | Count of time series |
| `median` | Median value |
| `top(N)` | Top N by value |

### Time Options

| Flag | Examples |
|------|----------|
| `--stime` (relative) | `30_min`, `1_h`, `6_h`, `1_d`, `7_d` |
| `--start-time` (absolute, unix) | `--start-time=1700000000` |
| `--end-time` (absolute, unix) | `--end-time=1700003600` |

### Aggregation Types (via `meta ods.category query -a`)

For percentile and statistical aggregations, use `meta ods.category query`:

```bash
meta ods.category query -e my.entity -k my.key -a p99 --hours=24
```

Aggregation types: `raw`, `avg`, `sum`, `count`, `min`, `max`, `stddev`, `p10`, `p50`, `p95`, `p99`

## Output Options

All commands support: `--output=table` (default), `--output=json`, `--output=yaml`, `--output=csv`

| Option | Command |
|--------|---------|
| Shareable Canvas URL | `meta ods.metric query ... --show-url` |
| Chart screenshot (PNG) | `meta ods.metric take-screenshot -u "<url>"` |
| Upload screenshot | `meta ods.metric take-screenshot -u "<url>" --upload` |
| Decode URL without executing | `meta ods.metric load-url -u "<url>" --decode-only` |
| Verbose/debug output | Add `--verbose` to any command |
| No value truncation | Add `--no-truncate` to any command |

## Key Reminders

1. **Use entity selectors** — `smc()`, `twtasks()`, `regex()` for multi-host queries
2. **Always use reductions** — When querying multiple entities, add `-r avg` or similar
3. **Start with short time ranges** — `--stime=30_min` before expanding
4. **Use `resolve` first** — Verify entity patterns before querying
5. **Use `keys` to discover metrics** — `meta ods.metric keys -e entity --prefix=fb303`
6. **Use `suggest-*` for fuzzy search** — When you don't know exact entity/key names
7. **Get chart URLs** — Use `--show-url` to generate shareable Canvas links
8. **Take screenshots** — Use `take-screenshot` for visual artifacts
9. **Check data delay** — ODS has ~1-2 minute delay; don't use very short time ranges
10. **Two query paths** — Use `ods.metric query` for transforms/reductions, `ods.category query` for aggregation types (p95, p99)

## References

- [Query Reference (transforms, reductions, selectors, time ranges)](references/query-reference.md)
- [Discovery Commands (keys, resolve, suggest, related, schema)](references/discovery-commands.md)
- [Category Management (list, metadata, query, attribution)](references/category-management.md)
- [Common Query Patterns (health checks, latency, TW jobs, trends)](references/common-patterns.md)
- ODS User Guide: https://www.internalfb.com/wiki/ODS/ODS_User_Guide/
- ODS Querying: https://www.internalfb.com/wiki/ODS/ODS_User_Guide/Querying/
- Entity Selection: https://www.internalfb.com/wiki/ODS/ODS_User_Guide/Querying/Selection/
- Tag-Based Aggregation: https://www.internalfb.com/intern/wiki/Monitoring-ODS/User_Guide/Sending_Data/Tag-Based_Aggregation/
- Rapido Documentation: https://fburl.com/rapido
- ODS Users Workplace Group: https://fb.workplace.com/groups/ods.users/
