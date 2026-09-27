---
name: sapphire-pipeline-validation
description: Use when validating a change to a Sapphire (Presto-on-Spark / Sapphire Velox) Dataswarm pipeline against its production run — e.g. lowering hash_partition_count, changing join/optimizer session properties, or mitigating Warm Storage broadcast-file (file_broadcast_*) fan-out. Covers choosing ./tester vs a read-only replay vs asking the owners, what an agent session can and cannot run, tester safety gotchas, where Sapphire metrics actually live, attributing Warm Storage broadcast directories to queries, and how to compare and report results honestly. Trigger keywords: sapphire tester, validate pipeline change, hash_partition_count, broadcast files, FILE_SVC_OPEN_FILE, warm_storage_file_service_activity, replay prod query, shared_acl ERR_DS_ACL_CONFLICT.
---

# Validating Sapphire Pipeline Changes

Sibling skills: `sapphire-local-dev` (running a query with the driver on the devvm), `testing-dataswarm-pipelines` (general `./tester` syntax), `waiting-without-polling` (waiting on Chronos jobs).

## 1. Pick the validation path

| Path | Runs as | Use when | Blocked by |
|---|---|---|---|
| `./tester` on Chronos | a service identity (`--chronos-owner`) | The output table has no `shared_acl`, and a prefixed test table is acceptable | `shared_acl` on the output dataset (see below) |
| Read-only replay (`sapphire-local`) | the human running it | The tester is blocked, or you want an exact same-SQL A/B with no table write | **Agent identities** — see below |
| Ask the owning team | them | Neither works, or the data is DSS4 and you can't compare it | — |

**Agents cannot run replays.** Warm Storage denies the Sapphire driver's private temp directory to AI-agent identities, even with an `agent_x509/claude_code_$USER.pem` cert:

```
failed to create temp directory ... AUTH_FAILED ... msg=AI agents are not allowed to set expiration times on directories ... WSE_SECURITY_AUTHORIZATION_CHECK_DENY
```

This is a deliberate control. Do not strip agent env vars or otherwise work around it. Prepare the script, dry-run it (`DRY=1`), and hand the user a command to run from a normal terminal (not `!` inside the agent session, which carries the agent identity), using absolute paths:

```
tmux new-session -d -s <name> '/abs/path/run_replay.sh; echo "EXIT=$?"; exec bash'
```

The driver log stays readable to the agent: `sapphire-local extract <log>` prints the `Rows:` result block and the execution query id (the one with the `_00000_` counter).

**Tester outputs with `shared_acl` fail.** If `fbcode/dataswarm-pipelines/upm_data/datasets/hive/<namespace>/<table>.py` declares `shared_acl=...`, the tester creates the prefixed table under the namespace ACL and then fails re-binding it (`ERR_DS_ACL_CONFLICT ... already have existing DATA_NAMESPACE ACL`). A different data-project ACL or a SELECT super-user grant does not fix it. Check before launching.

## 2. Tester safety and gotchas

- **Always prefix** (`-b test_<user>_<tag>_`) and confirm in the log that every `Processed 'out', recorded 'DatasetRecord(dataset_name=...` line names a prefixed table before trusting the run. Abort otherwise.
- **`--tasks` must be the last argument.** Put it before the task path and it swallows the positionals (`task_spec required`).
- **`--dry-run` still submits Chronos jobs** (they log `"dry_run": true` and run no query). Don't mistake them for the real run.
- **`-be <table> ...`** keeps the named `<TABLE:...>` references unprefixed, so the task reads production inputs while writing its own prefixed output. It only changes table-name resolution; it cannot redirect an output with a different name. Needed whenever a prefixed upstream table wouldn't exist.
- **`--use-my-data-project-acl presto`** works for other teams' pipelines, but `presto` is a Hive super-user data project and DI security has flagged using super-user data projects in testers (T152504844, T205667883). Tell the user before relying on it.
- **Dynamic children** (`DynamicPipelineOperatorV2`) can't be selected individually with `--tasks`; the parent fans out over everything it enumerates.
- **Test-only edits** (e.g. a smaller `TABLESAMPLE` for the test branch): the tester ships the working copy as a patch at submit time, so revert the edit right after `Tester Job Submitted` prints, and confirm with `sl diff`.
- **Queueing:** tester launchers often sit 1–5h in `presto_batch_testing` BCP queues or behind a DPO's `MAX_CONCURRENCY`. Budget for it; it is not a failure.
- Wait with a quiet loop on `meta chronos.job describe --instance-id <id> --output json | head -1` (state leaves `ready`/`running`), not repeated model checks.

## 3. Read-only replay recipe

