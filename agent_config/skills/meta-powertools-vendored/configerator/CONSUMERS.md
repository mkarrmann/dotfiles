# Checking Config Consumption (configerator_access_logs)

The `configerator_access_logs` Scuba table tracks real-time config consumption across production. Use it to determine whether a config is actively being read, by whom, and how — essential for safely deleting or modifying configs.

**When to use:**
- Before deleting a config: verify zero consumers in production
- Before making breaking changes: identify all consumers and their languages
- Auditing config usage: find which services/hosts/processes read a config
- Understanding consumption patterns: check access frequency and freshness

## Key Columns

| Column | Description |
|--------|-------------|
| `logical_config_name` | Config path without `source/` prefix or `.cconf` suffix (e.g., `ipnext/deployment/quota`) |
| `api_language` | Language of the consumer (C++, Python, Rust, etc.) |
| `process_name` | Name of the process consuming the config |
| `tw_job` | Tupperware job name of the consumer |
| `host` / `hostnameshort` | Host consuming the config |
| `service_id` | Service ID of the consumer |
| `tiers` | Tier information |
| `config_accessed` | Whether config was actually read (1=yes, 0=subscribed only) |
| `is_prod` | Production traffic (1=yes) |
| `success` | Successful access (1=yes) |
| `invalid_config` | Config validity (0=valid) |
| `is_canary` | Canary traffic (0=not canary) |
| `subscriber_count` | Number of active subscribers |
| `access_commit_timestamp` | Timestamp of the config version being consumed |
| `sample_rate` | Hits / access count |
| `distinct_hosts` | Aggregate: number of distinct consuming hosts |
| `top_level_directory` / `two_level_directory` | Config path hierarchy for browsing |

## Recommended Filters

Always apply these base filters for accurate production consumption data:

| Filter | Value | Reason |
|--------|-------|--------|
| `config_accessed = 1` | Only actual reads | Excludes subscription-only entries |
| `is_prod = 1` | Production only | Excludes dev/test traffic |
| `success = 1` | Successful reads | Excludes failed attempts |
| `invalid_config = 0` | Valid configs | Excludes invalid/broken configs |
| `is_canary = 0` | Non-canary | Excludes canary test traffic |
| `logical_config_name = "<path>"` | Your config | The config you're investigating |

These filters are encoded as a `--where` JSON array for the `meta scuba` CLI (see examples below).

## Querying with `meta scuba` CLI

Use `meta scuba.dataset query` to query the table from the command line. The config path used in `logical_config_name` is the config path without the `source/` prefix or `.cconf`/`.mcconf` suffix (e.g., for `source/ipnext/deployment/quota.cconf`, use `ipnext/deployment/quota`).

### Base where clause

All examples below use this base `--where` clause with the recommended filters. Replace `<CONFIG_PATH>` with your config path:

```bash
--where '[
  {"column":"logical_config_name","op":"eq","values":["<CONFIG_PATH>"]},
  {"column":"config_accessed","op":"eq","values":["1"]},
  {"column":"is_prod","op":"eq","values":["1"]},
  {"column":"success","op":"eq","values":["1"]},
  {"column":"invalid_config","op":"eq","values":["0"]},
  {"column":"is_canary","op":"eq","values":["0"]}
]'
```

### Check if a config has any consumers (safe to delete?)

Group by `api_language` to see a high-level breakdown of who consumes the config. If zero results, the config is likely safe to delete.

```bash
meta scuba.dataset query \
  -d configerator_access_logs \
  -a count \
  -g api_language \
  --where '[
    {"column":"logical_config_name","op":"eq","values":["ipnext/deployment/quota"]},
    {"column":"config_accessed","op":"eq","values":["1"]},
    {"column":"is_prod","op":"eq","values":["1"]},
    {"column":"success","op":"eq","values":["1"]},
    {"column":"invalid_config","op":"eq","values":["0"]},
    {"column":"is_canary","op":"eq","values":["0"]}
  ]' \
  --hours=168 \
  -l 20
```

Use `--hours=168` (7 days) as a starting window. For infrequently accessed configs, extend to `--hours=720` (30 days).

### Find consuming processes and their host spread

Group by `process_name` and include `distinct_hosts` as an aggregate to see how many hosts each process runs on:

