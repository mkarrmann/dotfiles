# Scuba SQL Reference

Complete SQL reference for `meta scuba.dataset query --sql`. For structured query flags, see SKILL.md.

---

## Time Filtering (Mandatory)

Every Scuba SQL query **MUST** include a `WHERE time` filter. Queries without time filters will timeout or be throttled.

```sql
WHERE time >= now()-3600          -- Last hour
WHERE time >= now()-86400         -- Last 24 hours
WHERE time >= now()-604800        -- Last 7 days
WHERE time >= now()-2592000       -- Last 30 days
```

| Duration | Seconds |
|---|---|
| 1 hour | 3600 |
| 1 day | 86400 |
| 1 week | 604800 |
| 30 days | 2592000 |

**NEVER use:** `'1d'`, `interval '24' hour`, `UNIX_TIMESTAMP()` — none are supported.

---

## Common SQL Patterns

### Time Series

```bash
meta scuba.dataset query -d table_name --sql "
SELECT
  STRFTIME(time, '%Y-%m-%d') AS date,
  COUNT(*) AS cnt
FROM table_name
WHERE time >= now()-604800 AND time <= now()
GROUP BY date
ORDER BY date
"
```

### Aggregations and Breakdowns

```bash
meta scuba.dataset query -d table_name --sql "
SELECT
  dimension,
  COUNT(*) AS total_count,
  APPROX_COUNT_DISTINCT(user_id) AS unique_users,
  SUM(value) AS total_value,
  AVG(value) AS avg_value
FROM table_name
WHERE time >= now()-86400 AND time <= now()
GROUP BY dimension
ORDER BY total_count DESC
"
```

### Error Rate with HAVING

```bash
meta scuba.dataset query -d table_name --sql "
SELECT
  service_name,
  COUNT(*) AS total_requests,
  SUM(CASE WHEN error IS NOT NULL THEN 1 ELSE 0 END) AS errors,
  (SUM(CASE WHEN error IS NOT NULL THEN 1 ELSE 0 END) * 100.0 / COUNT(*)) AS error_rate_pct
FROM table_name
WHERE time >= now()-3600 AND time <= now()
GROUP BY service_name
HAVING COUNT(*) > 100
ORDER BY error_rate_pct DESC
LIMIT 20
"
```

### Top N Analysis

```bash
meta scuba.dataset query -d table_name --sql "
SELECT
  error_message,
  COUNT(*) AS occurrences
FROM table_name
WHERE time >= now()-86400 AND time <= now()
  AND error_message IS NOT NULL
GROUP BY error_message
ORDER BY occurrences DESC
LIMIT 10
"
```

### Percentile Calculations

```bash
meta scuba.dataset query -d table_name --sql "
SELECT
  APPROX_PERCENTILE(latency_ms, 0.5) AS p50_ms,
  APPROX_PERCENTILE(latency_ms, 0.95) AS p95_ms,
  APPROX_PERCENTILE(latency_ms, 0.99) AS p99_ms,
  AVG(latency_ms) AS avg_ms,
  MAX(latency_ms) AS max_ms
FROM table_name
WHERE time >= now()-3600 AND time <= now()
"
```

### Filtering with Multiple Conditions

```bash
meta scuba.dataset query -d table_name --sql "
SELECT
  region,
  flavor,
  COUNT(*) AS cnt,
  AVG(duration_sec) AS avg_duration
FROM table_name
WHERE time >= now()-86400 AND time <= now()
  AND region IN ('LHR', 'DEN', 'BRU')
  AND flavor LIKE '%ci%'
  AND error IS NULL
GROUP BY region, flavor
ORDER BY cnt DESC
"
```

### Regex Pattern Matching

```bash
meta scuba.dataset query -d table_name --sql "
SELECT message, COUNT(*) AS cnt
FROM table_name
WHERE time >= now()-3600 AND time <= now()
  AND REGEXP_MATCH(message, 'error|failure|crash')
GROUP BY message
LIMIT 20
"
```

Double backslashes for regex escape sequences in shell: `'error_code_\\d+'`

### Arrays (normvector/tags)

```bash
meta scuba.dataset query -d table_name --sql "
SELECT task_id, owners
FROM table_name
WHERE time >= now()-86400 AND time <= now()
  AND INCLUDES(owners, Array('username'))
LIMIT 10
"
```

### Rate Calculations

