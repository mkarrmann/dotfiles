# Networking & SMC

> 173 posts in TW Group FAQ | Primary Scuba: `smcbridge_v2_errors` | Primary CLI: `smc`

## Debugging Playbook

**CLI**: `tw print <job_handle>` to inspect `smc_bridges`, `ports`, and `network_policy` configuration. Pick the matching section:

| Symptom | Go to |
|---------|-------|
| Stuck in "Enabling/Disabling SMC" | [SMC Stuck States](#smc-stuck-states) |
| "undefined port in smc_bridges" | [Undefined Port](#undefined-port) |
| Tasks disabled in SMC tier | [Tasks Disabled in SMC](#tasks-disabled-in-smc) |
| "no hosts available in SMC" | [No Hosts in SMC](#no-hosts-in-smc) |
| SMC tier deletion failures | [SMC Tier Deletion](#smc-tier-deletion) |

---

### SMC Stuck States
**Scuba**: `smcbridge_v2_errors`
- Columns: `error_code`, `tier_name`, `error_msg`, `exception_name`, `job_handle_list`
- Filter: `tier_name = <your_tier>` (most reliable) or `job_handle_list CONTAINS <your_handle>` (note: `job_handle` is **not** a logged column — do not use `job_handle = ...`; and `job_handle_list` may miss some tier-level errors, so `tier_name` is preferred)
-> If `ERR_UNAUTHORIZED` on `modifyEndpoints` or `modifyHierarchy`: the job's service identity is missing from the parent SMC tier ACL. Add it to all required actions. See [SMC Security](https://www.internalfb.com/wiki/Smc/User_Guide/SMC_Security/#tupperware-static-shard) for which service identities and actions are needed for various operations.
-> If `ERR_VERSION_CONFLICT`: multiple jobs or scheduler shards contend on the same tier. Set up dedicated tiers per job with parent tier propagation.
**Scuba**: `smcbridge_v2_transactions` -- check what transactions the scheduler attempted (add/delete/service-data-change) and correlate with errors above.
- Columns: `tier_name`, `action`, `category`, `name`, `hostname`
- Filter: `tier_name = <your_tier>` or `name LIKE '%your/job/handle%'` (for category=service)
**CLI**: After fixing ACL, run `tw update <spec> <handle>` to retry.
**Example posts**: [1](https://fb.workplace.com/groups/1473492212957333/permalink/3500140403625827), [2](https://fb.workplace.com/groups/1473492212957333/permalink/3543005856005948)

### Undefined Port
**CLI**: `tw print --user-job-spec <job_handle>` to see which ports are bridged and which are defined.
-> The `smc_bridges` section references a port name not listed in the `ports` section. Either define the missing port or remove the bridge entry.
-> Note: `smc_bridges` is a job-level field and cannot be canaried. For partial pushes, port removal in smc_bridges only takes effect at the 100% phase.
**Example posts**: [1](https://fb.workplace.com/groups/1473492212957333/permalink/3479790015484223), [2](https://fb.workplace.com/groups/1473492212957333/permalink/3545585179081349)

### Tasks Disabled in SMC
**Scuba**: `smc_changelogger`
- Columns: `smc_tier`, `method`, `host_name`, `time`
- Filter: `smc_tier = <your_tier>`
-> SMCBridge enable/disable reflects task health. If a task goes unhealthy temporarily, SMC disables it and re-enables when healthy. Cross-reference with health check results.
**Scuba**: `tupperware_health_check_results`
- Columns: `job_handle`, `task_id`, `result`, `timestamp`
-> If health checks are intermittently failing, the SMC churn is expected. Fix the root cause (health check config, port binding, startup time).
**Example posts**: [1](https://fb.workplace.com/groups/1473492212957333/permalink/1400093054675289)

### No Hosts in SMC
**CLI**: `smcc list-hosts <tier_name>` to check tier membership.
-> If the tier is empty: check if the underlying TW job is running. A stopped job means no tasks in the tier.
-> If the tier has hosts but routing fails: check `scheduling.smc_recursive = True` if hosts are inherited from parent tiers. Also verify the service identity has `tupperware_machine_admin_dynamic` permission on the machine tier ACL.
**Example posts**: [1](https://fb.workplace.com/groups/1473492212957333/permalink/1921984361953151), [2](https://fb.workplace.com/groups/1473492212957333/permalink/3493664000940134)

### SMC Tier Deletion
**CLI**: `tw delete --no-interactive --delete-smc-tiers --force <job_handle>`
-> If deletion fails with `SMC_TIER_RESOLVE_ERROR` (tier already deleted): this is a known JCP v2 bug, fixed in D70350235.
-> If stuck in "disabling smc tier": the service identity needs `modifyEndpoints` on the tier ACL. Check `smcbridge_v2_errors` for the exact permission failure.
**Example posts**: [1](https://fb.workplace.com/groups/1473492212957333/permalink/3483535358619665), [2](https://fb.workplace.com/groups/1473492212957333/permalink/3494802420826292)

## Best Practices & How-To

### How to update the SMC parent tier
SmcBridge only adds parents by default and does not remove existing ones. Inheriting from multiple parents is invalid, so SMC rejects the change. Manual steps: (1) `smcc rm-parent <tier> <old_parent>`, (2) `smcc add-parent <tier> <new_parent>`, (3) `smcc update-smc-dependent <tier>`. A property `tw_smcbridge_delete_parents` exists but is not recommended due to ordering issues.
**Example posts**: [1](https://fb.workplace.com/groups/1473492212957333/permalink/3505155479790986)

### How to handle SMC Bridge dropping custom properties
When TW deallocates and reallocates a task (even to the same host), SMC Bridge treats it as delete+add, dropping custom properties. Your application must handle re-setting custom properties on task re-creation. There are no guarantees that a task leaving a host will not reallocate back to the same host with a new container ID.
**Example posts**: [1](https://fb.workplace.com/groups/1473492212957333/permalink/3440504622746096)

### How to avoid port conflicts between containers
With IP-per-Task (IPT) enabled, each container has its own IP, so port conflicts are impossible. Without IPT, `port=AUTO` gives unique ports most of the time but is not 100% guaranteed. Migrate to the IPT model, which is the future default.
**Example posts**: [1](https://fb.workplace.com/groups/1473492212957333/permalink/3294750050831531)

### How to set sysctl values (e.g., net.core.somaxconn) in a TW container
Enable network namespaces (NetNS) and set sysctl values as pre-run steps in the jobspec. Ensure netns is enabled via `network_policy.ip_policy` (deprecated spec fields may not enable it). Not all sysctl values are supported yet; support can be added on request.
**Example posts**: [1](https://fb.workplace.com/groups/1473492212957333/permalink/3511396872500180)

## Common Questions

### Q: Does TW remove tasks from SMC during crash loops?
**A:** No. TW intentionally keeps crashing tasks in SMC to avoid churning the tier. The application/routing layer is expected to detect failures and failover.

### Q: How to get the SMC tiers of the current job from within a task?
**A:** The `SMC_TIERS` environment variable contains all bridged tier names. However, using it for anything other than logging is not recommended. Use Configerator or gflags for server-side configuration instead.

### Q: What is the recommended way to get real-time active task count?
**A:** Use ODS `tw.job_size` metric or the TW Read API `getJob`. SMC tier membership may have stale/disabled entries. The TW API is the most accurate source.

### Q: Can smc_bridges fields be canaried?
**A:** No. `smc_bridges` is a job-level field and cannot be canaried per-task. Use `tw update` on the whole job for job-level changes.

## Reference Tables

### Scuba Tables
| Table | When to Use | Key Columns |
|-------|------------|-------------|
| `smcbridge_v2_errors` | Primary table for SMC sync errors | `tier_name`, `error_code`, `exception_name`, `job_handle_list` (note: `job_handle` is not a logged column — use `tier_name` or `job_handle_list CONTAINS`) |
| `smcbridge_v2_transactions` | Track SMC tier/service transactions (add/delete/data-change) | `tier_name`, `action`, `category`, `name`, `hostname` |
| `smc_changelogger` | Track SMC tier state changes (enable/disable) | `smc_tier`, `method`, `host_name` |
| `service_router` | Debug SR routing issues | `tier_name`, `host`, `routing_state` |
| `hipster_aclchecker_checks` | Debug ACL permission denials on SMC tiers | `deciding_resource_name`, `deciding_accessor`, `deciding_action` |
| `tupperware_task_events` | Correlate task events with SMC changes | `job`, `task`, `event_name` |

### CLI Commands
| Command | When to Use |
|---------|------------|
| `smcc props <tier_name>` | Check SMC tier properties and existence |
| `smcc list-hosts <tier_name>` | List hosts/tasks in an SMC tier |
| `smcc add-parent <tier> <parent>` | Add a parent tier |
| `smcc rm-parent <tier> <parent>` | Remove a parent tier |
| `tw print --user-job-spec <handle>` | Inspect smc_bridges and ports config |
| `tw resolve <handle>` | Resolve task-to-host/IP mapping |
| `tw update <spec> <handle>` | Retry after fixing SMC ACL issues |
| `tw delete --delete-smc-tiers --force <handle>` | Force delete with SMC tier cleanup |
