# Tupperware Allowance Query Skill

**Object Type Enum:** `11` (Allowance)

**Query Language:** See [universal-search-syntax.md](universal-search-syntax.md) for how to run queries (thriftdbg command syntax, single-line rule, quick-start examples).

---

## 1. Overview

An **Allowance** is a RAS (Resource Allowance System) primitive that defines fine-grained resource limits at an aggregate level. It is a number that determines how many resources (machine counts or RRU units) you are allowed to use. Allowances serve as parent nodes in the SAH (Server Accounting Hierarchy) tree, constraining how much aggregate capacity can be granted for use by child Allowances and Reservations.

When you create a Reservation, it must come from a parent Allowance. If the sum of child reservations would exceed the allowance, RAS refuses the request. The SAH tree structure is:

| Level | Node Type | Description |
|-------|-----------|-------------|
| **L1** | Product Group (PG) | Top-level allowance node (~30 divisions). Always enforced. |
| **L2** | Subdivision | Groups of related services |
| **L3** | Subdivision | Capacity managed by a team |
| **L4** | Reservation | Leaf nodes — the actual materialized machines |

**Allowance identity format:** Allowance name or ID (e.g. `my_team_allowance`).

---

## 2. Schema Diagram

> **Legend:** `[Indexed]` = field is searchable via `jsonPathFilter`.

```yaml
Resource.thrift::Allowance:
  name: string [Indexed]
  status: map<string, ResourceAllowanceStatus>
    resourceAllowance: ResourceAllowance
      resourceAllowanceId: ResourceAllowanceId
        id: string [Indexed]
      parentResourceAllowanceId: ResourceAllowanceId
        id: string [Indexed]
      version: i64 [Indexed]
      allowanceCapacity: AllowanceCapacity
        resourceAllowanceCapacityMap: map<string, CapacitySpecs>
          specs: list<CapacitySpec> [Indexed]
            aggregatePhysicalRequirements: list<AggregateResourceSpec> [Indexed]
              resourceType: i32 [Indexed]
              amount: double [Indexed]
            baseShapeRequirement: BaseShapeSpec
              logicalServers: list<LogicalServer> [Indexed]
                logicalServerType: i32 [Indexed]
                logicalServerSubTypeAllowlist: list<i32> [Indexed]
                intent: CapacityIntent enum [Indexed]
              subMachineShapes: list<SubMachineShape> — union [Indexed]
                fixedMemoryShape: CapacityShape
                  size: MemorySize enum
                customShape: CustomShape
                  cpuPercentage: i64
                  ramBytes: i64
                  diskBytes: i64
                gpuShape: GpuShape
                  numGpu: GpuSize enum
                multiDimensionalShape: MultiDimensionalShape
                  capacityShapeName: CapacityShapeName enum
                  capacityShapeDimensions: CapacityShapeDimensions
                    ramGb: i64
                    cpuRcu: double
                    flashTb: double
      enforced: bool [Indexed]
      priorityGroups: list<string> [Indexed]
      capacityDisruptionControl: CapacityDisruptionControl
        isEnabled: bool [Indexed]
        capacityDisruptionBudget: CapacityDisruptionBudget — union
          maxNumber: double
          maxPercentage: double [Indexed]
        doesPCLCountAgainstBudget: bool [Indexed]
        doesRandomFailureCountAgainstBudget: bool [Indexed]
      datacenterQuotaLimit: DatacenterQuotaLimit — union [Indexed]
        proportionalDcAntiAffinities: ProportionalDcAntiAffinities [Indexed]
          dcNameToMaxCapacity: map<string, double> [Indexed]
        uniformDcAntiAffinities: UniformDcAntiAffinities
          datacenters: set<DatacenterName enum>
    resourceAllowanceStatusType: ResourceAllowanceStatusType enum [Indexed]
    resourceAllowanceStatusReason: ResourceAllowanceStatusReason enum [Indexed]
    sahTreeCapacityStats: map<string, CapacityStats>
      requested: CapacitySpecs [Indexed]
      available: CapacitySpecs [Indexed]
      used: CapacitySpecs [Indexed]
      capacityStatsSources: list<CapacityStatsSource enum> [Indexed]
    allocationAccountingStats: map<string, AllocationAccountingStats> [Indexed]
      used: CapacityAmount
        lsstAmountMap: map<LogicalServerSubType, double> [Indexed]
      available: CapacityAmount
        lsstAmountMap: map<LogicalServerSubType, double> [Indexed]
    lastUpdatedTimeStampMs: i64 [Indexed]
```

