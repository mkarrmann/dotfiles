# Canary & Deployment Failures

> 292 posts in TW Group FAQ | Primary Scuba: `tupperware_canary_events` | Primary CLI: `tw canary`

## Canary Debugging Guide

### Architecture

The TW canary workflow flows through three services:

1. **TW API** — TWCLI or automation invokes `startCanary` RPC. TW API validates the request, converts specs, and forwards to the canary service.
2. **TW Canary Service** — Manages the canary lifecycle (state machine) and forwards canary container specs to JCP. Source: `fbcode/tupperware/front_end/canary/`.
3. **JCP (Job Control Plane)** — Treats canary as a special case of a TW update. Reconciles the canary container spec onto the job.

Debugging follows this pipeline in order: check what TW API received, then what the canary service did, then what JCP encountered.

### Canary Types

There are two main canary modes. Understanding which mode is in use is critical for debugging:

| Mode | Flag | Behavior | When to Use |
|------|------|----------|-------------|
| **Placement canary** | `--tasks <range>` | Applies canary spec to specific task IDs. Canary is tied to task placements. If a task's allocation changes (preemption, host failure, rebalance), the canary on that task is **reverted**. | Testing on specific tasks/hosts |
| **Container-count canary** | `--container-count <N>` | Applies canary spec to N containers regardless of placement. Not tied to specific task IDs. Canary persists through allocation changes. | Whole-job canary, when you want all (or N) tasks canaried regardless of placement changes |

**`--placements-best-effort` behavior**: When using placement canary with `--placements-best-effort`, the canary service will attempt to canary the specified tasks but uses best-effort placement. If a canaried task gets preempted or its allocation changes, the canary on that task is automatically reverted. This is by-design behavior.

**Common pitfall:** Using `--tasks 0-<N>` with `--placements-best-effort` for whole-job canary. If any task gets preempted or rescheduled during the canary, that task loses its canary spec. For whole-job canary, use `--container-count` instead.

See the canary wiki for details: https://fburl.com/wiki/bouur9u7

### Canary Lifecycle States

Defined in `tupperware/if/Canary.thrift`:

```
PENDING ──► STARTING ──► RUNNING ──► STOPPING ──► STOPPED ──► CLEANED_UP
   │            │            │                        │
   └──► FAILED  └──► FAILED  └── RUNNING_PARTIAL ─────┘
                                     │
                                     └──► STOPPING ──► ...
```

| State | Meaning |
|-------|---------|
| `PENDING` | Start request created; waiting for JCP reconciliation |
| `STARTING` | JCP reconciled; scheduler processing the canary update |
| `RUNNING` | All canary tasks running with canary spec |
| `RUNNING_PARTIAL` | Some canary tasks have been reverted (e.g. job resize) |
| `STOPPING` | Stop/revert update being applied |
| `STOPPED` | All canary tasks reverted to original spec |
| `FAILED` | Error occurred (JCP error, timeout, placement failure, task crash) |
| `CLEANED_UP` | Terminal state — artifacts removed from JCP DSS and scheduler |

### Scuba CLI Syntax Reference

**Critical**: Use `meta scuba.dataset query` for querying Scuba datasets. Follow these rules exactly:

- **Dataset**: `-d <table_name>` specifies the table
- **View**: `--view=samples` for raw events (or `--view=aggregates` for aggregated data)
- **Columns**: `-c col1,col2,col3` comma-separated list of columns to select
- **Time range**: `--hours=N` for last N hours (e.g., `--hours=24` for last 24 hours)
- **Filters**: `-w '[{"column":"col","op":"eq","values":["value"]}]'` JSON array of filter conditions
  - Supported ops: `eq` (equals), `ne` (not equals), `gt`, `lt`, `gte`, `lte`, `in`, `contains`
  - For numeric values, use numbers not strings: `"values":[1]` not `"values":["1"]`
- **Regex/SQL filters**: `--filter-sql="col RLIKE 'pattern'"` for complex conditions not supported by `-w`
- **Description**: `-r "Query description"` adds a description to the query
- **Schema**: Run `meta scuba.dataset describe -d <table>` before querying an unfamiliar table

### 3-Step Debugging Workflow

#### Step 1: Check TW API (`tupperware_api_service_cpp`)

