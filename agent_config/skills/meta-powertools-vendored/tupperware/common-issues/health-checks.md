# Health Check Failures

> 93 posts in TW Group FAQ | Primary Scuba: `tupperware_health_check_results` | Primary CLI: `tw task-control`

## Debugging Playbook

**CLI**: `tw task-control show-status <job_handle>` to check task states, then pick the matching section:

| Symptom | Go to |
|---------|-------|
| Tasks unhealthy but TW not restarting them | [Unhealthy But Not Restarting](#unhealthy-but-not-restarting) |
| "Cannot connect to ptail port" | [Ptail Port Errors](#ptail-port-errors) |
| SMC disabling tasks / monitoring noise | [SMC Disabling Tasks](#smc-disabling-tasks) |
| Tasks going unhealthy during a SEV | [Health Checks During SEVs](#health-checks-during-sevs) |
| Global job overloaded, tasks unhealthy on `tsp_global` | [Global Scheduler Overload](#global-scheduler-overload) |
| Health check endpoint hangs but process is running | [Deadlocked or Hung Process](#deadlocked-or-hung-process) |

---

### Unhealthy But Not Restarting
**Scuba**: `tupperware_health_check_results`
- Columns: `job`, `task`, `success`, `port_type`
- Filter: `job = <your_handle>`
- Check the actual health check results from the TW agent
- TW agent does localhost health checks, which differ from what appears in ODS
- Due to GIL contention, asyncio tasks sending ODS samples may not be scheduled, but local calls from TW agent can still succeed
- As long as the agent receives 1 successful health check before `failSeconds`, the task state is set back to healthy
**Action**: Implement the thrift `getStatus` method to respond with the desired healthiness state if default health checks are insufficient
**Example posts**: [1](https://fb.workplace.com/groups/1473492212957333/permalink/3511767659129768)

### Ptail Port Errors
**Scuba**: `tupperware_health_check_results`
- Columns: `job`, `task`, `success`
- Filter: `job = <your_handle>` — look for specific failure messages
**CLI**: `tw print <job_handle>` to inspect port configuration
- Health checks are configured in user code — the port change likely caused the health check to target a port the application no longer listens on
- Verify the port configuration matches the application's actual listening port
**Example posts**: [1](https://fb.workplace.com/groups/1473492212957333/permalink/3493987954241072)

### SMC Disabling Tasks
**Scuba**: `smc_changelogger`
- Check SMC state transitions — SMCBridge enable/disable reflects the health of the task
**Scuba**: `tupperware_health_check_results`
- Columns: `job`, `success`, `time`
- Cross-reference health state changes with SMC tier state
- If a task goes unhealthy temporarily, SMC will disable it and re-enable when healthy again — this is expected behavior
**Example posts**: [1](https://fb.workplace.com/groups/1473492212957333/permalink/1400093054675289)

### Health Checks During SEVs
**Scuba**: `tupperware_health_check_results`
- Columns: `job`, `success`, `port_type`
- Filter: `job = <your_handle>`
- The task failed TW health checks, typically a consequence of being overloaded rather than a root cause
- TW's health check contract is limited — for thrift health checks, it checks the value returned by the process on a standard call
- Unhealthy tasks in SMC still receive traffic because SMC/ServiceRouter relies on service router to mark tasks down, not TW health status
- Refer to the TW wiki on Task Issues for debugging health check failures
**Example posts**: [1](https://fb.workplace.com/groups/1473492212957333/permalink/3487324941574040)

### Global Scheduler Overload
**Scuba**: [`service_router`](https://fburl.com/scuba/eueggx8v)
- Look at the traffic and see where it is coming from
- If the job is bridged to an SMC tier and the job is global (e.g., on `tsp_global`), `svc_prefer_localities` should be set to `global`
- If not set correctly, the default regional routing can overload tasks depending on how the global scheduler places them to hosts and where traffic is coming from
- This mismatch between placement and traffic routing causes certain tasks to become unhealthy under load

**Action**: Configure `svc_prefer_localities = global` on the SMC tier for global jobs. See [this post](https://fb.workplace.com/groups/servicerouter/permalink/2368593423167546/) for details on configuring SMC properties correctly.

### Deadlocked or Hung Process
When the health check endpoint stops responding but the process is still running, the service may be deadlocked or hung.
**CLI**: `quickstack -fnp {your_process_id} | less` to get a snapshot of all threads
- [Quickstack](https://fb.workplace.com/groups/webfoundation/permalink/1052443351470946/) captures the current stack trace of every thread without attaching a debugger
- Use [Strobelight-for-services](https://www.internalfb.com/intern/wiki/Strobelight-for-services/) to get a sampled view of thread states over a period of time
- Compare thread states to identify threads that are blocked or waiting on locks indefinitely

**Action**: SSH into the task with `tw ssh <task_handle>`, find the process ID, and run quickstack before restarting. Copy the output to your devserver for analysis.

### Verify Resolution
**CLI**: `tw task-control show-status <job_handle>` to confirm tasks are healthy
**CLI**: `thriftdbg sendRequest getStatus <host:port>` to manually test thrift health check

## Best Practices & How-To

### How to configure health checks properly
Implement the thrift `getStatus` method to reflect actual service health. Set appropriate `failSeconds` values — too short causes false positives. Configure long failure periods before terminating to avoid cascading failures. Wire custom application metrics into the health check response.

### How to auto-restart tasks based on custom metrics
TW supports health checks but not specifying arbitrary counters for remediation. Wire up internal metrics to the thrift `getStatus` response and configure TW accordingly. Health checks are evaluated locally on each host, so kills due to health check failures can surpass configured stepsize/max task down. Only incorporate robust measures.
**Example posts**: [1](https://fb.workplace.com/groups/1473492212957333/permalink/3558074741165726)

### How to handle SMCBridge shutdown ordering
SMCBridge disables the task in the SMC tier before invoking the kill command. However, a custom `kill_command` with a sleep delay does not guarantee zero-traffic during shutdown since service router caching and connection draining have their own timelines. The recommended approach is to use the health check mechanism to signal unhealthy before shutdown.
**Example posts**: [1](https://fb.workplace.com/groups/1473492212957333/permalink/1893607147612502)

### How to use debug mode for GPU debugging
Use `tw job debug-mode --tools <TOOLS> <task_handles>` to enable debug mode with specific tools on specific tasks. Debug mode suspends health checks and makes nvidia-smi available. TW does not support tenant-based allowlists for nvidia-smi access.
**Example posts**: [1](https://fb.workplace.com/groups/1473492212957333/permalink/3494807597492441)

## Common Questions

### Q: Can TW auto-restart a task when a specific metric (like Thrift queue) exceeds a threshold?
**A:** Not directly — wire your metrics into the thrift `getStatus` response instead. See [How to auto-restart tasks based on custom metrics](#how-to-auto-restart-tasks-based-on-custom-metrics) above.

### Q: Does SMCBridge remove a service from the SMC tier before or after invoking the kill_command?
**A:** Before — but a sleep-based kill_command still does not guarantee zero-traffic. See [How to handle SMCBridge shutdown ordering](#how-to-handle-smcbridge-shutdown-ordering) above.

### Q: For Python services, does GIL contention affect health check responsiveness?
**A:** Yes — ODS may show no data while the task is actually healthy from TW's perspective. See [Unhealthy But Not Restarting](#unhealthy-but-not-restarting) above for details.

## Reference Tables

### Scuba Tables
| Table | When to Use | Key Columns |
|-------|------------|-------------|
| `tupperware_health_check_results` | Primary table for health check results | `job`, `task`, `success`, `port_type` |
| `scuba_tupperware_health_check_results` | Alternative health check table | `job_handle`, `health_result` |
| `tupperware_task_events` | Track task state changes around health issues | `job`, `event_name` |
| `service_router` | Check service router routing decisions | `tier_name`, `host` |
| `fleet_health` | Check fleet-wide health patterns | `host`, `health_state` |
| `smc_changelogger` | Track SMC tier state transitions | `tier_name`, `action` |

### CLI Commands
| Command | When to Use |
|---------|------------|
| `tw task-control show-status <job_handle>` | Check current task states and pending operations |
| `tw ssh <task_handle>` | SSH into task to debug health check issues |
| `tw job debug-mode --tools <TOOLS> <task_handles>` | Enable debug tools with health check suspension |
| `tw allocation explain <job_handle>` | Check allocation status for unhealthy tasks |
| `thriftdbg sendRequest getStatus <host:port>` | Manually test thrift health check |
| `quickstack -fnp {process_id} \| less` | Get thread stack snapshot for deadlock debugging |
| `tw update <job_handle>` | Update job after fixing health check configuration |
