# Tupperware Job Request History Scuba Dataset

**Purpose:** Tracks all operations performed on Tupperware jobs -- who initiated them, what operation was performed, the reason, and trace IDs for debugging. Use this dataset to audit job update history, identify who changed a job, and correlate operations with trace IDs for deeper investigation.

**Scuba Table:** `tupperware_job_request_history`

**Scuba UI:** https://www.internalfb.com/intern/scuba/query/?pool=uber&dataset=tupperware_job_request_history

**Related Datasets:**
- `tupperware_task_events` - For task-level state changes resulting from job operations
- `tupperware_job_events` - For job-level operation errors
- `tupperware_task_control_operations` - For pending task operations and rate limiting
- `tw_cli_usage` - For tracking who ran `tw update` and other CLI commands

---

## How to Get Schema

```bash
meta scuba.dataset query -d tupperware_job_request_history --limit=5 -r "Sample data to view schema"
```

### Key Columns

| Column | Type | Description |
|--------|------|-------------|
| `time` | bigint | Unix timestamp of the operation |
| `cluster_id` | string | Cluster where the operation was performed |
| `method_name` | string | Operation type (e.g., `updateJobWithRequestV2`, `startJobWithRequestV2`, `stopJob`, `deleteJob`, `resolveSmcTier`, `getJobStatus`) |
| `job` | string | Full job handle (e.g., `tsp_prn/team/service.prod`) |
| `job_state` | string | Job state at time of operation (e.g., `JOB_STATE_RUNNING`) |
| `task_ids` | string | Task IDs affected by the operation (if applicable) |
| `reason` | string | Human-readable reason for the operation |
| `who` | string | Identity of who/what initiated the operation (user, service, or automation) |
| `request` | string | Serialized Thrift request payload with operation details |
| `response` | string | Serialized response payload |
| `client_id` | string | Client identifier (e.g., `scheduler_proxy`, `user_cli`) |
| `processing_time` | bigint | Server-side processing time |
| `exception` | string | Exception details if the operation failed |

---

## Common Queries

### 1. Job Update History

See all operations performed on a specific job to understand its change history.

```bash
meta scuba.dataset query -d tupperware_job_request_history --view=samples -c time,method_name,who,reason,task_ids,cluster_id,job_state -w '[{"column":"job","op":"eq","values":["tsp_prn/team/service.prod"]}]' --hours=24 -r "Job update history"
```

### 2. Who Changed a Job

Identify who made changes to a job -- useful when a job was unexpectedly updated or stopped.

```bash
meta scuba.dataset query -d tupperware_job_request_history --view=samples -c time,method_name,who,reason,client_id -w '[{"column":"job","op":"eq","values":["tsp_prn/team/service.prod"]},{"column":"method_name","op":"eq","values":["updateJobWithRequestV2"]}]' --hours=24 -r "Who changed this job"
```

### 3. Pause/Resume Events

Find pause and resume operations on a job -- useful for debugging stuck updates where JCP paused the update.

```bash
meta scuba.dataset query -d tupperware_job_request_history --view=samples -c time,method_name,who,reason,client_id -w '[{"column":"job","op":"eq","values":["tsp_prn/team/service.prod"]},{"column":"method_name","op":"eq","values":["pauseUpdateWithRequest","resumeUpdateWithRequest"]}]' --hours=24 -r "Pause and resume events for job"
```

### 4. Update Operations for a Job

Find update operations for a job -- useful for understanding update patterns and frequency.

```bash
meta scuba.dataset query -d tupperware_job_request_history --view=samples -c time,job,method_name,who,reason,task_ids,cluster_id -w '[{"column":"job","op":"eq","values":["tsp_prn/team/service.prod"]},{"column":"method_name","op":"eq","values":["updateJobWithRequestV2"]}]' --hours=24 -r "Update operations for a job"
```

### 5. Recent Stop/Delete Operations

Find jobs that were recently stopped or deleted, useful during incident investigation.

```bash
meta scuba.dataset query -d tupperware_job_request_history --view=samples -c time,job,method_name,who,reason,client_id -w '[{"column":"method_name","op":"eq","values":["stopJob","deleteJob"]}]' --hours=1 -r "Recent stop and delete operations"
```

### 6. Operation Frequency by Type

See which operations are most common for a job -- helps identify if a job is being churned by automation.

```bash
meta scuba.dataset query -d tupperware_job_request_history -a count -g method_name,who -w '[{"column":"job","op":"eq","values":["tsp_prn/team/service.prod"]}]' --hours=24 -r "Operation frequency by type for job"
```

### 7. All Operations by a Specific User/Service

Find all job operations performed by a specific user or service identity.

```bash
meta scuba.dataset query -d tupperware_job_request_history --view=samples -c time,job,method_name,reason,client_id --filter-sql="who LIKE '%username%'" --hours=24 -r "All operations by specific user"
```

---

## Tips

1. **Use `who` to distinguish human vs automation:** The `who` column shows the identity that initiated the operation. Human users appear as unix usernames; automated systems (Conveyor, ShardManager, autoscaling) appear as service identities.

2. **Check `pauseUpdateWithRequest` for stuck updates:** When an update appears stuck, look for `pauseUpdateWithRequest` in the `method_name` column -- these show that something (Conveyor, SRMWWWHooks, a user resize) explicitly paused the update.

3. **`client_id` identifies the caller:** The `client_id` column shows which client made the request (e.g., `scheduler_proxy`, `user_cli`). Combined with `who`, this gives a complete audit trail.

4. **The `request` column contains full details:** The `request` column has the serialized request payload. It is verbose but contains the complete operation details including spec changes.

5. **No host filter needed:** Unlike `tw_task_packages`, this dataset does not require runtime host context. Filter by `job` handle or `trace_id`.

6. **Combine with `tw_cli_usage`:** For a complete audit trail, query both `tupperware_job_request_history` (server-side operation log) and `tw_cli_usage` (client-side CLI invocation log) with the same time window and job handle.

7. **Start with 24-hour window:** This dataset has lower volume than task-level datasets, so 24-hour queries are usually fast and appropriate for audit/investigation.
