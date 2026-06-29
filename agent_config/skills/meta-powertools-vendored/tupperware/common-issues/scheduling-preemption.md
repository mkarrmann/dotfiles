# Scheduling & Preemption

> 124 posts in TW Group FAQ | Primary Scuba: `tupperware_task_events` | Primary CLI: `tw task-control`

## Debugging Playbook

**CLI**: `tw task-control show <job_handle>` to check pending operations. Pick the matching section:

| Symptom | Go to |
|---------|-------|
| Tasks preempted unexpectedly | [Unexpected Preemption](#unexpected-preemption) |
| "Machine is in-use by another scheduler" | [Machine In-Use Error](#machine-in-use-error) |
| "scheduler shard switch" on update | [Scheduler Shard Switch](#scheduler-shard-switch) |
| Tasks stuck in ALLOCATED or FREEING | [Stuck Task States](#stuck-task-states) |
| "lost agent" / tasks going LOST | [Lost Agent](#lost-agent) |
| task lost / unavailability event investigation | [Task Lost / Unavailability Event Investigation](#task-lost--unavailability-event-investigation) |
| Crash-looping task stuck Pending / clock icon | [Crash-Loop Preemption Throttling](#crash-loop-preemption-throttling) |

---

### Unexpected Preemption
**Scuba**: `tupperware_task_events`
- Columns: `job`, `event_name`, `event_detail`, `host`, `time`
- Filter: `event_name = PREEMPT` or `event_name = TASK_STOP_MOVE`
- If event_detail is `capacityRebalancing` --> expected on elastic (Tier 3) capacity, not a bug
- If event_detail is `plannedMaintenance` --> host is going through maintenance cycle
- If event_detail is a scheduler move --> check `tupperware_task_control_operations` for who allowed it
**CLI**: `tw allocation explain <job_handle>` to understand allocation decisions
- Preemption is NOT permanent -- tasks can return to the same machine
- To permanently avoid a host: `tw bad-host <hostname> --reason "..."`
**Example posts**: [1](https://fb.workplace.com/groups/1473492212957333/permalink/3504397166533484), [2](https://fb.workplace.com/groups/1473492212957333/permalink/3553665144940019)

### Machine In-Use Error
**CLI**: `rbcli clear-machinedomain --domain RB_RETRY_<region> --rb <region> --host <hostname>`
- This clears the stale Resource Broker domain assignment
**CLI**: `tw sandbox resolve` to find and stop any running sandbox tasks on the machine
- Also check `tw job list` for the host -- may show stale allocations
- If on a devvm, check for tasks started via `tw solo`/`tw sandbox` that are not scheduler-owned
**Example posts**: [1](https://fb.workplace.com/groups/1473492212957333/permalink/2160911444215403), [2](https://fb.workplace.com/groups/1473492212957333/permalink/3511363065836894)

### Scheduler Shard Switch
**CLI**: `tw update --dry-run <job_handle>` to confirm the shard switch
- This happens when a reservation change moves the job to a different scheduler shard
- Option 1 (with downtime): `tw job stop` + `tw job delete` + `tw job start` with the new spec
- Option 2 (zero downtime): Start a new job on the new reservation, migrate traffic, delete old job
- Option 3 (zero downtime): Contact tupperware_scheduler_scalability oncall to align shards via `twi scheduler-shard-migration request-migration`
- If the reservation change was unintentional, revert the spec change
**Example posts**: [1](https://fb.workplace.com/groups/1473492212957333/permalink/3498439970462537), [2](https://fb.workplace.com/groups/1473492212957333/permalink/3645010065805526)

### Stuck Task States
**Scuba**: `tupperware_task_control_operations`
- Columns: `job`, `operationType`, `taskID`, `operationStatus`, `time`
- Filter: `job = <your_job_handle>`
- If stuck in ALLOCATED: may be waiting on VIP assignment or host agent communication
- If stuck in FREEING: check for VIP unassignment issues (`isVipUnassigned`)
**CLI**: `tw changes commit <job_handle>` to commit pending changes
**CLI**: `tw task-control apply-task-ops --all-ops <job_handle>` to force-apply pending operations
**Example posts**: [1](https://fb.workplace.com/groups/1473492212957333/permalink/3509796405993560)

### Lost Agent
**Scuba**: `tupperware_task_events`
- Columns: `job`, `event_name`, `host`, `time`
- Filter: `event_name = AGENT_LOST`
- Common causes: high CPU preventing agent from responding, network glitches, host issues
**CLI**: `below replay --host <hostname>` to check for CPU spikes not visible in ODS
- Reduce `failover_timeout` (default 900s/15min) to speed up host replacement (risk of false positives)
- Enable holdback on reservation to reserve CPU/RAM for system tasks
**Example posts**: [1](https://fb.workplace.com/groups/1473492212957333/permalink/3603818469924686)

#### Task Lost / Unavailability Event Investigation

Unavailability events are **not** an indicator of a Tupperware-induced problem. An event simply means the scheduler/resource broker could not contact the agent on a host. The cause could be anything: maintenance, power cuts, hardware failure, user or system workloads causing resource pressure, or anything else that makes a host unresponsive.

**Task lost** can have the same root causes as agent unavailability. Because of this, contacting TW@ is **often not an appropriate first move**. Many of these are user-induced or maintenance-induced issues that the Tupperware team is not equipped to root-cause. Gather the information below first -- it will significantly cut down on churn if you do need to escalate.

**Step 1 — Service logs**
- Check your own task logs using [Service Logs](https://internalfb.com/intern/wiki/Infra_Cloud/Service_Hosting/Tupperware/Tupperware_Monitoring/Service_Logs/)
- If logs are missing or truncated, review [Troubleshoot Logs](https://internalfb.com/intern/wiki/Infra_Cloud/Service_Hosting/Tupperware/Tupperware_Help_and_Troubleshooting/Troubleshoot_Logs_in_Tupperware/)

**Step 2 — TW host health checker**
- Check if the [Tupperware host health checker](https://www.internalfb.com/intern/wiki/Infra_Cloud/Service_Hosting/Tupperware/Tupperware_Monitoring/Service_Health/) considered the host healthy
- **Scuba**: [`tupperware_healthchecker_transitions`](https://fburl.com/scuba/tupperware_healthchecker_transitions/kjz1irbk)
- This alone is not a root cause, but helps confirm or narrow down which component could not contact the host

**Step 3 — `tw diag`**
- **CLI**: `tw diag <task/host>` for a multi-faceted diagnostic view of the task/host from the Tupperware ecosystem

**Step 4 — Netcons / kernel messages**
- Review [netcons Scuba tables](https://www.internalfb.com/intern/wiki/Kernel/Debugging/Monitoring/Netconsole/#Scuba) for kernel messages
- **Scuba (host)**: [`netcons`](https://fburl.com/scuba/netcons/r4i3hr3w)
- **Scuba (alarms)**: [`netcons_alarms`](https://fburl.com/scuba/netcons_alarms/slhdxj1b)

**Step 5 — `system.uptime` vs `tw_agent.uptime` correlation**
- **ODS**: [`system.uptime` / `tw_agent.uptime`](https://fburl.com/ods/xn8gyv02)
- If the host wasn't up or restarted, it is natural for the agent to be unavailable and the task lost -- no TW bug
- If the agent wasn't up but the host was, this is often a problem to discuss with the **tupperware agent team**

**Step 6 — Historical event correlation**
- [Serf history](https://fburl.com/serf/tqu00yw3) of the host
- [WTH history](https://www.internalfb.com/intern/wth/?host=<hostname>) of the host
- [FBAR log history](https://www.internalfb.com/intern/wiki/Infrastructure/FBAR/CLI_and_API/#fbar-log) or [FBAR status](https://www.internalfb.com/intern/wiki/Infrastructure/FBAR/CLI_and_API/#fbar-status) of the host
- If you have access to the host, check:
  - `dmesg` for kernel logs
  - `/var/log/messages` for system logs
  - `journalctl` for journal logs
  - `/var/facebook/tupperware/agent/logs/current` or `/var/facebook/logs/archive/tupperware_agent.log-*` for historical agent logs

**Step 7 — Resource pressure metrics**
- If a workload was hogging all I/O, memory, or CPU, the host may be considered unavailable because it is not responding to connection attempts for long periods
- Workload slice pressure usually indicates your own workloads causing issues. System slice pressure can point to system daemons, but could be caused by user workload (e.g., heavy task-induced scribe writes overworking host-level scribe processes)
- **ODS (I/O pressure)**: [`fburl.com/ods/pnk2qpsk`](https://fburl.com/ods/pnk2qpsk)
- **ODS (Memory pressure)**: [`fburl.com/ods/db9vaciy`](https://fburl.com/ods/db9vaciy)
- **ODS (CPU pressure)**: [`fburl.com/ods/smn20a4h`](https://fburl.com/ods/smn20a4h)
- Use [`below`](https://www.internalfb.com/intern/wiki/Resource_control/below_getting_started/) or [`atop`](https://www.internalfb.com/intern/wiki/Atop/) to dive into per-process resource pressure

### Crash-Loop Preemption Throttling

When a job is configured with `max_instance_restarts` as a finite number, after X crashes Tupperware moves the task to a new host, placing it in Pending state.

If a task is in a crash loop, Tupperware **throttles how fast preemptions can happen** to prevent adverse fleet and scheduler impacts. Tasks in a tight crash loop may stay in Pending instead of being allocated. This is visible in the UI as a **clock icon** next to the task.

**Workplace announcement**: [`tw.fyi/973774806139941`](https://fb.workplace.com/groups/tw.fyi/permalink/973774806139941/)

**Resolution:**
1. Fix the underlying crash-loop issue first
2. Manually allocate the throttled task:
```
tw changes show $jobHandle            # find the throttled task instance
tw changes commit $throttledTaskInstanceChange   # force-commit the throttled instance
```
3. Alternative: `tw restart --fast` on the affected task achieves the same effect

**Caveat:** Force-committing allocates the task but does **not** remove the throttling. If the task needs to be preempted again (because it is still crash-looping), preemptions will still be throttled. Make sure the underlying crash-loop cause is resolved first.

### Verify Resolution
**CLI**: `tw task-control show <job_handle>` to confirm no pending operations remain
**CLI**: `tw print <job_handle>` to verify the job spec is correct

## Best Practices & How-To

### How to handle preemption for tasks-per-host changes during pushes
There is an asymmetry in TW behavior: increasing tasks per host results in in-place update/restart, while decreasing triggers full job preemption. TW only sees resource changes (milliRRU) but cannot interpret application-level semantics. Plan accordingly and use max_total_down to control the blast radius.
**Example posts**: [1](https://fb.workplace.com/groups/1473492212957333/permalink/3495730400733494)

### How to control preemption rate during updates
Use `step_size` (absolute value) instead of `step_size_percent` to avoid bugs with RRU-based calculations. For shrink operations, configure `maxTotalDownEnabledForShrink` carefully. Use `max_total_down` to include tasks stopped due to maintenance, unlike `step_size` which only limits update-related downs.
**Example posts**: [1](https://fb.workplace.com/groups/1473492212957333/permalink/3484373871869147), [2](https://fb.workplace.com/groups/1473492212957333/permalink/3476594879313713)

### How to prevent tasks from being scheduled on specific hosts
Use `tw bad-host <hostname>` for permanent avoidance. Use `locality_constraints` in the spec for region avoidance. For temporary maintenance, use UEs (Unavailability Events) to preempt tasks before performing work on hosts.
**Example posts**: [1](https://fb.workplace.com/groups/1473492212957333/permalink/3505824489724085)

## Common Questions

### Q: Can LOST tasks be isolated to prevent resurrection and false alerts?
**A:** This remains an open problem. The scheduler cannot tell an unresponsive agent to kill tasks. When the agent recovers, tasks are killed within minutes. The design intentionally keeps services running via local restarts when schedulers are unavailable.

### Q: Is CPU pinning supported in TW?
**A:** TW does not provide CPU pinning. Even if an application claims to pin schedulers to cores, instrumentation may show migration between cores. This is a Service Router / cgroup-level concern, not a TW feature.

### Q: Can I configure which scheduler shard my job runs on?
**A:** Shard mapping is per-reservation and not user-controlled. The scheduler team handles shard distribution of critical workloads. Users should not attempt to manage shard assignments themselves.

### Q: What happens to tasks on elastic (Tier 3) capacity during rebalancing?
**A:** Elastic capacity has zero guarantees about duration or availability. Tasks can be preempted at any time for higher-priority needs. This is expected behavior, not a bug. Critical workloads should use guaranteed (Tier 1) capacity.

### Q: Will using twac to kill a container stop the scheduler from restarting it?
**A:** No. Using `twac kill-task` does not change the scheduler's intent -- it will persistently try to restart killed tasks. To truly stop a service, use `tw stop` to tell the scheduler.

## Reference Tables

### Scuba Tables
| Table | When to Use | Key Columns |
|-------|------------|-------------|
| `tupperware_task_events` | Track preemption, agent_lost, scheduling events | `job`, `event_name`, `event_detail`, `host` |
| `tupperware_task_control_operations` | Track task control ops during scheduling | `job`, `operationType`, `taskID`, `operationStatus` |
| `tupperware_jcp_tickers` | Track JCP scheduling decisions | `job_handle`, `ticker_type` |
| `tupperware_api_service_cpp` | Debug scheduler domain errors | `job_handle`, `error` |
| `tupperware_job_request_history` | Track scheduling requests | `job`, `method_name` |
| `tupperware_healthchecker_transitions` | Check host health checker state | `host`, `transition`, `time` |
| `netcons` | Kernel messages for a host | `host`, `message`, `time` |
| `netcons_alarms` | Kernel alarm events for a host | `host`, `alarm`, `time` |

### CLI Commands
| Command | When to Use |
|---------|------------|
| `tw task-control show <job_handle>` | Check pending task operations |
| `tw task-control apply-task-ops --all-ops <job_handle>` | Force-apply pending operations |
| `tw allocation explain <job_handle>` | Explain allocation/scheduling decisions |
| `tw allocation preempt <task_handle>` | Preempt a task to a different host |
| `tw bad-host <hostname>` | Permanently mark a host as bad |
| `rbcli clear-machinedomain --domain <domain> --rb <rb> --host <host>` | Clear stale RB domain |
| `twi scheduler-shard-migration request-migration` | Request zero-downtime shard migration |
| `tw changes commit <job_handle>` | Commit pending changes |
| `tw diag <task/host>` | Multi-faceted diagnostic view of task or host |
| `tw changes show <job_handle>` | Show pending changes including throttled instances |
| `tw changes commit <throttled_instance_change>` | Force-commit a throttled task instance |
| `tw restart --fast <task_handle>` | Fast-restart a task (alternative to manual commit) |
