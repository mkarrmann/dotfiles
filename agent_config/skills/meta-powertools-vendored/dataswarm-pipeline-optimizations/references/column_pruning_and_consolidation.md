### Pattern Description
Selecting more columns or computing more aggregates than needed wastes I/O, memory, and CPU. Common patterns include `SELECT *` instead of specific columns, computing all histogram/aggregate variants when only one is needed, scanning the same large table multiple times in separate tasks, and using window functions when simpler GROUP BY would suffice.

### How is this Pattern Detected?
Look for:
- `SELECT *` in queries (especially on wide tables with 100+ columns)
- Computing all histogram variants (percentiles, counts, sums) when only one aggregate is needed
- Multiple tasks in the same pipeline scanning the same large table for different subsets
- Window functions (`ROW_NUMBER`, `RANK`, `LAG`, `LEAD`) that could be replaced by GROUP BY + aggregate functions
- CTEs that select many columns but downstream only uses a few

### EXAMPLES
- [D91614242](/intern/diff/91614242/) — Column pruning on wide tables, saved 180K+ BCU
- [D94716590](/intern/diff/94716590/) — Computed only required aggregates, 60-67% BCU reduction
- [D88893560](/intern/diff/88893560/) — Consolidated multi-platform scans into single task, 45% reduction
- [D92897263](/intern/diff/92897263/) — Merged redundant scans with UNION ALL + COUNT_IF

### WHAT TO DO?

**1. Select only needed columns**
```sql
-- Before
SELECT * FROM wide_table WHERE ds = '<DATEID>'

-- After
SELECT user_id, event_type, event_time, metric_value
FROM wide_table WHERE ds = '<DATEID>'
```

**2. Compute only required aggregates**
```sql
-- Before: computing everything
SELECT
    key,
    COUNT(*) AS cnt,
    SUM(val) AS total,
    AVG(val) AS avg_val,
    APPROX_PERCENTILE(val, 0.5) AS p50,
    APPROX_PERCENTILE(val, 0.9) AS p90,
    APPROX_PERCENTILE(val, 0.99) AS p99,
    HISTOGRAM(val) AS hist
FROM big_table
GROUP BY key

-- After: only what downstream needs
SELECT
    key,
    COUNT(*) AS cnt,
    SUM(val) AS total
FROM big_table
GROUP BY key
```

**3. Merge multiple scans into one task**
```sql
-- Before: two separate tasks scanning the same table
-- Task 1: SELECT ... FROM events WHERE platform = 'ios'
-- Task 2: SELECT ... FROM events WHERE platform = 'android'

-- After: single task with conditional aggregation
SELECT
    user_id,
    COUNT_IF(platform = 'ios') AS ios_events,
    COUNT_IF(platform = 'android') AS android_events,
    SUM_IF(metric, platform = 'ios') AS ios_metric,
    SUM_IF(metric, platform = 'android') AS android_metric
FROM events
WHERE platform IN ('ios', 'android')
GROUP BY user_id
```

**4. Replace window functions with GROUP BY**
```sql
-- Before: window function for dedup
SELECT * FROM (
    SELECT *, ROW_NUMBER() OVER (PARTITION BY key ORDER BY ts DESC) AS rn
    FROM table_a
) WHERE rn = 1

-- After: GROUP BY with MAX_BY
SELECT
    key,
    MAX_BY(value, ts) AS latest_value,
    MAX(ts) AS latest_ts
FROM table_a
GROUP BY key
```

### CAVEAT
- Column pruning has the biggest impact on wide columnar tables (ORC/Parquet). On row-stored tables, the savings are smaller.
- When consolidating scans, ensure the merged query doesn't become too complex for the optimizer — test with tester.
- `MAX_BY` / `MIN_BY` are Presto-specific. For Spark, use the window function approach or equivalent UDFs.

### Estimated Savings
45-90% BCU reduction. Column pruning on wide tables (100+ columns) can save 180K+ BCU. Scan consolidation typically saves 45-67%.
