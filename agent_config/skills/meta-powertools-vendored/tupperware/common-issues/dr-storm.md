# DR & Storm Tests

> 5 posts in TW Group FAQ | Primary Scuba: `tupperware_task_events` | Primary CLI: `tw update`

## Debugging Playbook

| Symptom | Go to |
|---------|-------|
| Tasks not distributed across regions | [Region Distribution](#region-distribution) |
| Services not recovering after storm undrain | [Post-Storm Recovery](#post-storm-recovery) |
| Preparing for an MRDR storm test | [MRDR Preparation](#mrdr-preparation) |

---

### Region Distribution
**CLI**: `tw resolve <job_handle>` to see which regions tasks are running in
- If tasks are co-located in the same region, check the allocation policy
- `makeGlobalJobDistributeOverRegions` has a rounding bug for small jobs (3-4 tasks) -- it rounds up maxCount incorrectly
- Preferred: Use `makeAllocationExclusive(job, scope=REGION, exclusive_scope=False)` for explicit region spread control
**Scuba**: `tupperware_task_events`
- Columns: `job_handle`, `event_type`, `region`, `host`, `timestamp`
- Filter: `job_handle = <your_job_handle>`
**Example posts**: [1](https://fb.workplace.com/groups/1473492212957333/permalink/3555184871454713), [2](https://fb.workplace.com/groups/1473492212957333/permalink/3578482989124901)

### Post-Storm Recovery
**CLI**: `tw task-control show <job_handle>` to check for pending operations
- tsp_global has a ~20-minute failover SLO (empirical, not guaranteed) due to processing many moves during outages
- DR buffers on tsp_global are passive -- new containers take ~10 min to start
- Check if the issue is tracked by a SEV (e.g., S564602 for FRC/ATN MRDR)
- Stale endpoints after task replacement: TW does not provide at-most-once guarantees; the application must handle re-registration
**Example posts**: [1](https://fb.workplace.com/groups/1473492212957333/permalink/3603818469924686), [2](https://fb.workplace.com/groups/1473492212957333/permalink/3608503016122898)

### MRDR Preparation
- Ensure adequate capacity buffers: fund DR buffers in each region (N+2 regions minimum)
- For MRDR readiness: turning on MRDR guarantees capacity survives losing 2 regions if properly funded
- To pin tasks during testing: implement a custom task controller that does not ack preemption/UE events
- Check exclusion options: there is NO way to opt out of regional storm exercises for tsp_global jobs

## Best Practices & How-To

### How to configure region spread for tsp_global jobs
Use `makeAllocationExclusive(job, scope=REGION, exclusive_scope=False)` instead of `makeGlobalJobDistributeOverRegions`. The latter has implicit assumptions about sizing and rounding bugs with small jobs. Caveat: in a DR storm, half of each job will be lost and will not recover until the region recovers.
**Example posts**: [1](https://fb.workplace.com/groups/1473492212957333/permalink/3555184871454713)

### How to find which tasks are running in a specific tent/region
Use `tw resolve <job_handle>` to list all tasks and their hosts/regions. For tent-aware DR solutions, correlate task locations with tent assignments. ZKDaemon supports leader election with automatic failover for multi-region scheduling.
**Example posts**: [1](https://fb.workplace.com/groups/1473492212957333/permalink/3793464317626766)

### How to achieve active/passive failover across regions
For single-primary-task leader election with fast failover across regions, use ZKDaemon. For strong region spread guarantees, use regional reservations or `makeAllocationExclusive`. MRDR does not guarantee tasks are spread across specific regions -- it guarantees capacity survival.
**Example posts**: [1](https://fb.workplace.com/groups/1473492212957333/permalink/3608503016122898)

## Common Questions

### Q: Can certain TW jobs be excluded from storm/drain tests?
**A:** No. TW does not allow opting out of regional storm exercises. The global SR drain is fully intended to test everything. Coordinate with the DR team beforehand and ask customers to reduce demand for targeted regions.

### Q: How long does it take for services to recover after a storm undrain?
**A:** The tsp_global failover SLO is empirically ~20 minutes (p99), but violations are frequent. DR buffers are passive and new containers take ~10 minutes to start. Plan for this latency.

### Q: Does tsp_global guarantee container spread across regions?
**A:** No. tsp_global does not guarantee container spread. DR buffers are passive (need ~10 min for new containers). Users must fund their own DR buffers and set appropriate capacity per region.

## Reference Tables

### Scuba Tables
| Table | When to Use | Key Columns |
|-------|------------|-------------|
| `tupperware_task_events` | Track task movements during DR events | `job`, `event_name`, `region`, `host` |
| `tupperware_task_control_operations` | Monitor operations during storm tests | `job`, `operationType` |

### CLI Commands
| Command | When to Use |
|---------|------------|
| `tw resolve <job_handle>` | Check task-to-region distribution |
| `tw task-control show <job_handle>` | Check pending operations after storm |
| `tw update <spec> <job_handle>` | Update spec for DR configuration changes |
| `tw allocation preempt <task_handle>` | Move task during DR preparation |