1. Get the production query's exact SQL and settings: the Dataswarm child Chronos log has the rendered SQL (verify against `presto_queries.queryFileHexHash`), `presto_queries.session_properties_json`, and `presto_spark_execution_info_json` (effective `hashPartitionCount`, `maxExecutorCount`).
2. Pin the same packages production ran: `-j <presto.spark version>` and the paired `-c <presto.presto_on_spark_cpp hash>`. Check both still exist with `fbpkg info`.
3. Pin the same inputs: replace latest-ds lookups with the partitions production actually read. Partitions that landed after production started must be excluded.
4. Wrap the SELECT (drop the INSERT) as `SELECT count(*), checksum(col1), ... FROM (<select>) t`. Use `checksum(array_sort(x))` for set-like arrays (`SET_AGG` order is not deterministic).
5. Change only the setting under test; keep a control arm available (`HPC=<prod value> run_replay.sh`) for a same-cluster comparison.
6. Compute the same aggregates over the production output partition (normal Presto, read-only) before the replay finishes.

## 4. Where the numbers live

- `presto_queries` reports **0** for Sapphire rows, bytes, memory and shuffle. Use it only for `session_properties_json`, `presto_spark_execution_info_json`, `client_info` (`dateId`), `source`, and wall time.
- `spark_app_metrics` / `spark_stage_metrics` joined on `presto_query_id` give stage task counts, run time, CPU, shuffle, OOM tasks, failed stage attempts. Spark spill and peak-execution-memory counters are 0 for native execution, so they are not evidence of "no spill".
- Velox native spill shows up as Warm Storage `spill/presto_native/` directories under the query's `tempToPerm` root.
- Billed cost: Adheer's mitigation diffs report Sapphire Velox BCU-days from `warehouse_batch_compute_usage` (`source=SPARK`) and `di.wi_compute_resource_usage_hourly_physical` (`spark_compute` `bcu_seconds`); see D121847815, D119610927.
- Wall time against a production run is confounded (adhoc vs production queue, cluster, time of day, package version, input drift). Prefer billed BCU/CPU or a same-cluster control arm, and say which you used.

## 5. Warm Storage broadcast attribution

- Native broadcast files live at `{temp storage}/{queryId}/file_broadcast_{uuid}` (`BroadcastWrite.cpp`; `PrestoFacebookSparkNativeExecutionTaskFactory.java`). Each consumer task opens every file, so **opens ≈ producer tasks × consumer tasks**, where producers = build-side fragment tasks.
- `warm_storage_file_service_activity` samples ops under 1s at 1/1000 and logs ops of 1s or more unsampled (`sample_rate=1`). Stored rows (retention) therefore track slowness as much as volume; compare **estimated opens = `SUM(sample_rate)`**, and split out `WSE_THROTTLED_HOT_PATH`.
- Mapping: `warm_storage_directory_service_activity` (`DIRECTORY_SVC_GET_DIRECTORY`, `DIRECTORY_PATH LIKE '%<qid>'`) gives `cluster` + `context`; the same `context` on `warm_storage_file_service_activity` gives the `DIR_ID`. `ws-broadcast-stats <qid> <since_epoch>` in this directory does all three steps.
- Scuba SQL quirks (`meta scuba.dataset query --sql`): GROUP BY must name columns, not ordinals; use `APPROX_COUNT_DISTINCT`, not `APPROX_DISTINCT` or `COUNT(DISTINCT ...)`.

Fan-out shapes seen in practice (check the plan, don't assume "just lower HPC"):
- A broadcast `LEFT JOIN x ... WHERE x.k IS NULL` is rewritten by `LeftJoinNullFilterToSemiJoin` with a DISTINCT on the build side that runs HPC-wide, producing HPC tiny files. `rewrite_left_join_null_filter_to_semi_join=false` keeps the plain scan.
- `join_prefilter_build_side=true` adds an HPC-wide DISTINCT of the probe keys that is broadcast; it is useless when it filters nothing.
- A tiny table declared with thousands of buckets makes a bucketed scan fragment with one task per bucket; `prism_batch.bucket_execution_enabled=false` makes it a SOURCE fragment.

## 6. Comparing results and reporting

- Input drift is common: latest-ds lookups resolve later in a test, short-retention inputs expire, `TABLESAMPLE` differs per run, `ROW_NUMBER` ties break differently. When the test can't match production, recompute the pipeline's own SQL on the test's inputs and compare to the test table instead.
- Report per change **what actually executed and at what volume** (full query vs sampled vs one task of N vs dev shards), and separate correctness from performance. Never call something validated when only lint, `./linter`, EXPLAIN, or a dry run ran.
- Classic-Presto EXPLAIN (what an agent can run) does not show Sapphire's history-based broadcast choices; confirm plan effects from the run's stage task counts and broadcast file counts.
