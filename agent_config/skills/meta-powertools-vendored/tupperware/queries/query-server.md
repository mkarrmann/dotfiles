# Tupperware Server Query Skill

**Object Type Enum:** `5` (Server)

**Query Language:** See [universal-search-syntax.md](universal-search-syntax.md) for how to run queries (thriftdbg command syntax, single-line rule, quick-start examples).

## Unique Query Patterns

### Find Servers by Domain

```json
"where": {"jsonPathFilter": {"filters": [{
  "property": "$.domain",
  "cmp": 1,
  "value": "tsp_prn"
}]}}
```

### Find Enabled Servers (Boolean Filter)

Use `cmp:1` (EQ) with string value `"true"`:

```json
"where": {"jsonPathFilter": {"filters": [{
  "property": "$.enabled",
  "cmp": 1,
  "value": "true"
}]}}
```

### Find Servers for a Job

```json
"where": {"assocFilter": {"assocObjectType": 1, "assocObjectIds": ["<JOB_HANDLE>"]}}
```

### Find Servers for a Task

```json
"where": {"assocFilter": {"assocObjectType": 4, "assocObjectIds": ["<TASK_ID>"]}}
```

## Key Fields

| JSONPath | Description |
|----------|-------------|
| `$.name.hostname` | Fully qualified hostname |
| `$.enabled` | Accepts allocations (bool) |
| `$.domain` | Scheduler domain (tsp_prn, etc.) |
| `$.cluster` | Cluster name |
| `$.rack` | Rack identifier |
| `$.serverType` | Server type enum |
| `$.hwModelId` | Hardware model ID (i64) |
| `$.failureDomain` | Failure domain / MSB |


## Supported Associations

| assocObjectType | Status |
|-----------------|--------|
| Job (1) | ✅ Supported |
| Task (4) | ✅ Supported |
| Reservation (2) | ❌ NOT supported |

---

# Tupperware Server (MachineAllocation) — Language Reference (Agent Prompt)

This document describes the **Server** resource as defined in the Tupperware Universal Search system. It is intended to be used as an agent prompt so that an LLM can answer questions about servers, their allocation state, resources, and placement.

---

## 1. Overview

A **Server** (also referred to as a **Machine** or **MachineAllocation**) represents a physical or logical machine in the Tupperware infrastructure. The `MachineAllocation` struct captures the allocator's current view of a machine, including:

- **Machine identity** (hostname, domain, cluster, rack)
- **Resource availability** (free, allocated, holdback resources)
- **Allocated tasks** (tasks currently scheduled on the machine)
- **Hardware classification** (server type, processor type, logical server subtype)
- **Availability status** (enabled, disable notices, avoidance reasons)

In Universal Search, the Server object (`ObjectType.Server`, value `5`) is the `MachineAllocation` struct from `tupperware/api/if/Allocation.thrift`. It wraps all allocation-related state for a single machine.

**Server identity format:** Hostname (e.g., `prn001.prn1.facebook.com`).

### Schema Diagram

The diagram below is the **authoritative schema reference** for the Server resource. All fields, types, `[Deprecated]` and `[Indexed]` annotations are shown here. Sections 2–3 provide **field-level documentation only** (behavioral details, caveats, code examples) — they do not repeat the schema.

> **Legend:** `[Indexed]` = field is searchable via `jsonPathFilter`.
> `[Deprecated]` = field is deprecated.
>
> ⚠️ Server search indexing is limited — see "Indexed Fields & Operations" section for current status.

