---
name: chronos-debug
description: Debug Chronos job instances by checking status, analyzing exit codes and DAG structures, querying Scuba tables, and viewing logs. Use when investigating job failures, checking if a job succeeded, or debugging pipeline execution. Chronos job instance IDs start with "x" followed by 17-20 digits.
---

# Chronos Debug

**PREREQUISITE**: Use with `scuba_cli` skill - it provides the Scuba query
construction needed by this workflow.

**WARNING**: There is NO `chronos_status` command. This skill is the ONLY way to
check Chronos job status.

## Job Instance IDs

Format: `x` + 17-20 digits (e.g., `x28147507671475012`). Strip the `x` prefix for **Scuba queries** (integer, no quotes). **`chronos info`** requires the bare integer without the `x` prefix — passing `x<id>` causes a `ParseIntError` crash. Other CLI commands (e.g. `chronos log`, `chronos restart`) accept either form.

## Quick Reference

### 1. Find Child Jobs (DAG Structure)

- Query `chronos_job_instance_states` with columns: `CAST_AS_BIGINT(exit_code) AS ec`,
  `FROM_UNIXTIME(time) AS event_time`, `exit_code`, `job_instance_id`, `jobname`,
  `FROM_UNIXTIME(finished_running_at) AS finished_at`, `parent_job_instance_id`,
  `root_exit_code` where `parent_job_instance_id IN (PARENT_ID)` (integer, no
  quotes). **Important:** alias `CAST_AS_BIGINT(exit_code)` with a name other than
  `exit_code` (e.g. `ec`) — aliasing it as `exit_code` causes a Scuba cyclic
  dependency error.
- **REQUIRED**: Create detailed table showing ALL child jobs with exit code,
  status (✅/❌/⏭️), job instance ID, job name, finished time

### 2. Exit Codes

| Code | Meaning | Action |
|------|---------|--------|
| 0 | Success | Check logs if unexpected results |
| 3 | Sandcastle/Skycastle workflow failure | Check stdout for workflow URL |
| 90 | Failure | Fetch and analyze logs |
| 137 | OOM Kill | Increase memory/container size |
| 12021 | Abandoned | Skip - dependency failed, never ran |

### 3. Logs

```bash
chronos log -c atn -t stderr --tail 200 <job_id>      # Last 200 stderr lines
chronos log -c atn -t stdout --tail 200 <job_id>      # stdout for data quality
chronos log -c atn -t stderr --all <job_id> -p        # Full logs to console
chronos log -c atn -t stderr --all <job_id> -r "ERROR" -p  # Filter by regex
```

- **Always check both stdout AND stderr** - stdout often contains links to downstream systems
- Auto-fetch stderr for failures (exit 90 or non-zero, not 12021)
- Logs save to `/tmp/chronos_<job_id>_STDERR.log`
- Parse and present: error type, message, root cause, suggested fix
- Setup: `devfeature install chronos_scripts`

### 3a. Jobs Triggering External Workflows (Sandcastle/Skycastle)

Some Chronos jobs are wrappers that trigger Sandcastle/Skycastle workflows. For these jobs:

- **Exit code 3** typically means the downstream workflow failed
- **Always check stdout first** - it contains the workflow URL:
  ```bash
  chronos log -c <cluster> -t stdout --tail 100 <job_id>
  ```
- stdout will show lines like:
  ```
  Scheduled Sandcastle workflow: https://www.internalfb.com/sandcastle/workflow/...
  (Last Retry) Workflow finished with RunStatus.FAILURE: https://www.internalfb.com/sandcastle/workflow/...
  ```
- The actual failure details are in the Sandcastle workflow, not the Chronos logs
- Open the workflow URL to see which step failed and the real error message

### 4. Query Jobs

```bash
chronos query running -O $(whoami)                         # Your running jobs
chronos query finished -O $(whoami) --failed --begin=-6h   # Failed in last 6h
chronos query pending -O $(whoami)                         # Waiting jobs
chronos query finished --failed --last-attempt --begin=-2h # Failed, won't retry
chronos query finished -J 'DATASWARM\.ns.*' -c atn         # By job name regex
```

#### Finding the Latest Run of a Job by Name

**Always use `-J` (regex) not `-j` (exact match)** — exact match often returns nothing even for valid job names. Use `.*` anchors around the job name:

```bash
# Step 1: Find the cluster and confirm the job name (omit -c to search all clusters)
chronos query finished -J '.*<partial_job_name>.*' --begin=-48h -o cluster -o job -L 1
# Output: "<cluster>    <job display name>"

# Step 2: Get the latest job instance ID
chronos query finished -J '.*<partial_job_name>.*' --begin=-48h | tail -1

# Step 3: Use the cluster from step 1 for info and log commands
chronos info --cluster <cluster> <job_id>
chronos log --cluster <cluster> <job_id> -p
```

**Workflow for finding a job you've never queried before:**
1. `chronos query finished -J '.*<partial_name>.*' --begin=-48h -o cluster -o job -L 1` — get cluster and confirm job name
2. `chronos query finished -J '.*<partial_name>.*' --begin=-48h | tail -1` — get the latest ID
3. Use the cluster for `chronos info` / `chronos log`
4. If no results in 48h, extend to `--begin=-7d` (job may run infrequently)

### 5. Job Actions

```bash
chronos info --cluster <cluster> <job_id>                  # Job details (cluster flag required)
chronos restart -c <cluster> <job_id>                      # Restart job
chronos restart -c <cluster> --dont-restart-parent <job_id> # Restart only this job
chronos abandon -c <cluster> --message 'reason' <job_id>   # Stop without retry
```

## Scuba Tables

| Table | Use Case | Key Columns |
|-------|----------|-------------|
| `chronos_pending_job_instances` | Job pending? | `time`, `job_instance_id` |
| `chronos_running_job_instances` | Job running? | `time`, `job_instance_id` |
| `chronos_job_instance_states` | Completion, exit codes, DAG | `job_instance_id` (string), `parent_job_instance_id` (int), `exit_code`, `jobname`, `finished_running_at` |

**Query tips:**
- `FROM_UNIXTIME()` for readable timestamps
- `job_instance_id` needs quotes, `parent_job_instance_id` doesn't
- Default range: `time >= now() - 3*24*60*60`

## CLI: DAG Traversal

Instead of Scuba queries, use `chronos job find-failure-root-cause`:
```bash
chronos job find-failure-root-cause <job_instance_id>
```
Shows full DAG with failed jobs highlighted.

## Error Analysis

**Patterns to search in logs:**
- `VersionDateCoverageException` - Data coverage issue
- `processedRows=0` - No data written
- `All subtasks are dummy` - No work performed
- `Permission denied` - ACL issue
- `OutOfMemory` / `OOM` - Memory exhaustion
- `Query failed` / `SQL Error` - Presto issue

**Hidden failures (exit 0):** Check for `[ERROR]` entries, Python exceptions, or `processedRows=0`.

**Transient (retry):** Network timeouts, temporary unavailability.
**Permanent (investigate):** Schema mismatch, permissions, invalid SQL.

**Tip:** Scan logs for job IDs (`x\d{17,20}`) referencing other failed jobs.

## CLI Tips

- Secure cluster / timeout → Add `--legacy` flag
- Want retry → `chronos fail` (not `kill`)
- Stop permanently → `chronos abandon`
- Bulk ops → `--batch-size 100 --threads 20`
