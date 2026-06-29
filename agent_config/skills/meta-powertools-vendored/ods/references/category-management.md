# Category Management

ODS categories organize monitoring metrics. Use these commands to list, inspect, and query categories.

## meta ods.category list — List/Filter Categories

Lists ODS categories from Configerator with extensive filtering.

```bash
# List categories (default: first 10, ALLOWED status only)
meta ods.category list --limit=10

# Search by name
meta ods.category list --name=my_category

# Filter by oncall
meta ods.category list --oncall=my_team --limit=20

# Filter by defcon level
meta ods.category list --defcon=crit --limit=10

# Filter by limiter status
meta ods.category list --limiter-status=blocked --limit=10

# Show all statuses (not just ALLOWED)
meta ods.category list --all-statuses --limit=10

# Autoscale-enabled categories only
meta ods.category list --autoscale --limit=10

# Custom columns
meta ods.category list --columns=name,defconLevel,limiterStatus --limit=10

# JSON output
meta ods.category list --output=json
```

### Available Filters

| Filter | Description | Values |
|--------|-------------|--------|
| `--name` | Substring match on category name | any string |
| `--oncall` | Substring match on owner names | any string |
| `--description` | Substring match on description | any string |
| `--status` | Category status | `allowed`, `blocked`, `deleted`, `blocknew` |
| `--all-statuses` | Show all statuses | flag |
| `--autoscale` | Only autoscale-enabled | flag |
| `--no-index` | Only categories with indexing disabled | flag |
| `--limiter-status` | Limiter status | `allowed`, `high_usage`, `blocked` |
| `--defcon` | DEFCON level | `crit`, `high`, `mid`, `low` |
| `--read-behavior` | Read behavior | `allow`, `block`, `warning` |
| `--personal-data` | Personal data status | `not_filled_out`, `no_personal_data`, `has_personal_data` |
| `--has-retention` | Custom retention policy only | flag |
| `--min-submission-limit` | Minimum submission limit | integer |
| `--min-storage-limit` | Minimum storage limit | integer |
| `--min-max-timeseries` | Minimum max timeseries | integer |

## meta ods.category metadata — Category Details

Get detailed metadata for a specific ODS category including owner, limits, retention, and defcon level.

```bash
# Get category metadata
meta ods.category metadata -c my_category

# JSON output
meta ods.category metadata -c my_category --output=json
```

| Flag | Description |
|------|-------------|
| `-c`, `--category` | Category name (required) |

Returns: owner, oncall, description, defcon level, submission/storage limits, retention policy, status, and other configuration.

## meta ods.category query — OdsRouter Queries

Query ODS time-series data via the OdsRouter API. Use this when you need aggregation types (p95, p99) or regex matching on entity/key.

```bash
# Basic query (last 1 hour)
meta ods.category query -e my.entity -k my.key

# With aggregation type
meta ods.category query -e my.entity -k my.key -a p99

# Longer time range
meta ods.category query -e my.entity -k my.key --hours=24

# With data table granularity
meta ods.category query -e my.entity -k my.key -t week --hours=168

# Regex on key
meta ods.category query -e my.entity -k "my\\.key\\..*" --key-regex

# Regex on entity
meta ods.category query -e "my\\.entity\\..*" -k my.key --entity-regex

# Absolute time range
meta ods.category query -e my.entity -k my.key --start-time=1700000000 --end-time=1700003600

# JSON output
meta ods.category query -e my.entity -k my.key --output=json
```

### Aggregation Types (via `-a`)

| Type | Description |
|------|-------------|
| `raw` | Raw values (default) |
| `avg` | Average |
| `sum` | Sum |
| `count` | Count |
| `min` / `max` | Minimum / Maximum |
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

**When to use `ods.category query` vs `ods.metric query`**:
- Need percentiles (p50/p95/p99) or stddev → use `ods.category query -a`
- Need transforms (rate, avg window, delta) or reductions (sum, top N) → use `ods.metric query`
- Need entity selectors (smc, twtasks) → use `ods.metric query`
- Need regex matching on entity/key → either works (`ods.category query --entity-regex` or `ods.metric query -e "regex(...)"`)

## meta ods.category schema — Browse EKI

Browse the ODS entity/key schema. See [Discovery Commands](discovery-commands.md) for details.

## meta ods.category attribution — MCP Origin ID

Get MCP Origin ID attribution metadata for a category.

```bash
meta ods.category attribution -c my_category
meta ods.category attribution -c my_category --output=json
```

| Flag | Description |
|------|-------------|
| `-c`, `--category` | Category name (required) |
