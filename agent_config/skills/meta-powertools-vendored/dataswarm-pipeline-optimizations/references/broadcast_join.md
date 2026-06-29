### Pattern Description
When one side of a join is significantly smaller than the other (>100:1 ratio), a broadcast join sends the small table to all worker nodes instead of shuffling both tables. This avoids the expensive hash-partitioning and network transfer of the large table. Both Presto and Spark support broadcast joins, but they may not trigger automatically when table statistics are stale or absent.

### How is this Pattern Detected?
Look for:
- Joins where one table is orders of magnitude smaller than the other (>100:1 row or byte ratio)
- Small table fits in memory (<600MB for Presto, <1GB for Spark default threshold)
- No broadcast hint or session property set
- Dr. Presto showing shuffle-based join on a very small build side

### EXAMPLES
- [D87160635](/intern/diff/87160635/) — Added broadcast join hint, saved ~51K BCU
- [D88611063](/intern/diff/88611063/) — Switched from prefilter to broadcast for small table, saved ~34K BCU
- [D80381588](/intern/diff/80381588/) — Broadcast join optimization
- [D76776859](/intern/diff/76776859/) — Spark broadcast join threshold tuning

### WHAT TO DO?

**Presto: Session property**
```python
PrestoInsertOperatorWithSchema(
    ...
    session_properties={
        "join_distribution_type": "BROADCAST",
    },
)
```

**Presto: Query hint (per-join control)**
```sql
SELECT /*+ BROADCAST(small_table) */
    l.*, s.attribute
FROM large_table l
JOIN small_table s ON l.key = s.key
```

**Spark: Increase auto-broadcast threshold**
```python
HiveInsertOperatorWithSchema(
    ...
    spark_opts={
        "spark.sql.autoBroadcastJoinThreshold": "1073741824",  # 1GB
    },
)
```

### CAVEAT
- **Conflicts with `join_prefilter_build_side=True`**: If both are set, the prefilter may prevent the broadcast optimization. For small build tables, remove `join_prefilter_build_side` and use broadcast instead.
- **Memory limits**: If the small table is too large for broadcast (>600MB Presto, >1GB Spark), this will cause OOM errors. Check table size first.
- `join_distribution_type: "BROADCAST"` applies to ALL joins in the query. Use query hints for per-join control when only some joins should broadcast.

### Estimated Savings
34K–51K BCU per pipeline. Particularly effective for star-schema queries joining a large fact table to small dimension tables.
