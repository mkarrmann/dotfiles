# Tupperware Health Check Results Scuba Dataset

**Purpose:** Debug health check failures for Tupperware tasks. Shows pass/fail results per task, the health check type (thrift, HTTP, TCP), port details, and actual response data including error messages. Essential for diagnosing why tasks are stuck in `RUNNING_NOT_HEALTHY` state.

**Scuba Table:** `tupperware_health_check_results`

**Scuba UI:** https://www.internalfb.com/intern/scuba/query/?pool=uber&dataset=tupperware_health_check_results

**Related Datasets:**
- `tupperware_task_events` - For task state transitions (RUNNING_NOT_HEALTHY)
- `tupperware_crashes` - If the task is crashing before health checks pass

---

## How to Get Schema

```bash
meta scuba.dataset query -d tupperware_health_check_results --limit=5 -r "Sample data to view schema"
```

### Key Columns

| Column | Type | Description |
|--------|------|-------------|
| `time` | bigint | Unix timestamp of the health check |
| `job` | string | Full job handle |
| `task` | string | Full task handle |
| `success` | string | `1` for pass, `0` for fail |
| `hostname` | string | Host where the task is running |
| `port_name` | string | Named port being checked (e.g., `thrift`, `http`, `admin`) |
| `port_type` | string | Port type enum (e.g., `PORT_TYPE_THRIFT`, `PORT_TYPE_HTTP`) |
| `port_data` | string | Port number or additional port config |
| `results` | array\<string\> | JSON array with detailed check results including error messages |
| `expected_regex` | string | Expected response pattern for regex-based health checks |
| `health_check_timeout_seconds` | string | Configured timeout |
| `health_check_timeout_milliseconds` | string | Configured timeout in ms |
| `sample_rate` | string | Sampling rate for this data |
| `cluster` | string | Cluster name |
| `tw_user` | string | Job owner |
| `oncall_team` | string | Oncall team |
| `agent_version` | string | TW agent version |

### Results JSON Format

The `results` column contains a JSON array where each entry has:
- `networkEndpoint.port` — Port number
- `networkEndpoint.ip` — IP address
- `securityMech` — Security mechanism (1=plaintext, 2=TLS)
- `output` — Error message or response
- `code` — Result code (0=success, 2=connection error, 3=transport error)

---

## Common Queries

### 1. Health Check Failures for a Specific Task

See why a specific task is failing health checks.

```bash
meta scuba.dataset query -d tupperware_health_check_results --view=samples -c time,success,port_name,port_type,results -w '[{"column":"task","op":"eq","values":["your/job/handle/0"]},{"column":"success","op":"eq","values":["0"]}]' --hours=1 -r "Health check failures for specific task"
```

### 2. Health Check Failure Rate for a Job

See overall health check pass/fail ratio across all tasks in a job.

```bash
meta scuba.dataset query -d tupperware_health_check_results -a count -g success,port_name --filter-sql="job LIKE '%your/job/handle%'" --hours=1 -r "Health check failure rate for job"
```

### 3. Health Check Failures by Port Type

Understand which health check types are failing (thrift vs HTTP vs TCP).

```bash
meta scuba.dataset query -d tupperware_health_check_results -a count -g port_name,port_type,success --filter-sql="job LIKE '%your/job/handle%'" --hours=1 -r "Health check failures by port type"
```

### 4. Tasks Failing Health Checks on a Host

Check if a host has widespread health check failures (network issue).

```bash
meta scuba.dataset query -d tupperware_health_check_results -a count -g job,task,port_name -w '[{"column":"hostname","op":"eq","values":["your-hostname.facebook.com"]},{"column":"success","op":"eq","values":["0"]}]' --hours=1 -r "Tasks failing health checks on host"
```

### 5. Health Check Error Messages for a Job

Extract the actual error messages from failed health checks.

```bash
meta scuba.dataset query -d tupperware_health_check_results --view=samples -c time,task,port_name,hostname,results --filter-sql="job LIKE '%your/job/handle%' AND success = '0'" --hours=1 -r "Health check error messages for job"
```

---

## Tips

1. **Use `--format vertical` for results column:** The `results` column contains long JSON strings. Use `--format vertical` to read error messages clearly.

2. **Common failure patterns in results:**
   - `Connection refused (errno 111)` → Service not listening yet (still starting up)
   - `Connection reset by peer (errno 104)` → Service crashed or restarted during check
   - `Timed out` → Service is overloaded or hung
   - `SSL handshake failed` → TLS configuration issue

3. **success is a string:** Filter with `success = '0'` (string), not `success = 0` (integer).

4. **Health checks vs task state:** A task in `TASK_STATE_RUNNING_NOT_HEALTHY` state means health checks are failing. Use this dataset to find out why.

5. **Check both securityMech values:** Health checks are typically attempted with both TLS (securityMech=2) and plaintext (securityMech=1). If TLS works but plaintext doesn't, it's expected. If both fail, the service is truly unhealthy.

6. **Combine with tupperware_task_events:** Check if the task recently restarted — health check failures right after a restart are often transient while the service initializes.
