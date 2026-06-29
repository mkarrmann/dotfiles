# Host & Machine Issues

> 145 posts in TW Group FAQ | Primary Scuba: `fleet_health` | Primary CLI: `tw bad-host`

## Debugging Playbook

### Step 1: Determine if the issue is host-specific
**CLI**: `tw resolve <job_handle>` to get the host for each task.
-> If only specific tasks are failing while others are healthy, the issue is likely host-specific. If all tasks are failing, it is likely a job-level issue (spec, package, reservation).

### Step 2: Check the host health and route to the right section
**CLI**: `machinechecker <hostname>` to run hardware diagnostics.
**CLI**: `twhac -s <hostname> get-host-profile` to check the current host profile state.

| Finding | Go to |
|---------|-------|
| Hardware failure (GPU, NIC, disk) | [Bad Host Remediation](#bad-host-remediation) |
| NEWLY_PROVISIONED or stuck profile swap | [Host Profile Swap Issues](#host-profile-swap-issues) |
| Host looks healthy but tasks fail | [Application-Level Failures](#application-level-failures-on-specific-hosts) |
| Planned maintenance preemption | [Planned Maintenance](#planned-maintenance) |
| Accelerator bundle error (GPU hosts) | [Accelerator Bundle Failures](#accelerator-bundle-failures-gpu-hosts) |

### Step 3: Check additional host diagnostic signals

**Chef staleness:** A host where Chef hasn't run successfully in days may have stale configuration. Use [this ODS link](https://fburl.com/ods/gg155f4g) to check the age of the last successful Chef run for a host, and [this Scuba query](https://fburl.com/scuba/chef/oggksz70) to dive into Chef errors on a host (or in aggregate).

**Console message history (Netcons):** [Netcons](https://www.internalfb.com/intern/wiki/Diagnosing-and-resolving-host--network-issues-in-datainfra/netcons/) lets you query historical console messages on a host -- the same information you can get in real time from the `cons` command, but stored and queryable. Useful for spotting kernel panics, OOM kills, or hardware errors that occurred before you started investigating.

**Alarms on the host:** Run `alarms find -e $HOSTNAME -A` to check active alarms on a specific host. See [Alarms for Tupperware](https://www.internalfb.com/intern/wiki/Infra_Cloud/Service_Hosting/Tupperware/Tupperware_Monitoring/Alarms_for_Tupperware/) for more details on alarm types and thresholds.

---

### Bad Host Remediation
**CLI**: `tw bad-host --reason "<description>" <hostname>` to report the host.
-> TW allocator will preempt running workloads off the host. If `tw bad-host` fails with "owner_id" validation error, use: `tw bad-host --reason "<reason>" --manual-repair-owner="" <hostname>`.
-> If `tw bad-host` does not work for private pool or edge hosts, use `tw allocation preempt <job_handle>/<task_id>` to move specific tasks instead.
**Example posts**: [1](https://fb.workplace.com/groups/1473492212957333/permalink/3488851061421428), [2](https://fb.workplace.com/groups/1473492212957333/permalink/3625936504379549)

### Host Profile Swap Issues
**Scuba**: `host_profile_migrations`
- Columns: `hostname`, `state`, `message`, `event_category`
- Filter: `hostname = <your_host>`
-> If status is FAILED: the profile migration failed but the host was released to production anyway, a contract violation. Common causes: disk corruption, missing mounts, kernel update failures.
**Scuba**: `iaas_server_mover_operations`
- Columns: `hostname`, `move_type`, `operation_name`, `message`
- Filter: `hostname = <your_host>`
-> If a Server Mover operation is stuck (e.g., GUARANTEED_TO_UNRESERVED_RAS), the host is in limbo. Contact RB/HM oncall to purge the stuck move.
**CLI**: `tw allocation preempt <job_handle>/<task_id>` to move tasks off the stuck host.
**Example posts**: [1](https://fb.workplace.com/groups/1473492212957333/permalink/3558878704418663), [2](https://fb.workplace.com/groups/1473492212957333/permalink/3579255835714283)

### Automated Remediation (FBAR)
**Scuba**: `fbar_log` -- check if FBAR took automated actions on the host (reboots, repairs, prod disable). See [dataset reference](../datasets/fbar.md) for full schema and queries.
- Key columns: `entity`, `alert_name`, `remediation_module`, `event`, `event_type`
- Filter: `entity = <your_host>` AND `is_keypoint = 1`
-> If FBAR rebooted or disabled the host, this explains task disruptions. Correlate FBAR event timestamps with task restarts from `tupperware_task_events`.

### Application-Level Failures on Specific Hosts
**Scuba**: `fleet_health`
- Columns: `hostname`, `tw_availability_status`, `actual_host_profile`, `unavailability_category`
- Filter: `hostname = <your_host>`
-> Check for active UEs (unavailability events): planned maintenance, capacity rebalancing, or config updates. If a `configUpdateUnavailability` is blocking, RB oncall can purge it.
**CLI**: `rbcli search --match host_fqdn=<hostname>` to check allocation state and domain info.
**Example posts**: [1](https://fb.workplace.com/groups/1473492212957333/permalink/3500190666954134), [2](https://fb.workplace.com/groups/1473492212957333/permalink/3524019301237937)

### Planned Maintenance
-> If a task went pending due to planned maintenance and the reservation uses "As Is" availability guarantee, there are no replacement buffers. Switch to "Available" guarantee for high-availability services.
-> If the host was recalled from elastic, host profile swaps can take up to 9 minutes. Pending tasks should auto-resolve.
-> Planned maintenance cannot be stopped by task controllers. If the TC does not ack before the deadline, maintenance proceeds anyway.
**Example posts**: [1](https://fb.workplace.com/groups/1473492212957333/permalink/3619084638398069), [2](https://fb.workplace.com/groups/1473492212957333/permalink/3500190666954134)

### Agent-Level Debugging with twac (Escalation)

When scheduler-side tools (`tw resolve`, `tw job print`, Scuba) show inconsistencies — tasks the scheduler doesn't know about, state mismatches, or resource accounting disagreements — use `twac` to get the host agent's ground truth. `twac` talks directly to the TW agent on port 4330, bypassing the scheduler entirely.

**When to use:**
- `tw resolve` shows a task as RUNNING but the service is actually down (or vice versa)
- A host appears to have no tasks but is consuming resources (ghost/orphaned containers)
- Task behavior doesn't match the spec from `tw job print` (agent may have applied runtime modifications like RBA CPU adjustments)
- The scheduler is unavailable or unresponsive

**Key commands:**

```bash
# List all containers the agent knows about (compare with tw resolve for discrepancies)
twac ls -a -s <hostname>
twac ls -a -s <hostname> -F taskHandle,state,containerState --format json

# Check agent health
twac status -s <hostname>

# Dump agent's in-memory log (works even when agent is unhealthy)
twac memlog -s <hostname>

# Show the task spec as the agent sees it (may differ from scheduler's view)
twac export-task-spec -t <task_handle> -s <hostname> -f json

# Map a container path to the host filesystem (access logs on dead containers)
twac map-local-volume -t <task_handle> -p /logs -s <hostname>

# Show resource allotments with CPU pinning, shape, and cgroup paths
twac list-allotments -s <hostname>

# Visualize CPU topology with allotment pinning overlay
twac show-cpu-topology -s <hostname>

# Show per-container network devices and interfaces
twac net -s <hostname>
```

**Detecting ghost containers:**
1. List agent's containers: `twac ls -a -s <hostname> -F taskHandle,state`
2. List scheduler's containers: `tw search --value "hostname == <hostname>"`
3. Any task in the agent output missing from the scheduler output is an orphan

Ghost containers happen when cleanup RPCs from the scheduler never reach the agent (e.g., during shard migrations). They waste resources and can emit stale metrics. Escalate to `tupperware_scheduler_core` oncall for cleanup.

### Accelerator Bundle Failures (GPU Hosts)

The accelerator bundle (`/run/facebook/tw_ha_accelerator_bundle.conf`) maps physical GPU devices to logical IDs. If it fails to generate, no GPU tasks can start on the host.

**Common errors:**
- `Setup accelerator configuration failed`
- `Bundle inventory has a bundle with identifier which is not unique`
- `Failed to find Nvidia device with minor` (GPU fell off PCIe bus)
- `Node group size is not divisible` (missing GPU card)
- `Total NIC device count is invalid` (NIC swap issue)

**Debugging:**
1. Check if all GPUs are visible: `lspci -nnd 10de:` (NVIDIA) or `amd-smi` (AMD)
2. Compare `lspci` device count vs `/dev/nvidia?` count — a mismatch means a GPU fell off the bus
3. Check NVLink: `nvidia-smi nvlink -s` (look for `<inactive>`)
4. Check RDMA: `ls /dev/infiniband`
5. Inspect logs: look for `EventCategory: ACCELERATOR` in host_agent logs

**Fixes:**
- Missing GPU (fell off PCIe bus): drain host for repair via `tw bad-host`
- NVLink down: reboot; if persistent, send to repair
- MIG mismatch: `nvidia-smi -r` then `systemctl restart host_agent_mig_creation_oneshot.service`
- Regenerate bundle: `systemctl restart host_agent_accelerator_bundle_oneshot.service`
- Escalate to `hm_hardware_enablement` oncall for persistent bundle issues

## Best Practices & How-To

### How to get host profile information programmatically
For debugging, use `twhac -s <hostname> get-host-profile`. For programmatic access, use `hmcli ghc -t current --asset-id <ASSET_ID>`. Note: this returns what RB believes is the host profile, which may be stale during failed migrations. For fleet-wide queries, use the `fleet_health` Scuba dataset (~540 columns available).
**Example posts**: [1](https://fb.workplace.com/groups/1473492212957333/permalink/3512156662424201)

### How to handle reservation availability guarantees
"As Is" means no buffers -- tasks go pending during maintenance with no replacement capacity. "Available" includes SRF (Server Replacement Function) buffer for host replacements. For Tier 1 production services, always use "Available" guarantees. The unified buffer effort is addressing fragmentation between SRF and RF buffers.
**Example posts**: [1](https://fb.workplace.com/groups/1473492212957333/permalink/3500156383624129)

### How to configure local flash storage
Use managed flash with a BTRFS host profile (see TwsharedLocalFlash wiki). The data volume becomes the container root directory. Do not try to mount the same volume again in your spec or you will get conflicts. XFS is not supported for managed flash (BTRFS only). Direct IO compatibility needs verification.
**Example posts**: [1](https://fb.workplace.com/groups/1473492212957333/permalink/3540080992965101)

### How disk space dynamic reclamation works
Tupperware reclaims disk space from previous tasks as needed, so the amount of free space on a host fluctuates during normal operation. When you specify disk space requirements in your TW spec, Tupperware ensures the host has a **total** amount of disk space at least equal to what was requested, but **Tupperware makes no guarantees about how much space is free at any given time**. If your task's requirements are close to the physical disk capacity, you may see sporadic failures (usually while expanding packages) as free space rises and falls. Tupperware will always leave debugging info in place, so that space is not reclaimable. **Resolution:** reduce the amount of disk space needed by your task, or change your entitlement to a host type with a comfortable buffer of space.

### How to handle tasks stuck on hosts with disk issues
If you see "Failed to reformat storage device" or BTRFS corruption, remediate with: `rm -f /var/facebook/tupperware/agent/env_root_path`, reboot, unmount filesystem, reformat with `mkfs.btrfs /dev/md1p1 -f`, and retry provisioning. For immediate relief, preempt the task to a different host.
**Example posts**: [1](https://fb.workplace.com/groups/1473492212957333/permalink/1803089330275122)

## Common Questions

### Q: How do I control or debug OOM/oomd killing on a host?
**A:** See the [Controlling oomd Killing](https://www.internalfb.com/intern/wiki/Infra_Cloud/Service_Hosting/Tupperware/Best_Practices/Tupperware_Patterns/) guide for configuring oomd behavior, understanding kill thresholds, and preventing unwanted OOM kills. When tasks are being OOM-killed, there is a much greater chance the issue is service-specific (memory leak, undersized spec) rather than a host problem.

### Q: Does TW automatically detect bad hosts?
**A:** TW does not monitor for application-level regressions on hosts. It is the service owner's responsibility to identify and report bad hosts via `tw bad-host`. Machinechecker handles hardware-level detection (GPU failures, NIC flaps) but has a 30-minute check interval.

### Q: Can planned maintenance preemptions be prevented by task controllers?
**A:** No. Maintenance trains cannot be stopped by task control. If the TC does not ack the UE before the deadline, maintenance proceeds anyway. Use preemption control settings (max_total_down, step sizes) to limit blast radius.

### Q: Why does tw bad-host not work for edge or private pool hosts?
**A:** `tw bad-host` only supports immediate preemption for twshared hosts. For edge hosts, use the separate edge host report flow. For private pools, use `tw allocation preempt <task_handle>` to move individual tasks.

### Q: What causes "Host profile swap in progress, rejecting all tasks"?
**A:** The host is undergoing a profile migration (kernel, storage, or network changes). New containers cannot be created until the migration completes. If stuck for days, the migration likely failed. Contact HM data plane oncall and use `tw allocation preempt` to move tasks.

## Reference Tables

### Scuba Tables
| Table | When to Use | Key Columns |
|-------|------------|-------------|
| `fleet_health` | Host availability, profile status, UEs | `hostname`, `tw_availability_status`, `actual_host_profile`, `unavailability_category` |
| `host_profile_migrations` | Track profile migration status | `hostname`, `state`, `message` |
| `iaas_server_mover_operations` | Track server moves and capacity rebalancing | `hostname`, `move_type`, `operation_name` |
| `tupperware_task_events` | Task events on specific hosts | `host`, `job`, `event_name` |
| `tw_allocatorv2_machine_tag_change` | Host tag changes in allocator (status, maintenance_status, enable/disable) -- see [dataset reference](../datasets/tw_allocatorv2_machine_tag_change.md) | `machine`, `tag`, `action`, `value`, `reason_type` |
| `fbar_log` | Automated remediation actions on hosts (reboots, repairs, prod enable/disable) -- see [dataset reference](../datasets/fbar.md) | `entity`, `alert_name`, `remediation_module`, `event` |

### CLI Commands
| Command | When to Use |
|---------|------------|
| `tw bad-host --reason "<reason>" <hostname>` | Report a bad host for repair |
| `tw allocation preempt <job_handle>/<task_id>` | Move a task off a bad host |
| `machinechecker <hostname>` | Run hardware diagnostics |
| `twhac -s <hostname> get-host-profile` | Check current host profile |
| `rbcli search --match host_fqdn=<hostname>` | Check host allocation state |
| `hmcli ghc -t current --asset-id <ASSET_ID>` | Programmatic host profile query |
| `tw preempt <task_handle>` | Preempt a specific task |
| `alarms find -e $HOSTNAME -A` | Check active alarms on a specific host |
