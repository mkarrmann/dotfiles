# Tupperware Reservation Query Skill

**Object Type Enum:** `2` (Reservation)

**Query Language:** See [universal-search-syntax.md](universal-search-syntax.md) for how to run queries (thriftdbg command syntax, single-line rule, quick-start examples).

---

## 1. Overview

A **Reservation** (also called a **RAS Reservation**) is an IaaS capacity entity that represents a materialized amount of capacity in machines — a set of specific machines that Tupperware jobs can use. Unlike legacy Entitlements (which only grant permission), a Reservation guarantees specific machines in the selected pool and ensures allocation of spares for MSB failure tolerance.

Reservations are Level 4 nodes in the Service Account Hierarchy (SAH), backed by the **Resource Allowance System (RAS)**. RAS reads the CapacitySpec configuration and preallocates machines before TW Jobs are allocated.

**Reservation identity format:** Reservation name or ID (e.g. `my_team_reservation`)

There are **four reservation variants**:

| Variant | Spec Union Field | Description |
|---------|-----------------|-------------|
| **Guaranteed Regional** | `reservation` | Regional reservation with guaranteed machine ownership |
| **Elastic Regional** | `elasticReservation` | Regional reservation with elastic (reclaimable) capacity |
| **Global Guaranteed** | `globalReservation` | Global reservation distributed across regions with guaranteed capacity |
| **Global Elastic** | `globalElasticReservation` | Global reservation with elastic capacity across regions |

---

## 2. Schema Diagram

> ⚠️ **The machine count is in `aggregatePhysicalRequirements[].amount`, NOT in `fixedMemoryShape`.**
>
> ⚠️ **`resourceType` is NOT always `1`.** It uses the `ResourceType` enum (see §8) — common values include `SERVER_COUNT` (1), `RELATIVE_UNIT` (100/RRU), `RGPUS` (10), and others. Scripts that only check `resourceType == 1` will silently miss RRU-based and other reservation types. **Always check the `resourceType` before interpreting `amount`.**