---

## 3. Memory Shapes (M55, M64, etc.)

**M55, M64, M32 are NOT hardware model IDs.** They are **MemorySize enum values** (strings like `"M55"`, not integers).

| Shape | Typical Use |
|-------|-------------|
| M32 | Medium workloads |
| M55 | Production services |
| M64 | Large workloads |
| M244 | Memory-intensive apps |

```thrift
enum MemorySize {
  M1 = 1, M2 = 2, M3 = 3, M4 = 4, M6 = 6, M8 = 8,
  M12 = 12, M16 = 16, M24 = 24, M32 = 32,
  M26 = 26, M36 = 36, M42 = 42, M52 = 52, M55 = 55,
  M64 = 64, M84 = 84, M244 = 244,
}
```

---

## 4. Counting M55s Under an Allowance

> ⚠️ **The machine count is in `aggregatePhysicalRequirements[].amount`, NOT in `fixedMemoryShape`.**

To count M55s, you need to:
1. Query the allowance by name → get the allowance UUID
2. Query reservations with `$.allowanceId = UUID`
3. For each reservation, parse the capacity structure and match shapes

### Reservation Capacity Schema (Critical)

```yaml
specs: map<region, ReservationSpec>
  reservation: ResourceReservation
    resourceReservationCapacity: CapacitySpecs
      specs: list<CapacitySpec>
        aggregatePhysicalRequirements: list<AggregateResourceSpec>
          resourceType: i32        # 1 = machines
          amount: double           # ⬅️ COUNT OF MACHINES IS HERE
        baseShapeRequirement: BaseShapeSpec
          subMachineShapes: list<SubMachineShape>
            fixedMemoryShape: CapacityShape
              size: MemorySize enum  # "M55", "M64", etc. (NO count here!)
```

### Python Code to Count M55s

```python
import json

# Assuming reservations_json is the JSON response from Universal Search
data = json.loads(reservations_json)

m55_count = 0
for res in data.get('results', []):
    for region, spec in res.get('specs', {}).items():
        reservation = spec.get('reservation', {})
        capacity = reservation.get('resourceReservationCapacity', {})
        for cap_spec in capacity.get('specs', []):
            # Check shape
            base_shape = cap_spec.get('baseShapeRequirement', {})
            for sub_shape in base_shape.get('subMachineShapes', []):
                fixed_mem = sub_shape.get('fixedMemoryShape', {})
                if fixed_mem.get('size') == 'M55':
                    # Get count from aggregatePhysicalRequirements
                    for agg in cap_spec.get('aggregatePhysicalRequirements', []):
                        if agg.get('resourceType') == 1:  # 1 = machines
                            m55_count += int(agg.get('amount', 0))

print(f"Total M55 count: {m55_count}")
```

---

## 5. Field Reference

### ResourceAllowance Fields

- **`resourceAllowanceId`** — Unique allowance identifier. Global scope.
- **`parentResourceAllowanceId`** — Parent allowance in the SAH tree. Global scope.
- **`version`** — Current version of the allowance object. Regional scope.
- **`allowanceCapacity`** — Resource capacity allowance. Contains `resourceAllowanceCapacityMap`: a map keyed by pool name to `CapacitySpecs`. Regional scope.
- **`enforced`** — Whether the allowance is enforced across all pools. Global scope.
  - **Enforced:** Requested capacity comes from explicit values in the Allowance.
  - **Unenforced/Inherited:** Requested capacity is implicit from the parent's available amount.
