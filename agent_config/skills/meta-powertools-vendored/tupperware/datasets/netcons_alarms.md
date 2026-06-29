# Netcons Alarms Scuba Dataset

**Purpose:** Debug kernel-level events including kernel panics, OOM kills, and hardware failures on hosts. This is a cross-infra dataset (not Tupperware-specific) but essential for diagnosing host-level issues that affect Tupperware tasks. When tasks crash with SIGKILL/OOM and `tupperware_crashes` doesn't show the root cause, check this dataset.

**Scuba Table:** `netcons_alarms`

**Scuba UI:** https://www.internalfb.com/intern/scuba/query/?pool=uber&dataset=netcons_alarms

**Related Datasets:**
- `tupperware_crashes` - For Tupperware-level crash details
- `tupperware_task_events` - For task state transitions after host events
- `tupperware_agent` - For agent-level events on the same host

---

## How to Get Schema

```bash
meta scuba.dataset query -d netcons_alarms --limit=5 -r "Sample data to view schema"
```

### Key Columns

| Column | Type | Description |
|--------|------|-------------|
| `time` | bigint | Unix timestamp |
| `hostname` | string | Host where the event occurred |
| `alert` | string | Alert type/category |
| `msg` | string | Full kernel message |
| `description` | string | Human-readable description |
| `status` | string | Event status |
| `cont` | string | Whether this is a continuation of a previous message |
| `cluster` | string | Cluster name |
| `datacenter` | string | Datacenter |
| `ipaddr` | string | IP address of the host |
| `model_id` | bigint | Hardware model ID |
| `rack_location` | string | Rack location |
| `rack_position` | string | Position in rack |
| `rack_structure_id` | bigint | Rack structure identifier |
| `hostprefix` | string | Host prefix |
| `device_state` | string | Device state |
| `maint` | string | Maintenance status |
| `oos` | string | Out of service status |
| `is_test_kernel` | string | Whether running a test kernel |
| `version` | string | Kernel version |
| `loglevel` | bigint | Kernel log level |
| `facility` | bigint | Syslog facility |
| `seq` | bigint | Sequence number |
| `uptime_us` | bigint | Host uptime in microseconds |
| `userdata` | array\<string\> | Additional user-defined data |

---

## Common Queries

### 1. Kernel Events on a Specific Host

See all kernel alarms on a host (panics, OOM, hardware errors).

```bash
meta scuba.dataset query -d netcons_alarms --view=samples -c time,alert,msg,description,status -w '[{"column":"hostname","op":"eq","values":["your-hostname.facebook.com"]}]' --hours=24 -r "Kernel events on specific host"
```

### 2. OOM Events on a Host

Check if the kernel OOM killer was invoked on a host.

```bash
meta scuba.dataset query -d netcons_alarms --view=samples -c time,hostname,msg,description --filter-sql="hostname = 'your-hostname.facebook.com' AND (msg LIKE '%oom%' OR msg LIKE '%OOM%' OR alert LIKE '%oom%')" --hours=24 -r "OOM events on host"
```

### 3. Kernel Panics in a Cluster

Find hosts with kernel panics in a datacenter/cluster.

```bash
meta scuba.dataset query -d netcons_alarms --view=samples -c time,hostname,alert,msg,rack_location --filter-sql="datacenter = 'your_datacenter' AND (msg LIKE '%panic%' OR alert LIKE '%panic%')" --hours=24 -r "Kernel panics in cluster"
```

### 4. Alert Distribution on a Host

See what types of kernel events are happening on a host.

```bash
meta scuba.dataset query -d netcons_alarms -a count -g alert -w '[{"column":"hostname","op":"eq","values":["your-hostname.facebook.com"]}]' --hours=24 -r "Alert distribution on host"
```

### 5. Hardware Events in a Rack

Check for hardware issues in a specific rack (useful when multiple tasks crash in the same location).

```bash
meta scuba.dataset query -d netcons_alarms -a count -g hostname,alert,msg -w '[{"column":"rack_location","op":"eq","values":["your_rack_location"]}]' --hours=24 -r "Hardware events in rack"
```

---

## Tips

1. **This is a cross-infra dataset:** Not Tupperware-specific. It covers all kernel events on all hosts.

2. **Use for OOM root cause:** When `tupperware_crashes` shows exit_code=137 (SIGKILL), check this dataset for kernel OOM messages to confirm it was the OOM killer.

3. **Check host maintenance status:** The `maint` and `oos` columns show if the host is under maintenance or out of service, which can explain task evictions.

4. **Use 24-hour windows:** Kernel events are less frequent than task events. Use `now()-86400` instead of `now()-3600`.

5. **Combine with tupperware_crashes:** Find the hostname from `tupperware_crashes`, then check `netcons_alarms` for the same hostname and time window to identify kernel-level root causes.

6. **Rack-level correlation:** If crashes are concentrated in a rack (found via `tupperware_crashes`), query this dataset by `rack_location` to check for shared hardware issues.
