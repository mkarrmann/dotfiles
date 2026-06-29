# Job Updates & Stuck Updates

> 145 posts in TW Group FAQ | Primary Scuba: `tupperware_job_request_history` | Primary CLI: `tw update`

## Debugging Playbook

**CLI**: `tw task-control show-status <job_handle>` — look for pending task operations, rate limiting messages, or step size budget info.

| Symptom | Go to |
|---------|-------|
| "healthy task update rate limited (max: 0)" | [Host Profile / Concurrent Updates](#host-profile--concurrent-updates) |
| Task controller governing the update | [Task Controller Issues](#task-controller-issues) |
| STATUS_STEP_SIZE_LIMITED | [Step Size Issues](#step-size-issues) |
| Stuck in "Enabling/Disabling SMC" | [SMC Update Blocks](#smc-update-blocks) |
| Package or spec mismatch | [Stale Package / Spec](#stale-package--spec) |
| Scheduler shard switch required | [Scheduler Shard Switch](#scheduler-shard-switch) |
| Need to change job owner | [Job Owner Change](#job-owner-change) |
| Need to change reservation / entitlement_name | [Reservation (entitlement_name) Change](#reservation-entitlement_name-change) |
| Tasks stuck pending after crash loop (clock icon in UI) | [Preemption Throttling for Crash-Looping Tasks](#preemption-throttling-for-crash-looping-tasks) |
| "Tasks are running but the update isn't doing anything" | [Package Fetch Blocking Updates](#package-fetch-blocking-updates) |
| Scheduler actions unclear / unknown action type | [Scheduler Action Explanations](#scheduler-action-explanations) |
| ShardManager controlling task operations | [ShardManager Debugging](#shardmanager-debugging) |

---

### Host Profile / Concurrent Updates
**Scuba**: `tupperware_task_control_operations`
- Columns: `job`, `operationType`, `operationStatus`, `taskID`
- Filter: `job = <your_handle>`, `operationStatus = PENDING`
-> Host profile updates cause re-materialization, generating additional task moves that rate-limit your update to max 0. Concurrent UEs (planned maintenance, capacity rebalancing) also cause this.
**CLI**: `tw task-control apply-task-ops --all-ops <job_handle>` to force-apply all pending ops and unblock.
**Example posts**: [1](https://fb.workplace.com/groups/1473492212957333/permalink/3494001240906410), [2](https://fb.workplace.com/groups/1473492212957333/permalink/3499815863658281)

### Task Controller Issues
**CLI**: `tw job2 print <job_handle> --spec` and look for `scheduler_policies.deployment.task_control.tier_name`.
-> If `tier_name` is set (e.g., `webtaskcontrol.vcn.instagram.c2` for web pushes, `shardmanager.global` for SM), the task controller — not the TW scheduler — governs update pacing. The `tier_name` can be an SMC tier or an SRConfig name.
-> To check what the task controller is doing, query `tupperware_task_control_operations` in Scuba: look at `operationStatus` (ALLOWED = approved, REQUESTED = pending) and `taskControllerStatus` / `taskControllerStatusReason` for rejections.
-> If the task controller is approving ops (all ALLOWED) but the update is still paused, the pause is from JCP itself — check `tupperware_job_request_history` for `pauseUpdateWithRequest` events to see who or what called the pause API (e.g., Conveyor, SRMWWWHooks, a user resize).
**Scuba**: `tupperware_task_control_operations`
- Columns: `taskControllerStatus`, `taskControllerStatusReason`, `operationType`, `operationStatus`, `taskID`
- Filter: `job = <your_handle>`

### Scheduler Action Explanations
**Scuba**: `tupperware_scheduler_actions` — see what the scheduler decided to do and why. See [dataset reference](../datasets/tupperware_scheduler_actions.md) for full schema and queries.
- Columns: `job`, `action_type`, `reason_type`, `tasks`, `step_size`, `context`
- Filter: `job = <your_handle>`
-> Look at `action_type` and `reason_type` to understand scheduler decisions. The `context` column contains rich detail including manually acked operations.

### Step Size Issues
**CLI**: `tw print <job_handle>` to check `task_control_policy` and `step_size` settings.
-> If `step_size_percent` is set with NTID + Dynamic Upsize, switch to `step_size` (absolute value). The `step_size_percent` does not work correctly with RRU-based calculations.
-> If a custom task controller (e.g., ShardManager) is active, the spec step size is ignored; the task controller manages it internally. Unblock with: `tw task-control apply-task-ops <job_handle> --all-ops`.
**Example posts**: [1](https://fb.workplace.com/groups/1473492212957333/permalink/3484373871869147), [2](https://fb.workplace.com/groups/1473492212957333/permalink/3494834094156458)

### SMC Update Blocks
**Scuba**: `smcbridge_v2_errors`
- Columns: `job_handle`, `error_type`, `tier_name`, `action`
- Filter: `job_handle = <your_handle>`
-> If you see `ERR_UNAUTHORIZED` or ACL failures: the job's service identity is missing permissions on the parent SMC tier. Add it to `modifyEndpoints` and `modifyHierarchy` actions.
-> If you see `ERR_VERSION_CONFLICT`: multiple jobs bridging to the same tier causes contention. Set up dedicated tiers per job with parent tier propagation.
**Example posts**: [1](https://fb.workplace.com/groups/1473492212957333/permalink/3494674190839115), [2](https://fb.workplace.com/groups/1473492212957333/permalink/3508665792773288)

### Stale Package / Spec
**CLI**: `tw diff <spec_file> <job_handle>` to compare local spec vs running spec.
-> If `tw update` fails with a package error, use `TW_PUSHED_VERSION=<package_name>:<package_version> tw update <tw_file> <tw_job>` to override the package version.
-> If the job uses PUSHED_VERSION instead of a "prod" tag, you must set `TW_PUSHED_VERSION` explicitly on every update.
**Example posts**: [1](https://fb.workplace.com/groups/1473492212957333/permalink/3517345991905268), [2](https://fb.workplace.com/groups/1473492212957333/permalink/3539001676406366)

### Job Owner Change
There are two distinct fields that could be considered the "owner" in a job spec:
- The **[job handle](https://fburl.com/wiki/ds6q267y)** owner: If the handle changes, Tupperware considers it a completely new job. You must delete the previous job and start the new one.
- The **[ownership field](https://fburl.com/wiki/816kjxmz)**: This can be updated via `tw update` without needing to delete and recreate the job.

### Reservation (entitlement_name) Change
Changing a job's reservation requires a scheduler shard switch because each reservation is managed by a specific TW Scheduler Shard. Before the move can happen, both reservations must be managed by the same shard.

**CLI**: `tw update --dry-run <spec_file> <job_handle>` to preview and detect shard switches.

**Self-mitigation options (try in order):**

1. **Reuse your existing reservation** — Many changes can be done in [Capacity Portal](https://www.internalfb.com/capacity_portal) without touching the job spec: rename the reservation, move it from one L1/L2/L3 to another, and (optionally) transfer the allowance back to write it off.
2. **Virtual Job cross-scheduler feature** — If you are using a [Virtual Job](https://www.internalfb.com/intern/wiki/Infra_Cloud/Service_Hosting/Tupperware/Tupperware_Overview/Tupperware_Glossary/#virtual-job), check whether the ["Support for cross-scheduler reservation change"](https://fb.workplace.com/groups/826961712023862/permalink/1368508397869188/) feature can handle your case.
3. **New job handle** — Start a job with your new reservation using a new job handle, migrate traffic, then delete the old job. This is the safest approach for any manual job-level operation.
4. **Delete-wait-create** — If you can tolerate brief downtime (Tier 3/4/non-critical services, or other regions cover traffic): `tw delete` the job, **wait 60 seconds** for the TW Scheduler Shard to forget the old job, then create the job with the new reservation. The 60-second wait is critical.
5. **Escalation** — If none of the above options work, post in the [Tupperware@FB group](https://fb.workplace.com/groups/tw.cinc) with your full error message. Explain why the above approaches are not suitable. The oncall will evaluate your case.

**Example posts**: [1](https://fb.workplace.com/groups/1473492212957333/permalink/3498439970462537)

### Scheduler Shard Switch
**CLI**: `tw update --dry-run <spec_file> <job_handle>` to preview and detect shard switches.
-> If a shard switch is required (e.g., reservation change), see [Reservation (entitlement_name) Change](#reservation-entitlement_name-change) above for the full self-mitigation ladder. For zero-downtime migration, contact scheduler extensions oncall via `twi scheduler-proxy migration-executor request-migration`.
**Example posts**: [1](https://fb.workplace.com/groups/1473492212957333/permalink/3498439970462537)

### Preemption Throttling for Crash-Looping Tasks
If a job is configured with [`max_instance_restarts`](https://www.internalfb.com/intern/wiki/Infra_Cloud/Service_Hosting/Tupperware/Tupperware_Reference/LanguageReference/RestartPolicy/#max-instance-restarts-op) as a finite number, after X crashes Tupperware moves the task to a new host and it enters Pending state.

When a task is in a tight crash loop, Tupperware [throttles how fast preemptions can happen](https://fb.workplace.com/groups/tw.fyi/permalink/973774806139941/) to prevent adverse impacts on the fleet and scheduler. Throttled tasks stay in Pending and appear with a **clock icon** in the UI.

**How to resolve:**
1. Fix the underlying crash cause first (see [Task Runtime Issues](https://www.internalfb.com/intern/wiki/Infra_Cloud/Service_Hosting/Tupperware/Tupperware_Help_and_Troubleshooting/task-restarts/)).
2. Find the throttled task instance:
   ```bash
   tw changes show $jobHandle
   ```
3. Force-commit the throttled change to allocate the task:
   ```bash
   tw changes commit $throttledTaskInstanceChange
   ```
4. Alternatively, use `tw restart --fast` on the affected task.

**Caveat:** Force-committing allocates the task but does **not** remove the throttling. If the task crash-loops again and needs to be preempted, it will still be throttled. Ensure the root cause is resolved before force-committing.

### Package Fetch Blocking Updates
**Symptom:** "Tasks are running but the update isn't doing anything" — Tupperware may be fetching the fbpkg, which can take a long time.
-> In the output of `tw changes show`, look for the log message: `Package fetch has started, but not completed yet`.

**How to resolve:**
- Commit the changes: `tw changes commit`
- Or preempt the affected task.

**Prevention:** [Set a prefetch timeout](https://fburl.com/wiki/dlqzy457) to avoid indefinite fetch delays.

### ShardManager Debugging
[ShardManager](https://www.internalfb.com/intern/wiki/ShardManager/) is the most commonly used TaskController. If you're having issues with ShardManager task control, first determine if you have problems with shard assignments using `bunnylol sm`.

For detailed debugging and manual operation approval, see the [TaskControl debugging and operations guide](https://www.internalfb.com/intern/wiki/Infra_Cloud/Service_Hosting/Tupperware/Task_Controller/).

> [!WARNING]
> Manually approving operations can cause loss of availability for your service.

---

## Best Practices & How-To

### How to check deployment progress
```bash
# Check overall task states — RUNNING_NOT_HEALTHY, STAGING, CREATING = tasks mid-restart
tw resolve <job_handle>

# Check per-task package versions via Universal Search (see queries/query-task.md)
# tw job print shows the TARGET version; Universal Search shows what each task is ACTUALLY running

# Check Conveyor release pipeline status
conveyor release list --conveyor-id <service_path> --limit 3
```

### How to speed up push times
Conveyor and TW have separate, uncoordinated deployment policy guardrails. Changing `step_size_percent` in the TW spec will not affect `conveyor push plan`. To reduce push time, modify the Conveyor push configuration (phases, bake times) rather than the TW deployment policy.
**Example posts**: [1](https://fb.workplace.com/groups/1473492212957333/permalink/3498332000473334)

### How to safely update package versions
Use `TW_PUSHED_VERSION=<package_name>:<package_version> tw update <tw_file> <tw_job>` to specify the package version. There is no `--package` CLI flag on `tw job update` — the `TW_PUSHED_VERSION` env var is the only way to set the version.

Run `tw update --dry-run` first. If a Conveyor push is in progress, manual `tw update` may clobber or be clobbered by the push.
**Example posts**: [1](https://fb.workplace.com/groups/1473492212957333/permalink/3517345991905268), [2](https://fb.workplace.com/groups/1473492212957333/permalink/3578502545789612)

### How to use max_total_down vs step_size
`step_size` only limits tasks down due to code updates. `max_total_down` includes tasks down due to maintenance, making it safer for services that cannot tolerate simultaneous maintenance and update restarts. Use `max_total_down` for production services with planned maintenance exposure.
**Example posts**: [1](https://fb.workplace.com/groups/1473492212957333/permalink/3513161872323680)

### How to track job update history
Use the `tupperware_job_request_history` Scuba table (see [dataset reference](../datasets/tupperware_job_request_history.md)) or the `getJobHistoricalStates` API. The `tw_cli_usage` Scuba table can track who ran update commands. For scheduler action explanations, query `tupperware_scheduler_actions` (see [dataset reference](../datasets/tupperware_scheduler_actions.md)).
**Example posts**: [1](https://fb.workplace.com/groups/1473492212957333/permalink/3471257876514080), [2](https://fb.workplace.com/groups/1473492212957333/permalink/3506999229606611)

## Common Questions

### Q: Does tw update interact with an in-progress Conveyor push?
**A:** Yes, they can clobber each other. A newer feature blocks pushes if manual updates are detected, but coordination is not guaranteed. Avoid running `tw update` during active Conveyor pushes.

### Q: Can I canary allocation-related fields like SMC tier or entitlement?
**A:** No. Allocation fields are job-level and cannot be translated to task overrides. Use a full `tw update` and revert if needed, or create a separate test job.

### Q: Why does tw update show contHeapEnabledRatio changing when I did not modify it?
**A:** `tw diff` compares local repo state against the running spec. Values controlled by Feature Rollout Configs (FRCs) may differ between the scheduler spec and user spec, showing phantom diffs.

### Q: What happens to stopped tasks during an update?
**A:** Use `tw update --skip-stopped-jobs` to avoid updating stopped jobs. Stopped tasks are not restarted by an update unless explicitly included.

## Reference Tables

### Scuba Tables
| Table | When to Use | Key Columns |
|-------|------------|-------------|
| `tupperware_job_request_history` | Track update requests and history -- see [datasets/tupperware_job_request_history.md](../datasets/tupperware_job_request_history.md) for full schema and queries | `job`, `method_name`, `who`, `reason`, `cluster_id`, `time` |
| `smcbridge_v2_errors` | Debug SMC-related update blocks | `job_handle`, `error_type`, `tier_name` |
| `tupperware_task_control_operations` | Check pending task ops | `job`, `operationType`, `operationStatus` |
| `tw_cli_usage` | Track who ran tw update commands | `command`, `user`, `job_handle` |
| `tupperware_scheduler_actions` | Understand WHY the scheduler made specific update decisions -- see [dataset reference](../datasets/tupperware_scheduler_actions.md) | `job`, `action_type`, `reason_type`, `context` |
| `tupperware_jcp_tickers` | Check JCP processing state | `job_handle`, `ticker_type` |

### CLI Commands
| Command | When to Use |
|---------|------------|
| `tw update <spec> <handle>` | Apply job update from spec |
| `tw update --dry-run <spec> <handle>` | Preview update changes without applying |
| `TW_PUSHED_VERSION=<package_name>:<package_version> tw update <tw_file> <tw_job>` | Update with explicit package version (there is no --package flag) |
| `tw task-control show-status <handle>` | Check pending operations and rate limits |
| `tw task-control show-task-ops <handle>` | See pending TaskControl operations (distinct from `show-status`) |
| `tw task-control apply-task-ops --all-ops <handle>` | Force-apply all stuck task operations |
| `tw task-control apply-task-ops <handle> <opID1> <opID2> ...` | Apply specific task operations by operation ID |
| `tw diff <spec> <handle>` | Compare local spec vs running spec |
| `tw print <handle>` | Inspect current running job spec |
| `tw changes show <handle>` | Show current and pending changes (including throttled preemptions and package fetches) |
| `tw changes commit <handle>` | Commit staged changes |