Find the `startCanary` request in the TW API dataset to confirm the request was received and whether it succeeded or failed at the API layer.

```bash
meta scuba.dataset query -d tupperware_api_service_cpp --view=samples \
  -c time,method,client_id,error,error_message,response_size_bytes \
  -w '[{"column":"job_handle","op":"eq","values":["<JOB_HANDLE>"]}]' \
  --filter-sql="method RLIKE 'startCanary|StartCanary'" --hours=24 \
  -r "TW API startCanary requests"
```

**Scuba UI:** https://fburl.com/scuba/tupperware_api_service_cpp/xp40tjju

**What to look for:**
- `error = 1` — The request failed at TW API. Check `error_message` for details (e.g., validation failures, permission errors).
- No rows — The request never reached TW API. Check client-side logs or TWCLI output.
- `error = 0` — Request accepted. Proceed to Step 2.

#### Step 2: Check Canary Events (`tupperware_canary_events`)

Query the canary events dataset to trace the canary lifecycle:

```bash
meta scuba.dataset query -d tupperware_canary_events --view=samples \
  -c time,canary_id,event,state,details,exception,start_request_id,stop_request_id,user \
  -w '[{"column":"job","op":"eq","values":["<JOB_HANDLE>"]}]' --hours=24 \
  -r "Canary events"
```

**Scuba UI:** https://fburl.com/scuba/tupperware_canary_events/m16zcaqz

**Key columns:**
| Column | Purpose |
|--------|---------|
| `event` | Event type: `created`, `state_transition`, `tasks_canaried`, `tasks_uncanaried`, `stop_triggered`, `start_canary_failed`, `action_error`, etc. |
| `state` | Current canary state after this event (e.g., `PENDING`, `RUNNING`, `FAILED`) |
| `details` | Free-form detail string — contains enriched information about what happened |
| `exception` | Exception message if something went wrong |
| `canary_id` | User-provided canary ID |
| `start_request_id` | Links back to the original start request |
| `stop_request_id` | Present if a stop was requested |

**What to look for:**
- **No `created` event** — Canary service never received the request. Check TW API errors in Step 1.
- **Stuck in `PENDING`** — JCP hasn't reconciled. Proceed to Step 3.
- **`FAILED` state** — Check `details` and `exception` columns for the failure reason.
- **`start_canary_failed`** — Write to JCP DSS failed. Check JCP errors in Step 3.
- **`stop_triggered` unexpectedly** — Check `details` for reason (expiration, explicit stop, or failed task revert).
- **`tasks_lost`** — Tasks disappeared, possibly due to job resize during canary.
- **`tasks_uncanaried`** — Tasks had their canary spec reverted. For placement canary with `--placements-best-effort`, this happens when a task's allocation changes.
- **`allocations_changed`** — Task host allocation changed during canary. This is the trigger that causes placement canaries to revert affected tasks.
- **`RUNNING_PARTIAL` state** — Some tasks have been uncanaried while others still have the canary spec. Common with placement canary on large jobs with allocation churn.

#### Step 3: Check JCP (`tupperware_jcp_tickers`)

If the canary is stuck or failed due to JCP issues:

```bash
meta scuba.dataset query -d tupperware_jcp_tickers --view=samples \
  -c time,fed_handle,ticker_type,exception_msg,exception_type,shard_id \
  -w '[{"column":"fed_handle","op":"eq","values":["<JOB_HANDLE>"]},{"column":"exception","op":"eq","values":[1]}]' \
  --hours=24 -r "JCP exceptions"
```

For unreconciled state:

```bash
meta scuba.dataset query -d tupperware_jcp_tickers --view=samples \
  -c time,fed_handle,ticker_type,exception_msg,shard_id \
  -w '[{"column":"fed_handle","op":"eq","values":["<JOB_HANDLE>"]},{"column":"job_monitoring_long_running_unreconciled_ticker","op":"eq","values":[1]}]' \
  --hours=24 -r "JCP unreconciled tickers"
```

**Scuba UI:** https://fburl.com/scuba/tupperware_jcp_tickers/1scoufwf

**What to look for:**
- `exception_msg` — The specific error JCP encountered while reconciling the canary update.
- `job_monitoring_long_running_unreconciled_ticker = 1` — The job has been unreconciled for >5 minutes, indicating a stuck update.
- Common JCP errors: scheduler placement failures, DSS write conflicts, shard leadership issues.

