# Tupperware TaskControlOps Query Skill

## Overview

This skill provides the complete schema reference and query patterns for **TaskControlOps** objects in Tupperware Universal Search. TaskControlOps represent pending or active task control operations and unavailability events (UEs) for jobs with task control enabled.

### When to Use This Skill
- Investigating unavailability events (UEs) affecting jobs
- Debugging maintenance operations
- Checking planned maintenance train events
- Understanding task preemption reasons
- Finding pending/paused control operations

## Quick Reference

**Object Type Enum:** `18` (TaskControlOps)

**Query Language:** See [universal-search-syntax.md](universal-search-syntax.md) for how to run queries (thriftdbg command syntax, single-line rule, quick-start examples).

### Common Query Patterns

#### Get TaskControlOps for a Job

Query by ID using `idFilter` with `from:18`:

```json
{"request":{"select":{"allFields":{}},"from":18,"where":{"idFilter":{"ids":["tsp_vcn/warm_storage/ws.block_regional.vcn.hypernode.t8.ras"]}},"jsonResponseFormat":{}}}
```

#### Get TaskControlOps via Job Association

Query via job association using `assocFilter` with `from:18`:

```json
{"request":{"select":{"allFields":{}},"from":18,"where":{"assocFilter":{"assocObjectType":1,"assocObjectIds":["JOB_HANDLE"]}},"jsonResponseFormat":{}}}
```

See universal-search-syntax.md for how to run these queries.

## Schema Reference