```yaml
Resource.thrift::Reservation:
  name: string [Indexed]
  allowanceId: string [Indexed]
  specs: map<string, ReservationSpec>
    (each value is a ReservationSpec union — one of four variants)
    reservation: ResourceReservation
      resourceReservationId: ResourceReservationId
        resourceReservationId: string [Indexed]
      resourceReservationCapacity: CapacitySpecs
        specs: list<CapacitySpec>
          aggregatePhysicalRequirements: list<AggregateResourceSpec>
            resourceType: i32 [Indexed]       # ResourceType enum — see §8
            amount: double [Indexed]          # ⬅️ CAPACITY AMOUNT (unit depends on resourceType)
          baseShapeRequirement: BaseShapeSpec
            logicalServers: list<LogicalServer>
              logicalServerType: i32 [Indexed]
              logicalServerSubTypeAllowlist: list<i32> [Indexed]
              intent: CapacityIntent enum [Indexed]
            subMachineShapes: list<SubMachineShape> — union
              fixedMemoryShape: CapacityShape
                size: MemorySize enum [Indexed]   # "M55", "M64", etc. (NO count!)
                cpuLShapeTraits: CpuLShapeTraits — union
                  highCpu: HighCpu (empty struct)
                  lowCpu: LowCpu
                    minCpuPercentage: i64 [Indexed]
                    minCPUShapeName: MinCPUShapeName enum [Indexed]
                cpuResourceRequirement: CpuResourceRequirement — union
                  rcuBasedShape: RcuBasedShape enum [Indexed]
              customShape: CustomShape
                cpuPercentage: i64 [Indexed]
                ramBytes: i64 [Indexed]
                diskBytes: i64 [Indexed]
                networkBps: i64 [Indexed]
                flashBytes: i64 [Indexed]
              gpuShape: GpuShape
                numGpu: GpuSize enum [Indexed]
              gpuGenericShape: GpuGenericShape
                numGpu: GpuGenericSize enum [Indexed]
              gpuMigShape: GpuMigShape
                numGpu: GpuMigSize enum [Indexed]
              asicShape: AsicShape
                numAsic: AsicSize enum [Indexed]
              groupShape: GroupShape
                groupSize: GroupSize enum [Indexed]
                groupKey: GroupKey enum [Indexed]
                faultTolerance: i32 [Indexed]
              overcommitShape: OvercommitShape
                forceServersWithAllotmentInUse: bool [Indexed]
              multiDimensionalShape: MultiDimensionalShape
                capacityShapeName: CapacityShapeName enum [Indexed]
                capacityShapeDimensions: CapacityShapeDimensions
                  ramGb: i64 [Indexed]
                  cpuRcu: double [Indexed]
                  flashTb: double [Indexed]
      placementPolicy: PlacementPolicy
        resourceUsage: SharedResourceUsage
        proportionalAffinity: ProportionalAffinity
        dcAntiAffinityPolicy: DcAntiAffinityPolicy — union
          dcBlockList: set<string> [Indexed]
          dcAllowList: set<string> [Indexed]
        allowedCluster: string [Indexed]
      tagsToAssign: list<ResourceReservationTag> [Indexed]
      hostProfile: HostProfile
        hostname_scheme: string [Indexed]
        serverProfile: ServerProfile
          id: string [Indexed]
      resourceReservationName: string [Indexed]
      resourceAllowanceId: ResourceAllowanceId
        id: string [Indexed]
      version: i64 [Indexed]
      state: ResourceReservationState enum [Indexed]
      availabilityGuarantee: AvailabilityGuarantee enum [Indexed]
      allocatorOptions: AllocatorOptions
        oncall: string [Indexed]
        stackUp: bool [Indexed]
        holdbackType: HoldbackType enum [Indexed]
        allocatorType: AllocatorType enum [Indexed]
        globalSchedulerDomain: string [Indexed]  # "tsp_global" for global-on-regional reservations
      staticReservationState: StaticReservationState enum [Indexed]
      elasticCushion: ElasticCushion
        minCushionProportion: double [Indexed]
        minCushionServerCount: i64 [Indexed]
      shouldFreezeMaterialization: bool [Indexed]
      capacityDisruptionControl: CapacityDisruptionControl
        maxInFlightPreemptionProportion: double [Indexed]
        maxInFlightPreemptionAbsolute: i32 [Indexed]
        cooldownSeconds: i32 [Indexed]
        isEnabled: bool [Indexed]
    elasticReservation: ElasticResourceReservation
      resourceReservationId: ResourceReservationId
        resourceReservationId: string [Indexed]
      resourceReservationCapacityV2: ElasticCapacitySpecV2
        specs: list<ElasticHourOfDayCapacitySpec>
          baseShapeRequirement: BaseShapeSpec (see above)
          aggregatePhysicalRequirements: list<AggregateElasticResourceSpec>
            resourceType: i32 [Indexed]
            avgAmount: double [Indexed]
            maxAmount: double [Indexed]
      elasticReservationType: ElasticReservationType enum [Indexed]
    globalReservation: GlobalReservationWithVersion
      globalReservation: GlobalReservation
        resourceReservationCapacity: CapacitySpecs (see above)
        placementPolicy: PlacementPolicy (see above)
        globalRegionSet: GlobalRegionSet enum [Indexed]
        globalRegionSetPreferences: map<GlobalRegionSet enum, double> [Indexed]
        globalAvailabilityGuarantee: GlobalAvailabilityGuarantee enum [Indexed]
      version: i64 [Indexed]
    globalElasticReservation: GlobalElasticReservationWithVersion
      globalReservation: GlobalElasticReservation
        resourceReservationCapacityV2: ElasticCapacitySpecV2 (see above)
        globalRegionSet: GlobalRegionSet enum [Indexed]
        globalRegionPreferences: map<string, double> [Indexed]
      version: i64 [Indexed]
  status: map<string, ReservationStatus>
    (each value is a ReservationStatus union — one of four variants)
    status: ResourceReservationStatus
      resourceReservationId: ResourceReservationId
      resourceReservationRequested: CapacitySpecs [Indexed]
      resourceReservationAvailabilityStrict: CapacitySpecs [Indexed]
      resourceReservationUsage: CapacitySpecs [Indexed]
      resourceReservationStatusType: ResourceReservationStatusType enum [Indexed]
      poolName: string [Indexed]
      allocated: CapacityAmount
        lsstAmountMap: map<LogicalServerSubType, double> [Indexed]
      materialized: CapacityAmount
        lsstAmountMap: map<LogicalServerSubType, double> [Indexed]
    elasticStatus: ElasticResourceReservationStatus
      resourceReservationAllocatedV2: ElasticResourceReservationStatusSection [Indexed]
      resourceReservationUsageV2: ElasticResourceReservationStatusSection [Indexed]
    globalStatus: GlobalReservationCapacityMetrics
      requested: CapacitySpecs [Indexed]
      backingRegionalMaterialized: RegionalCapacitySpecs [Indexed]
      backingRegionalUsed: RegionalCapacitySpecs [Indexed]
      isDeficit: bool [Indexed]
    globalElasticStatus: GlobalElasticReservationCapacityMetrics
      requested: ElasticCapacitySpecV2 [Indexed]
      backingRegionalMaterialized: ElasticRegionalCapacitySpecs [Indexed]
```