### Common Failure Patterns

| Symptom | Where to Check | Likely Cause |
|---------|---------------|--------------|
| Canary never starts | Step 1 (TW API) | Request validation failed, permission error, or bad spec |
| Canary stuck in PENDING | Step 2 + Step 3 | JCP hasn't reconciled — check JCP exceptions |
| Canary goes to FAILED immediately | Step 2 (details/exception) | JCP write failure, timeout, or placement failure |
| Canary starts but tasks crash | Step 2 (`tasks_uncanaried`, `tasks_lost`) | Bad canary spec, container image issue, or resource limits |
| Canary missed a task / task not canaried | Step 2 (`allocations_changed`, `tasks_uncanaried`) | **Placement canary + allocation change.** Use `--container-count` instead of `--tasks` for whole-job canary |
| Canary in RUNNING_PARTIAL | Step 2 (`tasks_uncanaried`, `allocations_changed`) | Some tasks lost canary due to allocation changes. Expected with placement canary on large jobs with churn |
| "desired canary tasks N is higher than jobSize M" | Step 2 (details) + Step 3 (`USER_ERROR`) | **jobSize mismatch** — see below |
| `ERR_SPEC_NOT_SUPPORTED` / partial push conflict | Step 2 (details) + Step 3 | **Canary + partial push conflict** — see below |
| Spec validation error (e.g., port/smc_bridges) | Step 1 or Step 2 (details) | **Spec validation** — see below |
| Stale canary blocking new pushes | `tw canary list <handle>` | **Stale canary** — see below |
| Canary stopped unexpectedly | Step 2 (`stop_triggered` event details) | Expiration timeout, explicit stop by user/automation, or failed task revert |
| Canary stuck in RUNNING | Step 2 (no stop events) | Stop request not issued, or canary service not processing it |
| Canary stuck in STOPPING | Step 2 + Step 3 | JCP can't revert the update — check JCP exceptions |
| DSS revision conflicts in JCP | Step 3 (`exception_msg`) | High write contention — concurrent updates conflict with canary spec writes |

### Canary + Partial Push Conflict (`ERR_SPEC_NOT_SUPPORTED`)

A partial push reserves a percentage of tasks (e.g., 1% reserves at least 1 task). If the canary requests all remaining tasks, the total exceeds what's available and the canary fails with `ERR_SPEC_NOT_SUPPORTED`.

Example error:
> `ERR_SPEC_NOT_SUPPORTED: Update cannot proceed with job size 8. Canary count 8. Partial push percentages: 1% and required tasks count 1. Required minimum job size 9.`

**Debugging steps:**
1. Check Step 2 canary events for `FAILED` state with `ERR_SPEC_NOT_SUPPORTED` in `details`
2. Check for active partial pushes: `tw resolve <handle>` — tasks in `STAGING` state indicate an ongoing partial push
3. Check pending task ops: `tw task-control show-task-ops <handle>`

**Fix options:**
1. Reduce canary count to leave room for the partial push (e.g., `--task-count cco:7` instead of `cco:8`)
2. Complete the partial push first, then canary all tasks

### Spec Validation Errors

Common examples:
- `smc_bridges.port_name should be defined in ports` — a port referenced in `smc_bridges` is not defined in the `ports` section

**Debugging steps:**
1. Check Step 1 for `error = 1` with `error_message` describing the validation failure
2. If Step 1 shows success, check Step 2 for `FAILED` state with validation error in `details`
3. Inspect the spec: `tw print <handle>` to check ports, smc_bridges, and other sections
4. Validate before deploying: `tw validate <spec_file>`

**Fix:** Correct the spec (e.g., add missing port definitions) and redeploy with `tw update <handle>`.

### Stale Canaries Blocking New Pushes

Old canary records with expired packages can block new TW operations.

**Debugging steps:**
1. List active canaries: `tw canary list <handle>`
2. Look for canaries from days or weeks ago that were never properly stopped
3. Check if the canary's package has expired

**Fix:** Stop the stale canary: `tw canary stop --canary-id <id> <handle>`

### jobSize Mismatch: "desired canary tasks higher than jobSize"