```bash
meta scuba.dataset query -d table_name --sql "
SELECT
  SUM(requests) / ((MAX(time) - MIN(time)) / 60.0) AS requests_per_minute,
  SUM(errors) / ((MAX(time) - MIN(time)) / 60.0) AS errors_per_minute
FROM table_name
WHERE time >= now()-3600 AND time <= now()
"
```

### JSON Field Extraction

**MANDATORY**: JSON paths MUST use JSONPath `$.` prefix. See [json-field-extraction.md](json-field-extraction.md) for full details.

```bash
meta scuba.dataset query -d table_name --sql "
SELECT
  GET_JSON_OBJECT(json_column, '$.field_name') AS extracted_field,
  COUNT(*) AS cnt
FROM table_name
WHERE time >= now()-3600 AND time <= now()
  AND GET_JSON_OBJECT(json_column, '$.field_name') IS NOT NULL
GROUP BY extracted_field
ORDER BY cnt DESC
LIMIT 50
"
```

### Week-over-Week Comparison

UNION ALL is NOT supported. Use `--compare` for time comparisons (preferred), or run separate queries:

```bash
# Preferred: structured mode with --compare
meta scuba.dataset query -d table_name -a count --hours=168 --compare="-7 days"

# Alternative: separate SQL queries
meta scuba.dataset query -d table_name --sql "
SELECT COUNT(*) AS total, AVG(value) AS avg_value
FROM table_name
WHERE time >= now()-604800 AND time <= now()
"
```

---

## Advanced Techniques

### JOINs

Scuba supports JOINs with specific limitations:
- **Maximum 2 tables** per query
- Both tables must be in the same universe
- Only **equi-joins** (using `=`) are supported
- `ON` clause is required (implicit joins not supported)
- **Time filters required on BOTH tables**
- LEFT/RIGHT/OUTER JOINs are supported
- Self joins are supported (but the two sides must have different aliases)
- For long queries, use `--sql-file /path/to/query.sql`

#### Critical JOIN syntax rules

1. **No table aliases for column qualification.** Scuba parses `alias.column` (e.g., `a.time`) as a function call `OF(a, time)`, which fails with `We don't yet support UDF with signature OF(ANY, bigint)`. Always use the **full table name**: `events.\`time\``, not `a.\`time\``.

2. **All column names must be backtick-quoted and table-qualified.** In JOIN queries, Scuba's dynamic schema cannot infer which table a column belongs to. Always write `table_name.\`column_name\``.

3. **Use column aliases in GROUP BY / ORDER BY.** Writing `table_name.\`column\`` in a `GROUP BY` clause is misinterpreted as `OF(IDENTIFIER(table), ...)`. Instead, alias the column in `SELECT` and reference the alias in `GROUP BY`.

4. **Join key types must match.** If the join columns have different types (e.g., bigint vs string), use `CAST(table.\`column\` AS STRING)` or `CAST(table.\`column\` AS BIGINT)` in the `ON` clause. Available cast functions: `CAST(x AS STRING)`, `CAST(x AS BIGINT)`, `CAST(x AS DOUBLE)`, `CAST(x AS BOOL)`.

5. **Time filters on BOTH tables are imperative.** Use the pattern `NOW() - 3600 <= table.\`time\` AND table.\`time\` <= NOW()` for each table. Without this, the join may exceed memory limits.

#### Correct example

```bash
meta scuba.dataset query -d events --sql "
SELECT
  events.\`user_id\` AS uid,
  events.\`event_type\` AS event,
  users.\`user_name\` AS uname
FROM events
JOIN users ON events.\`user_id\` = users.\`user_id\`
WHERE
  NOW() - 3600 <= events.\`time\` AND events.\`time\` <= NOW()
  AND NOW() - 3600 <= users.\`time\` AND users.\`time\` <= NOW()
LIMIT 100
"
```

#### Example with type casting and GROUP BY

```bash
meta scuba.dataset query -d scuba_queries --sql "
SELECT
  scuba_queries.\`source\` AS query_source,
  AVG(scuba_queries.\`cpu_time\`) / 1000 AS avg_cpu_sec,
  COUNT(*) AS cnt
FROM scuba_queries
JOIN core_viz_session_actions
  ON scuba_queries.\`user_id\` = CAST(core_viz_session_actions.\`userid\` AS STRING)
WHERE
  NOW() - 3600 <= scuba_queries.\`time\` AND scuba_queries.\`time\` <= NOW()
  AND NOW() - 3600 <= core_viz_session_actions.\`time\` AND core_viz_session_actions.\`time\` <= NOW()
  AND core_viz_session_actions.\`action\` = 'scuba_tools_execute_query'
GROUP BY query_source
ORDER BY avg_cpu_sec DESC
LIMIT 50
"
```

