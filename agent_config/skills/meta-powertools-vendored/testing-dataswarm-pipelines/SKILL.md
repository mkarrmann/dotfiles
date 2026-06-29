---
name: testing-dataswarm-pipelines
description: Use when testing or backfilling dataswarm pipelines - covers tester syntax, nbackfill with OPEC mode, and output validation via presto CLI and table_differ
---

# Testing and Backfilling Dataswarm Pipelines

## Working Directory

Always run commands from the `dataswarm-pipelines` directory:

```bash
cd ~/fbsource/fbcode/dataswarm-pipelines
```

---

# Pipeline correctness essentials (verify BEFORE you test or submit)

A new or changed pipeline that produces a **curated Hive output table downstream consumers read** is not done just because the SQL/transform runs. Before `./tester` (and before `sl commit` / `jf submit --draft`), confirm it carries the essentials below — they are expected for such curated outputs, and omitting them is a common cause of review pushback. For the full DQ + signal-table API, the `dataswarm-add-dq-checks` skill is authoritative.

- **DQ checks** — add `.auto_dqs(...)` to the final output operator's chain (standard checks: row-count > 0, day-over-day row-count threshold, key-column NOT NULL, uniqueness). Set `dq_do_not_stop_pipeline=False` when a failed check must block the run, `=True` for non-blocking.
- **Signal table** *(only when a downstream pipeline depends on it)* — if a downstream pipeline `WaitFor`s this output (i.e. the table is a curated output others read), add `.signal_table()` immediately after `.auto_dqs(...)` (or use a `PublishSignalTableOperator`) so it reads `<LATEST_DS:<table>_signal>` only validated partitions; a plain replication that nobody waits on does not need one. For a **DataSync** replication that publishes a signal, chain `.publish(signal_name="ds", signal_value="<DATEID>")` on the load.
- **End-pipeline marker** — `enable_data_annotations_features(__name__)` at the end of the pipeline file (required when using the `input`/`output` data-annotations DSL).
- **UPM dataset schema** — for a NEW Hive output table, run `./linter --update-schema <task_path>` to generate the UPM DataSet under `upm_data/datasets/hive/`. (DataSync pipelines auto-generate the schema via the tester — no hand-written UPM needed there.)
- **Header + defaults** — `# @data-project-acl: <acl>` header and `GlobalDefaults.set(oncall=..., user=..., schedule=...)`; keep `oncall` identical to the UPM `@hive_dataset(...)` oncall.

**Pre-submit gates (run locally, in order):** `arc f` (format) -> `arc lint` -> `sl commit` (Sapling — never `git`) -> `jf submit --draft`. Note: `arc lint` may report `No linters to run` for a path with no registered linters — that still counts as having run the gate. If a runtime step (`./tester`, `./nbackfill`, `conf build`, `chronos sync`, `fbpkg`, `jf submit`) is blocked (missing credentials / no Dataswarm runtime), print the exact command and name the verification (tester run, Chronos job ID, Daiquery/Presto check) as an OUTSTANDING step — never fabricate a job ID or result.

