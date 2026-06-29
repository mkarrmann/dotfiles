### Pattern Description
Joins are one of the most expensive operations in distributed query engines. When tables have duplicate keys, or when the same large table is scanned multiple times, significant savings are possible by pre-computing aggregates before joining, consolidating redundant scans, or restructuring cumulative patterns. The key insight is: reduce row counts and scan counts before the expensive join operation.

### How is this Pattern Detected?
Look for:
- Joins on tables with duplicate keys where GROUP BY or DISTINCT could reduce rows before joining
- Multiple CTEs or tasks scanning the same large table independently
- Cumulative table patterns using FULL OUTER JOIN (can be replaced with reading N daily partitions + GROUP BY)
- CROSS JOIN UNNEST on arrays without pre-filtering rows that won't match
- Window functions (ROW_NUMBER, RANK) used just to deduplicate before a join

### EXAMPLES
- [D91949461](/intern/diff/91949461/) — Pre-aggregation before join reduced pipeline from 14K to 400 BCU (35x reduction)
- [D86698074](/intern/diff/86698074/) — Consolidated redundant scans, 58-62% BCU reduction
- [D92804318](/intern/diff/92804318/) — Pre-computation with GROUP BY before join
- [D88893560](/intern/diff/88893560/) — Consolidated multi-platform scans
- [D92897263](/intern/diff/92897263/) — Merged redundant table scans into UNION ALL + COUNT_IF
- [D89678082](/intern/diff/89678082/) — EXISTS pre-filter before CROSS JOIN UNNEST

### WHAT TO DO?

**1. Pre-aggregate before joining**
```sql
-- Before: joining with duplicate keys
SELECT a.*, b.metric
FROM table_a a
JOIN table_b b ON a.key = b.key

-- After: deduplicate/aggregate first
WITH deduped_b AS (
    SELECT key, SUM(metric) AS metric
    FROM table_b
    GROUP BY key
)
SELECT a.*, db.metric
FROM table_a a
JOIN deduped_b db ON a.key = db.key
```

**2. Pre-filter before CROSS JOIN UNNEST**
```sql
-- Before: unnest all rows then filter
SELECT t.id, u.element
FROM table_t t
CROSS JOIN UNNEST(t.array_col) AS u(element)
WHERE u.element IN (SELECT val FROM filter_table)

-- After: filter first with EXISTS
WITH filtered AS (
    SELECT t.*
    FROM table_t t
    WHERE EXISTS (
        SELECT 1 FROM filter_table f
        WHERE CONTAINS(t.array_col, f.val)
    )
)
SELECT f.id, u.element
FROM filtered f
CROSS JOIN UNNEST(f.array_col) AS u(element)
WHERE u.element IN (SELECT val FROM filter_table)
```

**3. Consolidate redundant table scans**
```sql
-- Before: two separate scans
WITH ios_data AS (
    SELECT user_id, metric FROM big_table WHERE platform = 'ios'
),
android_data AS (
    SELECT user_id, metric FROM big_table WHERE platform = 'android'
)
SELECT ... FROM ios_data JOIN android_data ...

-- After: single scan with conditional aggregation
SELECT
    user_id,
    SUM(CASE WHEN platform = 'ios' THEN metric END) AS ios_metric,
    SUM(CASE WHEN platform = 'android' THEN metric END) AS android_metric
FROM big_table
WHERE platform IN ('ios', 'android')
GROUP BY user_id
```

**4. Replace cumulative FULL OUTER JOIN**
```sql
-- Before: cumulative pattern with FULL OUTER JOIN
SELECT COALESCE(a.key, b.key), ...
FROM yesterday_cumulative a
FULL OUTER JOIN today_incremental b ON a.key = b.key

-- After: read N daily partitions and aggregate
SELECT key, SUM(metric), MAX(last_seen)
FROM daily_table
WHERE ds >= '<N_DAYS_AGO>'
GROUP BY key
```

### Estimated Savings
35x reduction in best cases (14K→400 BCU). Typical savings: 45-62% BCU reduction per pipeline.
