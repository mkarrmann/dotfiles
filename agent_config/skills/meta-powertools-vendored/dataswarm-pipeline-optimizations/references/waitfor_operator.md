### Pattern Description
Dataswarm pipelines often include wait operators that check for upstream data availability. `WaitForManifoldOperator` uses busy-waiting (polling in a tight loop), consuming BCU even while idle. Replacing it with `WaitForHiveOperator` (which yields the container while waiting) eliminates wasted BCU. Similarly, unnecessary `DummyOperator` tasks at the end of pipelines consume container resources without doing useful work.

### How is this Pattern Detected?
Look for:
- `WaitForManifoldOperator` in the pipeline DAG — busy-waiting operator
- `DummyOperator` at the end of the pipeline — unnecessary terminal task
- High ratio of Dataswarm container BCU vs actual query BCU (visible in pipeline metrics)
- Wait tasks running for extended periods (hours) burning BCU while idle

### EXAMPLES
- [D90212105](/intern/diff/90212105/) — Replaced `WaitForManifoldOperator` with `WaitForHiveOperator`, 73-90% BCU reduction on wait portion
- [D86421843](/intern/diff/86421843/) — Removed unnecessary `DummyOperator` tasks

### WHAT TO DO?

**1. Replace WaitForManifoldOperator with WaitForHiveOperator**
```python
# Before (busy-waiting — wastes BCU)
WaitForManifoldOperator(
    task_id="wait_for_upstream",
    manifold_path="manifold://bucket/path/to/signal",
    ...
)

# After (yielding — releases container while waiting)
WaitForHiveOperator(
    task_id="wait_for_upstream",
    table="namespace.table_name",
    ds="<DATEID>",
    ...
)
```

**2. Remove unnecessary DummyOperator**
```python
# Before
task_a >> task_b >> DummyOperator(task_id="end")  # REMOVE DummyOperator

# After
task_a >> task_b  # Pipeline ends with last real task
```

### CAVEAT
- `WaitForHiveOperator` only works for Hive table signals. If you're waiting on a Manifold file that doesn't correspond to a Hive table partition, you may need a different approach.
- Ensure the upstream table and partition exist in the Hive metastore before switching.

### Estimated Savings
73-90% BCU reduction on the wait portion of the pipeline. Total pipeline savings depend on how much time is spent waiting vs computing.
