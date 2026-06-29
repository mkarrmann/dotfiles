# Job Stop & Deletion

> 45 posts in TW Group FAQ | Primary Scuba: `tupperware_api_service_cpp` | Primary CLI: `tw delete`

## Debugging Playbook

**CLI**: `tw delete --stop <job_handle>` to stop and delete in one operation. If it fails, pick the matching section:

| Error | Go to |
|-------|-------|
| "Mutation rejected ... inactive state" | [Inactive State Errors](#inactive-state-errors) |
| RECV_EOF / RECV_TIMEOUT | [Timeout Errors](#timeout-errors) |
| Stuck in "disabling smc tier" | [SMC Deletion Blocks](#smc-deletion-blocks) |
| Job keeps respawning after deletion | [Respawning Jobs](#respawning-jobs) |
| Stuck in STOPPING state | [Stuck Stopping](#stuck-stopping) |

---

### Inactive State Errors
**CLI**: `tw task-control show-status <job_handle>` to check for pending operations.
-> If there are pending task ops: run `tw task-control apply-task-ops --all-ops <job_handle>` to clear them.
-> If task-control shows no pending ops but JCP has pending ops: this is a known inconsistency. Try `tw changes commit <job_handle>` to commit any pending changes.
**CLI**: `tw changes commit <job_handle>` then retry `tw delete --force <job_handle>`.
-> If still stuck after clearing ops: the job may have VIP unassignment issues (`isVipUnassigned: 1, isVipMapUnassigned: 0`) or stale scheduler state. Escalate to TW platform oncall for manual deletion via internal tools (twbraindoctor).
**Example posts**: [1](https://fb.workplace.com/groups/1473492212957333/permalink/3577616232544910), [2](https://fb.workplace.com/groups/1473492212957333/permalink/3628746107431922), [3](https://fb.workplace.com/groups/1473492212957333/permalink/3606558952983971)

### Timeout Errors
**CLI**: `tw delete <job_handle>` without `--stop` if the job is already stopped (avoids expensive stop API call).
-> RECV_EOF timeouts can be caused by bad API releases causing thread hangs. Try different variations:
  - `tw job delete <job_handle>` (alternative syntax)
  - `tw delete --force --stop --verbose --sync --max-wait-seconds 3600 <job_handle>` (extended timeout)
-> If the scheduler region is unavailable (e.g., during region power-off): the stop may have taken effect but will only complete when the region comes back online.
**Example posts**: [1](https://fb.workplace.com/groups/1473492212957333/permalink/3600455493594317), [2](https://fb.workplace.com/groups/1473492212957333/permalink/3588890104750856)

### SMC Deletion Blocks
**Scuba**: `smcbridge_v2_errors`
- Columns: `job_handle`, `error_type`, `tier_name`, `action`, `delegated_identity`
- Filter: `job_handle = <your_handle>`
-> If `ERR_UNAUTHORIZED` on `modifyEndpoints`: the service identity needs this permission on the SMC tier ACL. The job may have been transferred between teams without transferring all permissions.
-> If `SMC_TIER_RESOLVE_ERROR` (tier already deleted): this is a JCP v2 bug fixed in D70350235.
**CLI**: After fixing ACL: `tw delete --no-interactive --delete-smc-tiers --force <job_handle>`
**Example posts**: [1](https://fb.workplace.com/groups/1473492212957333/permalink/3494802420826292), [2](https://fb.workplace.com/groups/1473492212957333/permalink/3483535358619665)

### Respawning Jobs
-> Check if the job is managed by IPNext/ICSP or a global virtual job. `tw stop` will warn: "the stopped/deleted state will not persist past the next global virtual job update." To permanently delete, remove the job definition from the IPNext/ICSP configuration.
-> For Cogwheel/SyX managed jobs, use the JCP `remove` API instead of `tw delete`.
**Example posts**: [1](https://fb.workplace.com/groups/1473492212957333/permalink/3494716090834925)

### Stuck Stopping
**CLI**: `tw changes commit <job_handle>` to commit pending changes blocking the transition.
-> If the scheduler reports "Cannot delete a job that has available changes", commit the changes first.
-> If tasks are stuck with "Agent doesn't remember this task", the agent lost track after a host issue. Preempt the stuck tasks, then retry deletion.
**Scuba**: `tupperware_job_request_history`
- Columns: `job_handle`, `request_type`, `status`, `timestamp`
- Filter: `job_handle = <your_handle>`, `request_type = STOP or DELETE`
**Example posts**: [1](https://fb.workplace.com/groups/1473492212957333/permalink/3684578478515351), [2](https://fb.workplace.com/groups/1473492212957333/permalink/3570637039909496)

## Best Practices & How-To

### How to safely stop and delete a job
The safe order is: `tw stop <handle>` first (reversible via `tw restart`), wait for completion, then `tw delete <handle>`. Note that `tw delete` on a running job will stop and delete it automatically without requiring `--force` or `--stop` -- this is intentional behavior. For prod-tagged jobs, the CLI asks for confirmation.
**Example posts**: [1](https://fb.workplace.com/groups/1473492212957333/permalink/3626727487633784)

### How to handle sticky allocations during stop
If the job uses `keep_allocations_for_stopped_tasks`, stopping the job preserves the host allocation. Use `tw stop --remove-sticky-allocations <handle>` to release allocations during stop. For delete, `tw delete --force` automatically includes `--remove-sticky-allocations`.
**Example posts**: [1](https://fb.workplace.com/groups/1473492212957333/permalink/3516982158608318)

### How to preserve host assignment across stop/restart
Set `keep_allocations_for_stopped_tasks=True` in the job spec, then `tw stop` and later `tw restart`. The task returns to the same host. You can also use `tw allocation swap <source_task> <destination_task>` to move tasks between hosts.
**Example posts**: [1](https://fb.workplace.com/groups/1473492212957333/permalink/3607320179574515)

## Common Questions

### Q: Does tw delete have a step size for stopping tasks?
**A:** No. `tw delete` stops and removes all tasks at once. There is no gradual step-size-based teardown for deletion.

### Q: Can an erroneous tw stop or tw delete be cancelled?
**A:** `tw stop` is reversible via `tw restart`, which also cancels pending stop task ops. `tw delete` is NOT reversible -- once the tombstone flag is set, it cannot be cancelled.

### Q: How long does tw stop keep the container?
**A:** After `tw stop`, the scheduler destroys the container, but the TW Agent keeps the task object in its database for an extra 15-30 minutes in a "destroyed" state. This allows `twac export-task-spec` to work after stop.

### Q: Cannot delete sandbox2 jobs -- permission denied?
**A:** Use `tw sandbox2 remove` instead of `tw job delete` for sandbox2 jobs. Getting "PERMISSION DENIED" for devvm is a known issue; ensure you are on a proper devserver.

### Q: How to delete a job when the ACL was deleted?
**A:** You need someone with admin permissions to either recreate the ACL or use a different identity with access. Contact TW platform oncall if standard approaches fail.

## Reference Tables

### Scuba Tables
| Table | When to Use | Key Columns |
|-------|------------|-------------|
| `tupperware_api_service_cpp` | Debug API errors during deletion | `job_handle`, `method`, `error` |
| `tupperware_job_request_history` | Track stop/delete request history | `job`, `method_name`, `status` |
| `tupperware_jcp_tickers` | Check JCP processing state | `tw_job_handle`, `ticker_type` |
| `smcbridge_v2_errors` | Debug SMC tier deletion blocks | `tier_name`, `error_type`, `delegated_identity` |
| `tw_cli_usage` | Track who ran stop/delete commands | `command`, `user`, `job_handle` |

### CLI Commands
| Command | When to Use |
|---------|------------|
| `tw delete --stop <handle>` | Stop and delete in one operation |
| `tw delete --force <handle>` | Force delete (includes remove sticky allocations) |
| `tw delete --no-interactive --delete-smc-tiers --force <handle>` | Full force delete with SMC cleanup |
| `tw stop <handle>` | Stop a running job (reversible) |
| `tw stop --kill <handle>` | Force stop with SIGKILL |
| `tw stop --remove-sticky-allocations <handle>` | Stop and release host allocations |
| `tw task-control apply-task-ops --all-ops <handle>` | Clear stuck operations blocking deletion |
| `tw changes commit <handle>` | Commit pending changes to unblock delete |