```yaml
Allocation.thrift::MachineAllocation:
  name: MachineName [Indexed]
    hostname: string [Indexed]
    ipv6: string
    ipv4: string
  allocationState: AllocationState enum [Deprecated]
  enabled: bool [Indexed]
  allocatedTasks: list<TaskHandle>
    jobHandle: JobHandle
      cluster: string [Indexed]
      user: string [Indexed]
      name: string [Indexed]
      handle: string [Indexed]
    taskID: i32
    version: i32 (optional)
    handle: string
  freeResources: SystemResources
    ramBytes: i64
    cpuCores: i32
    diskBytes: i64
    flashBytes: i64
    numAccelerators: i32
    logicalCoresPercentage: i64
  allocatedResources: SystemResources
    ramBytes: i64
    cpuCores: i32
    diskBytes: i64
    flashBytes: i64
    numAccelerators: i32
    logicalCoresPercentage: i64
  allocatedPorts: list<i32> [Deprecated]
  domain: string [Indexed]
  cluster: string [Indexed]
  rack: string [Indexed]
  serverType: FbServerType enum [Indexed]
  clusterType: ClusterType enum [Indexed]
  processorType: ProcessorType enum [Indexed]
  hwModelId: i64 [Indexed]
  failureDomain: string [Indexed]
  holdbackResources: SystemResources
    ramBytes: i64
    cpuCores: i32
    diskBytes: i64
    flashBytes: i64
    numAccelerators: i32
    logicalCoresPercentage: i64
  disableNotice: AllocationDisableInFuture (optional)
    beginTimestamp: i64
    debugContext: string
  avoidanceReason: AllocationAvoidanceReason (optional)
    debugContext: string
  logicalServerSubType: LogicalServerSubType enum [Indexed]
  maxAllocatorSearchDelayMs: i64 (optional)
  deviceId: i64 [Indexed]
```

---

## 2. Field Reference

### `name` *(MachineName)*

The machine's identity, containing hostname and IP addresses.

- **`hostname`** *(string)*: Fully qualified hostname (e.g., `prn001.prn1.facebook.com`).
- **`ipv6`** *(string)*: IPv6 address of the machine.
- **`ipv4`** *(string)*: IPv4 address of the machine.

---

### `allocationState` *(AllocationState enum)* **[Deprecated]**

Legacy field representing the machine's allocation state. No longer populated — check `enabled` instead.

---

### `enabled` *(bool)*

Whether the machine is enabled for allocation. A machine that the scheduler cannot communicate with for too long becomes disabled. Disabled machines will not receive new task allocations.

---

### `allocatedTasks` *(list\<TaskHandle\>)*

Tasks currently allocated to this machine according to the allocator.

> ⚠️ **Caveat:** This list does not always line up precisely with where a task is currently running, as the scheduler must communicate with agents to make reality match the allocation.

Each `TaskHandle` contains:
- **`jobHandle`** *(JobHandle)*: Reference to the parent job (cluster, user, name, handle).
- **`taskID`** *(i32)*: Task identifier within the job.
- **`version`** *(optional i32)*: Task version (latest if not set).
- **`handle`** *(string)*: Full handle (`<cluster>/<user>/<name>/<taskID>[:version]`).

---

### `freeResources` *(SystemResources)*

Resources currently free and available for allocation on this machine.

> ℹ️ **Resource Accounting:** `freeResources + allocatedResources + holdbackResources` should sum to the total resources specified for the machine in SeRF (Service Registry Framework).

---

### `allocatedResources` *(SystemResources)*

Resources currently allocated to tasks on this machine.

---

### `allocatedPorts` *(list\<i32\>)* **[Deprecated]**

Legacy field for fixed port allocations. If a task using a fixed port lands on this machine, the port would appear in this list. This field is no longer reliably populated.

---

### `domain` *(string)*

The scheduler domain this machine belongs to. Examples:
- `tsp_global` — Global TSP scheduler
- `tsp_prn` — PRN region scheduler
- `prn1c13` — Specific cluster scheduler

---

### `cluster` *(string)*

Cluster name as defined in SeRF.

---

### `rack` *(string)*

Rack identifier as defined in SeRF.

---

### `serverType` *(FbServerType enum)*

Server type classification from SeRF. Determines hardware capabilities and use cases.

---

### `clusterType` *(ClusterType enum)*

Cluster type classification (e.g., production, staging, development).

---

### `processorType` *(ProcessorType enum)*

Processor type from the resources configuration. Indicates CPU generation and capabilities.

---

### `hwModelId` *(i64)*

Hardware model identifier. Used for detailed hardware classification and compatibility checks.

---

### `failureDomain` *(string)*

Failure domain (also known as MSB — Machine Service Block) this machine belongs to. Tasks can use failure domain constraints to ensure high availability across independent failure zones.

---

### `holdbackResources` *(SystemResources)*

Resources reserved for system use (agent overhead, OS requirements, etc.). These resources are not available for task allocation.