See the full [Language Reference](#language-reference) below for complete field documentation.

### Key Fields

| Field | Type | Description |
|-------|------|-------------|
| `taskControlStatus.requestedOps` | list | Pending task control operations awaiting execution |
| `taskControlStatus.pausedRequestedOps` | list | Operations paused pending user approval |
| `taskControlStatus.ackedOps` | list | Operations that have been acknowledged |
| `taskControlStatus.activeUnavailabilityEvents` | list | Current UEs affecting tasks |

### Unavailability Event Fields

| Field | Description |
|-------|-------------|
| `impact` | Impact type (e.g., `NETWORK_UNAVAILABLE`, `RUNTIME_STATE_LOSS`, `ROOT_STATE_LOSS`) |
| `context` | Description of the event (e.g., planned maintenance, agent unavailable) |
| `isMaintenanceTrain` | Whether this is part of a scheduled maintenance train |
| `expectedActions` | Actions the job can take (`PREEMPT`, `STOP`, `STAY`) |

### TaskControlOps Structure Overview

```yaml
TaskControlOps:
  jobHandle: string — Associated job handle
  taskControlStatus: JobTaskControlStatusResponse
    requestedOps: list<TaskOperation>
      taskOp: TaskControlTaskOp — union (taskGrow, taskStart, taskRestart, taskStop, taskShrink, etc.)
      taskID: i32
      operationContext: string
    pausedRequestedOps: list<TaskOperation>
    ackedOps: list<TaskOperation>
    activeUnavailabilityEvents: list<TaskUnavailabilityEvent>
      impact: ImpactType enum
      context: string
      isMaintenanceTrain: bool
      expectedActions: list<TaskUnavailabilityActionType enum>
```

### Impact Types

| Impact | Description |
|--------|-------------|
| `NETWORK_UNAVAILABLE` | Network connectivity will be lost |
| `RUNTIME_STATE_LOSS` | Running processes will be terminated |
| `ROOT_STATE_LOSS` | All data on disk will be lost |

### Expected Actions

| Action | Description |
|--------|-------------|
| `PREEMPT` | Job can preempt and restart elsewhere |
| `STOP` | Job can gracefully stop |
| `STAY` | Job can stay and ride out the event |

## Discover Available Fields

Use the `help()` API with `objectType:18` and `format:2` to discover available fields. See universal-search-syntax.md for the runnable command.

---

# Language Reference


# Tupperware TaskControlOps — Language Reference (Agent Prompt)

This document describes the **TaskControlOps** resource as defined in the Tupperware Universal Search system. It is intended to be used as an agent prompt so that an LLM can answer questions about task control operations, unavailability events, and their schema.

---

## 1. Overview

**TaskControlOps** represents pending or active task control operations and unavailability events (UEs) for a Tupperware job with task control enabled. Task control is the mechanism by which Tupperware coordinates lifecycle operations (restarts, updates, preemptions, drains) with an external task controller, allowing the controller to pace and acknowledge operations for safe execution.

In Universal Search, the TaskControlOps object (`ObjectType.TaskControlOps`, value `18`) is the `Resource.TaskControlOps` struct from `tupperware/universal_search/if/Resource.thrift`. It wraps a job handle and a `JobTaskControlStatusResponse` from the scheduler.

**TaskControlOps identity format:** Identified by job handle (e.g. `tsp_prn/myteam/my_service`).

### Schema Diagram

The diagram below is the **authoritative schema reference** for the TaskControlOps resource. All fields, types, `[Deprecated]` and `[Indexed]` annotations are shown here. Sections 2–3 provide **field-level documentation only** (behavioral details, caveats, code examples) — they do not repeat the schema.

> **Legend:** `[Indexed]` = field is searchable via `jsonPathFilter`.
> `[Deprecated]` = field is deprecated.

> ⚠️ TaskControlOps search has no indexed fields — `[Indexed]` tags will be added once searchable keys are deployed.

```yaml
Resource.thrift::TaskControlOps:
  jobHandle: string
  taskControlStatus: JobTaskControlStatusResponse
    jobHandle: string
    requestedOps: list<TaskOperation>
      operationID: string
      taskID: i32
      taskOp: TaskControlTaskOp — union
        taskGrow: TaskControlTaskGrow — empty struct
        taskStart: TaskControlTaskStart — empty struct
        taskRestart: TaskControlTaskRestart
          allowDynamicUpsize: bool
          isMutableConfigUpdate: bool
        taskStop: TaskControlTaskStop — empty struct
        taskShrink: TaskControlTaskShrink
          unavailability: TaskUnavailabilityMetadata
            eventID: string
            impact: ImpactType enum
            startTimeInSeconds: i64
            durationInSeconds: i64
            responseDeadlineInSeconds: i64
            creationTimeInSeconds: i64
            context: string
          operation: TaskOperationMetadata — empty struct
        taskMoveByUserRestriction: TaskControlTaskMoveByUserRestriction
          allowDynamicUpsize: bool
        taskMoveByAllocationChange: TaskControlTaskMoveByAllocationChange
          reason: TaskMoveByAllocationChangeReason enum
          drainInfo: DrainInformation
            maintenanceBeginTimeSeconds: i64
            maintenanceEndTimeSeconds: i64
            impactType: DrainImpactType enum
            maintenanceId: string
          allowDynamicUpsize: bool
        taskDrain: TaskControlTaskDrain [Deprecated]
          drainInfo: DrainInformation
            maintenanceBeginTimeSeconds: i64
            maintenanceEndTimeSeconds: i64
            impactType: DrainImpactType enum
            maintenanceId: string
      opMode: TaskControlOpMode enum
      source: TaskControlOperationSource enum
      operationContext: string
      opStatus: TaskControlStatus
        status: TaskOperationStatusCode enum
        message: string
      taskVersion: i32
      creationTimeMs: i64
      shardmgrDeprecatedId: string [Deprecated]
    pausedRequestedOps: list<TaskOperation> — same structure as requestedOps
    ackedOps: list<TaskOperation> — same structure as requestedOps
    jobTaskControlInfo: JobTaskControlInfo
      lastRpcTimestampMs: i64
      rpcSeqNum: i64
      lastSuccessfulRpcTimestampMs: i64
    activeUnavailabilityEvents: list<TaskUnavailabilityEvent>
      eventID: string
      taskID: i32
      taskVersion: i32
      impact: ImpactType enum
      startTimeInSeconds: i64
      durationInSeconds: i64
      responseDeadlineInSeconds: i64
      creationTimeInSeconds: i64
      context: string
      configUpdate: bool [Deprecated]
      expectedActions: list<TaskUnavailabilityActionType enum>
      isMaintenanceTrain: bool
    allowedUnavailabilityActions: list<TaskUnavailabilityAction>
      eventID: string
      taskID: i32
      taskVersion: i32
      actionType: TaskUnavailabilityActionType enum
```

---

## 2. Field Reference

### `jobHandle` *(string)*

The physical job handle in `<scheduler>/<owner>/<name>` format (e.g. `tsp_prn/myteam/my_service`). Identifies the job whose task control state is represented.

---

### `taskControlStatus` *(JobTaskControlStatusResponse)*

The full task control status from the scheduler. Defined in `tupperware/if/SchedulerService.thrift`.

#### `requestedOps` *(list\<TaskOperation\>)*

Operations requested by the scheduler but **not yet acknowledged** by the task controller. These are pending operations waiting for the controller to review and approve.

#### `pausedRequestedOps` *(list\<TaskOperation\>)*

Operations requested by the scheduler but **paused** via `twcli pause-update`. These operations will not be delivered to the task controller until unpaused.

#### `ackedOps` *(list\<TaskOperation\>)*

Operations that have been **acknowledged** (cleared for execution) by the task controller. An operation on this list may or may not have completed — the task controller is not required to report completion.

#### `jobTaskControlInfo` *(JobTaskControlInfo)*

Information about the health of the RPC channel between the scheduler and the task controller:

- **`lastRpcTimestampMs`** *(i64)*: Timestamp (ms) of the last RPC call from scheduler to task controller.
- **`rpcSeqNum`** *(i64)*: Current sequence number of the scheduler → task controller RPC.
- **`lastSuccessfulRpcTimestampMs`** *(optional, i64)*: Timestamp (ms) of the last successful RPC. Useful for diagnosing connectivity issues.

#### `activeUnavailabilityEvents` *(list\<TaskUnavailabilityEvent\>)*

Active unavailability events requested by the scheduler. These represent upcoming disruptions (maintenance, decommissions, etc.) that the task controller must respond to.

#### `allowedUnavailabilityActions` *(list\<TaskUnavailabilityAction\>)*

Actions that the task controller has decided to take in response to unavailability events. Each action references an event by `eventID` and specifies what the scheduler should do (preempt, stop, stay, upsize, or shrink).

---

### TaskOperation Fields

Defined in `tupperware/if/TaskControl.thrift`. Represents a single lifecycle operation for a task.

- **`operationID`** *(string)*: Unique identifier for this operation, assigned by the scheduler. Opaque to the task controller.
- **`taskID`** *(i32)*: Which task this operation applies to (zero-based index).
- **`taskVersion`** *(i32)*: The version of the task this operation targets.
- **`taskOp`** *(TaskControlTaskOp)*: The operation type — a union of:
  - `taskGrow` — Add a new task to the job and start it.
  - `taskStart` — Start an existing task.
  - `taskRestart` — Restart a task on the same host. Has `allowDynamicUpsize` (bool) and `isMutableConfigUpdate` (bool for mutable config updates).
  - `taskStop` — Stop a task.
  - `taskShrink` — Remove a task from the job. May include `unavailability` metadata and `operation` metadata.
  - `taskMoveByUserRestriction` — User-requested preemption. Has `allowDynamicUpsize`.
  - `taskMoveByAllocationChange` — Task must move due to allocation change. Has `reason` (TaskMoveByAllocationChangeReason), optional `drainInfo`, and `allowDynamicUpsize`.
  - `taskDrain` **[Deprecated]** — Use `TaskUnavailabilityEvent` instead.
- **`opMode`** *(TaskControlOpMode)*: Processing mode — `NORMAL` (safe pacing) or `FORCE` (relaxed throttling, e.g. user-requested immediate update).
- **`source`** *(TaskControlOperationSource)*: Who initiated the operation — `USER`, `SCHEDULER`, or `SYSTEM`.
- **`operationContext`** *(string)*: Human-readable context useful for logging and debugging.
- **`opStatus`** *(optional, TaskControlStatus)*: Current status of the operation, containing `status` (TaskOperationStatusCode) and `message`.
- **`creationTimeMs`** *(i64)*: Time when the operation was first created (ms since epoch). Note: timing semantics vary by operation type — see thrift source comments for details.
- **`shardmgrDeprecatedId`** *(string)* **[Deprecated]**: Internal field used by ShardManager only. Do not use.

---

### TaskUnavailabilityEvent Fields

Defined in `tupperware/if/TaskControl.thrift`. Represents an upcoming disruption to a task.

- **`eventID`** *(string)*: Unique identifier for this event.
- **`taskID`** *(i32)*: Task this event applies to.
- **`taskVersion`** *(i32)*: Task version this event applies to.
- **`impact`** *(ImpactType)*: The type of impact — see Impact Types table above.
- **`startTimeInSeconds`** *(i64)*: When the unavailability begins (UTC seconds). If `> 0`, the task controller must comply with a strict SLA. If `<= 0`, the scheduler waits for the controller to respond.
- **`durationInSeconds`** *(i64)*: How long the unavailability lasts. If maintenance gets extended, a new event with zero duration is sent.
- **`responseDeadlineInSeconds`** *(i64)*: Deadline for the task controller to respond (UTC seconds). Computed as `startTimeInSeconds - jobSpec.killTimeout`. If `0`, the scheduler waits indefinitely.
- **`creationTimeInSeconds`** *(i64)*: When this event was created (UTC seconds).
- **`context`** *(string)*: Human-readable event details including the event type, ID, and impact.
- **`configUpdate`** *(bool)* **[Deprecated]**: Internal scheduler field. Will be replaced.
- **`expectedActions`** *(list\<TaskUnavailabilityActionType\>)*: Set of actions the controller can take. If empty, the controller is not expected to respond. If the controller responds with an action not in this list, the scheduler will not apply it.
- **`isMaintenanceTrain`** *(optional, bool)*: Opt-in for twstorage only. True if the event is part of a maintenance train.

---

### TaskUnavailabilityAction Fields

Defined in `tupperware/if/TaskControl.thrift`. The controller's response to an unavailability event.

- **`eventID`** *(string)*: The unavailability event being responded to.
- **`taskID`** *(i32)*: Task this action applies to.
- **`taskVersion`** *(i32)*: Task version this action applies to.
- **`actionType`** *(TaskUnavailabilityActionType)*: What the scheduler should do — see Expected Actions table above.

---

## 3. Indexed (Searchable) Fields & Supported Operations

> ⚠️ **Note:** TaskControlOps has no indexed searchable keys currently. Queries are limited to ID-based lookups and association queries.

### Refreshing the indexed-fields list

Use the `help()` API with `objectType:18` and `format:2` to discover searchable fields. See universal-search-syntax.md for the runnable command.

### Supported Association Types

TaskControlOps support association-based queries with:

| Association Type | Description |
|-----------------|-------------|
| `Job` | Find task control operations for a specific job |

For CompareOp values and query syntax, see [universal-search-syntax.md](universal-search-syntax.md).

---

## 4. References

- **Resource.thrift**: `fbcode/tupperware/universal_search/if/Resource.thrift`
- **SchedulerService.thrift**: `fbcode/tupperware/if/SchedulerService.thrift`
- **TaskControl.thrift**: `fbcode/tupperware/if/TaskControl.thrift`
- **ResourceSearch.thrift**: `fbcode/tupperware/universal_search/if/ResourceSearch.thrift`
- **Job Language Reference**: https://www.internalfb.com/wiki/Infra_Cloud/Service_Hosting/Tupperware/Tupperware_Reference/LanguageReference/Job