---

## 3. Python Code to Count Capacity (Normalized to M55)

> ⚠️ **Always check `resourceType` before interpreting `amount`.** The most common types are `SERVER_COUNT` (1) and `RELATIVE_UNIT` (100/RRU), but others exist (see §8). Filtering only on one type will silently miss reservations using other types.

```python
import json

RRU_PER_M55 = 3.8  # Rough average; varies by LSST (3.33 SKL, 4.16 CPL, 5.78 MLN, 3.75 BGM)
SHAPE_GB = {"M3": 3, "M6": 6, "M12": 12, "M24": 24, "M55": 55, "M244": 244}

# Assuming reservations_json is the JSON response from Universal Search
data = json.loads(reservations_json)

total_m55_equiv = 0
for obj_str in data.get('jsonObjects', []):
    obj = json.loads(obj_str)
    for region, spec in obj.get('specs', {}).items():
        reservation = spec.get('reservation', {})
        capacity = reservation.get('resourceReservationCapacity', {})
        for cap_spec in capacity.get('specs', []):
            # Identify shape
            shape_name = None
            base_shape = cap_spec.get('baseShapeRequirement', {})
            for sub_shape in base_shape.get('subMachineShapes', []):
                if 'fixedMemoryShape' in sub_shape:
                    shape_name = sub_shape['fixedMemoryShape'].get('size')

            for agg in cap_spec.get('aggregatePhysicalRequirements', []):
                rt = agg.get('resourceType')
                amount = agg.get('amount', 0)
                if amount == 0:
                    continue
                if rt == 100:  # RELATIVE_UNIT (RRU) — convert to M55 equivalent
                    total_m55_equiv += amount / RRU_PER_M55
                elif rt == 1 and shape_name in SHAPE_GB:  # SERVER_COUNT with known shape
                    total_m55_equiv += amount * (SHAPE_GB[shape_name] / 55.0)
                # Other resourceTypes (RGPUS=10, CORES=6, etc.) need
                # domain-specific conversion — skip or handle as needed

print(f"Total M55 equivalent: {total_m55_equiv:,.0f}")
```

---

## 4. Field Reference

### Reservation Identity

- **`name`** — Human-readable reservation name. Used as a key to look up reservations.
- **`allowanceId`** — The ID of the parent L3 allowance node in the SAH hierarchy.

### Spec Variants (`specs.*`)

The `specs` map keys are region names (e.g. `prn`, `ash`). Each value is a `ReservationSpec` union with exactly one variant set.

#### Guaranteed Regional (`reservation`)

