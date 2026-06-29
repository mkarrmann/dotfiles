### Pattern Description
In Presto joins, the build side (right table) may contain orders of magnitude more rows than actually match the probe side. When 99%+ of build-side rows are filtered after the join, those rows were materialized and hashed unnecessarily. The `join_prefilter_build_side` session property instructs Presto to pre-filter the build side using the probe-side keys before building the hash table, drastically reducing memory and CPU usage.

### How is this Pattern Detected?
Look for:
- Joins where the build side has far more rows than the join output (visible in Dr. Presto query stats)
- Absence of `join_prefilter_build_side: True` in session properties
- Cases where a CTE or subquery could pre-filter the right table using `WHERE key IN (SELECT key FROM probe_table)` but doesn't
- Large dimension tables joined to narrow fact tables on a selective key

### EXAMPLES
- [D88209708](/intern/diff/88209708/) — Added `join_prefilter_build_side: True`, saved ~43K BCU
- [D88204859](/intern/diff/88204859/) — Prefilter build side optimization, saved ~15K BCU
- [D88051954](/intern/diff/88051954/) — Prefilter build side, significant BCU reduction
- [D94255382](/intern/diff/94255382/) — Prefilter with explicit CTE
- [D92607178](/intern/diff/92607178/) — Combined with other join optimizations

### WHAT TO DO?

**Option 1: Session property (simplest)**
Add `join_prefilter_build_side: True` to the operator's session properties:
```python
PrestoInsertOperatorWithSchema(
    ...
    session_properties={
        "join_prefilter_build_side": True,
    },
)
```

**Option 2: Explicit CTE prefilter (more control)**
Write an explicit pre-filter when you want precise control:
```sql
-- Before: large dimension table joined directly
SELECT f.*, d.attribute
FROM fact_table f
JOIN large_dim_table d ON f.dim_key = d.key

-- After: pre-filter dimension table to only matching keys
WITH filtered_dim AS (
    SELECT d.*
    FROM large_dim_table d
    WHERE d.key IN (SELECT DISTINCT dim_key FROM fact_table)
)
SELECT f.*, fd.attribute
FROM fact_table f
JOIN filtered_dim fd ON f.dim_key = fd.key
```

### CAVEAT
`join_prefilter_build_side: True` can **backfire** when the build table is already small. In that case, the prefilter prevents Presto from using a broadcast join, which would have been more efficient. See [D88611063](/intern/diff/88611063/) where removing this property and using broadcast instead saved BCU. **Always validate with tester.**

### Estimated Savings
15K–43K BCU per pipeline, depending on the size ratio between build and probe tables.
