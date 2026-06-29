# Tupperware Task Events Scuba Dataset

**Purpose:** The primary dataset for debugging task-level issues in Tupperware. Logs task state transitions, container lifecycle events, exit reports, and container creation failures. Data comes from both the scheduler and the TW agent.

**Scuba Table:** `tupperware_task_events`

**Scuba UI:** https://www.internalfb.com/intern/scuba/query/?pool=uber&dataset=tupperware_task_events

**Related Datasets:**
- `tupperware_crashes` - For crash details (exit messages, signal codes)
- `tupperware_health_check_results` - For health check pass/fail results
- `tupperware_job_events` - For job-level operation errors

---

## How to Get Schema

```bash
meta scuba.dataset query -d tupperware_task_events --limit=5 -r "Sample data to view schema"
```

### Key Columns

| Column | Type | Description |
|--------|------|-------------|
| `time` | bigint | Unix timestamp of the event |
| `job` | string | Full job handle (e.g., `tsp_prn/team/service.prod`) |
| `task` | string | Full task handle (job handle + `/instance_id`) |
| `event_name` | string | Event type (see below) |
| `event_detail` | string | Human-readable event description |
| `task_state` | string | Current task state after the event |
| `prev_state` | string | Task state before the event |
| `prev_state_duration_s` | bigint | Duration in seconds the task was in the previous state |
| `hostname` | string | Host machine running the task |
| `exit_code` | bigint | Process exit code (0 = clean, 137 = SIGKILL/OOM, 134 = SIGABRT) |
| `signal_code` | bigint | Signal that terminated the process (9 = SIGKILL, 6 = SIGABRT) |
| `transition_type` | string | What caused the state change (see below) |
| `container_state` | string | Container lifecycle state |
| `prev_container_state` | string | Previous container state |
| `info` | string | Additional context (often contains last log lines) |
| `cluster` | string | Cluster name |
| `tw_user` | string | Job owner |
| `oncall_team` | string | Oncall team responsible |
| `initiator` | string | Who/what triggered the event |

### Event Names

| event_name | Description |
|------------|-------------|
| `STATE_CHANGE` | Task state transition (most common useful event) |
| `CONTAINER_STATE_CHANGE` | Container lifecycle transition |
| `TASK_EXIT_REPORT` | Task exited — check exit_code and signal_code |
| `DESIRED_STATE_CHANGE` | Scheduler changed desired state |
| `DELETE_TASK_OBJECT_MATCH` | Task object cleaned up |
| `CONTAINER_DESTROYED_BEFORE_CREATION` | Container destroyed before it was fully created |
| `create container failure` | Container creation failed |
| `TICK` | Periodic heartbeat (high volume, filter out) |

### Task States

| task_state | Description |
|------------|-------------|
| `TASK_STATE_UNINITIALIZED` | Task created but not yet assigned |
| `TASK_STATE_STAGING` | Packages being staged on host |
| `TASK_STATE_RUNNING` | Task is running |
| `TASK_STATE_RUNNING_NOT_HEALTHY` | Running but failing health checks |
| `TASK_STATE_STOPPED` | Task stopped normally |
| `TASK_STATE_COMPLETED` | Task completed successfully |

### Transition Types

| transition_type | Description |
|-----------------|-------------|
| `ASSIGN_ALLOCATION` | Task allocated to a host |
| `MACHINE_RESERVED` | Host reserved for the task |
| `ALLOCATION_CONFIRMED` | Allocation confirmed |
| `CONTAINER_DESIRE_TO_START` | Container starting |
| `CONTAINER_CREATED` | Container running |
| `TASK_RUN` | Task binary started |
| `DESIRE_TO_STOP` | Stop requested |
| `CONTAINER_STOPPED` | Container stopped |
| `CONTAINER_DESTROYED` | Container cleaned up |
| `TASK_DEALLOCATED` | Task deallocated from host |
| `TASK_COMPLETED` | Task finished successfully |
| `TASK_FAILED` | Task failed |

---

## Common Queries

### 1. Task Lifecycle Timeline

See the full lifecycle of a specific task — state transitions, exit codes, and which hosts it ran on.

```bash
meta scuba.dataset query -d tupperware_task_events --view=samples -c time,event_name,event_detail,task_state,prev_state,prev_state_duration_s,transition_type,hostname,exit_code,signal_code -w '[{"column":"task","op":"eq","values":["your/job/handle/0"]},{"column":"event_name","op":"ne","values":["TICK"]}]' --hours=24 -r "Task lifecycle timeline"
```

### 2. Recent Task Failures for a Job

Find tasks that failed or crashed in a job within the last hour.

```bash
meta scuba.dataset query -d tupperware_task_events --view=samples -c time,task,event_detail,exit_code,signal_code,hostname,prev_state_duration_s --filter-sql="job LIKE '%your/job/handle%' AND event_name = 'TASK_EXIT_REPORT'" --hours=1 -r "Recent task failures for job"
```

### 3. OOM Kills (exit_code 137 / signal 9)

Find tasks killed by OOM killer across all jobs for a team.

```bash
meta scuba.dataset query -d tupperware_task_events --view=samples -c time,job,task,hostname,prev_state_duration_s,event_detail -w '[{"column":"event_name","op":"eq","values":["TASK_EXIT_REPORT"]},{"column":"exit_code","op":"eq","values":[137]},{"column":"oncall_team","op":"eq","values":["your_oncall_team"]}]' --hours=1 -r "OOM kills for team"
```

### 4. Container Creation Failures

Find tasks that failed to create containers (staging/setup issues).

```bash
meta scuba.dataset query -d tupperware_task_events --view=samples -c time,job,task,event_detail,hostname,info -w '[{"column":"event_name","op":"eq","values":["create container failure"]}]' --hours=1 -r "Container creation failures"
```

### 5. Task Crash Rate by Job

See which jobs have the highest task crash rate.

```bash
meta scuba.dataset query -d tupperware_task_events -a count -g job --filter-sql="event_name = 'TASK_EXIT_REPORT' AND exit_code != 0" --hours=1 -r "Task crash rate by job"
```

### 6. State Transitions on a Host

See all task events on a specific host to debug host-level issues.

```bash
meta scuba.dataset query -d tupperware_task_events --view=samples -c time,job,task,event_name,task_state,prev_state,exit_code,signal_code -w '[{"column":"hostname","op":"eq","values":["your-hostname.facebook.com"]},{"column":"event_name","op":"in","values":["STATE_CHANGE","TASK_EXIT_REPORT"]}]' --hours=1 -r "State transitions on host"
```

---

## Tips

1. **Always filter out TICK events:** `event_name != 'TICK'` — TICK events are periodic heartbeats and account for the majority of rows.

2. **Use TASK_EXIT_REPORT for crash analysis:** This event_name has populated exit_code and signal_code. Common patterns:
   - `exit_code=137, signal_code=9` → OOM kill (SIGKILL)
   - `exit_code=134, signal_code=6` → SIGABRT (assertion failure, crash)
   - `exit_code=1, signal_code=0` → Application error
   - `exit_code=0` → Clean exit

3. **Check prev_state_duration_s:** Shows how long a task was in its previous state before the event. Short durations on RUNNING→STOPPED transitions indicate crashes.

4. **Use job handle substring matching:** `strpos(job, 'team/service') > 0` is more flexible than exact match and handles shard/region prefixes.

5. **Start with 1-hour window:** This dataset is high-volume. Use `time >= now()-3600` initially, expand only if needed.

6. **Combine with tupperware_crashes:** For crash debugging, use this dataset for the timeline, then check `tupperware_crashes` for detailed exit messages.
