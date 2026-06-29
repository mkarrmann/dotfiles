# Tupperware Scheduler Actions Scuba Dataset

**Purpose:** Understand WHY the scheduler makes specific decisions during job updates. Records every action the scheduler takes including action type, reason, affected tasks, deployment health status (healthy/unhealthy task counts), step size, cancellation thresholds, and trace IDs.

**Scuba Table:** `tupperware_scheduler_actions`

**Scuba UI:** https://www.internalfb.com/intern/scuba/query/?pool=uber&dataset=tupperware_scheduler_actions

**Related Datasets:**
- `tupperware_task_control_operations` - Task control operations requested by the scheduler and their approval status
- `tupperware_job_request_history` - Job-level request history (start/update/delete)
- `tupperware_job_events` - Cross-system job events



---

## How to Get Schema

```bash
meta scuba.dataset query -d tupperware_scheduler_actions --limit=5 -r "Sample data to view schema"
```

### Key Columns

| Column | Type | Description |
|--------|------|-------------|
| `time` | int | Timestamp of the action |
| `job` | string | Job handle (e.g., `tsp_global/account/job_name`) |
| `action_type` | string | What the scheduler decided to do (e.g., `ACTION_UPDATE_TASK`, `ACTION_TASK_OPERATION_MANUALLY_ACKED`) |
| `reason_type` | string | Why the scheduler took this action |
| `tasks` | string | Set of affected task IDs |
| `pre_update_size` | int | Job size before the update |
| `post_update_size` | int | Job size after the update |
| `step_size` | int | Number of tasks updated per step |
| `randomize` | int | Whether task update order is randomized (0 = no, 1 = yes) |
| `restart_period_ms` | int | Restart period in milliseconds |
| `bad_update_threshold_size` | int | Threshold for marking an update as bad |
| `num_updated_healthy_tasks` | int | Count of tasks that have been updated and are healthy |
| `updated_healthy_tasks` | string | Set of task IDs that are updated and healthy |
| `num_updated_not_healthy_tasks` | int | Count of tasks that have been updated but are NOT healthy |
| `updated_not_healthy_tasks` | string | Set of task IDs that are updated but not healthy |
| `num_not_updated_healthy_tasks` | int | Count of tasks not yet updated that are healthy |
| `not_updated_healthy_tasks` | string | Set of task IDs not yet updated that are healthy |
| `num_not_updated_not_healthy_tasks` | int | Count of tasks not yet updated that are NOT healthy |
| `not_updated_not_healthy_tasks` | string | Set of task IDs not yet updated and not healthy |
| `task_controller_tier` | string | Task controller tier governing the update |
| `context` | string | Free-form context string with detailed action info (e.g., manually acked operations, user info) |
| `cluster_id` | string | TW cluster identifier (e.g., `tsp_nha`) |
| `host` | string | Host running the scheduler |
| `shard_id` | string | Scheduler shard identifier |
| `num_pending_changes` | int | Number of pending changes |
| `service` | string | Service name (typically `Scheduler`) |
| `update_driver_version` | string | Update driver version (e.g., `v2`) |

---

## Common Queries

### 1. All Scheduler Actions for a Job (Last 24 Hours)

See every action the scheduler took during an update to understand pacing, decisions, and deployment health.

```bash
meta scuba.dataset query -d tupperware_scheduler_actions --view=samples \
  -c time,job,action_type,reason_type,tasks,step_size,context \
  -w '[{"column":"job","op":"eq","values":["<JOB_HANDLE>"]}]' \
  --hours=24 -r "Scheduler actions for job"
```

### 2. Check Deployment Health During Update

See how many tasks were healthy vs unhealthy at each step of the update to diagnose slow or stuck rollouts.

```bash
meta scuba.dataset query -d tupperware_scheduler_actions --view=samples \
  -c time,job,action_type,num_updated_healthy_tasks,num_updated_not_healthy_tasks,num_not_updated_healthy_tasks,num_not_updated_not_healthy_tasks,step_size \
  -w '[{"column":"job","op":"eq","values":["<JOB_HANDLE>"]}]' \
  --hours=24 -r "Deployment health during update"
```

### 3. Find Manually Acknowledged Task Control Operations

Identify when a user manually approved task control operations to unblock a stuck update.

```bash
meta scuba.dataset query -d tupperware_scheduler_actions --view=samples \
  -c time,job,action_type,context \
  --filter-sql="job = '<JOB_HANDLE>' AND context RLIKE 'ACTION_TASK_OPERATION_MANUALLY_ACKED'" \
  --hours=24 -r "Manually acked task control operations"
```

### 4. Actions by Type Distribution

Understand what types of scheduler actions are happening for a job.

```bash
meta scuba.dataset query -d tupperware_scheduler_actions -a count \
  -g action_type,reason_type \
  -w '[{"column":"job","op":"eq","values":["<JOB_HANDLE>"]}]' \
  --hours=24 -r "Action type distribution for job"
```

---

## Tips

1. **Use with `tupperware_task_control_operations` for full picture:** `tupperware_scheduler_actions` shows what the scheduler decided; `tupperware_task_control_operations` shows what was requested to and approved by the task controller. Together they explain stuck updates.

2. **The `context` column is rich:** It contains free-form text with details like manually acked operations, the user who approved them, and update driver (UDv2) context. Use `RLIKE` to search within it.

3. **Deployment health columns show update progress:** Compare `num_updated_healthy_tasks` vs `num_updated_not_healthy_tasks` to gauge whether updated tasks are coming up healthy. A high `num_updated_not_healthy_tasks` indicates the update may be producing unhealthy tasks.

4. **`bad_update_threshold_size`:** This shows the guardrail threshold. If `num_updated_not_healthy_tasks` exceeds `bad_update_threshold_size`, the scheduler may slow down or cancel the update.

5. **Filter by `action_type` for specific behaviors:** Use `action_type = 'ACTION_TASK_OPERATION_MANUALLY_ACKED'` to find manual interventions, or other action types to trace specific scheduler decisions.