- **`resourceReservationCapacity`** — Capacity specifications expressed as a list of `CapacitySpec`. Each spec defines aggregate resource requirements (resource type + amount), a base shape (logical server types and sub-machine shapes).
- **`placementPolicy`** — Controls how machines are distributed across racks, datacenters, and other topology scopes.
- **`allocatorOptions`** — Allocator behavior settings:
  - `oncall`: The oncall team name responsible for the reservation.
  - `stackUp`: When `true`, the allocator packs jobs onto fewer machines.
  - `allocatorType`: `STANDARD` (default) or `SECOND_LEVEL_SCHEDULER`.
  - `globalSchedulerDomain`: Set to `"tsp_global"` for **global-on-regional** reservations — regional reservations that exclusively serve `tsp_global` jobs. Cannot be changed after creation.
- **`state`** — Reservation lifecycle state: `ACTIVE`, `DELETED`, `DELETION_REQUESTED`
- **`availabilityGuarantee`** — Buffer provisioning: `AS_IS`, `AVAILABLE`, `AVAILABLE_EXCEPT_MSB`
- **`staticReservationState`** — Controls whether machines are frozen: `NOT_APPLICABLE`, `STATIC`, `STATIC_MATERIALIZED`, `STATIC_REFILLABLE`

#### Elastic Regional (`elasticReservation`)

Elastic reservations provide capacity that may be reclaimed at any time by guaranteed reservation owners.

- **`resourceReservationCapacityV2`** — `ElasticCapacitySpecV2` containing hour-of-day-aware capacity specs.
- **`elasticReservationType`** — `PROD`, `EXPERIMENTAL_NPI`, or `GLOBAL_PROD`.

#### Global Guaranteed (`globalReservation`)

Global reservations distribute capacity across regions.

- **`globalRegionSet`** — Hard constraint on which regions the reservation can use.
- **`globalRegionSetPreferences`** — Soft constraint specifying preferred regional distribution.
- **`globalAvailabilityGuarantee`** — Buffer strategy: `NEED_REGION_AND_SFZ_BUFFERS`, `NO_BUFFERS`, `NEED_MULTI_REGION_BUFFERS`.

### Status Variants (`status.*`)

The `status` map keys are region names. Each value is a `ReservationStatus` union with exactly one variant set, matching the spec variant.

> ⚠️ **WARNING: `lsstAmountMap` — Use With Care**
>
> The `allocated` and `materialized` fields use `lsstAmountMap` (keyed by Logical Server Sub Type, e.g. `T1_BGM`, `T1_CPL`, `T1_SKL`). **Do NOT simply sum `lsstAmountMap` values to get a machine count.** Each LSST type has a different weight/capacity equivalent, and this agent does not have an LSST-to-machine-count mapping. Only use `lsstAmountMap` when LSST-level detail is specifically needed (e.g. "how many Skylake vs Cooper Lake machines"). For machine-count comparisons (requested vs. usage), use the `CapacitySpecs`-based fields which express amounts in `aggregatePhysicalRequirements[].amount` — the same unit as the reservation spec.

#### Correct Fields for Requested / Usage / Allocated

**1. Guaranteed Regional** — status variant: `status.<region>.status`

| Metric | Field | Unit | Description |
|--------|-------|------|-------------|
| **Requested** | `.resourceReservationRequested.specs[].aggregatePhysicalRequirements[].amount` | Depends on resourceType (see §8) | How much capacity was requested |
| **Availability** | `.resourceReservationAvailabilityStrict.specs[].aggregatePhysicalRequirements[].amount` | Depends on resourceType (see §8) | How much capacity is available to fill the request |
| **Usage** | `.resourceReservationUsage.specs[].aggregatePhysicalRequirements[].amount` | Depends on resourceType (see §8) | How much capacity is actually in use by tasks |
| **Deficit** | `.resourceReservationDeficit.specs[].aggregatePhysicalRequirements[].amount` | Depends on resourceType (see §8) | Shortfall: requested minus what RAS could fulfill |
| **Allocated (LSST)** | `.allocated.lsstAmountMap` | LSST amounts | Machines assigned, broken down by LSST type (⚠️ not comparable to machine count) |
| **Materialized (LSST)** | `.materialized.lsstAmountMap` | LSST amounts | Hardware on the ground, broken down by LSST type (⚠️ not comparable to machine count) |

