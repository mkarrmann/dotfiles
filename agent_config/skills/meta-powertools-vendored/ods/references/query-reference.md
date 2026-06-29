# Query Reference

## meta ods.metric query

Queries ODS time-series data via the Rapido API. Use this for transforms and reductions.

```bash
meta ods.metric query -e <entity> -k <key> [--stime=<time>] [-t <transform>] [-r <reduction>] [--output=json]
```

### Flags

| Flag | Description | Default |
|------|-------------|---------|
| `-e`, `--entity` | Entity selector (required) | — |
| `-k`, `--key` | Key/metric name (required) | — |
| `-t`, `--transform` | Transform(s), comma-separated | none |
| `-r`, `--reduction` | Reduction function | none |
| `--stime` | Relative time range | `1_h` |
| `--start-time` | Absolute start time (unix timestamp) | — |
| `--end-time` | Absolute end time (unix timestamp) | now |
| `--show-url` | Output a shareable Canvas Fiddle URL | — |
| `--view-params` | JSON view params for chart annotations (implies `--show-url`) | — |
| `-o`, `--output` | Output format: table, json, yaml, csv | table |
| `--no-truncate` | Disable value truncation in table output | — |
| `-v`, `--verbose` | Verbose output with stack traces | — |

### Transforms (via `-t`)

Transforms modify individual time series. Chain multiple transforms with commas.

| Transform | Description | Example |
|-----------|-------------|---------|
| `latest` | Most recent value only | `-t latest` |
| `rate` | Per-second rate of change | `-t rate` |
| `avg(N)` | Moving average over N seconds | `-t "avg(300)"` (5 min) |
| `sum(N)` | Moving sum over N seconds | `-t "sum(3600)"` (1 hr) |
| `delta` | Difference between consecutive points | `-t delta` |
| `scale(N)` | Multiply values by factor N | `-t "scale(0.001)"` |

**Chaining examples:**
```bash
# Rate then 5-minute average
-t "rate,avg(300)"

# Latest value, scaled to milliseconds
-t "latest,scale(1000)"
```

### Reductions (via `-r`)

Reductions aggregate multiple time series into one.

| Reduction | Description |
|-----------|-------------|
| `sum` | Sum across all entities |
| `avg` | Average across entities |
| `max` | Maximum value |
| `min` | Minimum value |
| `count` | Number of time series |
| `median` | Median value |
| `top(N)` | Top N time series by value |

### Time Ranges

**Relative (via `--stime`)**:

| Value | Duration |
|-------|----------|
| `5_min` | 5 minutes |
| `30_min` | 30 minutes |
| `1_h` | 1 hour |
| `6_h` | 6 hours |
| `1_d` | 1 day |
| `7_d` | 7 days |

**Absolute (unix timestamps)**:
```bash
meta ods.metric query -e host -k key --start-time=1700000000 --end-time=1700003600
```

### Shareable URLs and Annotations

```bash
# Generate a shareable Canvas Fiddle chart URL
meta ods.metric query -e "smc(my.tier)" -k "system.cpu-util-pct" --stime=1_h --show-url

# Add vertical markers (e.g., rollout timestamps)
meta ods.metric query -e "smc(my.tier)" -k requests --show-url \
  --view-params '{"vmarks":[{"value":"1775399728","title":"Rollout Start"}]}'
```

## meta ods.category query

Alternative query path via the OdsRouter API. Use this for aggregation types like percentiles.

```bash
meta ods.category query -e <entity> -k <key> [--hours=N] [-a <aggregation>] [-t <table>]
```

### Flags

| Flag | Description | Default |
|------|-------------|---------|
| `-e`, `--entity` | Entity name (required) | — |
| `-k`, `--key` | Key name (required) | — |
| `--hours` | Lookback hours | 1 |
| `--start-time` | Absolute start (unix timestamp, overrides `--hours`) | — |
| `--end-time` | Absolute end (unix timestamp) | now |
| `-a`, `--aggregation` | Aggregation type | `raw` |
| `-t`, `--table` | Data table granularity | `auto` |
| `--entity-regex` | Treat entity as regex | — |
| `--key-regex` | Treat key as regex | — |