#### Common JOIN errors

| Error | Cause | Fix |
|-------|-------|-----|
| `We don't yet support UDF with signature OF(ANY, bigint)` | Using table alias for column access (`a.\`time\``) | Use full table name: `events.\`time\`` |
| `Syntax error at "a"` | Table alias used anywhere | Remove aliases, use full table names |
| `Malformed ON conditions!` | ON clause contains non-equality or non-column expressions | Ensure ON uses only `table.\`col\` = table.\`col\`` (CAST is allowed) |
| `Join Key type does not match` | Join columns have different types (e.g., string vs bigint) | Use `CAST(table.\`col\` AS STRING)` or `CAST(table.\`col\` AS BIGINT)` |
| `Exceeded Maximum Join Memory Limit` | Too much data in the join | Add time filters on BOTH tables, ensure join key is unique on at least one side |
| `OF(IDENTIFIER(...))` in GROUP BY error | `table.\`col\`` used in GROUP BY | Alias the column in SELECT, use the alias in GROUP BY |

### Weighted Aggregations

Many Scuba tables use sampling and include a `weight` or `sample_rate` column. For accurate aggregations on sampled data, use the two-argument form:

```bash
meta scuba.dataset query -d table_name --sql "
SELECT
  dimension,
  SUM(value, weight) AS weighted_sum,
  AVG(value, weight) AS weighted_avg
FROM table_name
WHERE time >= now()-3600 AND time <= now()
GROUP BY dimension
"
```

The second argument to `SUM()` and `AVG()` is the weight column. Each row's contribution is multiplied by its weight, correcting for sampling bias.

### Conditional Aggregations

Use `SUM(IF(...))` to compute multiple metrics in a single query:

```bash
meta scuba.dataset query -d table_name --sql "
SELECT
  region,
  SUM(IF(status = 'success', 1, 0)) AS successful,
  SUM(IF(status = 'failed', 1, 0)) AS failed,
  SUM(IF(status = 'timeout', 1, 0)) AS timeouts,
  COUNT(*) AS total
FROM table_name
WHERE time >= now()-3600 AND time <= now()
GROUP BY region
"
```

### Manual Time Bucketing

Use modulo arithmetic to create custom time buckets:

```bash
meta scuba.dataset query -d table_name --sql "
SELECT
  (time - (time % 300)) AS time_bucket,
  COUNT(*) AS cnt
FROM table_name
WHERE time >= now()-3600 AND time <= now()
GROUP BY time_bucket
ORDER BY time_bucket
"
```

| Granularity | Modulo value |
|---|---|
| 1 minute | 60 |
| 5 minutes | 300 |
| 15 minutes | 900 |
| 1 hour | 3600 |
| 6 hours | 21600 |
| 1 day | 86400 |

### CASE WHEN

```bash
meta scuba.dataset query -d table_name --sql "
SELECT
  task_id,
  end_time - start_time AS duration_seconds,
  CASE
    WHEN end_time - start_time < 60 THEN 'fast'
    WHEN end_time - start_time < 300 THEN 'medium'
    ELSE 'slow'
  END AS speed_category
FROM table_name
WHERE time >= now()-3600 AND time <= now()
LIMIT 100
"
```

---

## Unsupported SQL Features

| Feature | Alternative |
|---------|------------|
| `COUNT(DISTINCT col)` | `APPROX_COUNT_DISTINCT(col)` or `APPROX_COUNT_DISTINCT_HLL(col, 16)` |
| `UNION ALL` / `UNION` | Run separate queries, or use `--compare` for time comparisons |
| Window functions (`OVER()`) | Not available — compute in post-processing |
| Subqueries / CTEs | Not available — run separate queries and combine |
| `LIKE 'prefix%suffix'` (mid-wildcard) | Peregrine rejects `%` between literals. Use `REGEXP_MATCH(col, 'prefix.*suffix')`. Note: `LIKE '%x%'`, `LIKE 'x%'`, `LIKE '%x'` all work — only `'literal%literal'` fails |

---

## Tips

1. **Always start with schema**: `meta scuba.dataset info -d table_name`
2. **Start small, then expand**: Begin with `SELECT * ... LIMIT 10`, then add complexity
3. **Filter before grouping**: Apply WHERE filters first, then GROUP BY (faster than HAVING)
4. **Batch queries**: Combine conditions with `OR` instead of running N separate queries
5. **Explicit LIMIT**: Default is 100 but always set an explicit LIMIT