See: https://www.internalfb.com/intern/wiki/Tupperware/Reference/Holdback/

---

### `disableNotice` *(optional AllocationDisableInFuture)*

Advance notice of machine disabling events in the future. When present, indicates the machine will become unavailable.

- **`beginTimestamp`** *(i64)*: Actual begin time of the event (e.g., 24 hours in the future). If more than one underlying AdvanceNotice UE exists concurrently, this is the earliest begin time.
- **`debugContext`** *(string)*: Debug context/information about the disable event.

---

### `avoidanceReason` *(optional AllocationAvoidanceReason)*

Unavailability events (UEs) that don't technically disable the machine but indicate it should be avoided for new allocations (e.g., `maybeUnhealthyUnavailability`).

- **`debugContext`** *(string)*: Debug information about the avoidance reason.

---

### `logicalServerSubType` *(LogicalServerSubType enum)*

Logical server subtype classification. Provides a fine-grained categorization of the machine's capabilities beyond the basic server type.

---

### `maxAllocatorSearchDelayMs` *(optional i64)*

Upper bound of 'delay' or 'staleness' for the Allocator Search information in this struct. Indicates how fresh the allocation data is. When `null`, the data freshness is not available.

---

### `deviceId` *(i64)*

Device identifier for the machine.

---

## 3. SystemResources Struct

The `SystemResources` struct represents resources on a machine used by Tupperware for allocation decisions.

| Field | Type | Description |
|-------|------|-------------|
| `ramBytes` | i64 | RAM in bytes |
| `cpuCores` | i32 | Number of CPU cores |
| `diskBytes` | i64 | Disk space in bytes |
| `flashBytes` | i64 | Flash storage in bytes |
| `numAccelerators` | i32 | Number of accelerators (GPUs, ASICs, etc.) |
| `logicalCoresPercentage` | i64 | Percentage of logical cores available |

---

## 4. Indexed (Searchable) Fields & Supported Operations

### Current Indexing Status

> ⚠️ **Note:** Server search indexing is currently limited. Not all fields shown in the schema are indexed. The indexed fields list below reflects what is currently searchable.

### Indexed Fields Summary

| Category | Example JSONPath | Type |
|----------|-----------------|------|
| **Hostname** | `$.name.hostname` | String |
| **Enabled** | `$.enabled` | Boolean |
| **Domain** | `$.domain` | String |
| **Cluster** | `$.cluster` | String |
| **Rack** | `$.rack` | String |
| **Server Type** | `$.serverType` | Enum |
| **Cluster Type** | `$.clusterType` | Enum |
| **Processor Type** | `$.processorType` | Enum |
| **HW Model ID** | `$.hwModelId` | Numeric |
| **Failure Domain** | `$.failureDomain` | String |
| **Logical Server SubType** | `$.logicalServerSubType` | Enum |
| **Device ID** | `$.deviceId` | Numeric |
| **Allocated Task Job** | `$.allocatedTasks.*.jobHandle.handle` | String |

### Refreshing the indexed-fields list

Use the `help()` API with `objectType:5` and `format:2` to discover searchable fields. See universal-search-syntax.md for the runnable command.

### Supported Association Types

| Association Type | Description |
|-----------------|-------------|
| `Job` | Find servers associated with a specific job |
| `Task` | Find servers where a specific task is allocated |

> ⚠️ Server→Reservation association is **NOT supported**. To find servers in a reservation, query the reservation's jobs first, then query servers by job association.

For CompareOp values and query syntax, see [universal-search-syntax.md](universal-search-syntax.md).

---

## 5. References

- **Allocation.thrift**: `fbcode/tupperware/api/if/Allocation.thrift`
- **Common.thrift**: `fbcode/tupperware/api/if/Common.thrift`
- **Allocator.thrift**: `fbcode/tupperware/if/Allocator.thrift`
- **ResourceSearch.thrift**: `fbcode/tupperware/universal_search/if/ResourceSearch.thrift`
- **Holdback Documentation**: https://www.internalfb.com/intern/wiki/Tupperware/Reference/Holdback/
- **Job Language Reference**: `fbcode/tupperware/universal_search/if/job_language_reference.md`
- **Task Language Reference**: `fbcode/tupperware/universal_search/if/task_language_reference.md`