**Variant notes:** a *modify-existing* pipeline keeps its existing output table + UPM schema (don't regenerate UPM — just make the requested change); a *backfill / monitoring* request on an already-landed pipeline creates NO new pipeline file (use `./nbackfill` + CDM portfolio / alerts); a *standalone Chronos job* uses a `ChronosJobConfig` `.cconf` + fbpkg-packaged binary and has no Hive pipeline, so DQ / signal table / UPM do not apply.

---

# Running Tester

## Basic Tester Syntax

```bash
./tester [options] <task_path> <date>
```

The task path is relative to the `tasks/` directory.

## Examples

**Standard test run:**
```bash
./tester --verbose ad_metrics/smb_ads/retrieval/smb_ads_metrics_daily.py 2026-02-01
```

**Test with table prefix:**
```bash
./tester --verbose -b test_${USER}_ namespace/path/pipeline.py 2026-01-30
```

**Test specific tasks:**
```bash
./tester --verbose --tasks task_name1 task_name2 namespace/path/pipeline.py 2026-01-30
```

## Tester Options Reference

### Core Options

| Option | Description |
|--------|-------------|
| `--verbose` or `-v` | Print status messages to stderr |
| `--debug` | Print status and debug messages to stderr |
| `--quiet` or `-q` | Print warnings and errors only |
| `--dry-run` | Preview without executing |
| `--no-lint` | Disable automatic linting of Hive/Presto queries |

### Table Options

| Option | Description |
|--------|-------------|
| `-b <prefix>` | Prefix to prepend to output tables (e.g., `test_${USER}_`) |
| `-be <tables>` | Exclude specific tables from prefixing |
| `--test-table-retention <1-89>` | Override default test table retention (days) |

### Task Selection

| Option | Description |
|--------|-------------|
| `--tasks <names>` | List of specific tasks to run |
| `--rtasks <patterns>` | Regex patterns of tasks to run |
| `-r` or `--task-regexp` | Treat task_spec as regular expression |
| `-p` or `--include_dependencies` | Include upstream dependencies |
| `-id` or `--include-downstream` | Include downstream task dependencies |

### Execution Options

| Option | Description |
|--------|-------------|
| `-l` or `--local-run` | Run tasks locally instead of Chronos |
| `--max-concurrent <1-23>` | Max concurrent runs in Chronos |
| `--stages {query,validate}` | Run only specified operator stages |
| `--skip-partition-check` | Skip partition existence checks |
| `--serialize` | Serialize pipeline locally, execute remotely |

### ACL and Permissions

| Option | Description |
|--------|-------------|
| `--use-my-data-project-acl <acl>` | Replace pipeline's ACL with your own |
| `--chronos-owner <oncall>` | Override Chronos job owner |

### Date Options

| Option | Description |
|--------|-------------|
| `--date-list <dates>` | List of dates or ranges (e.g., `2026-01-01#2026-01-05`) |

### Notifications

| Option | Description |
|--------|-------------|
| `--notify-failure` | Send email on failure |
| `--notify-success` | Send email on success |
| `--notify-emails <emails>` | Additional email addresses |
| `--notify-dataops` | Get DataOps Bot updates |
| `--tester-to-diff <diff>` | Add comment to diff on test start |
| `--append-to-test-plan <diff>` | Append chronos log to diff's test plan |

### Advanced Options

| Option | Description |
|--------|-------------|
| `--opec-mode {OPEC_ELIGIBLE,OPEC_ONLY}` | Run via OPEC capacity |
| `--ntlb-tier-override <1-5>` | Override NTLB tier for tester job |
| `--hive-pool <pool>` | Override hive pool |
| `--host-group <group>` | Override host group |
| `--data-profiler` | Run Data Profiler job after success |
| `--validate-annotations` | Validate pipeline's data annotations |

## Testing Pipelines with HackOperator (PHPOperator)

When your pipeline uses a `TransformOperator` backed by a Hack (PHP) transformer and you have **local changes** to the transformer (or are adding a new one), extra steps are required so the tester can find and execute your local Hack code.

### 1. Set `php_root` in the pipeline

Add `php_root` to the `TransformOperator` so it resolves the Hack file from your local `www` checkout:

```python
TransformOperator(
    ...,
    php_root="/data/sandcastle/boxes/fbsource/www",
)
```

> **Important:** Remove `php_root` before landing your diff — it is only needed for local testing.

### 2. Run the tester with `--execute_php` and `--local-run`

Both flags are required:

- `--execute_php` — tells the tester to execute Hack/PHP transformers locally.
- `--local-run` (or `-l`) — runs the pipeline locally instead of on Chronos, which is necessary for picking up your uncommitted changes.

```bash
./tester --verbose --execute_php --local-run namespace/path/pipeline.py 2026-02-09
```

### Why both flags?

Without `--local-run`, the job runs on Chronos where your local changes don't exist. Without `--execute_php`, the tester won't invoke the Hack transformer. Without `php_root`, the tester can't locate the transformer file on your machine.

---

# Running Backfills

## Basic Backfill Syntax

```bash
./nbackfill -ts <task_path> -s <start_date> -e <end_date> --remote --write-state [options]
```

The task path is relative to the `tasks/` directory.

## Examples

**Production backfill with OPEC only (no quota):**
```bash
./nbackfill \
    -ts ad_metrics/smb_ads/retrieval/smb_ads_metrics_daily.py \
    -s 2026-01-05 \
    -e 2026-02-01 \
    --remote \
    --write-state \
    --opec-mode OPEC_ONLY
```

**Standard production backfill:**
```bash
./nbackfill \
    -ts namespace/path/pipeline.py \
    -s 2026-01-15 \
    -e 2026-01-20 \
    --remote \
    --write-state
```

**SEV-related high priority backfill:**
```bash
./nbackfill \
    -ts namespace/path/pipeline.py \
    -s 2026-01-15 \
    -e 2026-01-15 \
    --remote \
    --write-state \
    --sev-number 12345678 \
    --ntlb-tier-override 1
```

## Backfill Options Reference

### Task Selection

| Option | Description |
|--------|-------------|
| `-ts <path>` | Task ID or pipeline file path (relative to `tasks/`) |
| `-t <pattern>` | Regular expression for task names |
| `--task-list <tasks>` | Restrict to specific task names |
| `--task-exclude <patterns>` | Skip tasks matching patterns |
| `-iu` or `--ignore-upstream` | Do not include upstream dependencies |
| `-id` or `--include-downstream` | Include downstream task dependencies |
| `--include-paused` | Include paused tasks |
| `--stages {query,validate}` | Run only specified operator stages |

### Date Options

| Option | Description |
|--------|-------------|
| `-s <date>` | Start date (YYYY-MM-DD or YYYY-MM-DD-HH:MM) |
| `-e <date>` | End date (defaults to start date if not specified) |
| `--date-list <dates>` | List of dates or ranges (e.g., `2026-01-01#2026-01-05`) |
| `-ip` or `--ignore-past` | Run dates in parallel ignoring depends_on_past |
| `-cw <days>` | Chunk date range into batches of N days |
| `--chunk-range-from-end-date` | Process chunks from end to start date |

### Execution Options

| Option | Description |
|--------|-------------|
| `--remote` | Run with local changes on Chronos (required for production) |
| `--dry-run` | Preview DAG without executing |
| `--preview` | Show preview only, do not run |
| `--run` | Execute the backfill (used with daemon) |
| `--synchronous` | Run backfill synchronously |
| `-ff` or `--fail-fast` | Kill all tasks on any failure |
| `--retries <N>` | Chronos retry count |
| `--yes-i-know-what-im-doing` | Non-interactive mode (no confirmation) |

### Task State Options

| Option | Description |
|--------|-------------|
| `-ws` or `--write-state` | Update DB state with success/failures |
| `-ss` or `--skip-success` | Skip task instances already marked successful |
| `-d` or `--db-awareness` | Equivalent to --skip-success and --write-state |
| `--only-unsuccessful` | Only include failed task instances |

### Priority and Resources

| Option | Description |
|--------|-------------|
| `--opec-mode {OPEC_ELIGIBLE,OPEC_ONLY}` | Run via OPEC capacity |
| `--sev-number <SEV>` | Associate with SEV for high priority |
| `--ntlb-tier-override <1-5>` | Override NTLB tier (1=highest) |
| `--hive-pool <pool>` | Override hive pool |
| `--host-group <group>` | Override host group |

### ACL and Permissions

| Option | Description |
|--------|-------------|
| `--use-my-data-project-acl <acl>` | Replace pipeline's ACL with your own |
| `--submit-through-server` | Submit via Thrift server (no local changes) |

### Table Options

| Option | Description |
|--------|-------------|
| `-b <prefix>` | Prefix to prepend to tables |
| `-be <tables>` | Exclude specific tables from prefixing |

### Concurrency Options

| Option | Description |
|--------|-------------|
| `-j <N>` | Max dates to run in parallel |
| `--max-concurrent <N>` | Max concurrent backfill runs per user per pipeline |
| `--date-groups` | Group task instances by date under parent jobs |
| `--parent-timeout <0-29>` | Chronos parent job timeout in days (default 29) |

### Notifications

| Option | Description |
|--------|-------------|
| `--notify-failure` | Notify on failure |
| `--notify-success` | Notify on success |
| `--notify-emails <emails>` | Additional email addresses |
| `--notify-dataops` | Get DataOps Bot updates |

### Cold Storage Options

| Option | Description |
|--------|-------------|
| `--restore-partitions` | Enable cold storage restore |
| `-rt <days>` | Retention for restored partitions (default 7) |
| `--restore-polling-interval <mins>` | Polling interval for restore status (default 15) |
| `--skip-partition-check` | Skip partition existence check |

### Cross-Pipeline Options

| Option | Description |
|--------|-------------|
| `--pull` | Pull upstream pipelines if input missing |
| `--push` | Push generated data to downstream pipelines |
| `--wait-for-runs <ids>` | Wait for other backfill run IDs |
| `--offline-output-partitions` | Offline old partitions (cross-pipeline backfill) |

### Verbosity Options

| Option | Description |
|--------|-------------|
| `--verbose` or `-v` | Print status messages |
| `--debug` | Print debug messages |
| `--quiet` or `-q` | Print warnings and errors only |
| `--no-tree` | Suppress dependency graph printout |
| `--no-lint` | Disable automatic query linting |

## OPEC Mode

| Mode | Description | Use case |
|------|-------------|----------|
| `OPEC_ONLY` | Only use spare capacity | Non-urgent backfills |
| `OPEC_ELIGIBLE` | OPEC when available, fallback to quota | Moderate priority |
| (not set) | Regular quota | Time-sensitive/SEV backfills |

---

# Permission Errors

If a tester or backfill run fails with permission errors (`ChronosAuthorizationException`, `Data Project ACL denied`, `does not have SELECT privileges`, or `Denied DPAS unified check`), invoke the `accessmate-dataswarm` skill to diagnose and resolve the issue. Paste the error output and it will identify the Data Project, explain the problem, and help submit access requests.

# Testing Workflow Overview

- run a ./tester flow
- put the chronos id of the tester run like x12345678 into the test plan
- wait for the chronos job to finish (poll_chronos_job.sh)
- check the chronos job log to see if there is any error
- check the output table to see if the data is correct
- add output validation to the test plan.

# Finding the test data

Let's assume the tester was already run and succeeded. You need to find the
output data and validate it. In this case we don't need to update to the actual
commit of the diff, we'll just validate it by inspecting the output in hive.

## Is it done?

If the chronos job is not complete, you probably have to wait for it to finish.

## Was it successful?

If poll_chronos_job indicates the job failed, the test tables may not have been
created and you may not be able to do validation.

## What's the test table name?

- Test partitions of a table are typically one day or one hour, prefixed with
  test\_. You can customize the prefix using the -b flag to tester, so if that's
  in the test plan, you can use it to find the test partition.
- The output table name is usually in the pipeline text in an `output.table`
  statement. If the pipeline has multiple tasks, you may need more context from
  the author to figure out which table to validate.

## What are the test table partitions?

- If the tester command line was available to you, you can find the date range
  here.
- Otherwise, if you know the partition already then you can check metastore for
  the available partitions by querying the virtual $partitions table like this

`select ds from "test_my_new_table_name$partitions" order by ds desc limit 5`

If you can't figure out the test table name, stop and ask for help. Otherwise,
proceed to validation.

# Validating the test data

We're going to use presto command line to validate the test data, so load up the
presto CLI skill from
~/fbcode/claude-templates/components/skills/presto-cli/SKILL.md. We always need to
know the namespace, which is the first token in the pipeline name (like
/tasks/infrastructure). Try presto queries like
`presto $NAMESPACE --execute "... your sql here..." --output-format ALIGNED` to
get output that looks nice in markdown.

## Discovering table schema

Before validating, discover the table structure:

```bash
presto $NAMESPACE --execute "DESCRIBE test_table_name"
```

This shows all columns, types, and which columns are partition keys - essential
for writing validation queries.

## Validation workflow

1. **Check source data exists**: Verify input tables have data for the test date
2. **Check row counts**: Ensure output has expected number of rows
3. **Validate distributions**: Check categorical columns have reasonable value
   distributions
4. **Spot-check logic**: Sample a few rows and verify calculations manually

## examples

### brand new table: inspect output directly

- how many rows are there?
- what are the ranges of values?
- are any categorical columns null?
- Are the numeric columns semantically valid?

Example validation queries:

```bash
# Total row count
presto $NAMESPACE --execute "
SELECT COUNT(*) as total_rows
FROM test_table_name
WHERE ds = '2025-12-07'
" --output-format ALIGNED

# Distribution of categorical values
presto $NAMESPACE --execute "
SELECT category_column, COUNT(*) as count
FROM test_table_name
WHERE ds = '2025-12-07'
GROUP BY category_column
ORDER BY category_column
" --output-format ALIGNED

# Check for nulls and ranges
presto $NAMESPACE --execute "
SELECT
    COUNT(*) as total_rows,
    COUNT(DISTINCT id_column) as distinct_ids,
    SUM(CASE WHEN important_column IS NULL THEN 1 ELSE 0 END) as null_count,
    MIN(numeric_column) as min_value,
    APPROX_PERCENTILE(numeric_column, 0.5) as p50_value,
    MAX(numeric_column) as max_value
FROM test_table_name
WHERE ds = '2025-12-07'
" --output-format ALIGNED
```

**Verify input data**: Before validating output, check that source data exists:

```bash
presto $NAMESPACE --execute "
SELECT COUNT(*) as source_row_count
FROM source_table_name
WHERE ds = '2025-12-07' AND <any_filters_from_pipeline>
" --output-format ALIGNED
```

### refactor only, no logic changes

In this case we want to show the table is identical. Try `table_differ` to
compare the tables. It has its own help, but the basic structure is like this
`table_differ test_$TABLE $NAMESPACE  $TABLE  $NAMESPACE $DS`

### logic change in the table.

If the table is making a specific intentional change, validate that the change
was precise. Check that the intended change happened and that unexpected changes
did not happen.

Try techniques like

- checking aggregated values between the test table and the prod table. For
  comparing many aggregated metrics at once, use the `pivoted-comparison` skill
  which provides a MAP_FROM_ENTRIES/UNNEST pattern that pivots all metrics into
  a readable side-by-side format with match/difference/pct_diff columns.
- joining test and prod tables together by a join key and comparing them
- printing out direct samples of interesting rows

### Miscellaneous tips:

- Most test plans only test one date or hour partition, unless they are trying
  to validate business logic over a large range
- production hive tables can be large - you will typically work with only one
  date partition at a time and it would be unusual to query lots of days of a
  very large table in the average test plan.
- prefer APPROX_DISTINCT instead of COUNT(DISTINCT) for performance
- use ARBITRARY() to select an example ID when grouping