The canary service sees a smaller `jobSize` than expected. The `jobSize` used for canary validation is derived from the scheduler spec, but the source of truth depends on the job's configuration.

**Debugging steps:**
1. Confirm the error in Step 2 `details` or Step 3 `exception_msg` (look for `exception_type = USER_ERROR`)
2. Check the job's current state: `tw job2 status <handle>` — compare `desiredJobSpec.jobSize` vs `updatedJobSpec.jobSize`
3. If they differ, the job likely has a stuck or incomplete update. Look for `job_monitoring_long_running_unreconciled_ticker = 1` in JCP tickers
4. Check the codebase — the spec conversion path in `TwJobSpecConverter.cpp` varies by reservation type
5. If a stuck update is the cause, **manually ack pending task ops** to complete it

### When Scuba Isn't Enough

The 3-step Scuba workflow identifies **symptoms**. For root cause analysis:
- **Check the codebase** — Understand the code paths that produce the values you see in Scuba. Search the canary service source at `fbcode/tupperware/front_end/canary/` and the spec converter at `fbcode/tupperware/front_end/federation/twjob/TwJobSpecConverter.cpp`.
- **Check scheduler state** — Use `tw job2 status <handle>` to compare desired vs running spec.
- **Check for stuck updates** — JCP tickers will show `job_monitoring_long_running_unreconciled_ticker = 1`, but you need scheduler state to understand why.
- **Check scheduler action explanations** — Query `tupperware_scheduler_actions` filtered by the job handle to see exactly what the scheduler decided and why (action type, reason, deployment health). See `datasets/tupperware_scheduler_actions.md`.

---

## Debugging Playbook

### Identify the canary or deployment issue type
**CLI**: `tw canary list <job_handle>` to check active canaries
**CLI**: `tw task-control show-task-ops <job_handle>` to check pending task operations

