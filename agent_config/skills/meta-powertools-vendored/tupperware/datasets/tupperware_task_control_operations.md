# tupperware_task_control_operations

Tracks task control operations — restarts, grows, kills, advance notices, and unavailability events — and whether the task controller approved or rejected them.

## When to Use

- Debugging why an update is paused or slow — check if the task controller is blocking operations
- Investigating task controller behavior — what operations were requested and their status
- Distinguishing between task controller rejections vs JCP-level pauses

## Schema

| Column | Type | Description |
|--------|------|-------------|
| `time` | bigint | Event timestamp (epoch seconds) |
| `job` | string | Job handle (e.g., `tsp_vcn/instagram/c2.web`) |
| `taskID` | bigint | Task ID within the job |
| `operationType` | string | Type of operation: `UPDATE`, `ADVANCE_NOTICE`, `UNAVAILABILITY_EVENT`, `GROW` |
| `operationStatus` | string | Status of the operation: `ALLOWED` (approved by TC), `REQUESTED` (pending TC approval) |
| `operationID` | string | Unique operation identifier |
| `opMode` | string | Operation mode: `TASK_CONTROL_NORMAL`, `0` |
| `operation` | string | Operation details |
| `taskControllerStatus` | string | Task controller's response status (often null when TC approves silently) |
| `taskControllerStatusReason` | string | Reason for TC approval/rejection (often null) |
| `taskAction` | string | The specific action taken on the task |
| `allowedReason` | string | Why the operation was allowed |
| `healthStatus` | string | Health status of the task at operation time |
| `smcTier` | string | SMC tier associated with the task |
| `taskHost` | string | Host running the task |
| `host` | string | Scheduler host processing the operation |
| `cluster_id` | string | Cluster identifier |
| `shard_id` | string | Scheduler shard identifier |
| `service` | string | Service name |
| `requestID` | string | Request identifier |
| `requestNum` | string | Request sequence number |
| `stopMode` | string | Stop mode if applicable |

## Key Queries

### Check if task controller is blocking an update
```sql
SELECT time, operationType, operationStatus, taskID,
       taskControllerStatus, taskControllerStatusReason
FROM tupperware_task_control_operations
WHERE job = '<job_handle>'
ORDER BY time DESC LIMIT 30
```
- If all `operationStatus = ALLOWED`: task controller is NOT blocking — check JCP for the pause cause
- If `operationStatus = REQUESTED` persists: task controller hasn't approved yet — it's the blocker

### Find task controller rejections
```sql
SELECT time, operationType, operationStatus, taskID,
       taskControllerStatus, taskControllerStatusReason
FROM tupperware_task_control_operations
WHERE job = '<job_handle>'
  AND taskControllerStatus IS NOT NULL
ORDER BY time DESC LIMIT 20
```

### Track operation types over time
```sql
SELECT operationType, operationStatus, COUNT(*) as cnt
FROM tupperware_task_control_operations
WHERE job = '<job_handle>'
GROUP BY operationType, operationStatus
```

## Interpreting Results

| Pattern | Meaning |
|---------|---------|
| All `UPDATE` ops are `ALLOWED`, but job still paused | JCP paused the update (not TC) — check `tupperware_job_request_history` for `pauseUpdateWithRequest` |
| `REQUESTED` ops stuck for minutes | Task controller is not approving — it may be rate-limiting or the TC service is down |
| `taskControllerStatusReason` has a message | TC explicitly rejected with a reason — address the reason |
| Many `ADVANCE_NOTICE` ops with `REQUESTED` | TC is being notified of upcoming operations — normal pre-flight behavior |
| `UNAVAILABILITY_EVENT` ops | Maintenance or host issues triggering task moves through the TC |
| `GROW` ops with `REQUESTED` | Job is scaling up but TC hasn't approved the new tasks yet |
