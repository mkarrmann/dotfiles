# Tupperware Task Query Skill

**Object Type Enum:** `4` (Task)

**Query Language:** See [universal-search-syntax.md](universal-search-syntax.md) for how to run queries (thriftdbg command syntax, single-line rule, quick-start examples).

## Unique Query Patterns

### Get Tasks for a Job

Use an association filter. **Default to `allFields`** to get the complete task object:

```json
{"request":{"select":{"allFields":{}},"from":4,"where":{"assocFilter":{"assocObjectType":1,"assocObjectIds":["<JOB_HANDLE>"]}},"jsonResponseFormat":{}}}
```

> **Performance tip:** For jobs with many tasks (>20), use `selectedJsonPaths` to reduce response size. See [Fetching Tasks for Large Jobs](#fetching-tasks-for-large-jobs) below.

### Get Tasks with Specific State (Multi-Filter)

To find tasks for a specific job, use the job association filter:

```json
"where": {"assocFilter": {"assocObjectType": 1, "assocObjectIds": ["<JOB_HANDLE>"]}}
```

> **Note:** `$.taskHandle.jobHandle.handle` is not indexed for tasks. Use `assocFilter` with `assocObjectType: 1` (Job) instead of `jsonPathFilter` to find tasks by job handle.

### Find Tasks on a Specific Host (via Server Association)

Use the server association filter (the most reliable method):

```json
"where": {"assocFilter": {"assocObjectType": 5, "assocObjectIds": ["<SERVER_HOSTNAME>"]}}
```

## Key Fields

| JSONPath | Description |
|----------|-------------|
| `$.taskHandle` | Task identity (contains `jobHandle`, `taskID`, `handle`) |
| `$.taskHandle.handle` | Full task handle string, e.g. `tsp_prn/myteam/my_service/0` |
| `$.taskHandle.taskID` | Zero-based task index (i32) |
| `$.taskHandle.jobHandle.handle` | Parent job handle string |
| `$.schedulerState` | Scheduler state enum (see values below) |
| `$.agentState` | Agent-reported state enum |
| `$.allocation.machine.name.hostname` | Hostname of the machine running this task |
| `$.allocation.machine.cluster` | Cluster the machine is in |
| `$.allocation.machine.logicalServerSubType` | Hardware type (e.g. T1_TRN, T1_BGM) |
| `$.allocation.machine.processorType` | Processor type (e.g. TURIN, BERGAMO) |
| `$.allocation.resourceAllotment.id.shapeName` | Capacity shape (e.g. M55) |
| `$.allocation.resourceAllotment.ramBytes` | Allocated RAM in bytes |
| `$.allocation.resourceAllotment.logicalCoresPercentage` | Allocated CPU |
| `$.ports` | Assigned ports (list of `{name, port}`) |
| `$.taskFailures` | Total restart count |
| `$.upTime` | Seconds alive |
| `$.latestTaskInfo` | Latest container info (exitCode, signalCode, startTime, endTime, taskIp) |
| `$.spec.requirements.packages` | Packages with name, version, uuid |

### TaskState enum values

Use the full enum string (not abbreviations) in filters:

| Value | Meaning |
|-------|---------|
| `TASK_STATE_RUNNING` | Running and healthy |
| `TASK_STATE_RUNNING_NOT_HEALTHY` | Running but failing health checks |
| `TASK_STATE_STOPPED` | Stopped |
| `TASK_STATE_STAGING` | Setting up container |
| `TASK_STATE_READY` | Ready for allocation (scheduler state) |
| `TASK_STATE_UNINITIALIZED` | Initial state |
| `TASK_STATE_LOST` | Agent lost contact |
| `TASK_STATE_FAILED` | Failed |
| `TASK_STATE_SHUTTING_DOWN` | Graceful shutdown in progress |
| `TASK_STATE_FETCHING` | Downloading package [Deprecated] |

---

## Fetching Tasks for Large Jobs

When fetching tasks for an entire job (especially jobs with 20+ tasks), the full task object is very large. Use `selectedJsonPaths` to return only what you need:

```json
{"request":{"select":{"selectedJsonPaths":["$.taskHandle","$.schedulerState","$.agentState","$.allocation.machine.name.hostname","$.allocation.machine.cluster","$.allocation.resourceAllotment.id.shapeName"]},"from":4,"where":{"assocFilter":{"assocObjectType":1,"assocObjectIds":["<JOB_HANDLE>"]}},"jsonResponseFormat":{}}}
```

**Common field selections by use case:**

| Use case | Fields to select |
|----------|-----------------|
| Task status overview | `$.taskHandle`, `$.schedulerState`, `$.agentState` |
| Task-to-host mapping | `$.taskHandle`, `$.allocation.machine.name.hostname`, `$.allocation.machine.cluster` |
| Resource allocation check | `$.taskHandle`, `$.allocation.resourceAllotment.id.shapeName`, `$.allocation.resourceAllotment.ramBytes` |
| Package version check | `$.taskHandle`, `$.spec.requirements.packages` |
| Crash investigation | `$.taskHandle`, `$.schedulerState`, `$.taskFailures`, `$.latestTaskInfo` |

> **Note:** `selectedJsonPaths` returns the full subtree rooted at the selected path. Selecting `$.allocation` returns the entire allocation object (machine, resources, placement, etc.).


## Supported Associations

| assocObjectType | Status |
|-----------------|--------|
| Job (1) | ✅ Supported |
| Server (5) | ✅ Supported |
| Reservation (2) | ❌ NOT supported |

---

# Language Reference


# Tupperware Task — Language Reference (Agent Prompt)

This document describes the **Task** resource as defined in the Tupperware Universal Search system. It is intended to be used as an agent prompt so that an LLM can answer questions about Task schema, fields, states, and runtime behavior.

---

## 1. Overview

A **Task** is an individual running instance of a Tupperware Job. Each Job has one or more tasks (replicas), identified by a task ID within the job. A Task represents a single container executing the job's command on a specific host.

In Universal Search, the Task object (`ObjectType.Task`) is the `Task.Task` struct from `tupperware/api/if/Task.thrift`. It contains state from both the **agent** (host-level) and **scheduler** (cluster-level), along with allocation, resource usage, network info, and history.

**Task identity format:** `<cluster>/<user>/<name>/<taskID>` (e.g. `tsp_prn/myteam/my_service/0`)

### Schema Diagram

The diagram below is the **authoritative schema reference** for the Task
resource. All fields, types, `[Deprecated]` and `[Indexed]` annotations are
shown here. Sections 2–3 provide **field-level documentation only** (behavioral
details, caveats, code examples) — they do not repeat the schema.

> **Legend:** `[Indexed]` = field is searchable via `jsonPathFilter`.
> `[Deprecated]` = field is deprecated.

> ⚠️ Task search is not yet available — `[Indexed]` tags will be added once
> the Task object type is deployed in Universal Search.

```yaml
Task.thrift::Task:
  # --- Identity ---
  taskHandle: TaskHandle
    jobHandle: JobHandle
      cluster: string
      user: string
      name: string
      handle: string — "cluster/user/name"
    taskID: i32 — 0-based index
    version: i32 — hotswap version
    handle: string — "cluster/user/name/taskID"
  agentState: TaskState enum
  schedulerState: TaskState enum

  # --- Spec (agent-facing configuration) ---
  spec: TaskSpec
    id: Identifier (name, user, cluster)
    taskID: i32
    taskSpecID: string
    command: Command (command, arguments, unix_user, capabilities, name, depends_on, type)
    logPolicy: LogPolicy (retentionDays, retentionSize, rotationSize, uploadPolicy)
    restartPolicy: RestartPolicy (daemon, maxInstanceRestarts, restartIntervalMicroSeconds, ...)
    requirements: Requirements
      packages: list<Package> (name, version, uuid, isRPM, tag, ...)
      requirements: ResourceLimit (ram, cpu, disk, flash, logicalCoresPercentage, accelerator, ...)
      ports: list<PortSpec> (name, port, healthCheck, healthCheckTimeoutSeconds, startUpGraceSeconds, ...)
      userDirectories: list<UserDirectory> (path, persist, shared, source, ...)
      systemFiles: list<JobSystemFile> (name, alternativeNames, copy, isOptional)
      dsrVips: list<string>
      useAllNics: bool
    killCommand: Command
    preRunCommand: Command
    runtimePolicy: RuntimePolicy (unixUser, unixGroup, standalone, ulimitArguments, ...)
    envVariables: map<string,string>
    killTimeout: i32, default 30
    monitoringPolicy: MonitoringPolicy (monitoringConfig, keyMetrics, healthCheckConfig)
    suicideTimeoutMs: i32
    version: i32
    hotswapPolicy: HotswapPolicy (hotswapType, command, stopOldProcessTimeoutSec, safeSwap)
    tmpfsSize: string
    chrootProfile: string
    ownership: Ownership (org_team, oncall_team)
    tmpPolicy: TmpWatchPolicy (invokeSeconds, directoryThresholdHours)
    secrets: list<SecretSpec> (install_path, name, group, mode, unix_user, unix_group)
    serviceIdentity: string [Deprecated]
    canaryInfo: CanaryInfo
      canaryId: string
      canaryTaskOverride: TaskOverride — mirrors TaskSpec structure (all optional overrides)
      hotswapTypeOnExpire: HotswapType enum
      expireTime: i64
      failureAction: CanaryFailureAction enum
    lxcConfig: LxcConfig (uts_isolation, ipc_isolation, pivot_root, user_isolation, bpf_token)
    stagingTimeoutMs: i32
    preRunSteps: list<Command> (each with name, depends_on for dependency DAG)
    imageConfig: ImageConfig (fbpkg: {name, version}, minRootfsSize, usesTwManagedImage)
    securityPolicy: SecurityPolicy (serviceIdentities, sshCertificates, reservationIdentity, ...)
    userAttributes: map<string,string>
    networkPolicy: TaskNetworkPolicy
      taskIpAllocationPolicy: TaskIpAllocationPolicy — union
        taskServiceIpPolicy: TaskServiceIpPolicy (enableSshd, enableTask, enableStableIp, ...)
        taskVirtualIpPolicy: TaskVirtualIpPolicy (virtualIp)
        taskIpamPolicy: TaskIpamPolicy (networkBackend, taskIp, taskFQDN, ...)
    twAllocationPlacement: string
    schedulerDomain: string
    isRecallable: bool
    sandboxSpec: SandboxSpec (packages, image, extraMounts)
    resourceAssignment: ResourceAssignment (accelerators)
    mutableSpec: MutableTaskSpec (userConfig, version, updateTimeoutSec)
    configPartitionSpecifier: ConfigPartitionSpecifier (targetConfigPartitionLabel, ...)
    taskTemplateMetadata: TaskTemplateMetadata (featureRolloutInfo)
    exitFiles: list<string>
    containerResources: ContainerResources (memoryBytes, logicalCoresPercentage, diskBytes, ...)
    codePartitionSpecifier: PartitionSpecifier (targetPartitionLabel, ...)
    vmConfig: VMConfig
      vm_mode: VMMode — union (process_vm, confidential_vm, container_vm)
      vm_image_config: VmImageConfig (rootfs, kernel, initrd, bootloader)
    evictabilitySignals: TaskEvictabilitySignals (serviceCriticality)

  # --- Allocation ---
  allocation: TaskAllocation
    machine: MachineAllocation
      name: MachineName
        hostname: string
        ipv6: string
        ipv4: string
      enabled: bool
      domain, cluster, rack: string
      serverType: FbServerType enum
      clusterType: ClusterType enum
      processorType: ProcessorType enum
      logicalServerSubType: LogicalServerSubType enum
      hwModelId: i64
      failureDomain: string — MSB
      freeResources: SystemResources (ramBytes, cpuCores, diskBytes, flashBytes, logicalCoresPercentage)
      allocatedResources: SystemResources (same fields)
      holdbackResources: SystemResources (same fields)
      disableNotice: AllocationDisableInFuture (beginTimestamp, debugContext)
      avoidanceReason: AllocationAvoidanceReason (debugContext)
      deviceId: i64
    failure: TaskAllocationFailure [Deprecated]
    placement: Placement (placementId)
    resourceAllotment: ResourceAllotment
      shape: CapacityShape — union (tShirt, rru, fullServer, gpu, gpuGeneric, asic, gpuMig, overcommit)
      id: ResourceAllotmentId
        shapeName: string
        uuid: string
        isAllotmentAssignedByRas: bool
        ownershipType: OwnershipGuaranteeType enum
      ramBytes, logicalCoresPercentage, diskBytes, flashBytes, acceleratorCount: i64
      rruContribution: double
      networkBps: i64
      evictionParameters: EvictionParameters (evictionPriority)
    gangId: string
    gangMemberId: i32
    isSspRoutable: bool

  # --- Runtime state ---
  ports: list<Port> (name, port)
  usage: SystemUsage [Deprecated] — ram, cpu, disk, flash
  hotswap: HotswapInfo (version, swapoutTask: Task — recursive)
  canary: Canary (canaryId, startTime, expireTime, experiment, tag)
  taskFailures: i32 — restart count
  upTime: i32 — seconds alive
  history: list<HistoricalRecord>
    timestamp: i64
    hostPort, hostname: string
    taskInfos: list<TaskInfo> — see latestTaskInfo
    stopContext: ExternalTaskStopContext (contexts, stopConfirmed)
    canary: Canary
  stoppedReason: TaskStoppedReason (stopContext, description)
  netFaultInjectionInfo: NetFaultInjectionInfo (startTimestampSec, maxDurationSec, usedTwFwService)
  containerUniqueID: ContainerUniqueID [Deprecated] (identifier)
  currentCapacityAmount: CapacityAmount — union (rru: {rruValue})

  # --- Latest task info ---
  latestTaskInfo: TaskInfo
    startTime: i32
    endTime: i32 — -1 if running
    agentState: TaskState enum
    exitCode, signalCode: i32
    exitMessage: binary
    pid: i32
    taskIp: string
    ports: list<Port> (name, port)
    sshdEndpoints: list<SshdEndpoint> (ip, port)
    agentStopContext: TaskStopContext (source, reason, extraInformation)
    taskOOMDetected: bool
    exitInfo: ContainerExitInfo (taskStopContext, exitTrigger, exitCompletedTimeSec, unixExitInfo, exitFiles)
    networkInfo: TaskNetworkInfo (networkType, allocatedIP, bgpVips, dsrVips, taskFQDN, ...)
    assignedAcceleratorIDs: set<i32>
    quorumMembership: QuorumMembership (runId, inQuorum, rank)
    uuid: UUID4ForTaskInfo (uuid4)
    taskDomainName: string

  # --- Scheduler observed state ---
  maxSchedulerDelayMs: i64
  taskObservedState: TaskObservedState — scheduler's view
    objectMetadata: ObjectMetadata (jobShardMigrationMetadata, partitionMetadata)
    taskHandle: TaskHandle — see above
    state: TaskState enum
    allocation: TaskObservedAllocation
      placement: Placement (placementId)
      gangId: string
      allotment: ResourceAllotment — see allocation.resourceAllotment above
      resourceAssignment: ResourceAssignment (accelerators)
      hostInfo: HostInfo (hostname, datacenter, cluster, rack, processorType, lsst, ...)
      healthEvents: list<HealthEvent> (timestampMs, eventDetail — union: containerStart, containerExit, healthStateChange, lostAgent)
      allocatedTimestampMs: i64
    containerInfo: ContainerInfo
      taskInstanceHandle: TaskInstanceHandle (job, task, version)
      containerId, containerInstanceUuid
      ports, sshdEndpoints, networkInfo
      startTimeMs, endTimeMs: i64 — endTimeMs ≤ 0 means running
      pid: i32
      exitInfo: ContainerExitInfo — see latestTaskInfo.exitInfo
      killContext: KillContext enum
      taskIp, taskFqdn: string
    pingAgentTimeStampMs: i64
    activePlacementRecords: ActivePlacementRecord (activePlacements)
    gangId, placementId: string
```


---

## 2. Task Field Reference

Defined in `tupperware/api/if/Task.thrift`. See the [Schema Diagram](#schema-diagram)
for the full type hierarchy and annotations.

### `taskHandle` *(TaskHandle)*

Uniquely identifies this task instance.

- **`jobHandle`**: The parent job's identity (cluster, user, name).
- **`taskID`** *(i32)*: Zero-based task index within the job. For a job with `jobSize=10`, task IDs range from 0 to 9.
- **`version`** *(optional, i32)*: Incremented on hotswap. Starts at 0.
- **`handle`** *(string)*: String representation, e.g. `tsp_prn/myteam/my_service/0`.

---

### `agentState` *(TaskState)*

Last known state of this task according to the **agent** (the host it was running on).

> ⚠️ This might not correspond to the state returned by the scheduler if the task is waiting for an allocation. Always check both `agentState` and `schedulerState` for the full picture.

See [TaskState enum](#31-taskstate) for all possible values.

---

### `schedulerState` *(optional, TaskState)*

The state of this task according to the **scheduler**. This is the authoritative state for allocation and lifecycle decisions. When `agentState` and `schedulerState` disagree, the scheduler state represents the intended target.

---

### `spec` *(TaskSpec)*

All configuration for the task that goes to the agent. This fully determines what the agent does in starting/running the task. See [TaskSpec Fields](#taskspec-fields) for full details.

---

### `allocation` *(TaskAllocation)*

Information on where the task is allocated. If allocation failed, contains information explaining why. See [TaskAllocation Fields](#taskallocation-fields).

---

### `ports` *(list of Port)*

Ports that the agent has assigned this task. Each Port has `name` (from `.tw` file, e.g. "thrift", "https") and `port` (actual assigned number).

Port assignments are available inside the container as environment variables: `TW_PORT_{name}` (e.g. `TW_PORT_thrift=17011`).

---

### `usage` *(SystemUsage)* **[Deprecated]**

Agent's measurement of the task's resource usage.

> ⚠️ **Deprecated:** This field is no longer populated and will be removed. Do not rely on it for new integrations.

- **`ram`** *(MemoryUsage)*: `noBufferNoCache` (bytes used directly), `cache` (kernel fs cache), `tmpfs`.
- **`cpu`** *(CpuUsage)*: `cores` (number in use). `pct` is **[Deprecated]**.
- **`disk`** / **`flash`** *(DiskUsage)*: `space` (bytes) and `files` (count).

---

### `hotswap` *(HotswapInfo)*

Hotswap state for the task.

- **`version`**: How many times this task slot has been hotswapped. 0 = never hotswapped.
- **`swapoutTask`**: Recursive reference — if a hotswap is currently in progress, this is the old `Task` that is being gracefully shut down while the new one starts.

---

### `canary` *(optional, Canary)*

Present if the task is part of a canary deployment. Contains `canaryId`, `startTime`, `expireTime`, optional `experiment`, and `tag`.

The environment variable `TW_CANARY_ID` is set inside the container when the task is part of a canary.

---

### `taskFailures` *(optional, i32)*

Total number of task restarts (both agent-initiated and scheduler-initiated). Useful for monitoring task stability.

---

### `upTime` *(optional, i32)*

The amount of time the task has been alive, measured in **seconds**.

---

### `history` *(optional, list of HistoricalRecord)*

Historical result records for this task. Does **not** include log rotation.

- **`timestamp`**: When this historical record was created.
- **`hostPort`** / **`hostname`**: The host where the task ran.
- **`taskInfos`**: List of `TaskInfo` snapshots for this period (see [TaskInfo Fields](#taskinfo-fields)).
- **`stopContext`** *(ExternalTaskStopContext)*: User-facing stop context with a list of `ExternalTaskStopContextEntry` entries (each containing `source`, `reason`, `extraInformation`, `description`) and a `stopConfirmed` boolean.

---

### `stoppedReason` *(optional, TaskStoppedReason)*

Only set if the task is stopped. Contains the stop context and a human-readable description.

---

### `netFaultInjectionInfo` *(optional, NetFaultInjectionInfo)*

Net fault injection info for the task instance, if any fault injection is active. Contains `startTimestampSec`, `maxDurationSec` (0 = stays in fault injection until shutdown), and `usedTwFwService`.

---

### `containerUniqueID` *(ContainerUniqueID)* **[Deprecated]**

Uniquely identifies the container for scheduler-agent communication. `identifier` matches regex `[a-z_][a-z0-9_.]*`, max 160 chars.

> ⚠️ **Deprecated:** This is an internal detail. Customers should use `fullUUID` instead and should not take a dependency on this field.

---

### `currentCapacityAmount` *(optional, CapacityAmount)*

The task's current capacity allocation in RRU (Relative Resource Units). Union with single variant `rru` containing `rruValue` (double).

---

### `latestTaskInfo` *(optional, TaskInfo)*

The most recent task runtime information snapshot. See [TaskInfo Fields](#taskinfo-fields).

---

### `maxSchedulerDelayMs` *(optional, i64)*

Indicates the upper bound of delay/staleness for scheduler-sourced information in this struct. Null means the data is not available.

---

### `taskObservedState` *(TaskObservedState)*

The scheduler's observed state for this task. See [TaskObservedState Fields](#taskobservedstate-fields).

- **`pingAgentTimeStampMs`** *(optional, i64)*: The earliest timestamp of the scheduler's periodic ping to the agent hosts across all tasks of a job. Indicates scheduler data freshness.

---

### TaskSpec Fields

All configuration that goes to the agent. Defined in `tupperware/if/AgentService.thrift`. This is the agent-facing task specification — a subset/projection of the job-level `JobSpec` customized for this specific task.

**Key fields:**
- **`id`**: Job identifier (cluster, user, name).
- **`taskID`**: This task's zero-based index within the job.
- **`taskSpecID`**: Uniquely identifies this TaskSpec; initialized by the scheduler.
- **`command`**: The main command to run. The `unix_user` field defaults to `nobody`; it is recommended to use `fbnobody` (which maps to uid 65534) to avoid conflicts with system user `nobody`. The `capabilities` list grants Linux capabilities (e.g. `NET_BIND_SERVICE`); these are **additive** — default caps are always present. `name` and `depends_on` are only meaningful inside `preRunSteps` (see below).
- **`requirements`**: Resource requirements (CPU, RAM, disk) for this task.
- **`killCommand`** / **`killTimeout`**: Graceful shutdown command and timeout (default 30s). The `kill_command` is invoked only after the main command is running; it is **not** invoked if the task is stopped during `pre_run` steps or package fetch. The kill command receives special stop-context environment variables (e.g. `TW_STOP_SOURCE`, `TW_STOP_REASON`) not available to the main process.
- **`preRunSteps`**: Commands executed before the main command on every restart. Each Command in the list can have a `name` and `depends_on` list to define a dependency DAG; commands with satisfied dependencies execute **in parallel**. If `depends_on` is empty or unset, the command depends on all previously listed commands. A pre-run step failure aborts task startup (exit trigger `EXIT_ON_USER_DEFINED_PRE_RUN_STEPS`).
- **`envVariables`**: Environment variables. Tupperware also sets default variables (see [Task Environment](#4-task-environment)).
- **`suicideTimeoutMs`** *(optional, i32)*: Maximum time (ms) for the task to remain in `RUNNING_NOT_HEALTHY` state before being killed. If unset, the task stays unhealthy indefinitely until the health check `failSeconds` threshold triggers a kill. This is distinct from `failover_timeout_ms` (a job-level setting that controls how long the scheduler waits before restarting an unhealthy task on a new host).
- **`securityPolicy`**: TLS certificates and service identities.
- **`tmpPolicy`** *(optional, TmpWatchPolicy)*: Controls automatic cleanup of `/tmp` inside the container. `invokeSeconds` sets the check interval; `directoryThresholdHours` sets the max age before directories are cleaned. Note: the `/tmp` path inside the container maps to the host filesystem under the task's chroot, **not** to the host `/tmp`.
- **`networkPolicy`**: Network configuration (IP-per-task, etc.).
- **`schedulerDomain`**: Which scheduler domain manages this task (e.g. `tsp_prn`).
- **`isRecallable`**: Whether the task can be recalled/preempted.
- **`exitFiles`**: Files whose creation signals the container should exit.
- **`serviceIdentity`** *(optional)* **[Deprecated]**: Use `securityPolicy.serviceIdentities` instead.
- **`containerResources`** *(optional, ContainerResources)*: Resource limits for the container — `memoryBytes`, `logicalCoresPercentage`, `diskBytes`, plus optional `acceleratorSpec` and `networkLimit`.
- **`mutableSpec`** *(optional, MutableTaskSpec)*: The mutable portion of a task spec — contains `userConfig`, a `version` string to uniquely identify each update, and `updateTimeoutSec`.
- **`evictabilitySignals`** *(optional, TaskEvictabilitySignals)*: Signals for selecting which allotment to evict due to overcommit; contains `serviceCriticality`.
- **`resourceAssignment`** *(optional, ResourceAssignment)*: Machine resource assignments (GPUs, ASICs, etc.) — contains `accelerators` (AcceleratorBundle).
- **`configPartitionSpecifier`** *(optional)*: Config partition specifier for config-state updates.
- **`taskTemplateMetadata`** *(optional)*: Template metadata used by JCP for task generation.
- **`codePartitionSpecifier`** *(optional)*: Code partition specifier for code updates.
- **`vmConfig`** *(optional)*: VM-specific configuration; if set, Tupperware creates a VM instead of a container. Supports `process_vm` (lightweight), `confidential_vm` (SEV encryption), and `container_vm` modes.
- **`restartPolicy`**: Controls restart behavior — `daemon` (restart forever), `maxInstanceRestarts`, exponential backoff settings. `enablePowerLossSirenTaskStop` and `keepRunningOnPowerLossSiren` control behavior during power loss events.
- **`runtimePolicy`**: Process execution context — `unixUser`/`unixGroup` for the main command, `preRunUnixUser`/`preRunUnixGroup` for pre-run steps. `standalone` disables chroot isolation. `ulimitArguments` sets resource limits.
- **`logPolicy`**: Log retention and upload settings — `retentionDays`, `rotationSize`, and `uploadPolicy` (for uploading logs to Manifold storage).
- **`monitoringPolicy`**: Monitoring and health check configuration — `monitoringConfig`, `healthCheckConfig`, and `keyMetrics` for ODS metrics collection.
- **`hotswapPolicy`**: Controls zero-downtime updates — `hotswapType` (e.g. `HOT_SWAP`, `WARM_SWAP`), custom swap command, and `stopOldProcessTimeoutSec`.
- **`canaryInfo`** *(optional)*: Present during canary deployments. Contains override spec (`canaryTaskOverride`) applied on top of the base spec, plus canary metadata (`canaryId`, `startTime`, `expireTime`, `failureAction`).

---

### TaskAllocation Fields

Where the task is allocated. Defined in `tupperware/api/if/Allocation.thrift`.

- **`machine`** *(MachineAllocation)*: The machine this task is allocated to.
- **`placement`** *(Placement)*: Placement identifier — contains `placementId` string.
- **`resourceAllotment`** *(ResourceAllotment)*: The resource allotment backing this task. Contains resource dimensions (`ramBytes`, `logicalCoresPercentage`, `diskBytes`, `flashBytes`, `acceleratorCount`), a `shape` (CapacityShape), unique `id` (ResourceAllotmentId), and optional fields for `acceleratorTopology`, `cpuPinOptions`, `rruContribution`, `networkBps`, and `evictionParameters`.
- **`gangId`** / **`gangMemberId`**: For gang-scheduled tasks, the gang identifier and member ID.
- **`isSspRoutable`** *(optional, bool)*: For SSP use only; indicates if the task is routable.

#### MachineAllocation Fields

- **`name`** *(MachineName)*: Machine identity — `hostname`, `ipv6`, `ipv4`.
- **`enabled`**: Whether the machine is enabled for allocation.
- **`domain`**: Scheduler domain (e.g. `tsp_global`, `tsp_prn`).
- **`cluster`** / **`rack`**: Physical location.
- **`serverType`** / **`clusterType`** / **`processorType`**: Hardware classification enums.
- **`logicalServerSubType`**: Logical server sub-type classification.
- **`failureDomain`**: MSB failure domain for DR purposes.
- **`freeResources`** / **`allocatedResources`** / **`holdbackResources`** *(SystemResources)*: Resource accounting — each contains `ramBytes`, `cpuCores`, `diskBytes`, `flashBytes`, `numAccelerators`, `logicalCoresPercentage`.
- **`disableNotice`** *(optional, AllocationDisableInFuture)*: Notice if allocation will be disabled in the future — contains `beginTimestamp` (i64) and `debugContext` (string).
- **`avoidanceReason`** *(optional, AllocationAvoidanceReason)*: Reason why this machine should be avoided — contains `debugContext` (string).
- **`maxAllocatorSearchDelayMs`** *(optional)*: Upper bound of allocator search delay in milliseconds.
- **`deviceId`** *(i64)*: Device identifier for the machine.

---

### TaskInfo Fields

Runtime information for a task instance. Defined in `tupperware/api/if/Task.thrift`.

**Task state information:**
- **`startTime`** / **`endTime`**: Unix timestamps. `endTime = -1` means still running.
- **`agentState`**: Task state at the time of this snapshot.

**Exit information:**
- **`exitCode`**: Process exit code (0 = success).
- **`signalCode`**: Signal that killed the process (e.g. 9 = SIGKILL, 15 = SIGTERM).
- **`exitMessage`**: Human-readable exit message.
- **`taskOOMDetected`** *(bool)*: Whether an out-of-memory condition was detected.
- **`exitInfo`** *(ContainerExitInfo)*: Detailed container exit info including trigger and timing (see [ContainerExitInfo Fields](#containerexitinfo-fields)).
- **`agentStopContext`**: Why the agent stopped this task (see [TaskStopContext Fields](#taskstopcontext-fields)).

**Runtime information:**
- **`chroot`**: Filesystem root path for the container.
- **`pid`**: Process ID of the main command.
- **`taskIp`**: IP address assigned to the task.
- **`ports`**: Assigned ports.
- **`sshdEndpoints`** *(list of SshdEndpoint)*: SSHD endpoints for `tw ssh` access — each with `ip` and `port`.
- **`networkInfo`** *(TaskNetworkInfo)*: Full network information (see [TaskNetworkInfo Fields](#tasknetworkinfo-fields)).
- **`assignedAcceleratorIDs`**: GPU/accelerator IDs if allocated.
- **`quorumMembership`** *(optional, QuorumMembership)*: For quorum-based gang scheduling — `runId` (zero-based gang auto-restart attempt), `inQuorum` (bool), and `rank` (zero-based rank in quorum).
- **`uuid`** *(UUID4ForTaskInfo)*: Full UUID of the container — contains `uuid4` string.
- **`taskDomainName`** *(optional, string)*: FQDN corresponding to the task IP (equals host hostname if using host IP).

---

### TaskObservedState Fields

The scheduler's observed state. Defined in `tupperware/scheduler/model/TaskObservedState.thrift`.

- **`objectMetadata`** *(ObjectMetadata)*: Metadata for the observed state object — contains `jobShardMigrationMetadata` and `partitionMetadata`.
- **`pingAgentTimeStampMs`** *(optional, i64)*: The earliest timestamp of the scheduler's periodic ping to agent hosts across all tasks of a job. Useful for gauging data freshness of scheduler-sourced fields.
- **`gangId`** *(string)*: The gangId of the currently running task, or the last known gangId. With fault-tolerant gangs, the gangId persists across deallocations and preemptions.

#### TaskObservedAllocation Fields

- **`hostInfo`** *(HostInfo)*: Machine details — `assetId`, `hostname`, `ipv6`, `ipv4`, `failureDomain`, `datacenter`, `cluster`, `rack`, `hwModelId`, `clusterType`, `serverType`, `processorType`, `lsst`.
- **`healthEvents`** *(list of HealthEvent)*: Time-ordered list of health events. Each event has `timestampMs`, `containerId` (ContainerUniqueID), `uuid` (UUID4ForTaskInfo), and `eventDetail` (a union of `containerStart`, `containerExit`, `healthStateChange`, or `lostAgent`).
- **`allocatedTimestampMs`**: When this task was allocated.
- **`resourceAssignment`** *(ResourceAssignment)*: Assigned resources — contains `accelerators` (AcceleratorBundle).

#### ContainerInfo Fields

- **`taskInstanceHandle`** *(TaskInstanceHandle)*: Instance identity — `job` (string), `task` (i32), `version` (i32).
- **`containerId`** *(ContainerUniqueID)*: Container identifier — `identifier` string.
- **`containerInstanceUuid`** *(UUID4ForTaskInfo)*: Container UUID — `uuid4` string.
- **`ports`** *(list of ServicePort)*: Assigned ports — each with `name` and `port`.
- **`sshdEndpoints`** *(list of SshdEndpoint)*: SSHD endpoints — each with `ip` and `port`.
- **`networkInfo`** *(TaskNetworkInfo)*: Network information for the container.
- **`startTimeMs`** / **`endTimeMs`**: Container lifecycle timestamps. `endTimeMs <= 0` means running.
- **`pid`**: Main process PID.
- **`taskIp`** / **`taskFqdn`**: Network identity. `taskIp` is the task IP if using IP-per-task, host IP if not. `taskFqdn` is the task domain name if using IP-per-task, host name if not.
- **`exitInfo`**: Detailed exit information if the container has stopped.
- **`killContext`** *(optional, KillContext enum)*: Why the container was killed. Values include:
  - `WIPE_CONTAINER`, `DISK_SPACE_KILL`, `OVERCOMMIT_KILL`, `HEALTH_CHECK_FAIL`, `AGENT_HELPER_LOST`, `TASK_LOST`, `TASK_CONTROL_KILL`, `TASK_PURGE`, `BY_UNIQUE_HANDLE`
  - Scheduler-initiated: `TASK_KILLED_BY_MAST`, `PRE_FETCH_TASK_KILL`, `TASK_RESTART`, `BELLJAR_TEAR_DOWN`, `TW_JOB_CONTROL_PLANE_KILL`, `MACHINE_MAINTAINANCE`, `OVERLOAD_TASK_KILL`, `UNEXPECTED_TASK`, `ALLOTMENT_EVICTING`

---

### TaskStopContext Fields

Why a task was stopped. Defined in `tupperware/if/TaskStopContext.thrift`. Contains `source` (TaskStopOperationSource), `reason` (TaskStopReason), and `extraInformation` (TaskStopExtraInformation).

The `extraInformation` struct contains:
- **`maintenanceStartTimestamp`** *(optional, i64)*: Set only if stop was triggered by host maintenance/decom. Indicates when the host starts maintenance (Unix epoch seconds).
- **`userStopContext`** *(optional, UserStopContext)*: User-triggered stop context containing `attributes` (map<string,string>).

> ℹ️ Stop context is also available to the `kill_command` via special environment variables inside the container. See [Task Stop Context wiki](https://www.internalfb.com/intern/wiki/Tupperware/Reference/Tupperware_Task_Stop_Context/).

---

### ContainerExitInfo Fields

Detailed exit information. Defined in `tupperware/if/AgentService.thrift`. Contains:

- **`taskStopContext`** *(TaskStopContext)*: Why the container was stopped.
- **`exitMessage`** *(binary)*: Human-readable exit message.
- **`exitTrigger`** *(ExitTrigger enum)*: What triggered the exit.
- **`exitCompletedTimeSec`** *(i64)*: When exit completed.
- **`triggerTimestampSec`** *(i64)*: When the exit was triggered.
- **`unixExitInfo`** *(optional, UnixExitInfo)*: Unix-level exit details — `exitTimestampSec` (i64), optional `exitCode` (i32), optional `signalCode` (i32).
- **`exitFiles`** *(map<string, ExitFile>)*: Collected exit files. Each `ExitFile` is a union of `content` (binary, file content if read successfully) or `error` (ExitFileError, read failure details).

---

### TaskNetworkInfo Fields

Network information for the task. Defined in `tupperware/if/AgentService.thrift`.

- **`networkType`** *(TaskNetworkType)*: `DEFAULT` (no IP-Per-Task), `SSHD_ONLY`, `BPF` (cgroup-bpf), `NETNS`.
- **`allocatedIP`**: IP address allocated to the task.
- **`bgpVips`** / **`dsrVips`**: BGP/DSR VIP addresses.
- **`vmNetworkInfo`** *(list of TaskVMNetworkInfo)*: VM network details — each with `vmIP`, `vmLinkName`, `vmIPPrefixLength`, `vmFQDN`.
- **`taskIPSubnetLength`**: Subnet length for the task IP.
- **`taskFQDN`**: Fully qualified domain name.
- **`vipConfigInfo`** *(TaskVipConfigInfo)*: VIP configuration — contains `BgpConfig` (list of VipAssignmentConfig).
- **`frontEndNICs`** *(list of InterfaceInfo)*: Front-end network interfaces — each with `ifname` and `serviceIP`.

---

## 3. Key Enums

### 3.1 TaskState

The full task lifecycle state machine. Defined in `tupperware/if/common.thrift`.

```thrift
enum TaskState {
  // Agent-visible states
  TASK_STATE_UNINITIALIZED = 0,
  TASK_STATE_STAGING = 1,                          // Setting up container
  TASK_STATE_RUNNING = 2,                           // Main command is running
  TASK_STATE_STOPPED = 3,                           // Cleanly stopped
  TASK_STATE_COMPLETED = 4,                         // Command exited successfully
  TASK_STATE_ABORTED = 5,                           // Command exited with error
  TASK_STATE_KILLED_BY_SIGNAL = 6,                  // Killed by signal
  TASK_STATE_SHUTTING_DOWN = 7,                     // Graceful shutdown in progress
  TASK_STATE_FORCED_SHUT_DOWN = 8,                  // Forcefully shut down (SIGKILL)
  TASK_STATE_ERROR = 9,                             // Agent error
  TASK_STATE_RESOURCE_ERROR = 10,                   // Resource allocation error
  TASK_STATE_LOST = 11,                             // Agent lost contact
  TASK_STATE_RUNNING_NOT_HEALTHY = 12,              // Running but health check failing
  TASK_STATE_FETCHING = 13,                         // Fetching packages [Deprecated]
  TASK_STATE_KILLED_HEALTH_CHECK_FAIL_TIMEOUT = 15, // Killed due to health check timeout
  TASK_STATE_SWAPPING_OUT = 17,                     // Being swapped out (hotswap) [Deprecated]
  TASK_STATE_ALLOCATED = 22,                        // Allocated but not yet staging [Deprecated]
  TASK_STATE_CREATEKEYS = 23,                       // Creating security keys [Deprecated]
  TASK_STATE_RESERVING_MACHINE = 24,                // Reserving machine resources
  TASK_STATE_DISABLING_SMC = 25,                    // Removing from SMC before stop
  TASK_STATE_CREATE_PERSISTENT_RESOURCE = 26,       // Creating persistent storage
  TASK_STATE_KILLING = 27,                          // Kill in progress
  TASK_STATE_PENDING_QUORUM = 28,                   // Waiting for quorum members

  // Scheduler-only states [128-255]
  TASK_STATE_READY = 128,                           // Ready for allocation
  TASK_STATE_ENABLING_SMC = 129,                    // Registering in SMC
  TASK_STATE_CREATING = 130,                        // Creating task on agent
  TASK_STATE_DESTROYING = 131,                      // Destroying task
  TASK_STATE_FINISH = 132,                          // Task lifecycle complete
  TASK_STATE_UPDATING = 133,                        // Spec update in progress
  TASK_STATE_LOST_AGENT = 134,                      // Lost contact with agent
  TASK_STATE_FREEING = 135,                         // Freeing allocated resources
}
```

**Final task states:** `STOPPED`, `COMPLETED`, `ABORTED`, `KILLED_BY_SIGNAL`, `FORCED_SHUT_DOWN`, `ERROR`, `RESOURCE_ERROR`, `LOST`, `KILLED_HEALTH_CHECK_FAIL_TIMEOUT`.

**Common state transitions:**

```text
READY → ALLOCATED → RESERVING_MACHINE → CREATEKEYS → STAGING → FETCHING → RUNNING
RUNNING → SHUTTING_DOWN → STOPPED/COMPLETED/ABORTED
RUNNING → RUNNING_NOT_HEALTHY → KILLED_HEALTH_CHECK_FAIL_TIMEOUT
RUNNING → SWAPPING_OUT (hotswap)
```

---

---

## 4. Task Environment

When a task runs, Tupperware sets up a comprehensive environment inside the container.

### Default Environment Variables

For a job with `cluster='test1_global', user='marcin', name='marcin_0'`:

```bash
TW_CHROOT_PROFILE=btrfs-based
TW_TASK_VERSION=6
TW_JOB_USER=marcin
TW_JOB_CLUSTER=test1_global
TW_JOB_NAME=marcin0
TW_UNIX_USER=nobody          # Recommend fbnobody (uid 65534) to avoid system nobody conflicts
TW_ONCALL_TEAM=mpawlowski5
TW_ORG_TEAM="Core Systems"
TW_TASK_ID=0
TW_PORT_thrift=17011         # One TW_PORT_{name} per declared port
container=lxc
container_uuid=07363b9c-...
FB_PAR_UNPACK_BASEDIR=/pars
CURL_CA_BUNDLE=/etc/pki/tls/certs/fb_certs.pem
```

> ℹ️ `TW_CANARY_ID` is optionally set if the task is part of a canary.

> ℹ️ `kill_command` sees special stop-context environment variables not available to the main process. See [Task Stop Context](https://www.internalfb.com/intern/wiki/Tupperware/Reference/Tupperware_Task_Stop_Context/).

> ⚠️ `container` and `container_uuid` are added for systemd container interface compliance. `container_uuid` is **deprecated** — use the [Task Metadata API](https://www.internalfb.com/intern/wiki/Tupperware/API/Agent_TaskMetadata_API/) instead. These variables may be removed in the future.

### Metadata Files

```text
/etc/tw/api/metadata.json          -- public API, stable fields
/etc/tw/api/user/metadata.json     -- user-defined attributes (from user_attributes)
/etc/tw/api/private                -- do not read, private TW location
/etc/twwhoami                      -- KEY=VALUE environment file (legacy)
```

> ⚠️ `/etc/twwhoami` is not recommended for querying. Prefer querying from the process environment or from the [Task Metadata API](https://www.internalfb.com/intern/wiki/Tupperware/API/Agent_TaskMetadata_API/). This file does **not** contain environment variables from the job spec.

### Mounts & Special Paths

| Path | Description |
|------|-------------|
| `/packages/<name>` | Fbpackage contents (read-only) |
| `/logs` | Task logs (stdout, stderr) — survives restarts |
| `/tw_cores` | Core dumps — survives restarts |
| `/dev/shm` | tmpfs (configurable via `tmpfs_size`) |
| `/tmp` | Container-local tmp directory (cleaned by `tmp_policy`); maps to chroot filesystem, **not** host `/tmp` |
| `/proc`, `/sys` | procfs, sysfs (read-only) |
| `/host-mounts` | Reserved, read-only — **do not use** |
| `/var/facebook/configerator-client` | Configerator filesystem (read-only) |
| `/var/facebook/tupperware/tls` | TLS certs — internal, **do not use directly** |
| `/var/facebook/x509_identities` | Links to TLS certs |
| `/var/facebook/rootcanal` | CA cert (read-only) |
| `/var/facebook/zeus` | Zeus data (read-only) |
| `/var/facebook/smcproxy` | SMC proxy (read-only) |
| `/var/facebook/smc` | SMC (read-only) |
| `/usr/local/fbcode` | fbcode platform (read-only bind mount) |
| `/opt/<tool>` | Debug tools (after `tw ssh --debug-mode`) |
| `/opt/cuda-toolkit` | CUDA binaries (always available) |

> ⚠️ Overriding (mounting over) these paths is not supported and will lead to unexpected results.

### Devices

Each container provides these device nodes:

```text
/dev/full, /dev/fuse, /dev/null, /dev/zero, /dev/random, /dev/urandom,
/dev/ptmx, /dev/tty, /dev/{stdout,stdin,stderr}
```

### Packages

Non-RPM packages are available at `/packages/<package_name>` (read-only). RPM packages are installed into the container filesystem and are not visible at a `/packages` path.

### SSH Access

`/etc/ssh/auth_principals/auth_principals_root` contains host principals plus `root@tw:<jobuser>__<jobname>`.

### Certificates

| Path | Contents |
|------|----------|
| `/var/facebook/tupperware/tls` | Server & client certs + ticket seeds |
| `/var/facebook/x509_identities` | Symlinks to TLS certs |
| `/var/facebook/rootcanal` | CA cert |
| `/etc/pki/tls/certs` | Additional cert bundles |
| `/var/facebook/x509` | Memcache, wormhole certs, etc. |

### Persistent Storage

Containers get a new chroot on each restart; the old one is garbage-collected. For persistent local data, use `user_directories` with `persist=True` (survives restarts on the same host but not host changes). Shared directories (`shared=True`) are namespaced by the job's `user` field. See [Task Local Storage](https://www.internalfb.com/intern/wiki/Tupperware/The_Hacker's_Guide_to_Tupperware/Task_Local_Storage/).

> ⚠️ All local storage solutions do not extend across host boundaries. A preemption or host change will **not** cause data to follow the task.

---

## 5. Indexed (Searchable) Fields & Supported Operations

> ⚠️ **Note:** Task search is not yet available in the production Universal
> Search tier. Once deployed, indexed fields will be annotated inline with
> `[Indexed]` tags in the Schema Diagram above.

Use the `help()` API with `objectType:4` and `format:2` to discover searchable fields. See universal-search-syntax.md for the runnable command.

Task indexed fields will be sourced from the Tupperware API's
`getTaskSearchKeys()` endpoint (including all keys from `TaskIndexingConfig`
plus key aliases).

### Supported Association Types

Tasks support association-based queries with:

| Association Type | Description |
|-----------------|-------------|
| `Job` | Find tasks belonging to a specific job |

For CompareOp values and query syntax, see [universal-search-syntax.md](universal-search-syntax.md).

---

## 6. References

- **Task Environment Wiki**: https://www.internalfb.com/intern/wiki/Tupperware/Reference/AgentApi/TaskEnvironment/
- **Task Environments Wiki**: https://www.internalfb.com/wiki/Infra_Cloud/Service_Hosting/Tupperware/Tupperware_Task_Environments/
- **Task Stop Context**: https://www.internalfb.com/intern/wiki/Tupperware/Reference/Tupperware_Task_Stop_Context/
- **Task Local Storage**: https://www.internalfb.com/intern/wiki/Tupperware/The_Hacker's_Guide_to_Tupperware/Task_Local_Storage/
- **Task Metadata API**: https://www.internalfb.com/intern/wiki/Tupperware/API/Agent_TaskMetadata_API/
- **Task.thrift**: `fbcode/tupperware/api/if/Task.thrift`
- **Common.thrift**: `fbcode/tupperware/api/if/Common.thrift`
- **Allocation.thrift**: `fbcode/tupperware/api/if/Allocation.thrift`
- **AgentService.thrift**: `fbcode/tupperware/if/AgentService.thrift`
- **common.thrift**: `fbcode/tupperware/if/common.thrift`
- **TaskStopContext.thrift**: `fbcode/tupperware/if/TaskStopContext.thrift`
- **TaskObservedState.thrift**: `fbcode/tupperware/scheduler/model/TaskObservedState.thrift`
- **ResourceSearch.thrift**: `fbcode/tupperware/universal_search/if/ResourceSearch.thrift`