| Symptom | Go to |
|---------|-------|
| Canary lifecycle issues (stuck, failed, partial) | [3-Step Debugging Workflow](#3-step-debugging-workflow) above |
| `UntrustedBuildEnvironment` / `UnreviewedCode` | [Identity Policy Errors](#identity-policy-errors) |
| Stale canaries blocking new pushes | [Stale Canaries Blocking New Pushes](#stale-canaries-blocking-new-pushes) above |
| Region-specific crashes during deployment | [Region-Specific Crashes](#region-specific-crashes) |
| Accidentally kicked off a full job update | [Revert Accidental Update](#revert-accidental-update) |

---

### Identity Policy Errors
- The error indicates the build was not performed in a trusted environment and the code has not been reviewed/landed
- The `--build` flag in the canary command creates an untrusted build
**Action**: Check the identity policy configuration for the service. Verify the build was performed in a trusted CI environment. Refer to the CISF TDA Compatible Job Deployment FAQ wiki for details.
**Example posts**: [1](https://fb.workplace.com/groups/1473492212957333/permalink/3494107110895823)

### Region-Specific Crashes
**Scuba**: `coredumper`
- Columns: `job_handle`, `signal`, `stack_trace`
- Filter: `job_handle = <your_handle>`, region filter
- Check crash stack for hardware-specific issues
**CLI**: `tw preempt <task_handle>` to move affected tasks to different hosts
- Region-specific crashes can be caused by hardware differences (e.g., different CPU models), package mismatches, or host-specific issues
- If issue persists only in one region, check for region-specific hardware or configuration differences
**Example posts**: [1](https://fb.workplace.com/groups/1473492212957333/permalink/2827482460791002)

### Revert Accidental Update
> This playbook is for answering user questions about accidental updates, not for emergency mitigation. For SEV-level rollback, escalate to the TW oncall.

**CLI**: `tw job pause-update <job_handle>` to pause the current update if still in progress
**Scuba**: `tupperware_api_service_cpp`
- Columns: `job_handle`, `error_type`
- Filter: `job_handle = <your_handle>` — find the previous NUJ ID
**CLI**: `tw update -i <old_job_spec> --force` to update forward with the old spec
- There is no direct way to cancel a TW update; you must update forward
- Use `tw canary` instead of unbounded task updates for future changes
**Example posts**: [1](https://fb.workplace.com/groups/1473492212957333/permalink/3187384488234755)

### Verify Resolution
**CLI**: `tw canary list <job_handle>` to confirm no stale canaries remain
**CLI**: `tw task-control show-status <job_handle>` to confirm tasks are healthy

## Best Practices & How-To

### How to canary a local diff change to a TW task
Follow these steps: (1) Build CM: `buck run <target>=publish`, (2) Get latest CM: `LATEST_CM=$(cm latest-contbuild <cm_name>)`, (3) Preserve CM: `cm preserve $LATEST_CM`, (4) Build canary request: `cm with-versions -c "$LATEST_CM" -- tw canary build-request ./<spec.tw> --tasks <id> --duration=24h --all-fields <job_handle>`, (5) Start canary: `cm with-versions -c "$LATEST_CM" -- tw canary start ./<spec.tw> <job_handle> --tasks <id> --duration=24h`, (6) List canaries: `tw canary list <job_handle>`, (7) Stop canary: `tw canary stop --canary-id <id> <job_handle>`.
**Example posts**: [1](https://fb.workplace.com/groups/1473492212957333/permalink/3430669660572902)

### How to run concurrent canaries on the same job
Use task count-based canaries instead of placement/task ID-based canaries. TW ensures count-based canaries are not on the same task. For small jobs needing explicit task IDs, set the canary tasks in the canary node configuration to avoid conflicts.
**Example posts**: [1](https://fb.workplace.com/groups/1473492212957333/permalink/3489767897996411)

### How to validate specs before deployment
Always validate specs with `tw validate` before pushing. Ensure all ports in `smc_bridges` are defined in the `ports` section. Set appropriate `killTimeout` in job spec for graceful canary shutdown. Verify build environment is trusted before deploying identity-policy-enabled services.

### How to prevent stale canaries
Clear stale canaries promptly to avoid blocking future pushes. Always use `tw canary` for testing changes instead of unbounded task updates.

## Common Questions

### Q: When canarying resource_limit changes (CPU), the changes do not take effect. Why?
**A:** Resource limit changes for CPU may not take effect during canary. This has been reported by users but the exact behavior may vary depending on the resource type and canary mechanism. A full job update is generally recommended for resource limit changes.
**Example posts**: [1](https://fb.workplace.com/groups/1473492212957333/permalink/2943723555934184)

## Reference Tables

### Scuba Tables
| Table | When to Use | Key Columns |
|-------|------------|-------------|
| `tupperware_canary_events` | Track canary lifecycle and failures | `job`, `canary_id`, `event`, `state`, `details`, `exception` |
| `tupperware_api_service_cpp` | Debug API-level errors during deployment | `job_handle`, `method`, `error`, `error_message` |
| `tupperware_jcp_tickers` | Debug JCP reconciliation issues affecting canaries | `fed_handle`, `ticker_type`, `exception_msg` |
| `conveyor_canary_logs` | Debug Conveyor-initiated canary issues | `service_id`, `push_id` |
| `tupperware_task_control_operations` | Check task update progress | `job`, `operationType` |
| `coredumper` | Analyze deployment crash dumps | `job_handle`, `signal`, `stack_trace` |
| `tupperware_scheduler_actions` | Understand scheduler decisions during canary/deployment updates | `job`, `action_type`, `reason_type`, `context` |

### CLI Commands
| Command | When to Use |
|---------|------------|
| `tw canary start ./<spec.tw> <job_handle> --tasks <id> --duration=24h` | Start a canary on specific tasks |
| `tw canary list <job_handle>` | List active canaries for a job |
| `tw canary stop --canary-id <id> <job_handle>` | Stop a specific canary |
| `tw update -i <old_spec> --force` | Revert to a previous spec |
| `tw job pause-update <job_handle>` | Pause an in-progress update |
| `tw print <job_handle>` | Inspect current job spec including ports and bridges |
| `tw validate <spec_file>` | Validate spec before deployment |

### Additional Resources

- **Canary Wiki:** https://fburl.com/wiki/bouur9u7
- **Canary Service Source:** `fbcode/tupperware/front_end/canary/`
- **Canary Thrift Definitions:** `fbcode/tupperware/if/Canary.thrift`
- **Spec Converter Source:** `fbcode/tupperware/front_end/federation/twjob/TwJobSpecConverter.cpp`