```bash
meta scuba.dataset query \
  -d configerator_access_logs \
  -a count \
  --aggregate-list '[{"column":"distinct_hosts","op":"sum"}]' \
  -g process_name \
  --where '[
    {"column":"logical_config_name","op":"eq","values":["ipnext/deployment/quota"]},
    {"column":"config_accessed","op":"eq","values":["1"]},
    {"column":"is_prod","op":"eq","values":["1"]},
    {"column":"success","op":"eq","values":["1"]},
    {"column":"invalid_config","op":"eq","values":["0"]},
    {"column":"is_canary","op":"eq","values":["0"]}
  ]' \
  --hours=168 \
  -l 20
```

### Find consuming Tupperware jobs

Group by `tw_job` to identify the specific TW jobs reading the config:

```bash
meta scuba.dataset query \
  -d configerator_access_logs \
  -a count \
  -g tw_job \
  --where '[
    {"column":"logical_config_name","op":"eq","values":["ipnext/deployment/quota"]},
    {"column":"config_accessed","op":"eq","values":["1"]},
    {"column":"is_prod","op":"eq","values":["1"]},
    {"column":"success","op":"eq","values":["1"]},
    {"column":"invalid_config","op":"eq","values":["0"]},
    {"column":"is_canary","op":"eq","values":["0"]}
  ]' \
  --hours=168 \
  -l 20
```

### Get a Scuba UI link for the query

Add `--show-url` to any query to get a clickable Scuba URL for further exploration in the web UI:

```bash
meta scuba.dataset query \
  -d configerator_access_logs \
  -a count \
  -g api_language \
  --where '[
    {"column":"logical_config_name","op":"eq","values":["ipnext/deployment/quota"]},
    {"column":"config_accessed","op":"eq","values":["1"]},
    {"column":"is_prod","op":"eq","values":["1"]},
    {"column":"success","op":"eq","values":["1"]},
    {"column":"invalid_config","op":"eq","values":["0"]},
    {"column":"is_canary","op":"eq","values":["0"]}
  ]' \
  --hours=168 \
  --show-url
```

### JSON output for scripting

Use `-o json` for machine-readable output:

```bash
meta scuba.dataset query \
  -d configerator_access_logs \
  -a count \
  -g process_name,api_language \
  --where '[
    {"column":"logical_config_name","op":"eq","values":["ipnext/deployment/quota"]},
    {"column":"config_accessed","op":"eq","values":["1"]},
    {"column":"is_prod","op":"eq","values":["1"]},
    {"column":"success","op":"eq","values":["1"]},
    {"column":"invalid_config","op":"eq","values":["0"]},
    {"column":"is_canary","op":"eq","values":["0"]}
  ]' \
  --hours=168 \
  -o json
```

## Fallback: `scuba` CLI

If the `meta` CLI is not available, the `scuba` CLI can be used as a fallback. It accepts raw SQL via the `-e` flag. This is **not preferred** — use `meta scuba.dataset query` when possible.

```bash
scuba -e "
  SELECT api_language, COUNT(*) AS hits
  FROM configerator_access_logs
  WHERE time >= NOW()-604800
    AND config_accessed = 1
    AND is_prod = 1
    AND success = 1
    AND invalid_config = 0
    AND is_canary = 0
    AND logical_config_name = 'ipnext/deployment/quota'
  GROUP BY api_language
  ORDER BY hits DESC
  LIMIT 20;
"
```

The time filter uses seconds: `NOW()-604800` = 7 days, `NOW()-86400` = 1 day, `NOW()-2592000` = 30 days.

Other useful groupings — replace `api_language` with `process_name`, `tw_job`, or `host` as needed.

## Interpreting Results

- **Zero rows / "No results found"**: No production consumers found in the time window. Extend the time range (e.g., 30 days with `--hours=720`) before concluding the config is unused — some configs are accessed infrequently.
- **Low hit count with few hosts**: May indicate a config that is consumed but not critical. Verify with the owning team before deleting.
- **Multiple `api_language` values**: Config is consumed by services in different languages — any schema changes must be backward-compatible across all consumer codebases.
- **High `subscriber_count`**: Many processes are subscribed to updates — changes will propagate widely; canary carefully.