Other fields: `resourceReservationStatusType` (`AVAILABLE`, `UNAVAILABLE`, `UPDATING`, `FULLFILLING`), `poolName` (e.g. `twshared`, `twdb`).

**2. Elastic Regional** — status variant: `status.<region>.elasticStatus`

| Metric | Field | Description |
|--------|-------|-------------|
| **Allocated** | `.resourceReservationAllocatedV2` | Elastic capacity currently allocated |
| **Usage** | `.resourceReservationUsageV2` | Elastic capacity currently in use by tasks |

**3. Global Guaranteed** — status variant: `status.<region>.globalStatus`

| Metric | Field | Unit | Description |
|--------|-------|------|-------------|
| **Requested** | `.requested.specs[].aggregatePhysicalRequirements[].amount` | Machine count | How many machines were requested globally |
| **Materialized** | `.backingRegionalMaterialized` | Regional machine counts | Hardware materialized per backing region |
| **Usage** | `.backingRegionalUsed` | Regional machine counts | Machines in use per backing region |
| **Deficit** | `.isDeficit` | Boolean | Whether the global reservation is in deficit |

**4. Global Elastic** — status variant: `status.<region>.globalElasticStatus`

| Metric | Field | Description |
|--------|-------|-------------|
| **Requested** | `.requested` | Elastic capacity spec requested |
| **Materialized** | `.backingRegionalMaterialized` | Elastic capacity materialized per backing region |
| **Usage** | `.backingRegionalUsed` | Elastic capacity in use per backing region |

---

## 5. Key Enums

### ResourceReservationState

```thrift
enum ResourceReservationState {
  ACTIVE = 1,
  DELETED = 2,
  DELETION_REQUESTED = 3,
}
```

### AvailabilityGuarantee

```thrift
enum AvailabilityGuarantee {
  AS_IS = 0,               // No failure buffers
  AVAILABLE = 1,           // RAS guarantees capacity through regional failures
  AVAILABLE_EXCEPT_MSB = 2 // [Deprecated]
}
```

### StaticReservationState

```thrift
enum StaticReservationState {
  NOT_APPLICABLE = 0,
  STATIC = 1,
  STATIC_MATERIALIZED = 2,
  STATIC_REFILLABLE = 3,
}
```

### ResourceReservationStatusType

```thrift
enum ResourceReservationStatusType {
  AVAILABLE = 0,
  UNAVAILABLE = 1,
  UPDATING = 2,
  FULLFILLING = 3,
}
```

### ElasticReservationType

```thrift
enum ElasticReservationType {
  PROD = 0,
  EXPERIMENTAL_NPI = 1,
  GLOBAL_PROD = 2,
}
```

---

## 6. Unique Query Patterns

### Get Reservations for a Job

```json
"where": {"assocFilter": {"assocObjectType": 1, "assocObjectIds": ["<JOB_HANDLE>"]}}
```

### Find Reservations by Allowance

```json
"where": {"jsonPathFilter": {"filters": [{
  "property": "$.allowanceId",
  "cmp": 1,
  "value": "<ALLOWANCE_ID>"
}]}}
```

### Filter by State

Find all active guaranteed reservations:

```json
"where": {"jsonPathFilter": {"filters": [{
  "property": "$.specs.*.reservation.state",
  "cmp": 1,
  "value": "1"
}]}}
```

### Filter by Hostname Scheme (Pool)

Find reservations in the `twshared` pool:

```json
"where": {"jsonPathFilter": {"filters": [{
  "property": "$.specs.*.reservation.hostProfile.hostname_scheme",
  "cmp": 1,
  "value": "twshared"
}]}}
```

### Filter by Oncall

```json
"where": {"jsonPathFilter": {"filters": [{
  "property": "$.specs.*.reservation.allocatorOptions.oncall",
  "cmp": 1,
  "value": "my_oncall_team"
}]}}
```

### Filter by Global-on-Regional (Global Scheduler Domain)

Find reservations using the global-on-regional feature (`globalSchedulerDomain = "tsp_global"`):

