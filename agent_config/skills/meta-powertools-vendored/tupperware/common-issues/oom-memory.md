# OOM & Memory Pressure

> 142 posts in TW Group FAQ | Primary Scuba: `netcons_ooms` | Primary CLI: `below`

## Debugging Playbook

**CLI**: `tw task-control show-status <job_handle>` to check task states. Pick the matching section:

| Symptom | Go to |
|---------|-------|
| Exit code 137 (SIGKILL) | [OOM Kills](#oom-kills) |
| `tw.mem-util-pct` near 100% but no kill | [Memory Spike Without OOM](#memory-spike-without-oom) |
| High io-wait after restart/push | [IO-Wait After Restart](#io-wait-after-restart) |
| "Dogpile.cpp" errors / transitively unhealthy | [Dogpile GC Issues](#dogpile-gc-issues) |
| Some tasks OOM, others fine | [Task-Specific OOM](#task-specific-oom) |
| OOM after config/model change, code unchanged | [Configuration-Driven OOM](#configuration-driven-oom) |

---

### OOM Kills
**Scuba**: `netcons_ooms`
- Columns: `killed_job`, `killed_task`, `time`, `cgroup_mem_current`
- Filter: `killed_job = <your_handle>`, time range covering the incident
- Confirms the OOM kill event and memory usage at time of kill
**Scuba**: `tupperware_unexpected_task_exits`
- Columns: `job_handle`, `exit_code`, `timestamp`
- Filter: `exit_code = 137` to find all OOM-related exits
**CLI**: `below replay --host <hostname> --time "1 hour ago"` to analyze memory usage per container
- Check if OOM is due to resource enforcement limits, not total host memory
- Enable holdback to reserve host operating memory; set `use_all_available_ram_exclusive=True`
- Note: enum value 0 for `resource_enforcement` means ENFORCE
- If more RAM is needed, get a Stackable M224 reservation
**Heap profiling at OOM time**: If using oomd to enforce resource limits, you can ask it to collect heap profiling data before killing your process. This captures what the heap looked like just before the kill (note: not every OOM will be profiled due to overhead). Compare OOM-time heap data against continuously collected heap profiles to spot what stands out.
- C++ ServiceFramework services: see [Strobelight Heap Profiler](https://www.internalfb.com/intern/wiki/Strobelight-for-services/heap-profiler/) for details and how to trigger manual runs
- Python services: see [Python Memory Profiling](https://www.internalfb.com/intern/wiki/Python/ProfilingPythonCode/Memory_Profiling/)
- General OOM debugging with Strobelight: [Strobelight for OOM](https://www.internalfb.com/intern/wiki/Strobelight-for-services/Debug_OOMs/#option-3-oomd-strobeligh)
- Full OOM debugging guide: [Debug OOMs](https://www.internalfb.com/wiki/Strobelight-for-services/How_To/Debug_OOMs/)
- Controlling oomd behavior: [Controlling oomd Killing](https://www.internalfb.com/intern/wiki/Infra_Cloud/Service_Hosting/Tupperware/Best_Practices/Tupperware_Patterns/)
**Example posts**: [1](https://fb.workplace.com/groups/1473492212957333/permalink/3488131034826764), [2](https://fb.workplace.com/groups/1473492212957333/permalink/3005401009766834)

### Memory Spike Without OOM
**CLI**: `below replay --host <hostname> -t <timestamp>` to see anon vs cache breakdown
- The OOM killer looks at anonymous memory (`anon-util pct`), not total memory
- File-backed pages (cache) can be reclaimed and do not count toward OOM threshold; file cache can make memory usage look worse than it is, so distinguish anon memory (real usage) from file cache when interpreting metrics
- OOM kills are triggered by oomd when pressure exceeds 80%
- `tw.mem-util-pct` value of 1 means 1%, not 100%
- **ODS granularity limitation**: ODS captures point-in-time samples at minute granularity (DAILY table only). Low ODS counters do NOT mean there was no sudden memory spike. Use `below` for per-cgroup memory usage at 5-second granularity to confirm whether a sudden spike occurred.
- Refer to the Monitoring Machine and Container Resources wiki for metric details
**Example posts**: [1](https://fb.workplace.com/groups/1473492212957333/permalink/3493941757579025), [2](https://fb.workplace.com/groups/1473492212957333/permalink/3494976477475553)

### IO-Wait After Restart
**Scuba**: `strobelight_services` / `offcpu` for io-wait patterns
- Root cause: cgroup memory protection retains memory from previous container after restart
- Shared memory protection prevents file cache from new container from being reclaimed → thrashing on boot drive
**CLI**: `tw print <job_handle>` to check `memoryProtectionConfig`
- Fix: Set `MEM_PROT_DISABLED` in the job spec to disable cgroup memory protection
- In Spec 2.0, `memoryProtectionConfig` support was added later; if `tw update` would trigger offboarding, use the chef-based workaround (adding reservations to an opt-out list) until the field is converted
**CLI**: `tw diff <job_handle>` to verify pending changes
**Example posts**: [1](https://fb.workplace.com/groups/1473492212957333/permalink/3388194664820402), [2](https://fb.workplace.com/groups/1473492212957333/permalink/3371414036498465)

### Dogpile GC Issues
**Scuba**: `tupperware_health_check_results`
- Columns: `job`, `success`, `task`
- Filter: `job_handle = <your_handle>`
- Check for health check failures caused by GIL contention in the Python service
**Scuba**: `netcons_ooms` to correlate OOM events with unhealthy transitions
- Root cause: Python not performing automatic garbage collection → OOM within 200-300 requests
- Fix: Add manual `gc.collect()` calls in the application
**Example posts**: [1](https://fb.workplace.com/groups/1473492212957333/permalink/3475647249408476)

### Task-Specific OOM
**Scuba**: `netcons_ooms`
- Columns: `killed_task`, `cgroup_mem_current`, `time`
- Filter: `killed_task = <specific_task_id>`
**CLI**: `below replay --host <hostname>` to compare memory usage across tasks
- This is typically an application issue, not a Tupperware issue
- Different tasks may load different data (e.g., different tensor shards) causing some to exceed limits
- Use `tw preempt <task_handle>` to move the task to a different host
- If memory usage climbs again, investigate why those specific tasks consume more memory
**Example posts**: [1](https://fb.workplace.com/groups/1473492212957333/permalink/3005401009766834), [2](https://fb.workplace.com/groups/1473492212957333/permalink/3555512774755256)

### Configuration-Driven OOM
**Scuba**: `netcons_ooms` to confirm OOM timing
- Root cause: tasks that load a large amount of state from configuration (e.g., ML models specified in a configerator config) can OOM when a config change increases memory consumption
- Check for recent configuration changes that correlate with the onset of OOM kills
- This is distinct from a code-level memory leak — the binary itself is unchanged, but the data it loads has grown
**CLI**: `tw print <job_handle>` to inspect the job spec for config references
- Compare memory consumed before and after the config change using `below replay` or ODS metrics
- Fix: revert the config change, increase the memory limit, or optimize the model/data being loaded

### Verify Resolution
**CLI**: `tw task-control show-status <job_handle>` to confirm tasks are healthy
**Scuba**: `netcons_ooms` — verify no new OOM events after fix

## Best Practices & How-To

### How to configure memory limits to prevent OOM
Enable holdback to reserve host operating memory for system tasks. Set `use_all_available_ram_exclusive=True` to get maximum available RAM. Set explicit `ResourceLimit(ram="XG")` rather than relying on defaults. Consider Stackable M224 reservations if more RAM per task is needed.

### How to disable cgroup memory protection
Set `MEM_PROT_DISABLED` in the TW spec. See [IO-Wait After Restart](#io-wait-after-restart) for the Spec 2.0 workaround when `tw update` would trigger offboarding.
**Example posts**: [1](https://fb.workplace.com/groups/1473492212957333/permalink/3371414036498465)

### How to monitor memory for OOM risk
Monitor `tw.mem-util-pct` (anon memory) rather than total memory for OOM risk — see [Memory Spike Without OOM](#memory-spike-without-oom) for the anon vs cache distinction. For Python services, implement explicit `gc.collect()` if GIL contention is observed.

## Common Questions

### Q: High memory consumption before service code even starts running. What is using memory?
**A:** This is typically all anonymous memory (heap allocations) attributed to the user binary, not TW overhead. Run locally in a sandbox to debug, and reach out to the Python infra team for tooling to trace allocations. This is not a TW issue.
**Example posts**: [1](https://fb.workplace.com/groups/1473492212957333/permalink/3539309009708966)

### Q: What metric determines OOM kill — total memory or anon memory?
**A:** Anonymous memory (`anon-util pct`), not total memory. See [Memory Spike Without OOM](#memory-spike-without-oom) for the full anon vs cache breakdown and oomd threshold details.

### Q: Does enum value 0 for resource_enforcement mean ENFORCE or DISABLED?
**A:** It means ENFORCE — see the note in [OOM Kills](#oom-kills).

## Reference Tables

### Scuba Tables
| Table | When to Use | Key Columns |
|-------|------------|-------------|
| `netcons_ooms` | Confirm OOM kill events | `killed_job`, `killed_task`, `time`, `cgroup_mem_current` |
| `tupperware_unexpected_task_exits` | Check unexpected exits including OOMs | `job`, `exit_code` |
| `tupperware_health_check_results` | Correlate OOM with health check failures | `job`, `success` |
| `tupperware_task_events` | Track task lifecycle around OOM events | `job`, `task`, `event_name` |

### CLI Commands
| Command | When to Use |
|---------|------------|
| `below replay --host <hostname> --time "1 hour ago"` | Analyze memory usage, CPU, io-wait on the host |
| `tw print <job_handle>` | Check resource limits, memory protection config |
| `tw bad-host <hostname>` | Report a bad host causing repeated OOMs |
| `tw preempt <task_handle>` | Move OOMing task to a different host |
| `tw ssh root <task_id>` | SSH into task to debug memory usage |
| `tw diff <job_handle>` | Compare local spec vs running spec for memory settings |