- **`capacityDisruptionControl`** — Disruption control configured at the allowance level.
- **`datacenterQuotaLimit`** — Default value for datacenter quota limits for reservations under this allowance.

### CapacityDisruptionControl Fields

- **`isEnabled`** — Controls whether disruption control settings are applied.
- **`capacityDisruptionBudget`** — The disruption budget (maxNumber or maxPercentage).
- **`doesPCLCountAgainstBudget`** — Whether PCL preemptions count against the budget.
- **`doesRandomFailureCountAgainstBudget`** — Whether random failure preemptions count against the budget.

### ResourceAllowanceStatus Fields

- **`resourceAllowanceStatusType`** — `AVAILABLE` or `UNAVAILABLE`.
- **`resourceAllowanceStatusReason`** — `DECOMMISSION` or `FAILURE`.
- **`lastUpdatedTimeStampMs`** — Timestamp of the last status update.

#### Capacity Stats Fields

There are two capacity stats systems at the allowance level. Use the right one depending on what you need:

| Field | Unit | When to use | Notes |
|-------|------|-------------|-------|
| **`sahTreeCapacityStats`** | Machine count (`CapacitySpecs`) | Comparing requested vs. available vs. used in machine-count terms | Only populated for **enforced** allowances. Returns `null` for unenforced allowances. |
| **`allocationAccountingStats`** | LSST amounts (`lsstAmountMap`) | When you need LSST-level breakdown | Populated for both enforced and unenforced allowances. |

**`sahTreeCapacityStats`** — Per-pool capacity stats (machine-count units). Each pool maps to:
- `requested` — Total capacity requested by child reservations (`CapacitySpecs`)
- `available` — Remaining capacity available for new reservations (`CapacitySpecs`)
- `used` — Capacity currently in use (`CapacitySpecs`)
- Use `specs[].aggregatePhysicalRequirements[].amount` to extract machine counts.
- **Only populated for enforced allowances.** For unenforced allowances, this field is `null` — query the child reservations directly instead (see "Analyzing Underutilization" below).

**`allocationAccountingStats`** — Per-pool allocation accounting stats. Each pool maps to:
- `used.lsstAmountMap` — Machines in use, keyed by LSST type
- `available.lsstAmountMap` — Machines available, keyed by LSST type

> ⚠️ **WARNING: `lsstAmountMap` — Use With Care**
>
> `lsstAmountMap` keys are Logical Server Sub Types (e.g. `T1_BGM`, `T1_CPL`, `T1_SKL`). **Do NOT simply sum `lsstAmountMap` values to get a machine count.** Each LSST type has a different weight/capacity equivalent, and this agent does not have an LSST-to-machine-count mapping. Only use `lsstAmountMap` when LSST-level detail is specifically needed (e.g. "how many Skylake vs Cooper Lake machines").

#### Analyzing Underutilization

To analyze underutilized capacity under an allowance:

1. Query the allowance by name → get the allowance UUID
2. Query reservations with `$.allowanceId = UUID` (object type 2)
3. For each reservation, compare these status fields per region:
   - **Requested:** `status.<region>.status.resourceReservationRequested.specs[].aggregatePhysicalRequirements[].amount`
   - **Usage:** `status.<region>.status.resourceReservationUsage.specs[].aggregatePhysicalRequirements[].amount`
   - **Underutilized = Requested − Usage**

These fields use the same machine-count unit as the reservation spec and are directly comparable. Do NOT use `allocated.lsstAmountMap` for this comparison — it uses a different unit system.

---

## 6. Key Enums

### ResourceAllowanceStatusType

```thrift
enum ResourceAllowanceStatusType {
  AVAILABLE = 0,
  UNAVAILABLE = 1,
}
```

### ResourceAllowanceStatusReason

```thrift
enum ResourceAllowanceStatusReason {
  DECOMMISSION = 0,
  FAILURE = 1,
}
```

