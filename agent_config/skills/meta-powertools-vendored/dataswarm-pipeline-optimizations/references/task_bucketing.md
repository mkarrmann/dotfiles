### Pattern Description
Task bucketing and sharding optimizations reduce BCU by aligning Spark shuffle partitions with the underlying table's bucket structure, and by breaking large monolithic tasks into smaller sharded units. When shuffle partitions are misaligned with table bucketing, Spark performs unnecessary re-shuffles. When a single task processes all data in one pass, it may use more resources than necessary.

### How is this Pattern Detected?
Look for:
- `spark.sql.shuffle.partitions` set to a value that doesn't match the input table's bucket count
- Large Spark tasks processing all data in one pass without sharding
- Join-by-key opportunities on bucketed tables (join key matches bucket key)
- `DynamicPipelineOperator` absent when the workload is naturally shardable
- Tables with known bucketing (check table DDL for `CLUSTERED BY ... INTO N BUCKETS`)

### EXAMPLES
- [D94149226](/intern/diff/94149226/) — Task bucketing alignment, saved ~60K BCU
- [D90730866](/intern/diff/90730866/) — Sharding optimization, 91% BCU reduction
- [D83862985](/intern/diff/83862985/) — Bucketing-aware join, saved ~200K+ BCU
- [D84528772](/intern/diff/84528772/) — DynamicPipelineOperator sharding

### WHAT TO DO?

**1. Align shuffle partitions with table bucketing**
```python
# Check the input table's bucket count first
# If table has 512 buckets, align shuffle partitions
spark_opts={
    "spark.sql.shuffle.partitions": "512",  # Match table bucket count
}
```

**2. Enable bucketing-aware operations**
```python
spark_opts={
    "spark.sql.fb.only.skipShuffleInBucketedWrite": "true",
    "spark.sql.fb.only.unionWithBucketing": "true",
    "spark.sql.fb.only.removeUnnecessaryShuffleSort": "true",
}
```

**3. Use DynamicPipelineOperator for sharding**
Break large tasks into sharded units that process data in parallel:
```python
DynamicPipelineOperator(
    ...
    shard_key="ds",  # or other natural partition key
    num_shards=10,
)
```

**4. Join-by-key 3-step pattern for bucketed tables**
When joining on the bucket key:
```sql
-- Step 1: Extract distinct keys from one side
WITH keys AS (
    SELECT DISTINCT join_key FROM small_table
)
-- Step 2: Colocated filter on bucketed table using bucket-aware scan
, filtered_big AS (
    SELECT b.*
    FROM big_bucketed_table b
    WHERE b.join_key IN (SELECT join_key FROM keys)
)
-- Step 3: Correctness join
SELECT f.*, s.attribute
FROM filtered_big f
JOIN small_table s ON f.join_key = s.join_key
```

### CAVEAT
- Only align shuffle partitions when you've confirmed the input table is bucketed — arbitrary alignment can hurt performance
- `DynamicPipelineOperator` adds pipeline complexity — use only when the BCU savings justify it
- Bucketing-aware Spark opts are Meta-specific (`spark.sql.fb.only.*`) — they may not be available in all environments

### Estimated Savings
60K–200K+ BCU per pipeline. Bucketing alignment on large tables typically saves 60-91% BCU.