```json
"where": {"jsonPathFilter": {"filters": [{
  "property": "$.specs.*.reservation.allocatorOptions.globalSchedulerDomain",
  "cmp": 1,
  "value": "tsp_global"
}]}}
```

### Filter by Global Deficit Status

```json
"where": {"jsonPathFilter": {"filters": [{
  "property": "$.status.*.globalStatus.isDeficit",
  "cmp": 1,
  "value": "true"
}]}}
```

### Get Servers for a Reservation

> ⚠️ **Direct Server→Reservation association NOT supported.**

**Workaround (2-step):**
1. Get jobs using reservation: `from:1, assocFilter: {assocObjectType:2, assocObjectIds:[reservation_id]}`
2. Get servers for jobs: `from:5, assocFilter: {assocObjectType:1, assocObjectIds:[job_ids]}`

---

## 7. Supported Associations

| assocObjectType | Status |
|-----------------|--------|
| Job (1) | ✅ Supported |

---

## 8. Capacity Normalization (Resource Types & RRU)

### Resource Types

`aggregatePhysicalRequirements[].resourceType` is an `i32` whose values come from the `ResourceType` enum (defined in `iaas/resources/ResourceTypes.thrift`). It determines the unit of `amount`:

| Value | Name | Unit | Description |
|------:|------|------|-------------|
| 1 | `SERVER_COUNT` | count | Whole machines or stackable shapes (M55, M24, etc.) |
| 2 | `RAM` | GB | Memory |
| 3 | `DISK` | GB | Disk |
| 4 | `FLASH` | GB | Flash storage |
| 5 | `DISAGG_FLASH` | GB | Disaggregated flash |
| 6 | `CORES` | count | CPU cores |
| 7 | `SERVICE_TIME` | sec/sec | Service time |
| 8 | `NORMALIZED_POWER` | kW | Power |
| 9 | `BLOB_COUNT` | count | Blob count |
| 10 | `RGPUS` | count | rGPU count |
| 100 | `RELATIVE_UNIT` | RRU | Benchmarked relative unit (varies by LSST and RRU table) |

The two most common in practice are `SERVER_COUNT` (1) and `RELATIVE_UNIT` (100). **Always check `resourceType` before interpreting `amount` — do not assume it is always machine count.**

### Normalizing to M55 Equivalents

To compare capacity across different reservation types, normalize to M55 equivalents:

**RRU to M55** — divide by the RRU weight (varies by hardware generation):

| LSST | RRU per server | M55 per server | RRU per M55 |
|------|---------------:|---------------:|------------:|
| T1_SKL (Skylake) | 3.33 | 1 | 3.33 |
| T1_CPL (Cooperlake) | 4.16 | 1 | 4.16 |
| T1_MLN (Milan) | 5.78 | 1 | 5.78 |
| T1_BGM (Bergamo 256GB) | 15.00 | 4 | 3.75 |

Use **~3.8 RRU per M55** as a rough weighted average when the LSST mix is unknown.

**Memory shapes to M55** — convert by memory ratio (the M number is GB of RAM):

| Shape | Memory | M55 Equivalent |
|-------|--------|---------------:|
| M3 | 3 GB | 0.055 |
| M6 | 6 GB | 0.109 |
| M12 | 12 GB | 0.218 |
| M24 | 24 GB | 0.436 |
| M55 | 55 GB | 1.000 |
| M244 | 244 GB | 4.436 |

Formula: `M55_equivalent = amount * (shape_GB / 55)`

---

## 9. References

- **RAS Reservations Wiki**: https://www.internalfb.com/wiki/Infra_Cloud/Service_Hosting/Tupperware/Tupperware_Capacity/Resource_Allowance_System/Reservations/
- **Manage Reservations (Capacity Portal)**: https://www.internalfb.com/wiki/Capacity/Capacity_Management/PRM/CapacityPortal/Actions/ManageReservations/
- **Resource.thrift**: `fbcode/tupperware/universal_search/if/Resource.thrift`
- **ResourceReservation.thrift**: `fbcode/iaas/resource_allowance_system/reservations/if/ResourceReservation.thrift`
