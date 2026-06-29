# Allocator Machine Tag Change Scuba Dataset

**Purpose:** Tracks every machine tag change made by the TW allocator -- status changes, maintenance status transitions, device enable/disable, and other tag mutations. Use this dataset to understand host-level state changes that affect task allocation and scheduling.

**Scuba Table:** `tw_allocatorv2_machine_tag_change`

**Scuba UI:** https://www.internalfb.com/intern/scuba/query/?pool=uber&dataset=tw_allocatorv2_machine_tag_change

**Related Datasets:**
- `tupperware_task_events` - Task-level state transitions (correlate with host changes)
- `tw_allocator_v2_allocation_failures` - Allocation failures that may result from tag changes
- `fbar` - Automated remediation actions that trigger tag changes
- `fleet_health` - Overall host health and availability status

---

## How to Get Schema

```bash
meta scuba.dataset query -d tw_allocatorv2_machine_tag_change --limit=5 -r "Sample data to view schema"
```

### Key Columns

| Column | Type | Description |
|--------|------|-------------|
| `time` | bigint | Unix timestamp of the tag change event |
| `action` | string | Type of change: `add_value`, `remove_value`, or `replace_values` |
| `host` | string | Allocator host that processed the change |
| `cluster_id` | string | Cluster where the machine resides |
| `machine` | string | FQDN of the machine whose tag was changed |
| `reason_comments` | string | Human-readable explanation for the change |
| `reason_task` | string | Task handle that triggered the change (empty if not task-related) |
| `reason_type` | string | Category of the reason for the change |
| `tag` | string | Tag name that was changed (e.g., `status`, `maintenance_status`) |
| `value` | string | New value set for the tag |
| `prev_values` | string | Previous tag values (Python list literal, e.g., `['0']`) |
| `new_values` | string | New tag values after the change (Python list literal) |

### Tag Names

| tag | Description |
|-----|-------------|
| `status` | Machine DeviceStatus (values map to `DeviceStatus` enum: e.g., LIVE, DEAD, INTRANSIT) |
| `maintenance_status` | Machine MaintenanceStatus (values map to `MaintenanceStatus` enum) |

### Action Types

| action | Description |
|--------|-------------|
| `add_value` | A new value was added to the tag |
| `remove_value` | A value was removed from the tag |
| `replace_values` | All existing values were replaced (set or unset) |

---

## Common Queries

### 1. Tag Changes for a Specific Machine

See all tag changes for a host to understand its state history.

```bash
meta scuba.dataset query -d tw_allocatorv2_machine_tag_change --view=samples -c time,action,tag,value,prev_values,new_values,reason_type,reason_comments,reason_task -w '[{"column":"machine","op":"eq","values":["your-hostname.facebook.com"]}]' --hours=24 -r "Tag changes for machine"
```

### 2. Tag Changes Related to a Job

Find tag changes triggered by or affecting a specific job handle.

```bash
meta scuba.dataset query -d tw_allocatorv2_machine_tag_change --view=samples -c time,action,machine,tag,value,reason_comments,reason_task -w '[{"column":"reason_task","op":"regeq","values":[".*your/job/handle.*"]}]' --hours=24 -r "Tag changes for job"
```

### 3. Status Changes Across a Cluster

See all device status changes in a cluster to identify widespread issues.

```bash
meta scuba.dataset query -d tw_allocatorv2_machine_tag_change --view=samples -c time,machine,action,value,prev_values,reason_type,reason_comments -w '[{"column":"cluster_id","op":"eq","values":["your_cluster"]},{"column":"tag","op":"eq","values":["status"]}]' --hours=24 -r "Status changes in cluster"
```

### 4. Maintenance Status Transitions

Track machines entering or exiting maintenance status.

```bash
meta scuba.dataset query -d tw_allocatorv2_machine_tag_change --view=samples -c time,machine,action,value,prev_values,reason_type,reason_comments -w '[{"column":"tag","op":"eq","values":["maintenance_status"]}]' --hours=1 -r "Maintenance status transitions"
```

### 5. Tag Change Distribution by Action Type

See the volume and distribution of tag change actions over time.

```bash
meta scuba.dataset query -d tw_allocatorv2_machine_tag_change -a count -g action,tag --hours=1 -r "Tag change distribution"
```

### 6. Tag Changes for Multiple Hosts (Runtime Hosts)

When debugging a job, check tag changes across all hosts where it ran.

```bash
meta scuba.dataset query -d tw_allocatorv2_machine_tag_change --view=samples -c time,machine,action,tag,value,reason_type,reason_comments,reason_task -w '[{"column":"machine","op":"eq","values":["host1.facebook.com","host2.facebook.com"]}]' --hours=24 -r "Tag changes for runtime hosts"
```

---

## Tips

1. **Requires host information:** To debug a specific job, first query `tupperware_task_events` to find which hosts the job ran on, then query this dataset for those hosts.

2. **Status values are integers:** The `value` column for `status` and `maintenance_status` tags contains integer enum values (e.g., `0`, `1`). Map them using `DeviceStatus` and `MaintenanceStatus` enums from the TW codebase.

3. **`reason_task` format:** Task handles appear as `job_handle/instance_id` and may include `JobQDisp` prefixes (e.g., `JobQDisp123 tsp_prn/team/service/0 JobQDisp456`). Use regex matching when filtering.

4. **Correlate with task events:** Combine with `tupperware_task_events` to see how a machine tag change (e.g., entering maintenance) affected tasks running on that host.

5. **`prev_values` and `new_values` are string representations of Python sets:** Parse them carefully -- they look like `{'value1', 'value2'}` or `set()`.
