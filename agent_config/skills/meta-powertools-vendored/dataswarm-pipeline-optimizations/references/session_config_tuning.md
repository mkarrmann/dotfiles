### Pattern Description
Dataswarm pipeline session configuration directly affects query execution efficiency. Common anti-patterns include forcing override of auto-tuned efficiency configs, hardcoding shuffle partition counts, missing Adaptive Query Execution (AQE) settings for Spark, and missing BCU-optimized session properties for Presto. Fixing these misconfigurations often yields significant savings with minimal query changes.

### How is this Pattern Detected?
Look for:
- `force_override_efficiency_configs=True` in operator config — disables automatic tuning
- Hardcoded `spark.sql.shuffle.partitions` values — prevents auto-tuning
- Missing AQE configuration on Spark jobs with data skew
- Missing BCU-optimized session properties on Presto operators
- Excessive or unnecessary session properties that conflict with each other

### EXAMPLES
- [D93599609](/intern/diff/93599609/) — Removed `force_override_efficiency_configs`, 56% BCU reduction
- [D90467156](/intern/diff/90467156/) — Added BCU-optimized session properties
- [D92252753](/intern/diff/92252753/) — Fixed session config for better auto-tuning

### WHAT TO DO?

**1. Remove `force_override_efficiency_configs=True`**
```python
# Before
PrestoInsertOperatorWithSchema(
    ...
    force_override_efficiency_configs=True,  # REMOVE THIS
)

# After
PrestoInsertOperatorWithSchema(
    ...
    # Let the system auto-tune efficiency configs
)
```

**2. Remove hardcoded shuffle partitions (Spark)**
```python
# Before
spark_opts={
    "spark.sql.shuffle.partitions": "200",  # REMOVE — let auto-tuning decide
}

# After — remove the key entirely, or set to auto
spark_opts={
    # shuffle.partitions handled by auto-tuning
}
```

**3. Add AQE config for Spark jobs with data skew**
```python
spark_opts={
    "spark.sql.adaptive.enabled": "true",
    "spark.sql.adaptive.skewJoin.enabled": "true",
    "spark.sql.adaptive.coalescePartitions.enabled": "true",
}
```

**4. Add BCU-optimized Presto session properties**
```python
session_properties={
    "hash_partition_count": 8192,
    "join_reordering_strategy": "ELIMINATE_CROSS_JOINS",
    "optimize_hash_generation": True,
}
```

### CAVEAT
- **Don't blindly add session properties** — they can backfire. [D93633730](/intern/diff/93633730/) made BCU worse by adding inappropriate session configs. Always validate with tester.
- `hash_partition_count: 8192` is a good default but may not suit all queries (very small queries may perform worse with too many partitions).
- Removing `force_override_efficiency_configs` is almost always safe and beneficial, as it re-enables automatic performance tuning.

### Estimated Savings
28-90% BCU reduction. Removing `force_override_efficiency_configs` alone typically saves 28-56%.
