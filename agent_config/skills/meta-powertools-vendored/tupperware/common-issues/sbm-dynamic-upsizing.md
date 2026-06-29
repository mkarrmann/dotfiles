# Shared Buffer Manager & Dynamic Upsizing

> Primary Scuba: `icsp_log` | Primary CLI: `tw` | Oncall: `icsp_domain_tupperware`
>
> Wiki: [TW Shared Buffer Manager](https://www.internalfb.com/wiki/Infra_Cloud/Service_Hosting/ICSP/ICSP_Domain_Configurations/Tupperware/TW_Shared_Buffer_Manager/)

The Shared Buffer Manager (SBM) is a component of the ICSP Tupperware Domain that manages the capacity buffer used by Dynamic Upsizing (DU)-enabled Tupperware jobs during updates. It enforces that no more than X% of a reservation's capacity is used by Dynamic Upsizing at a time, even when many DU-enabled jobs in the same reservation are updating simultaneously. This improves reliability in setups where many NTID- and DU-enabled jobs share a reservation.

## When This Runbook Applies

- SBM is enabled for the job (check the TW UI: hover over job update markers in the chart — if "dynamic step sizing" is shown as enabled, SBM is managing DU buffer usage)
- `tw task-control show-status <job_handle>` shows job operations are being rate-limited with a step size of 0 — this usually indicates SBM is throttling the job to stay within the DU buffer limit
- Job updates are stuck or progressing slowly and the job uses Dynamic Upsizing
- DU budget is not being granted to a region
- Tasks are pending during an update on a DU-enabled job

## Debugging Workflow

### Step 1: Confirm SBM is involved

There are multiple ways to confirm SBM is managing the job:

1. **TW UI**: Open the TW UI for the job (`bunnylol tw <job_handle>`) and hover over any job update marker in the chart. If SBM is managing the DU buffer usage, the popup will indicate that "dynamic step sizing" is enabled.

2. **Step size of 0**: Run `tw task-control show-status <job_handle>`. If job operations are being rate-limited with a step size of 0, this usually indicates SBM is throttling the job to stay within the DU buffer amount.

3. **ICSP override spec**: Query the job's `ICSP_OVERRIDE_SPEC` using `getJobObjectAtRevision()` and check if the response has a nonnull value for `dynamicStepSize`. If the job has a nonnull `dynamicStepSize` and NTID is enabled, then SBM is managing its DU buffer.

If none of these indicators are present, this runbook does not apply — check [Job Updates](./job-updates.md) instead.

### Step 2: Check if the reservation is capacity-exhausted

Query the `icsp_log` Scuba dataset for the job's reservation and region. Use `meta scuba.dataset info -d icsp_log` to discover current column names before querying.

Look for `emptyAllotments == 0`. If no empty allotments exist, Dynamic Upsizing cannot work because DU requires free capacity in the reservation to temporarily upsize tasks.

```bash
# Check reservation capacity via icsp_log
# Example query (discover exact columns first):
meta scuba.dataset query -d icsp_log --view=samples -l 20 --hours=6 \
  -w '[{"column":"<reservation_column>","op":"eq","values":["<reservation_name>"]}]'
```

Also cross-reference with the Capacity/SAH UI: https://www.internalfb.com/intern/services/sah/

**If the reservation is exhausted:**
- **Root cause**: No free capacity for DU to use. This is generally not a TW issue.
- **Resolution**:
  - Increase capacity in the reservation for the affected region, OR
  - Downsize or remove existing jobs to free up at least 1 allotment for DU
- **Escalation**: Escalate to whoever manages capacity for the affected reservation. For IPNext IP (`ipnext_ip_prod/deployment`), post in [Inference Platform Users](https://fb.workplace.com/groups/inferenceusers) or escalate to the IPNext capacity team.

### Step 3: Check SBM buffer utilization

Query the `icsp_log` Scuba dataset, filtering by the job's reservation and region. Use `meta scuba.dataset info -d icsp_log` to discover current column names before querying.

Example Scuba query: https://fburl.com/scuba/icsp_log/am5vgods

Filter on `structType = TwSbmUtilization` and then look for `bufferSize` and `bufferUsed`:

| Condition | Meaning | Action |
|-----------|---------|--------|
| `bufferUsed < bufferSize` | Buffer has capacity available | SBM should not be blocking — investigate other causes (see [Job Updates](./job-updates.md)) |
| `bufferUsed == bufferSize` | Buffer is 100% utilized | SBM is correctly preventing additional DU — this is expected behavior. Proceed to Step 4 to find what is holding the buffer. |

### Step 4: Identify which jobs are currently holding DU buffer


1. Identify the TW task handle in the Tupperware Controller service that was processing the SBM reconciliation loop for the reservation, by looking at the "tw task handle" column related to the `TwSbmUtilization` logs in the `icsp_log` Scuba table query from Step 3.
2. Open the task logs for that task (for example, bunnylol "logarithm tsp_global/icsp/controller_tupperware.ipnext.criticality_high/0`). Filter to the logs that contain the string "sbm_{reservation_name}", which are the logs emitted by the SBM reconciliation loop for that reservation.
3. SBM logs messages of the format "Found pendingDemand={}, currentUsage={} for {job handle}" . The jobs that are currently holding DU buffer are the jobs that have currnentUsage > 0 .

**ICSP controller task log format**

Example log line:
```
I0609 16:09:33.899574  1129 JobFetcher.cpp:172 req:013000000000011f] [tw_shared_buffer_manager:services/ipnext_ip_prod/deployment/domains/tupperware/resources/root/sbm/sbm_sr__ipnext_ig_shared__qe__t16_grand_teton__g1?facet=AUTOMATION:4ko6a2AP1d_9345] Found pendingDemand=1, currentUsage=0 for tsp_nha/ipnext_ip_prod/deployment.partition.default_gti96g1.m2120361842_slatest
```

Explantion:
- `I0609 16:09:33.899574  1129 JobFetcher.cpp:172 req:013000000000011f]` is generated by GLOG/XLOG
- `tw_shared_buffer_manager` is the CPPlatform resource kind
- `services/ipnext_ip_prod/deployment/domains/tupperware/resources/root/sbm/sbm_sr__ipnext_ig_shared__qe__t16_grand_teton__g1?facet=AUTOMATION` is the CPPlaform URI for the resource
- `4ko6a2AP1d_9345` is the CPPlatform tick id. All logs emitted by the same reconcilation loop tick have the same tick id.
- The remainder of the line is the log message content

### Step 5: Check for DU task allocation failures

If the job holding the buffer has a pending DU task, check for allocation failures:

Query the `tw_allocator_v2_allocation_failures` Scuba dataset. Use `meta scuba.dataset info -d tw_allocator_v2_allocation_failures` to discover current column names before querying.

Example Scuba query: https://fburl.com/scuba/tw_allocator_v2_allocation_failures/zj9ror9b

```bash
meta scuba.dataset query -d tw_allocator_v2_allocation_failures --view=samples \
  -l 20 --hours=6 \
  -w '[{"column":"job_handle","op":"eq","values":["<buffer_holding_job_handle>"]}]'
```

**If allocation failures are found:**
- The DU task cannot be placed, which means the buffer stays held and blocks all other DU-enabled jobs in the reservation
- Resolve the allocation failure for the buffer-holding job first (see [Allocation & Capacity](./allocation-capacity.md) for detailed allocation failure debugging)
- Once the allocation issue is resolved, SBM will reclaim the buffer and grant it to queued jobs

### Step 6: Present findings

```
## Diagnosis

**SBM Status**: [Buffer exhausted | Reservation exhausted | Allocation failure blocking buffer]
**Buffer**: bufferUsed=<N> / bufferSize=<M>
**Blocking job**: <job_handle holding the buffer> (if applicable)
**Evidence**: <Scuba query links, TW UI observations>

## Resolution
<specific steps based on root cause>

## Escalation
For SEVs or urgent issues: `icsp_domain_tupperware` oncall
```

---

## Key Concepts

### Dynamic Upsizing (DU)

Dynamic Upsizing is a mechanism that temporarily increases a task's resource allocation during updates. Instead of stopping an old task and starting a new one (which causes brief downtime), DU starts the new task alongside the old one on additional capacity, then drains the old task gracefully. This requires free capacity in the reservation.

### How SBM Works

Without SBM, if many DU-enabled jobs in a shared reservation update simultaneously, they could each claim DU capacity and exhaust the reservation's buffer — starving other jobs. SBM enforces a configurable cap (X%) on how much reservation capacity can be consumed by DU at any given time, serializing DU grants when the buffer is full.

### NTID (Noncontiguous Task ID)

NTID is a related feature often enabled alongside DU. When both NTID and DU are enabled, SBM coordinates the buffer usage across all jobs in the reservation.

### TW Scheduler JobObservedState

`JobObservedState` is an object written by the TW Scheduler. SBM reads it via the TWAPI `getJob` RPC in order to determine:
- `jobObservedState.sharedBufferInfo.demand`
   - The current number of tasks in the job that are waiting to begin dynamic upsizing. When this is nonzero, that implies the job needs DU buffer to accomplish a job update, restart, TW Rebalancer-driven preemption, etc.
- `jobObservedState.sharedBufferInfo.currentUsage`
   - The current number of extra DU tasks that will eventually be stopped. When this is nonzero, that implies the job is in the middle of Dynamic Upsizing and using some of the shared DU buffer.

If there are suspected issues with these values, escalate to the "tupperware_scheduler_core" oncall.

---

## Reference Tables

### Scuba Tables

| Table | When to Use | Key Columns |
|-------|-------------|-------------|
| `icsp_log` | Primary table for SBM state — buffer size, utilization, empty allotments | Discover columns with `meta scuba.dataset info -d icsp_log` |
| `tw_sbm_job_debug` | Per-job buffer allocation decisions — why each job did or did not receive DU buffer on a tick (priority inputs, score, rank) | Discover columns with `meta scuba.dataset info -d tw_sbm_job_debug` |
| `tw_allocator_v2_allocation_failures` | Check if DU tasks are failing to allocate | `job_handle`, `failures`, `available_allotments` |

### `TwSbmUtilization` Scuba fields

`TwSbmUtilization` is logged to `icsp_log` once per tick per region (filter `structType = TwSbmUtilization`). Each Scuba column is prefixed with `TwSbmUtilization_` — e.g. the `bufferSize` field is the `TwSbmUtilization_BufferSize` column.

| Field | Type | Description |
|-------|------|-------------|
| `reservation` | string | Reservation name SBM is managing. |
| `reservationId` | string | UUID of the reservation. |
| `region` | string | Region this row describes (e.g. `prn`, `global`). |
| `bufferUnit` | string | Unit for the size fields below: `gpu_cards` for full-server (IPNext) reservations, otherwise `allotments`. |
| `bufferSize` | i32 | Size of the DU push buffer for the region, in `bufferUnit` units. This is the cap on how much reservation capacity DU may use at once. |
| `bufferUsed` | i32 | Amount of the DU buffer currently in use, in `bufferUnit` units. |
| `reservationSizeInDUBufferUnits` | i32 | Total reservation size for the region, in `bufferUnit` units. NOTE: when `bufferUnit = gpu_cards` this is the total number of GPU cards, NOT the number of hosts. |
| `totalJobSizeInDUBufferUnits` | i32 | Sum over all jobs in the reservation of each job's task count in the region, scaled to `bufferUnit` units. Compare against `reservationSizeInDUBufferUnits` to gauge reservation headroom. NOTE: This is computed based on the job size, which means it includes any pending tasks that are not actually allocated. |
| `emptyAllotments` | i32 | Number of empty allotments in the reservation region (always in allotments). `0` means the reservation is capacity-exhausted. |
| `unexpectedlyOccupied` | bool | `true` when `totalJobSizeInDUBufferUnits > reservationSizeInDUBufferUnits - bufferSize` — i.e. the jobs are occupying space that should be kept free as DU buffer. When this is true, the service (IPNext) needs to shrink job sizes or else DU-based updates may be unexpectly slow or get stuck. |
| `jobsUsingBuffer` | list&lt;string&gt; | Regional job handles currently holding DU buffer (dynamic step size > 0) in the region. |
| `longestBufferOccupancyMs` | i64 | Duration (ms) of the longest currently-held DU buffer grant in the region. |
| `longestBufferOccupancyJobHandle` | string | Regional job handle that has held DU buffer the longest in the region. |

### `TwSbmJobDebug` Scuba fields

`TwSbmJobDebug` gives per-job visibility into SBM's buffer-allocation decisions: for every job SBM is managing, it records the priority inputs, the resulting priority score and rank, and any step-size change made on that tick. Use it to answer "why did (or didn't) this job get DU buffer on this tick, and where did it sit in the priority order?".

It is logged once per job per region per tick. Unlike `TwSbmTickLog` / `TwSbmUtilization`, it does **not** go to `icsp_log` — it has its own `tw_sbm_job_debug` Scuba table (controlled by the `--tw_sbm_job_debug_scuba_table` gflag). Discover columns with `meta scuba.dataset info -d tw_sbm_job_debug`. Each Scuba column is prefixed with `TwSbmJobDebug_` — e.g. the `priorityRank` field is the `TwSbmJobDebug_PriorityRank` column. The `bool` fields below are logged as integers (`0` / `1`) in Scuba.

The weights applied to the priority inputs (per-criticality multipliers, the immutable-appconfig-update / container-revert / immutable-appconfig-revert multipliers, and the additive GPU-card weight) are set in the Configerator config `tupperware/shared_buffer_manager/sbm_solver_config` (struct `SBMSolverConfig` / `SBMPriorityWeights`, defined in `configerator/source/tupperware/shared_buffer_manager/sbm_solver_config.thrift`). That config also supports per-reservation overrides via its `overrides` list. The weighted priority path is gated per-reservation by the `kEnableSbmPriorityWeightsConfig` JustKnob; when disabled, `SBMSolver` falls back to strict criticality ordering with hardcoded multipliers.

| Field | Type | Description |
|-------|------|-------------|
| `regionalJobHandle` | string | Regional job handle this row describes. |
| `region` | string | Region this row describes (e.g. `prn`, `ldc`). |
| `reservation` | string | Reservation name SBM is managing. |
| `bufferDemand` | i32 | Number of tasks in the job waiting to begin Dynamic Upsizing — i.e. how much DU buffer the job is requesting this tick. Nonzero means the job needs buffer to make progress. |
| `bufferCurrentUsage` | i32 | Number of extra DU tasks the job is currently running that will eventually be stopped — i.e. how much DU buffer the job is already holding. |
| `currentDynamicStepSize` | i32 | The job's dynamic step size at the start of the tick (before any change SBM makes this tick). |
| `gpuCardsPerTask` | i32 | Number of GPU cards each task in the job consumes. A priority input — larger jobs cost more buffer per step. |
| `criticalityTier` | string (optional) | The job's criticality tier (a priority input). Omitted when the job has no criticality tier. |
| `waitStartTimeMs` | i64 (optional) | Epoch time (ms) when the job started waiting for DU buffer. Omitted when the job is not waiting. |
| `waitDurationMs` | i64 (optional) | How long (ms) the job has been waiting for buffer as of this tick (`now - waitStartTimeMs`). Longer waits raise priority. Omitted when the job is not waiting. |
| `isUpdatingImmutableAppconfig` | bool | `true` when the job is performing an immutable-appconfig update (a priority input). |
| `isContainerRevert` | bool | `true` when the job's update is a container revert (a priority input). |
| `isImmutableAppconfigRevert` | bool | `true` when the job's update is an immutable-appconfig revert (a priority input). |
| `priorityScore` | i64 (optional) | The job's computed priority score (= `-weightedWaitDuration`); **smaller** values indicate higher priority. Set only for jobs that competed for buffer this tick. |
| `priorityRank` | i32 (optional) | 0-based position of the job in the sorted priority list — `0` is the highest-priority job, served first. Set only for jobs that competed for buffer this tick. |
| `newStepSize` | i32 (optional) | The new dynamic step size SBM assigned the job this tick. Set **only** when SBM changed the job's step size on this tick; absent when the step size was unchanged. |

### Useful Links

| Resource | URL |
|----------|-----|
| SBM Wiki | https://www.internalfb.com/wiki/Infra_Cloud/Service_Hosting/ICSP/ICSP_Domain_Configurations/Tupperware/TW_Shared_Buffer_Manager/ |
| IPNext NTID Oncall Runbook | https://www.internalfb.com/wiki/Inference/Inference:_Internal/Teams/Capacity_Management_Team/IPNext_0/Oncall_Runbooks/IPnext_NTID_Oncall_Runbook/ |
| TW Platform SBM Internal Runbook | https://www.internalfb.com/wiki/Tupperware_Internal/Platform_Team/Oncall/ICSP_Oncall/DU_Shared_Buffer_Manager_(SBM)_Internal_Runbook |
| Capacity Portal (SAH) | https://www.internalfb.com/intern/services/sah/ |

### CLI Commands

| Command | When to Use |
|---------|-------------|
| `tw resolve <job_handle>` | Check current task states and pending tasks |
| `tw task-control show-status <job_handle>` | Check for pending task operations and rate limiting |
| `bunnylol tw <job_handle>` | Open TW UI to check SBM/DU status in update markers |

### Common Error Patterns

| Symptom | Likely Cause | Go to |
|---------|-------------|-------|
| Job update stuck, "dynamic step sizing" enabled | SBM buffer fully utilized | [Step 3](#step-3-check-sbm-buffer-utilization) |
| `tw task-control show-status` shows step size of 0 on DU-enabled job | SBM rate-limiting job operations to stay within DU buffer | [Step 1](#step-1-confirm-sbm-is-involved) |
| `emptyAllotments == 0` in icsp_log | Reservation capacity exhausted — no room for DU | [Step 2](#step-2-check-if-the-reservation-is-capacity-exhausted) |
| `bufferUsed == bufferSize` and another job's DU task is pending | Allocation failure blocking buffer reclamation | [Step 5](#step-5-check-for-du-task-allocation-failures) |
| Multiple DU-enabled jobs stuck in same reservation | SBM serializing DU grants — expected behavior when buffer is full | [Step 4](#step-4-identify-which-job-is-holding-the-du-buffer) |
