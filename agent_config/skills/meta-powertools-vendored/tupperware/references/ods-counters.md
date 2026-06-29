# ODS Counters for Tupperware Monitoring

Reference for all ODS counters exported by Tupperware at the job, task, host, and SMC tier levels, plus common query patterns for debugging.

**Primary wiki**: [Cheatsheet: Counters for Tupperware Monitoring](https://www.internalfb.com/wiki/Infra_Cloud/Service_Hosting/Tupperware/Tupperware_Monitoring/Cheatsheet%3A_Counters_for_Tupperware_Monitoring/)

---

## Entity Naming

| Level | Entity Format | Key Prefix | Example |
|---|---|---|---|
| **Job** | `tupperware.jobs.<job_handle>` | `tw.*` | `tupperware.jobs.tsp_prn/myteam/myservice.prod` |
| **Task** | `<task_handle>` (raw) | `tw.*` | `tsp_prn/myteam/myservice.prod/0` |
| **Host** | `<hostname>` (short, no FQDN) | `system.*` | `twshared8667.04.maz1` |
| **SMC Tier** | `tupperware.schedulers.<scheduler_pool>` | `tw.*` | `tupperware.schedulers.tsp_global` |

## Entity Selectors

```bash
# All tasks in a job — use tw.* keys
ods query --stime 1_h 'twtasks(JOB_HANDLE)' 'tw.mem.rss_bytes' 'latest' 'avg'

# Via SMC tier → task entities — use tw.* keys
ods query --stime 1_h 'smc(MY.TIER, selector=twtask)' 'tw.cpu.total' 'latest' 'avg'

# Via SMC tier → job entity — use fb303.* or tw.* job-level keys
ods query --stime 1_h 'smc(MY.TIER, selector=twjob)' 'fb303.requests.count' 'rate' 'sum'

# Direct job entity query
ods query --stime 1_h 'tupperware.jobs.JOB_HANDLE' 'tw.tasks_healthy' 'latest'

# Host-level query — use system.* keys with bare hostname as entity
ods query --stime 1_h 'HOSTNAME' 'system.cpu-util-pct' 'latest'
ods query --stime 1_h 'HOSTNAME' 'system.mem-util-pct' 'latest'

# Discover available keys for an entity
ods eki --entity='TASK_HANDLE' --key_prefix='tw.' --limit=100
ods eki --entity='tupperware.jobs.JOB_HANDLE' --key_prefix='tw.' --limit=100
ods eki --entity='HOSTNAME' --key_prefix='system.' --limit=100
```

**Selector tips:**
- `selector=host` (SMC default) → often has no `tw.*` keys; use `system.*` keys instead
- `selector=twtask` → best for per-task `tw.*` metrics
- `selector=twjob` → best for `fb303.*` and job-level `tw.*` metrics

---

## Job Counters

Entity: `tupperware.jobs.<job_handle>`

| Key | Description |
|---|---|
| `tw.job_state` | Current job state (from `JobState` enum). Viewable historically in ODS. |
| `tw.job_size` | Task count per the job spec (not healthy count). Useful as denominator for %-based alerts. |
| `tw.job_res_limit_mem` | Memory resource limit from the job spec. |
| `tw.job_res_limit_cpu` | CPU resource limit from the job spec. |
| `tw.job_res_limit_disk` | Disk resource limit from the job spec. |
| `tw.tasks_running` | Number of tasks in RUNNING state. |
| `tw.tasks_healthy` | Number of healthy tasks. Perfectly healthy: `tw.tasks_healthy == tw.job_size`. |
| `tw.tasks_unhealthy` | Number of unhealthy tasks. Always `tw.job_size - tw.tasks_healthy`. |
| `tw.task_state_<state>` | Count of tasks in a given state. Notable: `tw.task_state_running`, `tw.task_state_running_not_healthy` (running but fb303 reports unhealthy). |
| `tw.unintended_job_restarts.rate.60` | Rate of unexpected restarts across all tasks in last 60s. |
| `tw.unintended_job_restarts.sum.60` | Total unexpected restarts across all tasks in last 60s. |

---

## Task Counters

Entity: `<task_handle>` (e.g., `tsp_prn/myteam/myservice.prod/0`)

### Health & Restart

| Key | Description |
|---|---|
| `tw.task_running_and_healthy` | `1` if running + healthy fb303 status, `0` otherwise. |
| `tw.task_running_and_has_been_healthy` | Latches to `1` once healthy; stays `1` until task stops. |
| `tw.unintended_task_restarts.rate.60` | Rate of unexpected restarts in last 60s. Includes OOM kills, crashes, staging timeouts. Excludes CLI-initiated and maintenance restarts. |
| `tw.unintended_task_restarts.sum.60` | Sum of unexpected restarts in last 60s. |
| `tw.task_restarted.count.{60,600,3600}` | Total container starts (including first start). Resets on update/canary/preempt. |
| `tw.task_failed.count.{60,600,3600}` | Container failures: agent restart during staging, container start failure, forced shutdown (kill timeout, unhealthy), agent helper lost. |
| `tw.host_connection_last_success` | Last epoch timestamp when scheduler reached the agent. Stale = connectivity problem. |

### CPU (`tw.cpu.*`)

| Key | Description |
|---|---|
| `tw.cpu.user` | % of logical cores in user space. 100 = 1 logical core. |
| `tw.cpu.kernel` | % of logical cores in kernel space. |
| `tw.cpu.total` | Total logical core usage %. |
| `tw.cpu.throttled` | Cumulative throttle time %. 200 over 60s = 120s total throttled. |
| `tw.container.cpu-util-pct` | % of **allocated** CPU used (sys + usr). Best for capacity planning. |
| `tw.container.cpu-sys` | % of allocated CPU in kernel space. |
| `tw.container.cpu-usr` | % of allocated CPU in user space. |
| `tw.cpu.reservation_pct` | Alias for `tw.container.cpu-util-pct`. |

**Key distinction:** `tw.cpu.total` = % of machine's logical cores. `tw.container.cpu-util-pct` = % of your allocated CPU.

### Memory (`tw.mem.*`)

| Key | Description |
|---|---|
| `tw.mem.rss_bytes` | RSS = active_anon + inactive_anon + file_mapped. The "working set." |
| `tw.mem.total` | Total cgroup memory (from `memory.current`). |
| `tw.mem.anon` | Anonymous (heap) memory in bytes. Non-reclaimable. Steadily increasing = possible leak. |
| `tw.mem.anon.util-pct` | Anon memory as % of cgroup limit. High = OOM risk. Best alarm metric with senpai. |
| `tw.mem.util-pct` | Total memory (anon + filecache) as % of cgroup limit. **Recommended alarm metric.** |
| `tw.mem.file` | File-backed memory (cache). Reclaimable unless tmpfs/shared/mlocked. |
| `tw.mem.cache_bytes` | Page cache size (note: known calculation issues). |
| `tw.mem.tmpfs` | tmpfs/ramfs usage. |
| `tw.mem.persistent_tmpfs` | Memory from persistent tmpfs/ramfs dirs. |
| `tw.mem.unevictable` | Non-reclaimable memory in bytes. |
| `tw.mem.vm` | Process VM size in bytes. |
| `tw.mem.swap.current` | Swap space used by cgroup. |
| `tw.mem.zswap.current` | zSwap compressed memory used. |
| `tw.mem.reservation_pct` | **WARNING: Inaccurate — do not use.** Use `tw.mem.util-pct` instead. |

**Best practice:** Enable senpai + alarm on `tw.mem.util-pct`. Senpai applies memory pressure so kernel reclaims cold pages, giving accurate readings. Auto-enabled for all twshared jobs (except T6).

### Network (`tw.net.*`)

| Key | Description |
|---|---|
| `tw.net.rx.bytes` / `tw.net.tx.bytes` | Total received/transmitted bytes. |
| `tw.net.rx.pps` / `tw.net.tx.pps` | Packets per second. |
| `tw.net.rx.bps` / `tw.net.tx.bps` | Bits per second. |
| `tw.net.udp.rx.pps` / `tw.net.udp.tx.pps` | UDP packets per second. |
| `tw.net.udp.rx.bps` / `tw.net.udp.tx.bps` | UDP bits per second. |
| `tw.net.rx.softirq.hit` | NET_RX_SOFTIRQ entries on task's CPUs. |
| `tw.net.rx.softirq.stolen` | Positive = received more packets than CPUs processed; negative = CPU cycles "stolen" for other tasks. |

All counters exclude localhost traffic. Both IPv4 and IPv6 counted.

### Disk (`tw.disk.*`)

| Key | Description |
|---|---|
| `tw.disk.chroot` | Main filesystem (chroot) disk usage in bytes. Accounts for transparent compression. |
| `tw.disk.persist` | Persistent directory disk usage in bytes. |
| `tw.disk.rootdisk.read_bps` | Disk read throughput (bytes/sec). |
| `tw.disk.rootdisk.write_bps` | Disk write throughput (bytes/sec). |

### GPU / Accelerator (`tw.accelerator.*`)

| Key | Description |
|---|---|
| `tw.accelerator.util-pct` | Sum of GPU utilization across all allocated devices. |
| `tw.accelerator.util-pct-max` | Max individual device utilization. |
| `tw.accelerator.mem-used-pct` | Sum of GPU memory usage % across devices. |
| `tw.accelerator.mem-used-pct-max` | Max individual device memory usage %. |
| `tw.accelerator.num-devices` | Number of accelerator devices allocated. |

Also available from Dynolog: `dyno.twtask.sm_active_avg`, `dyno.twtask.gpu_power_draw_avg`, `dyno.twtask.hbm_mem_bw_util_avg`, etc.

### Firewall (`tw.twfw.*`, only if TwFw enabled)

| Key | Description |
|---|---|
| `tw.twfw.{egress,ingress}.pkt` | Packets processed/passed. |
| `tw.twfw.{egress,ingress}.pkt.dropped` | Packets dropped. |
| `tw.twfw.{egress,ingress}.pkt.not_matched` | Packets not matching any rule. |

---

## SMC Tier Counters

Entity: `tupperware.schedulers.<scheduler_pool>`

| Key | Description |
|---|---|
| `tw.smc_services` | Count of SMC service instances (bridge services only). |
| `tw.smc_services_enabled` | Count of ENABLED SMC service instances. |
| `tw.smc_user_errors` | Flag (1 = active) if any user SMC error in 60s window. |

---

## Host Counters

Entity: `<hostname>` (short hostname, e.g., `twshared8667.04.maz1`)

Use these for host-wide resource utilization across all stacked tasks. Essential for noisy-neighbor investigations where you need the aggregate view, not individual task metrics.

### CPU (`system.cpu*`)

| Key | Description |
|---|---|
| `system.cpu-util-pct` | Overall CPU utilization % across all cores. |
| `system.cpu-busy-pct` | CPU busy % (user + system + irq). |
| `system.cpu-user` | CPU time in user space %. |
| `system.cpu-sys` | CPU time in kernel space %. |
| `system.cpu-softirq` | CPU time in softirq %. High values indicate network processing overhead. |
| `system.cpu-iowait` | CPU time waiting for I/O %. |
| `system.cpu-idle` | CPU idle %. |

### Memory (`system.mem*`)

| Key | Description |
|---|---|
| `system.mem-util-pct` | Overall memory utilization %. |
| `system.mem_used` | Total used memory in bytes. |
| `system.mem_free` | Free memory in bytes. |
| `system.mem_free_nobuffer_nocache` | Free memory excluding buffers and cache. |
| `system.mem_anon` | Anonymous memory in bytes. |
| `system.mem_slab` | Kernel slab memory in bytes. |
| `system.mem_kernel` | Total kernel memory in bytes. |

### Network (`system.net*`)

| Key | Description |
|---|---|
| `system.net-cpu-busy` | CPU % consumed by network processing (SoftIRQ). High values = NIC is driving CPU starvation. |
| `system.net-cpu-busy-max` | Peak network CPU busy %. |
| `system.net-sirq-max` | Peak SoftIRQ % on any CPU core. >85% indicates saturation. |
| `system.net-sirq-gt85` | Count of CPU cores with SoftIRQ >85%. |
| `system.net.tcp.rxmits_per_s` | TCP retransmissions per second. High = congestion or packet loss. |
| `system.net.tcp.socket_count` | Total TCP socket count on host. |
| `system.net.tcp.mem` | TCP memory usage. |
| `system.net.tcp.memory_pressures_per_s` | TCP memory pressure events per second. |

---

## Common Query Patterns

### Health & Restarts
```bash
# Job health overview
ods query --stime 6_h 'tupperware.jobs.JOB_HANDLE' 'tw.tasks_healthy' 'latest'
ods query --stime 6_h 'tupperware.jobs.JOB_HANDLE' 'tw.tasks_unhealthy' 'latest'

# Unexpected restart rate
ods query --stime 6_h 'tupperware.jobs.JOB_HANDLE' 'tw.unintended_job_restarts.sum.60' 'latest'

# Per-task health
ods query --stime 1_h 'twtasks(JOB_HANDLE)' 'tw.task_running_and_healthy' 'latest'
```

### Memory Investigation (OOM / Leak)
```bash
# RSS across all tasks
ods query --stime 1_h 'twtasks(JOB_HANDLE)' 'tw.mem.rss_bytes' 'latest' 'avg'

# Anon memory % (OOM risk indicator)
ods query --stime 1_h 'twtasks(JOB_HANDLE)' 'tw.mem.anon.util-pct' 'latest' 'max'

# Memory trend (leak detection)
ods query --stime 24_h 'twtasks(JOB_HANDLE)' 'tw.mem.util-pct' 'avg(5m)' 'avg'

# Top memory consumers
ods query --stime 30_min 'twtasks(JOB_HANDLE)' 'tw.mem.rss_bytes' 'latest' 'top(10)'
```

### CPU Investigation
```bash
# CPU utilization (% of allocated)
ods query --stime 1_h 'twtasks(JOB_HANDLE)' 'tw.container.cpu-util-pct' 'latest' 'avg'

# CPU throttling
ods query --stime 1_h 'twtasks(JOB_HANDLE)' 'tw.cpu.throttled' 'latest' 'max'

# CPU trend
ods query --stime 24_h 'twtasks(JOB_HANDLE)' 'tw.container.cpu-util-pct' 'avg(5m)' 'avg'
```

### Network Investigation
```bash
ods query --stime 1_h 'twtasks(JOB_HANDLE)' 'tw.net.rx.bps' 'latest' 'sum'
ods query --stime 1_h 'twtasks(JOB_HANDLE)' 'tw.net.tx.bps' 'latest' 'sum'
```

### Disk Investigation
```bash
ods query --stime 1_h 'twtasks(JOB_HANDLE)' 'tw.disk.chroot' 'latest' 'max'
ods query --stime 1_h 'twtasks(JOB_HANDLE)' 'tw.disk.rootdisk.write_bps' 'latest' 'avg'
```

### GPU Investigation
```bash
ods query --stime 1_h 'twtasks(JOB_HANDLE)' 'tw.accelerator.util-pct' 'latest' 'avg'
ods query --stime 1_h 'twtasks(JOB_HANDLE)' 'tw.accelerator.mem-used-pct' 'latest' 'max'
```

### Host-Level Investigation (Noisy Neighbor / Resource Contention)
```bash
# First resolve the task to find its host
tw resolve JOB_HANDLE

# Host CPU utilization (total across all stacked tasks)
ods query --stime 6_h 'HOSTNAME' 'system.cpu-util-pct' 'avg(5m)'

# Host memory utilization
ods query --stime 6_h 'HOSTNAME' 'system.mem-util-pct' 'avg(5m)'

# Host network CPU overhead (SoftIRQ-driven CPU starvation indicator)
ods query --stime 6_h 'HOSTNAME' 'system.net-cpu-busy' 'avg(5m)'
ods query --stime 6_h 'HOSTNAME' 'system.net-sirq-max' 'avg(5m)'

# Host TCP retransmissions (network congestion indicator)
ods query --stime 6_h 'HOSTNAME' 'system.net.tcp.rxmits_per_s' 'avg(5m)'

# Discover all system.* keys for a host
ods eki --entity='HOSTNAME' --key_prefix='system.' --limit=100
```

### fb303 Service Counters (via SMC tier)
```bash
ods query --stime 30_min 'smc(MY.TIER, selector=twjob)' 'fb303.requests.count' 'rate' 'sum'
ods query --stime 30_min 'smc(MY.TIER, selector=twjob)' 'fb303.requests.latency_ms.p99' 'avg(5m)' 'avg'
ods query --stime 30_min 'smc(MY.TIER, selector=twjob)' 'fb303.errors.count' 'rate' 'sum'
```

### Discovery
```bash
# Find all keys for a task entity
ods eki --entity='TASK_HANDLE' --key_prefix='tw.' --limit=100

# Find all keys for a job entity
ods eki --entity='tupperware.jobs.JOB_HANDLE' --key_prefix='tw.' --limit=100

# Resolve entities in a job
ods resolve 'twtasks(JOB_HANDLE)'

# Resolve SMC tier to TW tasks
ods resolve 'smc(MY.TIER, selector=twtask)'

# Get shareable chart URL
ods query --stime 1_h 'twtasks(JOB_HANDLE)' 'tw.mem.rss_bytes' 'latest' 'avg' --fburlonly
```

---

## Alert Configuration

Reusable alert functions: `configerator/source/monitoring/common/tupperware1.mon.cinc`

Common alert patterns:
- **Healthy task %**: `tw.tasks_running / tw.job_size`
- **Memory approaching limit**: alarm on `tw.mem.util-pct` (with senpai) or `tw.mem.anon.util-pct` (without)
- **Unexpected restarts**: alarm on `tw.unintended_job_restarts.rate.60`

Non-default counters can be opted in via the [monitoring config](https://www.internalfb.com/intern/wiki/Infra_Cloud/Service_Hosting/Tupperware/Tupperware_Reference/LanguageReference/monitoring/#monitoring-config-option) in your job spec.

---

## Caveats

- `tw.mem.reservation_pct` is **inaccurate — do not use**. Use `tw.mem.util-pct` instead.
- `tw.mem.cache_bytes` has known calculation issues (incorrect subtraction of shmem).
- On cgroup2, shmem is part of active_anon + inactive_anon, so do not add `tw.mem.tmpfs` to `tw.mem.rss_bytes` (double-counting).
- Default counter set: `configerator/source/monitoring/common/rampage.cinc`
- Full available counters: `fbcode/tupperware/if/AgentService.thrift`
