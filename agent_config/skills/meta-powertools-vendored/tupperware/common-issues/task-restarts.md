# Task Restarts & Crashes

> 210 posts in TW Group FAQ | Primary Scuba: `tupperware_task_events` | Primary CLI: `tw task-control`

## Debugging Playbook

**CLI**: `tw task-control show-status <job_handle>` to check task states and pending operations. Pick the matching section:

| Symptom | Go to |
|---------|-------|
| Crash loop (staging → not healthy → crashed) | [Crash Loop](#crash-loop) |
| Exit code 127 ("command not found") | [Command Not Found](#command-not-found) |
| "spec MMID does not match" / stuck creating | [MMID Mismatch](#mmid-mismatch) |
| High unintended restart counters | [High Restart Counters](#high-restart-counters) |
| Tasks stuck in "aborted" state | [Stuck Aborted](#stuck-aborted) |

---

### Crash Loop
**CLI**: Get recent logs first:
```bash
tw log <task_handle> --file stderr -s "30 minutes ago"
tw log <task_handle> --file stderr --pattern "ERROR|FATAL|exception" -C 5
```
**Scuba**: `coredumper` — use `meta scuba.dataset info -d coredumper` to discover column names before querying (key columns include `tw_job`, `tw_stack_trace`, `signal`)
**Scuba**: `tupperware_task_events` — filter by `event_name = TASK_EXIT_REPORT` to find crash/exit events. Use `meta scuba.dataset info -d tupperware_task_events` to verify column names.
**CLI**: `tw bad-host <hostname>` — if crashes are host-specific, report the bad host
**CLI**: `tw preempt <task_handle>` — move crashing task to a different host
**CLI**: `tw ssh --debug <task_handle>` — use debug mode to keep container up after crash for investigation

**To capture a core dump for segfault analysis (exit 139):**
Follow the [Pre-Restart Data Collection](#pre-restart-data-collection) procedure above to capture strobelight profiles, quickstack output, and core dumps before the container recycles.

**Analyzing core dumps with GDB/LLDB:**
Coredumps should typically be analyzed with GDB. Use the "debug command" column in the `coredumper` Scuba dataset to get a single command to initiate a debugging session.
- General GDB usage: `/intern/wiki/GDB/`
- Python services with GDB: `/intern/wiki/Python/Debugging_with_GDB/`
- LLDB migration guide: `https://www.internalfb.com/intern/wiki/SaND/GDB_to_LLDB_Migration/debug_coredump/`

**TW UI for crash investigation:**
- Task crashes are visible as red dots in the gantt chart in the Tupperware UI.
- In the Tasks tab, expand Recent History and browse the icons on the left to jump to the log section corresponding to each run.

**Example posts**: [1](https://fb.workplace.com/groups/1473492212957333/permalink/3488851061421428)

### Command Not Found
**CLI**: `tw print <job_handle>` to verify package configuration
- Exit code 127 means the binary specified in the spec is not found in the package
- Caused by a parameter/package mismatch (e.g., wrong package name after migration)
- Verify the package name matches the fbpkg being deployed
- Check both the spec and the running container for binary paths
**Example posts**: [1](https://fb.workplace.com/groups/1473492212957333/permalink/1930071364230436)

### MMID Mismatch
> [!NOTE]
> This is a rare issue typically associated with specific incidents/SEVs, not a common day-to-day problem.

**Scuba**: `rblib_operations`
- Check host materialization state
- This is caused by host materialization being out of sync
**CLI**: `tw task-control apply-task-ops` to clear stuck operations
- Fix: Run a fast host pool update on the host to resolve the MMID mismatch
- Often related to a provisioning event during a known incident
**Example posts**: [1](https://fb.workplace.com/groups/1473492212957333/permalink/1388177655559540)

### High Restart Counters
**Scuba**: `tupperware_task_events`
- Columns: `job`, `event_name`, `time`
- Filter: `event_name = TASK_EXIT_REPORT` (note: `UNEXPECTED_EXIT` is not a valid event_name value — always verify with `meta scuba.column values -d tupperware_task_events -c event_name`)
- Check if restarts correlate with maintenance or elastic capacity preemption
**Scuba**: `dataplacer_moves`
- Columns: `Group`
- Correlate autoscaler shrinks with restart events — DP detects upcoming shrink, removes assignment, triggers clean exit (exit code 0), but TW counts it as unintended restart
- For elastic tier 3 capacity, all tasks can be preempted at any time — noisy restart metrics are expected
**CLI**: `tw bad-host <hostname>` — if XFS in-memory corruption on some hosts contributes to restart counts
**Example posts**: [1](https://fb.workplace.com/groups/1473492212957333/permalink/3492987024341165), [2](https://fb.workplace.com/groups/1473492212957333/permalink/3476594879313713)

### Stuck Aborted
**Scuba**: `tupperware_task_events`
- Columns: `job`, `event_name`, `time`
- Filter for the time period before tasks entered aborted state
**Scuba**: `coredumper` — check for crashes before the aborted transition
**CLI**: `tw task-control show-status <job_handle>` to check pending operations
- Logs may be gone if the host was reallocated
- A TW team member can manually restart affected tasks
- If the issue recurs, investigate the underlying host issues
**Example posts**: [1](https://fb.workplace.com/groups/1473492212957333/permalink/3470526156587252)

### Verify Resolution
**CLI**: `tw task-control show-status <job_handle>` to confirm no pending operations remain
**Scuba**: `tupperware_task_events` — verify no new unexpected exits after fix

## Crash Loop Sub-types

When classifying a crash loop, identify the specific sub-type:

| Sub-type | Evidence | Remediation |
|----------|----------|-------------|
| **OOM crash loop** | Exit 137, RSS near memory limit | Increase memory limit or fix memory leak |
| **Health check crash loop** | Exit 0 + restart, health check failures | Increase `initial_delay_seconds`/`timeout_seconds`, fix health check endpoint |
| **Startup crash** | Exit 1, immediate restart, errors in logs | Fix application bug, rollback bad deploy, revert config change |
| **Segfault crash loop** | Exit 139, core dump references | Check for bad native code deploy, file task against library owner |
| **Bad binary/entrypoint** | Exit 126 or 127 | Fix package contents or entrypoint configuration |
| **Preemption loop** | Exit 143, repeated preemptions | Check reservation capacity, priority settings |
| **Missing shutdown handler** | Alternating exit 143 then 137 | Implement SIGTERM handler for graceful shutdown within the kill timeout window |
| **Bad host crash loop** | Failures only on specific hosts, disk full, mount errors, `TASK_STOP_HEALTH_FAILURE` with `twtask-main.service never ran` | Preempt task to move to different host; bad-host the machine; check `fbar` Scuba for automated remediation actions on the host |
| **Disk full loop** | `DISK_FULL` preemption, task rescheduled on same host | Preempt to different host; agent may auto-reclaim disk but can take time |
| **Container setup failure loop** | `tw-sidecar-ready.service` failure, `failed to setup fs required by tupd init` | Escalate to TW oncall — likely agent rollout or host issue |
| **Invalid package loop** | `NO_SUCH_VERSION`, `METADATA_VERSION_NOT_FOUND_ERROR`, fbpkg version expired | Update job to a valid package version; check `fbpkg versions <package_name>` |
| **Agent crash during startup** | Single task restarting on stacked host, other tasks fine, agent errors in diag timeline | Preempt task to retry; if persistent, escalate to TW oncall (may be agent rollout issue) |

### Exit Code Quick Reference

| Exit Code | Signal | Meaning | Likely Cause |
|-----------|--------|---------|--------------|
| 0 + restart | — | Clean exit but restarted | Health check failure |
| 1 | — | Application error | Code bug, missing dependency, bad config |
| 126 | — | Permission denied | Binary not executable |
| 127 | — | Command not found | Wrong entrypoint, missing binary |
| 134 | SIGABRT | Abort | Assertion failure, `abort()` called |
| 137 | SIGKILL | Killed by kernel/OOM | Memory limit exceeded |
| 139 | SIGSEGV | Segmentation fault | Memory corruption, native crash |
| 143 | SIGTERM | Graceful termination | Preemption, scale-down, or maintenance |
| 255 | — | Exit status out of range | Uncaught exception, SSH failure |

### Restart Pattern Analysis

| Pattern | Interpretation |
|---------|---------------|
| Restarts every few seconds, exit 137 | OOM — memory exhaustion on startup |
| Restarts every few minutes, exit 137 | Memory leak — RSS grows until limit is hit |
| Immediate restart, exit 1 | Startup crash — config or dependency issue |
| Restarts after fixed interval, exit 0 | Health check timeout — service starts but becomes unresponsive |
| Restarts getting further apart | Backoff working — transient issue may be resolving |
| Alternating exit 143 then 137 | Missing SIGTERM handler — task ignores SIGTERM, gets SIGKILL |

## Best Practices & How-To

### Pre-Restart Data Collection

> [!WARNING]
> **Before you restart your task, collect important data.** Once a task restarts, logs, core dumps, and profiling data from the crashed run may be lost. Follow these steps first.

**Step 1 — Get strobelight profiles:**
Use `bunnylol cpprof` to pull CPU profiles. For memory issues (OOM, leaks), collect heap/malloc profiles.

**Step 2 — SSH with quickstack tools:**
```bash
tw ssh <task_handle> --debug-mode-tools=quickstack
```

**Step 3 — Find the process PID:**
```bash
ps aux | grep "<process name>"
```

**Step 4 — Grab a quickstack snapshot:**
```bash
/opt/quickstack/bin/quickstack -fnp <pid> &> /logs/quickstack.txt
```

**Step 5 — Trigger a core dump:**
```bash
kill -6 <pid>
```

**Step 6 — Wait for the core dump to appear:**
```bash
ls -lhtr /tw_cores
```

**Step 7 — Copy data to your devserver before the container recycles:**
```bash
tw scp <task_handle>:/tw_cores/<core name> .
tw scp <task_handle>:~/quickstack.txt .
tw scp <task_handle>:/logs/<log file name> .
```

### How to handle elastic tier 3 restart noise
For elastic tier 3 capacity, accept that high restart counts are normal and adjust monitoring. Filter out autoscaler-induced restarts by correlating with `dataplacer_moves`. Decide whether to ignore this job in health checks or deal with false positive push failures.
**Example posts**: [1](https://fb.workplace.com/groups/1473492212957333/permalink/3492987024341165)

### How to debug crash loops without losing the container
Enable TW debug mode for investigating crash loops. Use `tw ssh --debug <task_handle>` to keep the container running after a crash, allowing manual investigation of state, logs, and memory.

**Debug mode mechanism:** `tw job debug-mode <job_handle>` disables health checks and pauses the state machine. This keeps or advances the task state to running so you can SSH in and debug. Use `tw job debug-mode --help` for additional options.

**Host-level fallback paths** — if the container is inaccessible (debug mode fails, container won't start), access data directly on the host:
- stderr/stdout logs: `/var/facebook/tupperware/agent/data/$team/$job/$tasknum/persist-dirs/logs`
- Packages: `/var/facebook/tupperware/agent/packages`
- Agent logs: `/var/facebook/tupperware/agent/logs/current` or `/var/facebook/logs/archive/tupperware_agent.log-*`

### "Agent tells the helper to stop" is not an error
This is a normal shutdown message. The Tupperware Agent (which manages containers) tells the container (owned by agent-helper) to begin the shutdown process. Do not treat this as an error or crash indicator.

### How to prevent CPU contention causing excessive restarts
Enable NUMA binding for CPU-sensitive workloads. Without CPU pinning/NUMA node binding, many tasks sharing a host causes hardware resource contention (particularly memory bandwidth). Stackable reservations perform CPU pinning that is NUMA and GPU aware.
**Example posts**: [1](https://fb.workplace.com/groups/1473492212957333/permalink/3493079257665275)

### How to handle tasks repeatedly entering pending state
Containers exiting with exit code 0 immediately (e.g., failing to parse input data) triggers TW exponential backoff throttling, showing up as "pending". Fix the application to not exit immediately.
**Example posts**: [1](https://fb.workplace.com/groups/1473492212957333/permalink/3555736611399539)

## Common Questions

### Q: When Autoscaler shrinks a job, terminated tasks are treated as unintended restarts. Is this expected?
**A:** Yes, this is a race condition between Data Placer (DP) and Tupperware. DP detects the upcoming shrink and removes the assignment, triggering a clean exit (exit code 0). However, TW still counts exit code 0 as an unintended restart if TW did not initiate it. Filter out these "intended restarts" by correlating with DP moves using the `dataplacer_moves` Scuba table.
**Example posts**: [1](https://fb.workplace.com/groups/1473492212957333/permalink/3476594879313713)

### Q: CPU utilization is 400% higher than expected when scaling out tasks. Why?
**A:** Caused by lack of CPU pinning/NUMA node binding. When many tasks share a host without NUMA-aware binding, there is hardware resource contention (particularly memory bandwidth). Enable NUMA binding to resolve. Stackable reservations perform CPU pinning that is NUMA and GPU aware.
**Example posts**: [1](https://fb.workplace.com/groups/1473492212957333/permalink/3493079257665275)

## Reference Tables

### Scuba Tables
| Table | When to Use | Key Columns |
|-------|------------|-------------|
| `tupperware_task_events` | Track task lifecycle events | `job`, `task`, `event_name` |
| `coredumper` | Analyze crash dumps and stack traces | `tw_job`, `signal`, `tw_stack_trace` (verify with `meta scuba.dataset info`) |
| `tupperware_crashes` | Status/log messages when a task terminates unexpectedly (segfault, OOM) — distinct from `coredumper` and `tupperware_task_events` | `job`, `task` |
| `tupperware_unexpected_task_exits` | Find unexpected exits | `job`, `exit_code` |
| `tupperware_health_check_results` | Check health transitions around crashes | `job`, `task` |
| `dataplacer_moves` | Correlate autoscaler shrinks with restarts | `Group` |
| `fbar_log` | Check if FBAR took automated actions (reboot, repair, prod disable) on the host -- see [dataset reference](../datasets/fbar.md) | `entity`, `alert_name`, `remediation_module`, `event` |
| `tw_allocatorv2_machine_tag_change` | Check allocator tag changes on the host (status, maintenance) -- see [dataset reference](../datasets/tw_allocatorv2_machine_tag_change.md) | `machine`, `tag`, `action`, `value` |

### CLI Commands
| Command | When to Use |
|---------|------------|
| `tw task-control show-status <job_handle>` | Check pending task operations |
| `tw task-control apply-task-ops --all-ops <job_handle>` | Clear stuck task operations |
| `tw bad-host <hostname>` | Report a bad host causing crashes |
| `tw preempt <task_handle>` | Move crashing task to a different host |
| `tw ssh --debug <task_handle>` | SSH with debug mode to inspect crashes |
| `tw restart <task_handle>` | Restart a specific task |
| `twac mlv -t <handle> -p /logs -s <host>` | Escalation: map container log path to host filesystem when `tw log` and `tw ssh` both fail |
| `twac export-task-spec -t <handle> -s <host>` | Escalation: verify actual running spec when behavior doesn't match `tw job print` |
| `twac ls -a -s <hostname>` | Escalation: detect ghost containers when scheduler state seems inconsistent (see [host-machine.md](./host-machine.md#agent-level-debugging-with-twac-escalation)) |
