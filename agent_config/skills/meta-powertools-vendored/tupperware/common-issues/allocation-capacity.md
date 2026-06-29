# Allocation & Capacity

> 820 posts in TW Group FAQ | Primary Scuba: `tw_allocator_v2_allocation_failures` | Primary CLI: `rbcli`
>
> Wiki: [Allocation Issues](https://www.internalfb.com/wiki/Infra_Cloud/Service_Hosting/Tupperware/Tupperware_Help_and_Troubleshooting/Allocation_Issues/)

All allocation issues ultimately manifest as **pending tasks** — tasks that cannot be placed on a host. This workflow diagnoses why, classifies the root cause as **USER ERROR** (fix it yourself) or **INFRA ERROR** (oncall/wait), and provides resolution steps.

> **Data sources**: Allocator SPD pipeline (`AllocationFailuresSpdLogger::checkAndRecordAllocationFailures`), `AllocationFailureReason` enum in `common.thrift`, and community knowledge from 820 allocation-related posts in [TW Workplace group](https://fb.workplace.com/groups/1473492212957333)

## Debugging Workflow

### Step 1: Extract job handle

From user input (job handle, Workplace post URL, task handle, alert URL). Accepted formats:
- Job handle: `tsp_prn/team/job.name.prod`
- Task handle: `tsp_prn/team/job.name.prod/42` (strip the task index)
- Workplace post URL: load the post via `knowledge_load` and extract the job handle
- Alert URL: load the alert and extract the job handle from alert details
- If no job handle can be determined, **ask the user** before proceeding

### Step 2: Confirm pending tasks and gather initial state

```bash
tw resolve <job_handle>
```

Report the count (e.g., "3 of 10 tasks are pending, 7 allocated"). If no tasks are currently pending, the issue may have resolved — note this and query a broader Scuba time window in Step 3.

Also run:
```bash
tw allocation explain <job_handle>
```

Check allotment states for the reservation to see if any are stuck in CREATED (not yet usable):
```bash
rbcli search --target allotments_table \
    --match resource_materialization_id=<entitlement_uuid> \
    --match region=<region> \
    --group-by state
```
Allotments in CREATED state indicate the host hasn't completed setup (e.g., host profile migration blocked, Server Provisioner stuck with `ERR_INVALID_REQ_PROCESSING_ANOTHER_PROFILE`). This blocks the online update move → blocks `ALLOTMENT_UPDATE_RAS` → allotments stay CREATED → tasks can't allocate.

> **Interpreting `tw allocation explain` output**: When the output says "allotment only has X available", this means **remaining unused capacity** on that allotment, NOT the total allotment size. Stackable allotments can run multiple tasks, so the "available" amount is the total allotment size minus resources already consumed by other tasks on the same allotment. To determine the actual allotment size, check the reservation's capacity shape in Universal Search or Capacity Portal.

### Step 3: Get allocation failure data from Scuba

Query `tw_allocator_v2_allocation_failures` for the job:

```sql
SELECT failures, info, available_allotments, available_rrus, internal_error,
       used_rollout_features
FROM tw_allocator_v2_allocation_failures
WHERE job_handle = '<job_handle>'
LIMIT 10;
```

The `failures` JSON contains failure codes and human-readable messages:
```json
{"failureCounts":[
  {"failure":{"failureCode":94,"errorMessage":"Reservation capacity exhausted..."},"count":5},
  {"failure":{"failureCode":7,"errorMessage":"Excluded by exclusion lock..."},"count":3}
]}
```

To see failure distribution, use `meta scuba.dataset query` with structured mode (the `failure_reason` column does not exist — failure details are in the `failures` JSON column):
```bash
meta scuba.dataset query -d tw_allocator_v2_allocation_failures --view=samples -c job_handle,failures,available_allotments,available_rrus --hours=24 -w '[{"column":"job_handle","op":"eq","values":["<job_handle>"]}]' -l 20
```

If no results: tasks may be pending for non-allocation reasons (package fetch, health check). Check with `tw task-control show-status <job_handle>`.

### Step 4: Classify the root cause

Use the failure code from Step 3 to look up the root cause. See [Failure Code Decision Tree](#failure-code-decision-tree) below for the priority-ordered lookup. The allocator checks conditions in a fixed priority order — the **first match wins**.

If multiple failure codes are present, use the **primary one** based on priority order but list all observed codes.

### Step 5: Investigate and resolve

Follow the section for your failure code below. Each section includes targeted queries and resolution actions.

---

#### Zero Capacity (enums 90, 91)

**Bucket**: USER ERROR

**What's happening**: The reservation has 0 server count or 0 RRU configured. No hosts exist for task placement.

**Resolution**:
- Resize the reservation via [Capacity Portal](https://www.internalfb.com/intern/services/sah/) or `rbcli`
- Verify the reservation ID in the job spec is correct

---

#### No Hosts in SMC Tier (enum 92)

**Bucket**: USER ERROR

**What's happening**: The SMC tier specified in the job spec has no hosts assigned. Either the tier name is wrong or hosts haven't been assigned yet.

**Investigation**:
```bash
tw print <job_handle>  # check the SMC tier name in the spec
```

**"Not considered for allocation" message**: If the allocator reports "Host was not considered for allocation", check whether the job spec sets `smc_enabled_hosts_only` or `serf_enabled_hosts_only`. When these fields are set, you must verify that the candidate host is both present in the SMC tier AND enabled in SMC/SeRF. A host that is in the tier but disabled in SMC or SeRF will be silently skipped. See the [smc_enabled_hosts_only](https://www.internalfb.com/intern/wiki/Infra_Cloud/Service_Hosting/Tupperware/Tupperware_Reference/LanguageReference/Scheduling/#smc-enabled-hosts-only-o) and [serf_enabled_hosts_only](https://www.internalfb.com/intern/wiki/Infra_Cloud/Service_Hosting/Tupperware/Tupperware_Reference/LanguageReference/Scheduling/#serf-enabled-hosts-only) spec references for details.

**Resolution**:
- Verify the SMC tier name is correct
- If a new tier, hosts need to be assigned by the capacity team
- If the tier was recently created, it may take time for hosts to be provisioned
- If `smc_enabled_hosts_only` or `serf_enabled_hosts_only` is set, ensure the host is enabled in the corresponding system

---

#### Resource Limit Mismatch (enum 93)

**Bucket**: USER ERROR

**What's happening**: The task spec requests more resources (CPU, RAM, disk, etc.) than any single allotment can provide.

**Investigation**:
```bash
# Check what capacity shapes are available
rbcli search --target allotments \
    --match resource_materialization_id=<entitlement_uuid> \
    --match region=<region> \
    --group-by capacity_shape_name
```

**Colocation note**: If jobs are colocating on the same host, the total resource limit for all colocated jobs cannot exceed the machine limit. A resource limit mismatch may appear when individual job limits seem fine but their sum exceeds machine capacity.

**Common cause — CPU cores mismatch on edge hardware**:
Different hardware models have different specs (e.g., DL385G11_GEN_TRAFFIC has 32 cores, not 48). Verify actual hardware:
```bash
tw resolve <job_handle>  # see which hosts are allocated
serf get datacenter=<dc>,cluster=<cluster>,device_type=SERVER --fields=model
```
Note: `ResourceLimit.cpu=18` translates to 3600 logical cores percentage (200 * 18). M55 shape comes with minimum 17 logical CPU cores (3400). Since 3600 > 3400, allocation fails.

**Resolution**:
- Lower the resource limits in the task spec to fit available allotments
- For CPU mismatch: lower `ResourceLimit.cpu` to match the hardware (e.g., 17 or less for M55)
- Check the minimum resource guarantee for your capacity shape type

**Example posts**: [1](https://fb.workplace.com/groups/1473492212957333/permalink/819884334240490), [2](https://fb.workplace.com/groups/1473492212957333/permalink/3555563434750190)

---

#### Reservation Exhausted (enum 94)

**Bucket**: USER ERROR if over-subscribed, INFRA ERROR if UEs reduced capacity

**What's happening**: All allotments in the reservation are used. There is no free capacity to place the task.

**Investigation — determine which bucket**:
```bash
rbcli search --target allotments \
    --match resource_materialization_id=<entitlement_uuid> \
    --match region=<region> \
    --group-by failure_classification_category
```

If `failure_classification_category` shows `PLANNED`, `UNPLANNED`, or `DECOMMISSIONING` entries, UEs have reduced effective capacity → **INFRA ERROR**. If only `AVAILABLE` (i.e., reservation is genuinely full), it's → **USER ERROR**.

**Additional context**:
- Multiple shapes on same host with 1-task-per-host limit — only one shape per host can be used
- "As Is" guarantee provides no extra buffer for maintenance — switch to "Available" guarantee
- Buffer capacity set aside for maintenance/failure scenarios is not available for normal scheduling
- Allotments may have insufficient **remaining** capacity because other tasks already consume most resources on the same allotment — check what else is running

**Resolution**:
- **User error (over-subscribed)**: reduce task count or request more capacity via Capacity Portal
- **Infra error (UEs reducing capacity)**: check the UE timeline; most resolve automatically. If prolonged, escalate to oncall. See [Machine Unavailability](#machine-unavailability-enum-47) for detailed UE investigation

**Example posts**: [1](https://fb.workplace.com/groups/1473492212957333/permalink/1407100540641757), [2](https://fb.workplace.com/groups/1473492212957333/permalink/3512104292429438)

---

#### Elastic Recall (enum 81)

**Bucket**: INFRA ERROR — typically auto-resolves in 15-30 minutes

**What's happening**: Machines are being recalled from elastic (best-effort) reservations and reallocated to guaranteed reservations. During recall, tasks on the machine are stopped and the machine is reassigned. The allocator does not coordinate between jobs, so re-allocation can take 16-30 minutes.

**Investigation**:
```bash
rbcli allocation-intent-get-devices-elastic-recall-in-progress
```

Query `tw_allocator_elastic_recall_tracking` via Scuba for recall lifecycle details:
```sql
SELECT tracking_id, event_name, host_fqdn, device_id,
       startTimeInMs, completionTimeInMs
FROM tw_allocator_elastic_recall_tracking
WHERE time > now()-86400
  AND elastic_reservation_id = '<reservation_id>'
ORDER BY time DESC
```
See [Elastic Recall Deep Dive](#elastic-recall-deep-dive) for full schema, lifecycle states, and queries.

**If recalls are slow or timing out**:
```sql
-- Timeout rate by cluster
SELECT cluster_id, event_name, COUNT(*) as cnt
FROM tw_allocator_elastic_recall_tracking
WHERE time > now()-86400
  AND event_name IN ('COMPLETED', 'TIMEOUT')
GROUP BY cluster_id, event_name
```
Common causes: contention (multiple reservations requesting the same machine), shard overload, infrastructure delays. Typical P50 < 5 min, P99/max can reach ~90 min.

**Resolution**:
- **Wait**: most recalls auto-resolve in 15-30 minutes
- **Prevent future occurrences**: use dedicated (non-elastic) reservations for critical services
- **If prolonged**: escalate to TW oncall with job handle and Scuba query results

**Example posts**: [1](https://fb.workplace.com/groups/1473492212957333/permalink/3144875502485654)

---

#### Machine Unavailability (enum 47)

**Bucket**: INFRA ERROR

**What's happening**: Machines have active unavailability events (UEs) — maintenance, hardware failures, or other issues — on allotments that could otherwise fit the task.

**Investigation**:
```bash
# Check what UE types affect the reservation
rbcli search --target allotments \
    --match resource_materialization_id=<entitlement_uuid> \
    --match region=<region> \
    --group-by unavailability_type

# Check UE types across the region
rbcli search --match region=<region> --group-by=unavailability_type
```

**If all machines in reservation are in maintenance**:
Machines in the same failure domain can all be affected by a single maintenance event (e.g., boxcar maintenance). For "as-is" reservations with no infra-managed buffers, there is no automatic recovery mechanism. Alternative capacity in another cluster would require preempting other users.

**Resolution**:
- Most UEs resolve automatically — check the UE timeline
- If prolonged, contact oncall
- To prevent future impact: switch from "As Is" to "Available" guarantee type, which reserves capacity for maintenance scenarios

**Example posts**: [1](https://fb.workplace.com/groups/1473492212957333/permalink/847521741635956)

---

#### Exclusion Lock Conflict (enum 7)

**Bucket**: USER ERROR if from job spec, INFRA ERROR if from system

**What's happening**: An exclusion lock prevents the task from being placed on available allotments.

**Investigation — determine the source**:
Query `tw_allocator_v2_allocation_failures` in Scuba filtered by `job_handle` and examine the `failures` JSON for lock details:
- Lock name contains the job's own exclusion lock name (from `makeAllocationExclusive`) → **USER ERROR**
- Lock name references system/infra processes → **INFRA ERROR**

**Common USER ERROR — makeAllocationExclusive on stackable reservations**:
Using `makeAllocationExclusive` on a stackable RRU reservation with multiple shapes per BGM host leads to only one task per machine. Note: some reservations are configured with special spread settings on the RAS end, which allows multiple shapes per host even with exclusive allocation — check the reservation's RAS configuration.

**Resolution**:
- **User error**: remove `makeAllocationExclusive` from the spec and make the job stackable
- **Infra error**: escalate to oncall with the lock details from Scuba

**Example posts**: [1](https://fb.workplace.com/groups/1473492212957333/permalink/3554894394817094)

---

#### Generic / Allocator Bug (enum 33 / fallback)

**Bucket**: DEPENDS — requires deeper investigation

**What's happening**: No machine satisfies the allocation query. This is the catch-all when no specific SPD check matches.

**Investigation**:
```bash
tw allocation explain <job_handle>
```
```sql
-- Check for recent allocator rollout changes
SELECT feature_name, region, COUNT(*) as cnt
FROM tupperware_scheduler_feature_rollout
WHERE time > now()-86400
GROUP BY feature_name, region
```

```sql
-- Check which rollout features were actually used during failing allocations
SELECT used_rollout_features, COUNT(*) as cnt
FROM tw_allocator_v2_allocation_failures
WHERE job_handle = '<job_handle>'
  AND time > now()-86400
GROUP BY used_rollout_features
ORDER BY cnt DESC
```

> `used_rollout_features` shows features actually **checked and enabled** during allocation — more precise than `enabled_rollout_features`. If a recently rolled-out feature appears here for failing allocations but not successful ones, it is likely the root cause.

**If large-scale pending with available machines**: Can be caused by bugs in the allocator selection logic (e.g., `MachineSelectorStateFilter` bug).

**Resolution**:
- Try stopping and restarting the job to force fresh allocation
- If the issue persists, post in [TW Workplace group](https://fb.workplace.com/groups/1473492212957333) with job handle and Scuba query results
- Escalate to TW oncall if it affects many jobs

**Example posts**: [1](https://fb.workplace.com/groups/1473492212957333/permalink/3492173514422516)

---

### Step 6: Present findings and verify resolution

**Present findings** with a clear bucket label:
```
## Diagnosis: [USER ERROR | INFRA ERROR]

**Failure code**: <AllocationStatus code and name>
**What's happening**: <plain-English explanation>
**Evidence**: <key data from Scuba output>

## Resolution
<specific steps from the failure code section above>
```

**Verify resolution**:
```bash
tw allocation explain <job_handle>  # confirm no allocation failures
tw task-control show-status <job_handle>  # confirm tasks are running
```

### Edge Cases

- **No Scuba data**: If `tw_allocator_v2_allocation_failures` shows no recent entries, tasks may be pending for non-allocation reasons (package fetch, health check). Check `tw task-control show-status <job_handle>`.
- **Multiple failure codes**: Report the primary one from the priority order but list all observed codes.
- **Stale data**: Scuba data reflects recent allocation attempts. If the user reports the issue started hours ago, query for the relevant time window.
- **VCP (Virtual Capacity Pool)**: VCP pending-task issues are **out of scope** for this workflow. VCP has its own allocation pipeline and debugging workflow.
- **Drains — 10-minute heuristic**: When a machine is drained (e.g., for maintenance), a new machine should be found shortly and the task restarted. If a replacement is not found within ~10 minutes, the cause is usually that allocation preferences are too narrow (e.g., constrained to a single data hall in a single region) or the job gets machines through another mechanism (e.g., SMC tier). Jobs that cannot tolerate preemption or have a single always-running task likely need architectural changes — see [Tupperware Patterns](https://www.internalfb.com/intern/wiki/Infra_Cloud/Service_Hosting/Tupperware/Best_Practices/Tupperware_Patterns/).
- **`locality_constraints` caveat**: See the [Human-Readable Error Messages](#human-readable-error-messages) section for details on how `locality_constraints` can suppress error messages during allocation.

---

## AllocationStatus Quick Reference

Most common codes seen in allocation investigations:

| Code | Enum | Description |
|------|------|-------------|
| `SUCCESS` | 0 | Allocation was successful |
| `RESOURCE_FAILURE_CPU` | 2 | Not enough CPU resources |
| `RESOURCE_FAILURE_RAM` | 3 | Not enough RAM resources |
| `RESOURCE_FAILURE_DISK` | 4 | Not enough disk resources |
| `RESOURCE_FAILURE_FLASH` | 5 | Not enough flash resources |
| `RESOURCE_FAILURE_NETWORK` | 6 | Not enough network resources |
| `RESOURCE_FAILURE_ACCELERATOR` | 53 | Not enough accelerator (GPU) resources |
| `EXCLUSION_LOCK_CONFLICT` | 7 | Could not acquire exclusion locks |
| `ALLOCATION_FAILURE_GENERIC` | 33 | Default failure — no machine satisfies query |
| `MACHINE_UNAVAILABILITY_ON` | 47 | Machine has active unavailability events |
| `MACHINE_BEING_RECALLED_FROM_ELASTIC` | 81 | Machine being recalled from elastic reservation |
| `RESOURCE_FAILURE_ZERO_SERVER_COUNT` | 90 | Reservation has 0 server count |
| `RESOURCE_FAILURE_ZERO_RRU` | 91 | Reservation has 0 RRU |
| `RESOURCE_FAILURE_ZERO_HOST_ASSIGNED_TO_SMC_TIER` | 92 | SMC tier has no hosts |
| `RESOURCE_FAILURE_LIMIT_MISMATCH` | 93 | Task spec exceeds allotment capacity |
| `RESOURCE_FAILURE_RESERVATION_EXHAUSTED` | 94 | All allotments in reservation are used |

For the complete AllocationStatus enum: `fbcode/tupperware/allocator_v2/core/if/AllocationStatus.thrift`

---

## Failure Code Decision Tree

The allocator SPD pipeline checks failure conditions in a fixed priority order. The **first match wins** — later checks are skipped.

| Priority | Check Function | Failure Code(s) |
|----------|---------------|-----------------|
| 1 | `noAvailableCapacityCheck` | `RESOURCE_FAILURE_ZERO_SERVER_COUNT` (enum 90) or `RESOURCE_FAILURE_ZERO_RRU` (enum 91) |
| 2 | `noHostAvailableForSmcCheck` | `RESOURCE_FAILURE_ZERO_HOST_ASSIGNED_TO_SMC_TIER` (enum 92) |
| 3 | `resourceFailureCheck` | `RESOURCE_FAILURE_LIMIT_MISMATCH` (enum 93) or `RESOURCE_FAILURE_RESERVATION_EXHAUSTED` (enum 94) |
| 4 | `elasticRecallTaskPendingCheck` | `MACHINE_BEING_RECALLED_FROM_ELASTIC` (enum 81) |
| 5 | `machineUnavailabilityCheck` | `MACHINE_UNAVAILABILITY_ON` (enum 47) |
| 6 | `exclusionLockReservationExhaustedCheck` | `EXCLUSION_LOCK_CONFLICT` (enum 7) |
| 7 | `exclusionLockLimitMismatchCheck` | `EXCLUSION_LOCK_CONFLICT` (enum 7) |
| 8 | `exclusionLockStackableReservationCheck` | `EXCLUSION_LOCK_CONFLICT` (enum 7) |
| 9 | `machineFilterMaxCountErrorCheck` | Fallback — reports the most common per-machine failure from `SelectionFailureReasons` |

If none of the above checks match, the fallback (priority 9) examines per-machine failure counts and reports the single most common `AllocationStatus` code.

---

## Elastic Recall Deep Dive

### What is Elastic Recall?

Elastic recall is the process by which the allocator reclaims machines from elastic (best-effort) reservations and reallocates them to guaranteed reservations or higher-priority workloads. During recall, tasks on the machine are stopped and the machine is reassigned.

### Recall Lifecycle

The `tw_allocator_elastic_recall_tracking` Scuba dataset tracks the full recall lifecycle:

```
WANT_ALLOCATE → STARTED → ALLOCATED → COMPLETED
                                    → FINISHED
                       → TIMEOUT (if recall takes too long)
                       → ABORTED (if recall is cancelled)
```

### Dataset Schema

| Column | Type | Description |
|--------|------|-------------|
| `allocator_task_id` | string | Internal allocator task identifier |
| `allotment_id` | string | The allotment being recalled |
| `cluster_id` | string | Cluster where the recall is happening |
| `completionTimeInMs` | bigint | When the recall completed (epoch ms) |
| `device_id` | bigint | Device/server being recalled |
| `elastic_reservation_id` | string | The elastic reservation losing the machine |
| `event_name` | string | Recall event type (see lifecycle above) |
| `gang_priorities` | array | Gang scheduling priorities involved |
| `guaranteed_reservation_id` | string | The guaranteed reservation gaining the machine |
| `host` | string | Allocator host processing the recall |
| `host_fqdn` | string | FQDN of the machine being recalled |
| `is_completed` | bigint | 1 if the recall is complete, 0 if still in progress |
| `service` | string | Always "Allocator" |
| `shard_id` | string | Allocator shard processing the recall |
| `startTimeInMs` | bigint | When the recall started (epoch ms) |
| `task_handle` | string | Task being affected by the recall |
| `time` | bigint | Scuba event timestamp (unix epoch seconds) |
| `tracking_id` | string | Unique ID to track a single recall end-to-end |

### Key Queries

```sql
-- Overall recall health: event distribution in last 24h
SELECT event_name, COUNT(*) as cnt
FROM tw_allocator_elastic_recall_tracking
WHERE time > <epoch_24h_ago>
GROUP BY event_name ORDER BY cnt DESC

-- Slow recalls (duration > 10 minutes)
SELECT tracking_id, cluster_id, task_handle, host_fqdn,
       (completionTimeInMs - startTimeInMs) / 1000 as duration_secs
FROM tw_allocator_elastic_recall_tracking
WHERE time > <epoch_24h_ago>
  AND startTimeInMs > 0 AND completionTimeInMs > 0
  AND (completionTimeInMs - startTimeInMs) > 600000
ORDER BY duration_secs DESC
LIMIT 50

-- Recalls affecting a specific job's reservation
SELECT tracking_id, event_name, host_fqdn, device_id,
       startTimeInMs, completionTimeInMs
FROM tw_allocator_elastic_recall_tracking
WHERE time > <epoch_24h_ago>
  AND elastic_reservation_id = '<reservation_id>'
ORDER BY time DESC
```

### CLI Commands for Active Recalls

```bash
# See all devices currently undergoing elastic recall
rbcli allocation-intent-get-devices-elastic-recall-in-progress

# Count servers being recalled for a specific reservation by region
rbcli allocation-intent-get-devices-elastic-recall-in-progress \
    | grep <reservation_id> \
    | awk '{print $2}' | sort | uniq -c
```

### Common Causes of Slow/Timed-Out Recalls

- **Contention**: Multiple guaranteed reservations requesting the same recalled machine simultaneously
- **No coordination**: The allocator does not coordinate elastic recalls between different jobs
- **Shard overload**: Too many concurrent recalls overwhelming a single allocator shard
- **Infrastructure delays**: Network or service delays in the recall pipeline

### Typical Recall Durations

- **Average**: ~6 minutes (374 seconds)
- **P50**: Under 5 minutes
- **P99/Max**: Can reach up to ~90 minutes (5.3M ms) in worst cases

A high TIMEOUT rate in a cluster signals systemic issues (overloaded allocator shard, infrastructure delays, or contention).

---

## Investigating Reservation Capacity

### Using Universal Search

See [universal-search-syntax.md](../queries/universal-search-syntax.md) for full syntax.

```bash
# Get all reservations for a job
thriftdbg sendRequest search '{"request":{
  "select":{"allFields":{}},
  "from":2,
  "where":{"assocFilter":{"assocObjectType":1,"assocObjectIds":["<job_handle>"]}},
  "jsonResponseFormat":{}
}}' --tier tupperware.universal_search.experiment.test --request_timeout_ms 90000 | jq

# Get reservation details by ID (to see requested vs allocated capacity)
thriftdbg sendRequest search '{"request":{
  "select":{"allFields":{}},
  "from":2,
  "where":{"idFilter":{"ids":["<reservation_id>"]}},
  "jsonResponseFormat":{}
}}' --tier tupperware.universal_search.experiment.test --request_timeout_ms 90000 | jq

# Get servers allocated to a reservation (to see actual utilization)
thriftdbg sendRequest search '{"request":{
  "select":{"selectedFields":["$.hostname","$.region","$.schedulerState"]},
  "from":5,
  "where":{"assocFilter":{"assocObjectType":2,"assocObjectIds":["<reservation_id>"]}},
  "jsonResponseFormat":{}
}}' --tier tupperware.universal_search.experiment.test --request_timeout_ms 90000 | jq
```

### Using rbcli

```bash
# Check allotments for a reservation (shows allocated vs available capacity)
rbcli search --target allotments \
    --match resource_materialization_id=<entitlement_uuid> \
    --match region=<region> \
    --group-by capacity_shape_name

# Check failure classifications in allotments
rbcli search --target allotments \
    --match resource_materialization_id=<entitlement_uuid> \
    --match region=<region> \
    --group-by failure_classification_category

# Check unavailability types affecting a reservation's allotments
rbcli search --target allotments \
    --match resource_materialization_id=<entitlement_uuid> \
    --match region=<region> \
    --group-by unavailability_type

# Full allotment dump (JSON for detailed analysis)
rbcli search --target allotments \
    --match resource_materialization_id=<entitlement_uuid> \
    --match region=<region> \
    --json
```

See [rbcli Validated Tag Reference](#rbcli-validated-tag-reference) for the full list of validated `--match` and `--show`/`--group-by` fields per target.

> [!NOTE]
> Tags are target-specific. `capacity_shape_name` works on `allotments` but NOT on the default `server` target. `host_fqdn` works on `server` but NOT on `allotments`. Always specify `--target` explicitly.

### Investigating UEs with rbcli

1. **Check active UEs in a region**: `rbcli search --match region=<region> --group-by=unavailability_type`
2. **Check UE details on specific server**: `rbcli search --match id=<device_id> --show unavailability_data --json`
3. **Check allocation intent state**: `rbcli allocation-intent-get --json | jq '.[] | select(.state != 2)'` to find in-progress intents
4. **Check server move state**: `rbcli pmdi --region <region> --device-id-list=<id> --group-by-state`
5. **Check allotment-level unavailability**: `rbcli search --target allotments --match device_id=<id> --show uuid` then query each allotment

---

## Human-Readable Error Messages

The allocator also produces human-readable error strings (visible in the TW UI and `tw allocation explain` output) that complement the numeric failure codes above. These messages describe per-host or per-entitlement rejection reasons.

> [!NOTE]
> If the job spec uses `locality_constraints`, you may see **no error messages** at all for rejected hosts. Tupperware gives no guarantees for capacity when locality constraints are in effect.

### Entitlement Reported Errors (entitlement full)

These errors indicate the entitlement has reached its capacity limit:

| Error Message | Meaning |
|---------------|---------|
| "Entitlement met and capAtEntitlement=true" | The entitlement's cap has been reached and no more tasks can be allocated |
| "Entitlement ProcessorType ArchRange full OR No ArchRange found: PROCESSOR" | No capacity remaining for the requested processor architecture within the entitlement |

### Host Errors for Entitlements

These errors mean individual hosts cannot accommodate additional tasks under the entitlement:

| Error Message | Meaning |
|---------------|---------|
| "Port already in use" | The requested port is already bound by another task on the host |
| "Not enough resource Capacity. Requested *req-amount* but only *avail-amount* available" | Insufficient CPU, RAM, or other resources on the host to fit the task |
| "Would exceed the max ref count of *count* on exclusion lock" | Adding this task would exceed the exclusion lock's maximum reference count |

**Resolution**: Make tasks stackable, reduce task count, delete unused jobs from the entitlement, or increase the entitlement size.

### Entitlement/Job Cannot Use Host

These errors occur when the entitlement does not match the host hardware:

| Error Message | Meaning |
|---------------|---------|
| "Servers with Haswell processors require an upgraded entitlement" | Host has Haswell-era CPUs; the entitlement must be upgraded to use this hardware |
| "No entitlement exists for server type" | The server's hardware type has no corresponding entitlement configured |
| "Server type does not match entitlement" | The entitlement is for a different server type than this host |

### Available Hosts Errors

These errors indicate Tupperware cannot find any available hosts in certain [domains](https://www.internalfb.com/intern/wiki/Infra_Cloud/Service_Hosting/Tupperware/Tupperware_Spec_2.0/LanguageReference_Spec_2.0/Job/Handle/#domain-string):

| Error Message | Meaning |
|---------------|---------|
| "No capacity in cluster" | No machines available in the target cluster |
| "Machine is in-use by another scheduler" | Host is managed by a different scheduler instance |
| "Cannot stack task with other oncall teams" | Oncall team isolation prevents colocation with existing tasks |
| "Other exclusive entitlement exists on the machine" | Another entitlement has exclusive access to this host |
| "No Machines: No machines found" | No machines found matching the allocation criteria |
| "In decom blacklist" | Host is on the decommission blacklist and cannot accept new tasks |
| "Machine Disabled: Machine is disabled in allocator" | Host has been disabled in the allocator (check maintenance status) |
| "Request specifies skip for this machine" | The allocation request explicitly skips this host |

---

## Investigating Maintenance Status

To check whether a specific machine is under maintenance:

1. **Bunnylol**: `bunnylol tw <hostname>` → click **Allocator View**. If the `unavailability_reason` and `maintenance_reason` tags are not set, there is no ongoing maintenance for this machine.
2. **Scuba**: Query the [`tw_allocatorv2_machine_tag_change`](https://fburl.com/scuba/tw_allocatorv2_machine_tag_change/xpsrnd1p) dataset to see recent tag changes (enable/disable, maintenance status transitions) for a host.

### Finding Disabled Hosts in a Guaranteed Reservation

Use `reservation_info_guaranteed` as the match key (different from `resource_materialization_id` used for allotment queries):

```bash
# List disabled hosts with unavailability data
rbcli search --match region=<region> \
    --match reservation_info_guaranteed=<reservation_id> \
    --match host_disable_status=DISABLED \
    --show host_fqdn --show unavailability_data

# Group by unavailability type
rbcli search --match reservation_info_guaranteed=<reservation_id> \
    --group-by unavailability_type \
    --match region=<region>
```

### Finding Eligible Empty Hosts

To find hosts in a reservation that are eligible and do not have tasks running:

```bash
hostselect <reservation_id>! -w | grep -v tsp_*
```

---

## Best Practices & How-To

### How to choose the right guarantee type
Use "Available" guarantee instead of "As Is" for reservations needing maintenance buffers. "As Is" provides no extra buffer — all hosts subject to maintenance can leave tasks pending. "Available" guarantee reserves capacity for maintenance scenarios.

### How to size CPU and RAM constraints for hardware
Verify CPU core requirements match the actual hardware using `serf get`. Set explicit `ResourceLimit` values that fit within the target hardware's constraints. Note `ResourceLimit.cpu=N` translates to `N * 200` logical cores percentage. Check the minimum CPU cores for your shape type.

### How to avoid elastic recall delays
Use dedicated reservations for critical services to avoid elastic recall delays. Monitor `tw_allocator_v2_allocation_failures` for early detection of capacity issues. Use `tw_allocator_elastic_recall_tracking` to identify timeout patterns and slow recalls in specific clusters.

### How to find your reservation's entitlement UUID for rbcli queries
Use Universal Search to get reservation details for your job, then extract the `resource_materialization_id` from the reservation spec. Alternatively, check the job spec's reservation configuration in the TW console or via `tw job print <job_handle>`.

---

## Common Questions

### Q: Is the 15k task limit per TW job still valid?
**A:** Yes, the 15k limit still has technical merit. Recent experiments with ~50k tasks surfaced many issues. Scheduler bottlenecks continue. May be able to bump to 30-50k eventually. Job groups are being discussed internally as an alternative.
**Example posts**: [1](https://fb.workplace.com/groups/1473492212957333/permalink/3498847860421748)

### Q: Why are elastic recalls slow or timing out?
**A:** See [Elastic Recall Deep Dive — Common Causes](#common-causes-of-slowtimed-out-recalls).

---

## Reference Tables

### Scuba Tables
| Table | When to Use | Key Columns |
|-------|------------|-------------|
| `tw_allocator_v2_allocation_failures` | Primary table for allocation failures | `job_handle`, `failures` (JSON), `available_allotments`, `available_rrus`, `region` — verify columns with `meta scuba.dataset info` |
| `tw_allocator_v2_allocation_requests` | Track allocation requests and outcomes | `job_handle`, `allotment_id`, `used_rollout_features`, `enabled_rollout_features` |
| `tw_allocator_elastic_recall_tracking` | Track elastic recall lifecycle and timing (see [Deep Dive](#elastic-recall-deep-dive)) | `tracking_id`, `event_name`, `cluster_id`, `host_fqdn`, `startTimeInMs`, `completionTimeInMs` |
| `tw_allocatorv2_machine_tag_change` | Track machine tag changes (status, maintenance_status, enable/disable) — see [dataset reference](../datasets/tw_allocatorv2_machine_tag_change.md) | `machine`, `tag`, `action`, `value`, `reason_type` |
| `ras_machines_allocations` | Check RAS-level capacity | `reservation_uuid`, `region` |
| `tupperware_task_events` | Track task state during allocation | `job`, `task_state` |
| `tupperware_scheduler_feature_rollout` | Check recent allocator rollout changes | `featureDiff`, `domain` — verify columns with `meta scuba.dataset info` |

### CLI Commands
| Command | When to Use |
|---------|------------|
| `tw allocation explain <job_handle>` | Explain why tasks are failing to allocate |
| `tw resolve <job_handle>` | See current task states and host assignments |
| `rbcli search --target allotments --match resource_materialization_id=<uuid> --match region=<region>` | Query allotments for a reservation (capacity breakdown) |
| `rbcli search --target allotments --match resource_materialization_id=<uuid> --group-by failure_classification_category` | Check failure classifications in allotments |
| `rbcli search --target allotments --match resource_materialization_id=<uuid> --group-by unavailability_type` | Check what UEs affect a reservation's allotments |
| `rbcli search --target allotments_table --match resource_materialization_id=<uuid> --match region=<region> --group-by state` | Check allotment states (IN_USE vs CREATED) — CREATED means host setup incomplete |
| `rbcli allocation-intent-get-devices-elastic-recall-in-progress` | See active elastic recalls |
| `rbcli allocation-intent-get --json` | Get all allocation intents (filter with jq for in-progress) |
| `tw task-control show-status <job_handle>` | Check task states (useful when no allocation failures found) |
| `tw task-control apply-task-ops --all-ops <job_handle>` | Clear stuck allocation operations |
| `tw preempt <task_handle>` | Force task to reallocate |
| `tw print <job_handle>` | Inspect current job spec |
| `hostselect <reservation_id>! -w \| grep -v tsp_*` | Find eligible empty hosts in a reservation (hosts with no tasks running) |
| `rbcli search --match region=<region> --match reservation_info_guaranteed=<id> --match host_disable_status=DISABLED --show host_fqdn --show unavailability_data` | Find disabled hosts in a guaranteed reservation |
| `rbcli search --match reservation_info_guaranteed=<id> --group-by unavailability_type --match region=<region>` | Group unavailability types for a guaranteed reservation |
| `bunnylol tw <hostname>` → Allocator View | Check `unavailability_reason` and `maintenance_reason` tags for maintenance status |
| `serf get datacenter=<dc>,cluster=<cluster>,device_type=SERVER --fields=model` | Check hardware specs |

### Universal Search Commands (see [universal-search-syntax.md](../queries/universal-search-syntax.md))
| Query | When to Use |
|-------|------------|
| `search from:2 where:assocFilter(job)` | Get reservations for a job |
| `search from:5 where:assocFilter(reservation)` | Get servers allocated to a reservation |
| `search from:4 where:assocFilter(job)` | Get tasks for a job (see counts by state) |
| `search from:18 where:idFilter(job)` | Get TaskControlOps (active UEs, pending ops) |
| `help objectType:2` | Discover all searchable reservation fields |

### rbcli Target Selection Guide

Tags are **target-specific**. A tag valid on `allotments` may be invalid on `server` (the default) and vice versa. Always specify `--target` explicitly.

| Question | Target |
|----------|--------|
| Reservation allotment counts, shapes, health | `--target allotments` |
| Host-level details (FQDN, CPU, disable status) | `--target server` (default) |
| Capacity planning, reservation names | `--target cd` |
| Allotment table direct queries (by UUID) | `--target allotments_table` |

### rbcli Validated Tag Reference

Tags below have been CLI-verified. Use these instead of guessing from `--print-available-tags` output, which lists all tags across targets without distinguishing validity.

**Allotments target (`--target allotments`)** — primary target for reservation/capacity analysis:

| Field | --match | --show/--group-by | Description |
|-------|:---:|:---:|-------------|
| `resource_materialization_id` | yes | yes | Primary key for reservation queries |
| `device_id` | yes | yes | Server device ID |
| `capacity_shape_name` | yes | yes | M55, M244, etc. |
| `failure_classification_category` | yes | yes | AVAILABLE, DECOMMISSIONING, PLANNED, UNPLANNED |
| `unavailability_type` | yes | yes | Type of unavailability event |
| `unavailability_type_server_reshaping` | yes | yes | Reshaping-specific UE type |
| `unavailability_type_capacity_rebalancing` | yes | yes | Rebalancing-specific UE type |
| `allocation_owner_id` | yes | yes | e.g. tsp_prn.shared (see note below) |
| `uuid` | yes | yes | Allotment UUID |
| `reserved_to` | yes | yes | e.g. RB_CAAS_RA_prn |
| `logical_server_subtype` | yes | yes | LSST code (10006, 10010, etc.) |
| `rb_shard_id` | yes | yes | Resource Broker shard |
| `region` | yes | yes | Region name |
| `host_fqdn` | **no** | **no** | Use server target |
| `cpu_cores` | **no** | **no** | Use server target |
| `host_disable_status` | **no** | **no** | Use server target |
| `logical_server_type` | **no** | **no** | Use server target |
| `reservation_entitlement_id` | **no** | **no** | Not searchable on any target |

> **`allocation_owner_id` does NOT determine job scheduling eligibility.** When you see allotments owned by a regional scheduler (e.g., `tsp_nha.shared`) vs the global scheduler (`tsp_global.shared`), this does NOT mean regional-owned allotments are invisible or unavailable to global jobs. Global jobs CAN be placed on allotments regardless of the `allocation_owner_id`. Do not conclude that a mismatch between `allocation_owner_id` and the job's scheduler domain is the cause of allocation failures.

**Server target (`--target server`, the default)** — use for host-level queries:

| Field | --match | --show/--group-by | Notes |
|-------|:---:|:---:|-------|
| `id` | yes | yes | Device ID |
| `host_fqdn` | yes | **no** | Match only, not group-by |
| `region` | yes | yes | |
| `logical_server_subtype` | yes | yes | |
| `failure_classification_category` | yes | yes | |
| `allocation_owner_id` | yes | yes | |
| `resource_materialization_id` | yes | **no** | Match only, not group-by |
| `host_disable_status` | yes | yes | |
| `cpu_cores` | yes | yes | |
| `logical_server_type` | yes | yes | |
| `reserved_to` | yes | yes | |
| `capacity_shape_name` | **no** | **no** | Use allotments target |

**Capacity Data target (`--target cd`)** — use for capacity planning:

| Field | --match | --show/--group-by | Notes |
|-------|:---:|:---:|-------|
| `capacity_shape_name` | **no** | yes | Show/group-by only |
| `guaranteed_reservation_name` | **no** | yes | Show/group-by only |
| `reserved_to` | **no** | yes | Show/group-by only |
| `logical_server_subtype` | yes | yes | |
| `failure_classification_category` | yes | yes | |
| `resource_materialization_id` | **no** | **no** | Use allotments target |
