# FBAR (Facebook Automated Remediation) Scuba Dataset

**Purpose:** Tracks automated remediation actions taken by FBAR on hosts -- reboots, repairs (online/offline), power-off, production enable/disable. Use this dataset to understand what automated actions were taken on hosts that may have affected running tasks.

**Scuba Table:** `fbar_log`

> **Note:** The legacy `fbar` table is deprecated. Use `fbar_log` instead. See https://fburl.com/31m9cmze for details.

**Scuba UI:** https://www.internalfb.com/intern/scuba/query/?pool=uber&dataset=fbar_log

**Related Datasets:**
- `tupperware_task_events` - Task-level events to correlate with FBAR actions on a host
- `tw_allocatorv2_machine_tag_change` - Allocator tag changes that may be triggered by FBAR actions
- `tupperware_crashes` - Crash details for tasks affected by FBAR actions

---

## How to Get Schema

```bash
meta scuba.dataset schema -d fbar_log
meta scuba.dataset query -d fbar_log --limit=5 --view=samples -c time,host_name,alert_name,event,event_type,entity,remediation_module -r "Sample data to view schema"
```

### Key Columns

| Column | Type | Description |
|--------|------|-------------|
| `time` | bigint | Unix timestamp of the FBAR event |
| `host_name` | string | FQDN of the host running the FBAR remediation |
| `entity` | string | Target entity (host) being remediated |
| `alert_name` | string | Name of the alarm that triggered the remediation |
| `event` | string | Event that occurred (e.g., `remediation`, `alarm_rate_limited`) |
| `event_type` | string | Event type classification (e.g., `info_message`, `warn_message`, `alarm_rate_limited`) |
| `remediation_module` | string | The remediation module FBAR invoked (e.g., `os.remediations.NetworkChangeNeeded`) |
| `tw_job_handle` | string | Tupperware job handle associated with the entity |
| `tw_task_id` | int | Tupperware task ID associated with the entity |
| `oncall` | string | Oncall team for the entity |
| `details` | tagset | Detailed description of the remediation event (use `any`/`all`/`none` operators for filtering) |
| `exception_message` | string | Exception message if the remediation encountered an error |
| `is_keypoint` | bigint | Whether this is a key step in the FBAR flow (1 = important) |
| `fbje_id` | int | FBJE (Facebook Job Engine) ID for the remediation job |
| `weight` | bigint | Sampling weight of the record |

---

## Common Queries

### 1. FBAR Events for a Specific Host

See all remediation actions taken on a host to understand its recent history.

```bash
meta scuba.dataset query -d fbar_log --view=samples -c time,host_name,entity,alert_name,event,event_type,remediation_module,details -w '[{"column":"entity","op":"eq","values":["your-hostname.facebook.com"]}]' --hours=24 -r "FBAR events for host"
```

### 2. Key Remediation Events Only

Filter to important steps (keypoints) to reduce noise from informational messages.

```bash
meta scuba.dataset query -d fbar_log --view=samples -c time,entity,alert_name,event,remediation_module,details -w '[{"column":"entity","op":"eq","values":["your-hostname.facebook.com"]},{"column":"is_keypoint","op":"eq","values":[1]}]' --hours=24 -r "FBAR keypoint events for host"
```

### 3. FBAR Events Affecting a Tupperware Job

Find FBAR actions on hosts running a specific TW job.

```bash
meta scuba.dataset query -d fbar_log --view=samples -c time,entity,alert_name,event,event_type,remediation_module,details -w '[{"column":"tw_job_handle","op":"eq","values":["tsp_prn/team/service.prod"]}]' --hours=24 -r "FBAR events for TW job"
```

### 4. Remediation Distribution by Alert

See which alerts are triggering the most remediations.

```bash
meta scuba.dataset query -d fbar_log -a count -g alert_name,event -w '[{"column":"is_keypoint","op":"eq","values":[1]}]' --hours=1 -r "FBAR remediation distribution by alert"
```

### 5. Remediation Exceptions and Errors

Find FBAR events that encountered exceptions or warnings.

```bash
meta scuba.dataset query -d fbar_log --view=samples -c time,entity,alert_name,remediation_module,exception_message -w '[{"column":"event_type","op":"eq","values":["warn_message","error_message"]}]' --hours=1 -r "FBAR remediation exceptions"
```

### 6. FBAR Events Across Multiple Hosts

When debugging a job, check FBAR actions on all hosts where tasks ran.

```bash
meta scuba.dataset query -d fbar_log --view=samples -c time,entity,alert_name,event,remediation_module,details -w '[{"column":"entity","op":"eq","values":["host1.facebook.com","host2.facebook.com"]},{"column":"is_keypoint","op":"eq","values":[1]}]' --hours=24 -r "FBAR events for runtime hosts"
```

---

## Tips

1. **Use `fbar_log`, not `fbar`:** The legacy `fbar` dataset is deprecated with no queryable columns via the meta CLI. Always use `fbar_log` for manual Scuba queries.

2. **Filter by `is_keypoint=1`:** FBAR logs many informational messages. Filtering to keypoints focuses on the important remediation steps.

3. **`entity` vs `host_name`:** The `entity` column is the host being remediated. The `host_name` column is the host running the FBAR agent (which may be different). Filter by `entity` to find remediations targeting a specific host.

4. **Correlate with task restarts:** When tasks restart unexpectedly, check FBAR events on the same host around the same time. A host reboot or prod_disable by FBAR explains why tasks were killed.

5. **`tw_job_handle` for job correlation:** Use the `tw_job_handle` column to find FBAR events that affected hosts running a specific Tupperware job.

6. **`details` column has diagnostic info:** The `details` column contains the most useful freeform text about what FBAR found and did.