### Aggregation Types (via `-a`)

| Type | Description |
|------|-------------|
| `raw` | Raw values (default) |
| `avg` | Average |
| `sum` | Sum |
| `count` | Count |
| `min` / `max` | Min / Max |
| `stddev` | Standard deviation |
| `p10` | 10th percentile |
| `p50` | 50th percentile (median) |
| `p95` | 95th percentile |
| `p99` | 99th percentile |

### Data Table Granularity (via `-t`)

| Value | Description |
|-------|-------------|
| `auto` | Automatic (default) |
| `raw` | Raw resolution |
| `week` | Weekly aggregation |
| `month` | Monthly aggregation |
| `year` | Yearly aggregation |

### When to Use `ods.category query` vs `ods.metric query`

| Use Case | Command |
|----------|---------|
| Transforms (rate, avg window, delta, scale) | `meta ods.metric query` |
| Reductions (sum, avg, top N across entities) | `meta ods.metric query` |
| Entity selectors (smc, twtasks, regex) | `meta ods.metric query` |
| Percentile aggregations (p50, p95, p99) | `meta ods.category query` |
| Standard deviation | `meta ods.category query` |
| Regex on entity or key | `meta ods.category query` |
| Canvas URL / shareable link | `meta ods.metric query --show-url` |

## Entity Selectors (Detail)

### SMC Selector — `smc()`

```
smc([tier=]<name>, [selector=<type>], [recurse=<regex>], [only_leaves=<bool>], [<filter>=<value>])
```

**Selector types** (`selector=`):

| Selector | Description | Best for |
|----------|-------------|----------|
| `host` (default) | Hostnames | `system.*` metrics |
| `twtask` | Tupperware task names | `tw.*` metrics |
| `twjob` | Tupperware job handles | `fb303.*`, `tw.*` metrics |
| `tier` | SMC tier names (discovery only) | Finding child tiers |
| `lucky` | Both twtask and host | Mixed queries |

**Recursive child tiers** (`recurse=`):

```bash
# Include ALL child tiers
meta ods.metric query -e "smc(my.tier, recurse=.*)" -k "system.cpu-idle" -t latest -r avg

# Only child tiers matching pattern
meta ods.metric query -e "smc(my.tier, recurse=child\\..*)" -k "system.cpu-idle" -t latest -r avg

# Leaf tiers only (no double-counting)
meta ods.metric query -e "smc(my.tier, recurse=.*, only_leaves=True)" -k "system.cpu-idle" -t latest -r avg
```

**Filters** (ANDed together):

```bash
# Filter by hostname pattern
meta ods.metric resolve -e "smc(my.tier, hostname=.*\\.frc.*)"

# Filter by datacenter
meta ods.metric resolve -e "smc(my.tier, hostname=.*\\.prn1\\..*)"

# Production-enabled only
meta ods.metric resolve -e "smc(my.tier, production=true, enabled=true)"

# With port
meta ods.metric resolve -e "smc(my.tier, use_port=true)"
```

**Level filtering** (`level=`):
- `level=1` — immediate children only
- `level=2+` — level 2 and deeper
- `level=1;3` — levels 1 and 3

### Other Selectors

```bash
# Tupperware tasks
meta ods.metric query -e "twtasks(my_tw_job)" -k "tw.mem.rss_bytes" -t latest -r sum

# Regex match
meta ods.metric query -e "regex(devvm.*\\.prn1)" -k "system.cpu-idle" -t latest -r avg

# Multiple tiers
meta ods.metric query -e "smc(tier1), smc(tier2)" -k "system.cpu-idle" -t latest -r avg

# Cluster selector
meta ods.metric resolve -e "cluster(type=FRONTEND, state=CLUSTER_IN_USE)"

# Reverse lookup: find tiers containing a host
# Use: reverse_smc(<host>[:<port>])
```

### Related Selectors

| Selector | Shortcut for |
|----------|-------------|
| `tw(job)` | Query Tupperware jobs directly |
| `twtasks(job)` | `tw(job, selector=tasks)` |
| `twhosts(job)` | `tw(job, selector=hosts)` |
| `twtiers(job)` | `tw(job, selector=tier)` |