### CapacityIntent

```thrift
enum CapacityIntent {
  DEFAULT = 0,        // Minimum machine requirement
  HASWELL = 1,
  BROADWELL = 2,
  SKYLAKE_SP = 5,
  COOPER_LAKE = 6,
  // ... additional hardware generations ...
  ONLY_HASWELL = 100, // Exact match
  ONLY_LSST = 99998,  // Single logical server subtype
  CUSTOM = 99999,     // Customized list
}
```

---

## 7. Unique Query Patterns

### Find Allowance by Name

```json
"where": {"jsonPathFilter": {"filters": [{
  "property": "$.name",
  "cmp": 1,
  "value": "tw_platform"
}]}}
```

### Find Child Allowances by Parent

```json
"where": {"jsonPathFilter": {"filters": [{
  "property": "$.status.*.resourceAllowance.parentResourceAllowanceId.id",
  "cmp": 1,
  "value": "<PARENT_ALLOWANCE_ID>"
}]}}
```

### Find Reservations Under an Allowance

Use `from:2` (Reservation) with `$.allowanceId` filter (**NOT** `$.resourceAllowanceId`):

```json
"where": {"jsonPathFilter": {"filters": [{
  "property": "$.allowanceId",
  "cmp": 1,
  "value": "<ALLOWANCE_UUID>"
}]}}
```

### Filter by Enforced Status

```json
"where": {"jsonPathFilter": {"filters": [{
  "property": "$.status.*.resourceAllowance.enforced",
  "cmp": 1,
  "value": "true"
}]}}
```

### Filter by Disruption Control Enabled

```json
"where": {"jsonPathFilter": {"filters": [{
  "property": "$.status.*.resourceAllowance.capacityDisruptionControl.isEnabled",
  "cmp": 1,
  "value": "true"
}]}}
```

---

## 8. Indexed (Searchable) Fields Summary

Allowance has **60+ indexed fields**. Key searchable paths:

| Category | Example JSONPath | Type |
|----------|-----------------|------|
| **Identity** | `$.name` | String |
| **Allowance ID** | `$.status.*.resourceAllowance.resourceAllowanceId.id` | String |
| **Parent ID** | `$.status.*.resourceAllowance.parentResourceAllowanceId.id` | String |
| **Version** | `$.status.*.resourceAllowance.version` | Numeric |
| **Enforced** | `$.status.*.resourceAllowance.enforced` | Boolean |
| **Disruption Enabled** | `$.status.*.resourceAllowance.capacityDisruptionControl.isEnabled` | Boolean |
| **Status Type** | `$.status.*.resourceAllowanceStatusType` | Enum |
| **SAH Stats (requested)** | `$.status.*.sahTreeCapacityStats.*.requested.specs.*.aggregatePhysicalRequirements.*.amount` | Numeric |
| **SAH Stats (available)** | `$.status.*.sahTreeCapacityStats.*.available.specs.*.aggregatePhysicalRequirements.*.amount` | Numeric |

### Refreshing the indexed-fields list

Use the `help()` API with `objectType:11` and `format:2` to discover searchable fields. See universal-search-syntax.md for the runnable command.

---

## 9. Supported Associations

| Association Type | Status |
|-----------------|--------|
| Reservation | ✅ Find allowances associated with a specific reservation |
| Job, Task, Server | ❌ NOT supported — query Reservations by `$.allowanceId` instead |

---

## 10. References

- **Resource.thrift**: `fbcode/tupperware/universal_search/if/Resource.thrift`
- **ResourceAllowance.thrift**: `fbcode/iaas/resource_allowance_system/allowances/if/ResourceAllowance.thrift`
- **RASCommon.thrift**: `fbcode/configerator/structs/iaas/resource_allowance_system/allowances/if/RASCommon.thrift`
- **CapacityDisruptionControl.thrift**: `fbcode/iaas/resource_allowance_system/allowances/if/CapacityDisruptionControl.thrift`
