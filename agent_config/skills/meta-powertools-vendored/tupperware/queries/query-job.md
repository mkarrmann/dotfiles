# Tupperware Job Query Skill

**Object Type Enum:** `1` (Job)

**Query Language:** See [universal-search-syntax.md](universal-search-syntax.md) for how to run queries (thriftdbg command syntax, single-line rule, quick-start examples).

## Unique Query Patterns

### Find Jobs by Owner (oncall)

```json
"where": {"jsonPathFilter": {"filters": [{
  "property": "$.schedulerSpec.ownership.oncall_team",
  "cmp": 1,
  "value": "your_oncall_team"
}]}}
```

### Get Jobs by ServiceID

```json
"where": {"jsonPathFilter": {"filters": [{
  "property": "$.schedulerSpec.serviceMetadata.serviceID",
  "cmp": 1,
  "value": "tupperware/api"
}]}}
```

### Find Jobs by Name Pattern (Regex)

```json
"where": {"jsonPathFilter": {"filters": [{
  "property": "$.schedulerSpec.id.name",
  "cmp": 10,
  "value": ".*recommend.*"
}]}}
```

## Key Fields

| JSONPath | Description |
|----------|-------------|
| `$.schedulerSpec.id.cluster` | Cluster name |
| `$.schedulerSpec.id.user` | User/domain |
| `$.schedulerSpec.id.name` | Job name |
| `$.schedulerSpec.ownership.oncall_team` | Oncall team |
| `$.schedulerSpec.serviceMetadata.serviceID` | SMC Service ID |
| `$.status.desiredState.desiredJobState` | Current job state |
| `$.userSpec.handle.domain` | Spec 2.0 domain |
| `$.userSpec.handle.group` | Spec 2.0 group |
| `$.userSpec.handle.name` | Spec 2.0 name |


## Related Queries

Get reservations for a job: `from:2, assocFilter: {assocObjectType:1, assocObjectIds:[JOB_HANDLE]}`

Get tasks for a job: `from:4, assocFilter: {assocObjectType:1, assocObjectIds:[JOB_HANDLE]}`

---

# Language Reference


# Tupperware Job — Language Reference (Agent Prompt)

This document describes the **Job** resource as defined in the Tupperware Universal Search system. It is intended to be used as an agent prompt so that an LLM can answer questions about Job schema, fields, and configuration.

---

## 1. Overview

A **Job** is the core unit of deployment in Tupperware. It represents a service or workload running on Meta's infrastructure. Each Job is uniquely identified by the triple `(cluster, user, name)`.

In Universal Search, the Job object (`ObjectType.Job`) aggregates four sub-structures defined in `tupperware/universal_search/if/Resource.thrift`:

- **`schedulerSpec`** — Low-level scheduler job specification. The internal representation used by the Tupperware scheduler (100+ fields). See [Section 3](#3-schedulerspec-field-reference).
- **`userSpec`** — High-level user-facing job definition (Spec 2.0). This is the preferred way to define jobs. See [Section 2](#2-userspec-field-reference-spec-20).
- **`status`** — Runtime status from the Tupperware API, including desired state, convergence, errors. See [Section 4](#4-job-status).
- **`history`** — List of historical job action records (start, stop, update, etc.). Each record contains a `nujSpecSourceId` used to query the full historical spec via NujSpec (ObjectType 16). See [Section 5](#5-job-history).

### Schema Diagram

The diagram below is the **authoritative schema reference** for the Job
resource. All fields, types, `[Deprecated]` and `[Indexed]` annotations are
shown here. Sections 2–5 provide **field-level documentation only** (behavioral
details, caveats, code examples) — they do not repeat the schema.

> **Legend:** `[Indexed]` = field is searchable via `jsonPathFilter`.
> `[Deprecated]` = field is deprecated.

```yaml
Resource.thrift::Job:
  # ── userSpec (Spec 2.0, NOT indexed — use schedulerSpec paths for queries) ──
  userSpec: job_v2.Job
    handle: Handle
      domain: string                      # e.g. tsp_prn, tsp_global
      group: string                       # namespace (maps to legacy "user")
      name: string
    ownership: Ownership
      oncall: string
    security_policy: SecurityPolicy
      secrets: list<Secret>
      security_domain: string
      identities: list<Identity>
    discovery: Discovery
      service_id: string
      job_tags: list<string>
      smc_bridges: list<SMCBridge>
      load_balancers: list<LoadBalancerBridge>
      deployment_group_id: string
      bgp_vip_config: BgpVipConfig
      binary_flavor_rru: double
    monitoring: Monitoring
      monitoring_config: string
      health_check_config: string
    scheduler_policies: Scheduling
      deployment: DeploymentControl        # limits, task_control (see Section 2)
      restart: Restart enum
      restart_backoff: RestartBackoff enum
      preemption: Preemption — union       # defaultPreset | statefulService | never | zeus | ...
      prefetch: ContainerPrefetch enum
      enable_power_loss_siren_task_stop: bool
      keep_running_on_power_loss_siren: bool
      mast_scheduling: MastScheduling
      gang_scheduling: GangSchedulingConfig
      static_sharding: StaticShardingConfig
      expire_time: i64
    allocation: Allocation — union
      reservation: Reservation
      resource_based_allocation: ResourceBasedAllocation
      follow: Follow
      private_pool: PrivatePool
      sandbox: Sandbox
      mast_allocation: MastAllocation
    scaling: Scaling — union
      manual: Manual
      automatic: Automatic
      capacity: WholeCapacity
    hot_swap: HotSwap
      disabled: HotSwapDisabled
      same_host: HotSwapSameHost
    container_rollouts: list<ContainerRollout>
      rollout_id: string
      rollout_percentage_ppm: i32
      container: Container — union
        classic_container: ClassicContainer  # see Section 2 for full field docs
          packages: list<Package>
          command: Command
          pre_run_commands: list<Command>
          kill_command: Command
          kill_timeout: KillTimeout enum
          environment_variables: map<string,string>
          resources: Resources
          ports: map<PortName,Port>
          security: ContainerSecurity
          network: Network
          logging: Logging
          image: FileSystemImage
          vm_config: VMConfig
    config_sequence: ConfigSequence
    code_sequence: CodeSequence

  # ── schedulerSpec (indexed fields — use these paths in jsonPathFilter) ──
  schedulerSpec: SchedulerService.JobSpec
    id: Identifier
      cluster: string [Indexed]
      user: string [Indexed]
      name: string [Indexed]
    command: Command
      command: string [Indexed]
      arguments: list<string> [Indexed]
    commandLine: string [Indexed]
    jobSize: i32 [Indexed]
    jobSizeFixed: bool [Indexed]
    envVariables: map<string,string> [Indexed]
    killTimeout: i32
    suicideTimeoutMs: i32
    pushTags: list<string> [Indexed]
    expireTime: i64 [Indexed]
    reservationHandle: string [Indexed]
    requirements: Requirements
      packages: list<Package>
        name: string [Indexed]
        uuid: string [Indexed]
        version: i32 [Indexed]
        isRPM: bool [Indexed]
      requirements: SystemResources
        accelerator: AcceleratorSpec
          numAccelerators: i32 [Indexed]
        enableSwap: bool [Indexed]
    constraints: Constraints
      smcTier: string [Indexed]
      smcAllowEmptyTier: bool [Indexed]
      maxSmcTierShrinkLimitPercentage: i32 [Indexed]
    deployment: DeploymentPolicy
      update: DeploymentParameters
        taskControlParameters: TaskControlParameters
          context: string [Indexed]
          taskControllerTier: string [Indexed]
    restartPolicy: RestartPolicy
    runtimePolicy: RuntimePolicy
      unixUser: string [Indexed]
      preRunUnixUser: string [Indexed]
    ownership: Ownership
      oncall_team: string [Indexed]
    allocationPolicy: AllocationPolicyHolder
      name: string [Indexed]
      sticky: bool [Indexed]
      usedPolicy: string [Indexed]
      fullPolicy: AllocationPolicy
        containerPool: string [Indexed]
        ownerPrefixes: list<string> [Indexed]
        securityDomain: string [Indexed]
        usedPolicy: string [Indexed]
        requirements: AllocationRequirement
          entitlement_uuid: string [Indexed]
    smc: SmcSpec
      tiers: list
        tierName: string [Indexed]
        properties: SmcPropertiesSpec
          extraServiceProperties: list
            name: string [Indexed]
            values: list<string> [Indexed]
          selectWeightProperties: SmcSvcSelectWeightSpec
            svcSelectMode: SmcSvcSelectMode enum [Indexed]
    monitoringPolicy: MonitoringPolicy
      healthCheckConfig: string [Indexed]
      monitoringConfig: string [Indexed]
    maintenancePolicy: MaintenancePolicy
      maxDownTime: i32 [Indexed]
    preRunSteps: list<Command>
      command: string [Indexed]
      unix_user: string [Indexed]
    imageConfig: ImageConfig
      fbpkg: Fbpkg
        version: string [Indexed]
    securityPolicy: SecurityPolicy
      serviceIdentities: list
        name: string [Indexed]
      reservationIdentity: Identity
        id_data: string [Indexed]
    serviceMetadata: ServiceMetadata
      serviceID: string [Indexed]
      tags: list<string> [Indexed]
    networkPolicy: JobNetworkPolicy
      jobIpAllocationPolicy: JobIpAllocationPolicy — union
        jobVirtualIpPolicy: JobVirtualIpPolicy
          virtualIpPrefix: string [Indexed]
    targetSize: CapacityAmount
      tasks: CapacityTasks
        taskCount: i32 [Indexed]
      rrus: CapacityRRUs
        milliRRUs: i32 [Indexed]
    containerManifest: TaskContainerManifest
      handle: ContainerManifestHandle
        id: string [Indexed]
        name: string [Indexed]
    lxcConfig: LxcConfig
      user_isolation: bool [Indexed]
    taskIdentifierAssignment: TaskIdentifierAssignment
      enable_ntid: EnableNTID
        allow_dynamic_upsizing_for_task_restarts_and_updates: bool [Indexed]
    gangSchedulingConfig: GangSchedulingConfig
      gang_size: i32 [Indexed]
      enable_multihost_topology: bool [Indexed]
    canaryInfo: CanaryInfo
      expireTime: i64 [Indexed]
    agentInternalConfig: AgentInternalConfig
      config: map<string, string>
        enable_ipalloc_task: string [Indexed]
      resourceControlConfig: ResourceControlConfig
        swapConfig: SwapConfig
          swapType: SwapType enum [Indexed]
    configReferences: list
      packagedConfigSpec: PackagedConfigSpec
        name: string [Indexed]
    jcpTwJobMetadata: JcpTwJobMetadata
      virtualJobHandle: string [Indexed]
      jcpJobRevisions: JcpJobRevisions
        jobSpecRevision: i64 [Indexed]
    templateSelectors: list
      containerRolloutTemplate: ContainerRolloutTemplate
        taskTemplate: TaskTemplate
          containerTemplate: ContainerTemplate
            command: Command
              command: string [Indexed]
            lxcConfig: LxcConfig
              user_isolation: bool [Indexed]
            requirements: Requirements
              packages: list
                uuid: string [Indexed]
    userJobSpecLocation: UserJobSpecLocation
      raw_absolute_path: string [Indexed]
      project_relative_path: string [Indexed]
      config_reference: ConfigReference — union
        named_user_job_config: NamedUserJobConfigReference — union
          spec_file_source: NamedUserSpecFileSource
            spec_source_id: i64 [Indexed]
    # Non-indexed schedulerSpec fields (queryable via SELECT, not WHERE):
    # logPolicy, hotswapPolicy, authToken, reliabilityPolicy, lbPools,
    # secrets, migrationPolicy, autoTasksPerMachine, sandboxSpec,
    # mutableConfig, quorumBasedGang, staticSharding, vmConfig, ...

  # ── status (NOT indexed) ──
  status: GetJobStatusResponse
    desiredState: JobDesiredState
      desiredJobState: DesiredJobState enum  # ALLOCATED | STARTED | STOPPED | DELETED | PAUSED
    observedJobRevision: map<i64, list<ObservedStatus>>
    converged: bool
    errors: map<string, list<ErrorDetail>>
    containerDeploymentState: map<string, list<ContainerDeploymentState>>

  # ── history (NOT indexed) ──
  history: list<JobHistoricalRecord>
    timestamp: i64
    action: SchedulerJobActions enum
    authToken: AuthToken
    commandLine: string
    reason: string
    traceID: string
    stopType: JobStopType enum
    nujSpecSourceId: i64              # ID for querying NujSpec (ObjectType 16)
```

---

## 2. userSpec Field Reference (Spec 2.0)

This is the preferred, high-level job definition. Defined in `tupperware/api/experimental/job_v2.thrift`. Refer to the [Schema Diagram](#schema-diagram) for the full field hierarchy.

### `handle` *(required, Handle)*

Uniquely identifies the job.

- **`domain`**: Cluster or domain scheduler. Maps to the legacy `cluster` field.
  - Regional: `tsp_prn`, `priv_prn`, `tsp_ash`, `priv_frc`
  - Global: `tsp_global`, `priv_global`
  - Cluster-bound (select use cases only): `prn1c13`
- **`group`**: User/namespace. Maps to the legacy `user` field. Should be the generic role user name (e.g. `search`, `multifeed`). Functions only as a namespace — unrelated to unix user.
- **`name`**: Job name. Valid characters: `[a-zA-Z0-9_\-\.]`

⚠️ **Immutable:** `domain`, `group`, and `name` **cannot be changed** after a job has started. To change any of them, start a new job with the desired values and stop the old job.

---

### `ownership` *(required, Ownership)*

- **`oncall`** *(required, string)*: Short name for the oncall team owning the job. Must have at least one person in rotation. See [oncall tool](https://www.internalfb.com/intern/oncall/).
- The owners are responsible for handling requests from Tupperware including regular maintenance (removing deprecated features) and emergency situations.
- If the oncall rotation becomes invalid, further operations on the job may be blocked. An auditing process opens tasks when oncall data becomes outdated.
- The listed oncall is the recipient of default Tupperware alarms.
- This field is **not** used for ACL enforcement.

```python
Job(
    ...
    ownership=Ownership(oncall_team="rb2"),
    ...
)
```

> ℹ️ `org_team` is **deprecated** in TW Spec 2.0. Specifying the team name is not necessary as `oncall_team` already provides the necessary accountability.

---

### `security_policy` *(optional, SecurityPolicy)*

Configuration for security parameters. Currently configures a list of identities deployed to the job. An identity determines what TLS certificates are deployed and how the job authenticates to other jobs.

- **`_setup_tls_status`** *(optional, SetupTlsStatus)* **[Deprecated]**: Promoted for migration. Do not use this field.

See [Secure Thrift/User Guide/TLS/Tupperware Service Identities](https://www.internalfb.com/intern/wiki/Secure_Thrift/User_Guide/TLS/Tupperware_Service_Identities/) for setup details.

---

### `discovery` *(optional, Discovery)*

Service discovery configuration.

- **`service_id`**: The Service ID for service discovery.
- **`smc_bridges`**: Specifies how tasks are registered in SMC tiers. See [SmcBridge](https://www.internalfb.com/intern/wiki/Tupperware/UG/ref/langref/smcbridge/).
- **`load_balancers`**: Load balancer bridge configurations.
- **`job_tags`**: Tags for the job.
- **`ip_policy`**: IP policy configuration. **[Deprecated]**
- **`deployment_group_id`**: Deployment group identifier.
- **`vip_configs`**: VIP configurations. **[Deprecated]** — use `bgp_vip_config` instead.
- **`bgp_vip_config`**: BGP VIP configuration.
- **`binary_flavor_rru`**: Binary flavor RRU value for capacity accounting.

---

### `deployments` *(deprecated)* **[Deprecated]**

> ℹ️ Deprecated. Use `container_rollouts` instead.

---

### `monitoring` *(optional, Monitoring)*

Specifies monitoring configurations. See [MonitoringPolicy](https://www.internalfb.com/intern/wiki/Tupperware/Reference/LanguageReference/monitoring/).

---

### `scheduler_policies` *(required, Scheduling)*

Specifies where tasks are executed and how the scheduler manages them.

- **`deployment`**: Speed and pace of update/restart. See [DeploymentPolicy](https://www.internalfb.com/intern/wiki/Tupperware/UG/ref/langref/deployment/).
- **`restart`**: What to do when a task terminates. By default tasks are always restarted right away. See [RestartPolicy](https://www.internalfb.com/intern/wiki/Tupperware/UG/ref/langref/restart/).
- **`preemption`**: How the job handles preemption events. Union with variants: `defaultPreset`, `unreliableNetworkEnvironment`, `statefulService`, `never`, `internalTupperwareScheduler`, `legacy` (deprecated), `zeus`, `neverRestartNever`.
- **`prefetch`**: Container prefetching configuration.
- **`enable_power_loss_siren_task_stop`** *(bool)*: If true, tasks are stopped on power loss siren events.
- **`task_identifier_assignment`**: Task identifier assignment strategy.
- **`keep_running_on_power_loss_siren`** *(bool)*: If true, tasks keep running during power loss siren events (overrides `enable_power_loss_siren_task_stop`).
- **`prep_restart_policy`** *(optional)*: Policy for preparation restart limits (max instance restarts).
- **`mast_scheduling`** *(optional)*: MAST scheduling configuration.
- **`gang_scheduling`** *(optional)*: Gang scheduling configuration.
- **`static_sharding`** *(optional)*: Static sharding configuration.
- **`expire_time`** *(optional, i64)*: Job lifetime in seconds.
- **`task_customization_arguments`** *(optional)*: Arguments for per-task customization.

---

### `allocation` *(required, Allocation — union)*

Specifies how the job obtains capacity.

- **Reservation**: Uses a pre-allocated reservation of capacity.
- **ResourceBasedAllocation**: RBA — specify resources and let the scheduler find hosts. To reference a container, use `reservation_handle = 'foo/A'` where `foo` is the Reservation ID and `A` is the Container Name. See [RBA wiki](https://www.internalfb.com/intern/wiki/Tupperware/Deployments/Resource_Based_Allocations/).
- **Follow**: Follow another job's allocation.
- **PrivatePool**: Use a dedicated private pool of machines.
- **Sandbox**: Sandbox allocation for development/testing.
- **MastAllocation**: MAST-managed allocation.
- **GangAllocationPolicy**: Gang scheduling across multiple resources.

See [Allocation Policy](https://www.internalfb.com/intern/wiki/Tupperware/UG/ref/langref/allocation/).

---

### `scaling` *(required, Scaling — union)*

- **Manual**: User owns the job size.
- **Automatic**: SRM (Scalability & Resource Management) owns the size.
- **WholeCapacity**: Scheduler owns the size.

---

### `hot_swap` *(optional, HotSwap)*

If configured, during updates that would normally restart a task, Tupperware starts a **new instance** first and allows graceful shutdown of the old task, minimizing downtime. Supports the new task starting on the same machine or a different machine. Tupperware syncs the new task to SMC once it enters RUNNING state.

See [HotswapPolicy](https://www.internalfb.com/intern/wiki/Tupperware/UG/ref/langref/hotswap/).

---

### `container_rollouts` *(optional, list of ContainerRollout)* — **preferred**

The preferred way to define containers (replacing `deployments`). Each rollout contains a `container` (union — typically `classic_container`) plus rollout metadata.

See [ClassicContainer Fields](#classiccontainer-fields) below for the full field reference.

---

### `config_sequence` / `code_sequence` *(optional)*

Configuration and code sequencing for advanced deployment orchestration.

---

### ClassicContainer Fields

The primary container type. Defined in `tupperware/api/experimental/container.thrift`. See the [Schema Diagram](#schema-diagram) for the complete field listing.

**`packages`** *(required, list of Package)*
A list of fbpackages. Contents are unpacked in `/packages/$package_name` in each task's chroot. See [Package](https://www.internalfb.com/intern/wiki/Tupperware/UG/ref/langref/package/).

---

**`command`** *(required, Command)*
Command to run. Specify the full path, e.g. `/packages/$package_name/$binary_name`.

---

**`pre_run_commands`** *(optional, list of Command)*
List of commands to run prior to executing `command`. These commands are executed every time a task is restarted. The task will not start if any command fails (non-zero exit status). Execution stops on the first failing command.

**Key behaviors:**
- Commands execute in the exact order listed, unless a custom `name` is specified — in that case they run in parallel unless `depends_on` is set.
- These commands do **not** have a timeout. If you require a timeout or are executing a potentially-hanging command, you are responsible for handling that.
- `tw stop` won't stop the container until `pre_run_commands` execution completes.
- If `unix_user` is not specified on a command, it defaults to the job's `unix_user`. Recommendation: use `fbnobody`.
- If your pre-run step needs elevated privileges, run as a non-root user with appropriate [Linux capabilities](https://man7.org/linux/man-pages/man7/capabilities.7.html) rather than granting all capabilities as root.

```python
pre_run_commands = [Command(
    command='/bin/echo meh',
    unix_user='root',
    capabilities=[LinuxCapability.<capability>, ...],
    name="some_name",       # optional
    depends_on=[],           # optional
)]
```

> **Tip:** If your pre-run step is a multi-line script, prepend with `set -eo pipefail` to ensure middle-of-script failures are caught:
> ```python
> pre_run_commands = [Command(
>     command='set -eo pipefail; /bin/true; /bin/echo meh',
>     unix_user='root',
>     capabilities=[LinuxCapability.<capability>, ...]
> )]
> ```

**Named steps with dependencies:** When a `name` is specified, sequential ordering is bypassed — all named steps run in parallel unless `depends_on` is specified. Names must match `[A-Za-z0-9_]+`.

```python
pre_run_commands = [
    Command(
        command="what_your_heart_desires",
        name="some_step_chevron_7_locked",
        depends_on=["some_step_foo", "some_step_bar"]
    )
]
```

⚠️ **Warning:** If your job opted out of user namespaces, you will be unable to run as root.

---

**`kill_command`** *(optional, Command)*
Tupperware executes this command when a task needs to be stopped. If not specified, `SIGTERM` is sent to all processes. If the task fails to terminate within `kill_timeout` (default 30s), `SIGKILL` is sent regardless of whether a `kill_command` is specified.

**Stop context:** If stop was triggered from outside the container, the `kill_command` can consume context containing the reason for stopping. This can be leveraged for [power loss event handling](https://www.internalfb.com/intern/wiki/Power_Loss_Siren/#4-1-1-shutdown-handler).

**Environment:** The env variable `$TASK_PID` is provided — the pid of the `command` process. No other environment is guaranteed to be preloaded. `$USER` will be set to the job's `unix_user`, but `.bashrc` / `.bash_profile` are **not** sourced — your script must source those explicitly if needed.

**⚠️ Critical caveats:**

1. If the container did not finish executing all pre-run steps, `kill_command` will **not** be invoked and the container shuts down immediately. `kill_command` may also not be invoked in certain OOM situations.
2. Your `kill_command` should work even if `$TASK_PID` is already terminated. Otherwise, once `kill_command` and `$TASK_PID` exit, SIGKILLs may be sent to all remaining processes immediately.
3. `$TASK_PID` is responsible for coordinating the lifetime of all other user-spawned processes. When `$TASK_PID` dies, TW considers the container ready for shutdown.
4. In practice: when `$TASK_PID` dies, processes in its cgroup are SIGKILLed immediately. If `$TASK_PID` dies AND `kill_command` has finished, shutdown proceeds ASAP. Processes in unknown cgroups (created outside `$TASK_PID`'s cgroup) may be killed in an undefined manner and may delay container shutdown.

```bash
# Send signal to all child processes, then to $TASK_PID
pkill -P $TASK_PID; kill $TASK_PID
```

---

**`kill_timeout`** *(optional, KillTimeout, default: 30 seconds)*
After executing `kill_command` (or sending `SIGTERM`), if the user process (`$TASK_PID`) does not exit within this time, Tupperware sends `SIGKILL` to **all processes** in the container, including `kill_command` processes. To shutdown cleanly, services can initiate clean shutdown via `kill_command` or catch `SIGTERM` directly.

---

**`environment_variables`** *(optional, map\<string, string\>)*
Environment variables added before executing each task (including pre-run and kill commands). **Overwrites** any existing environment variables with the same name.

- Variable names must match: `[_a-zA-Z0-9]*`
- Variable values should match: `[ _@=;:.,a-zA-Z0-9]*` (not yet enforced, but recommended)

```python
environment_variables={'GDFONTPATH': '/packages/tupperware/dev/gantt/'}
```

> **Note:** Tupperware already sets up environment variables for task/job discovery. Also available at `/etc/twwhoami`. See [TW task environment docs](https://www.internalfb.com/intern/wiki/Tupperware/Reference/AgentApi/TaskEnvironment/).

---

**`directories`** *(optional, list of Directory)*
Directories Tupperware will create inside the task chroot. Does **not** imply persistence across restarts. See [Directory](https://www.internalfb.com/intern/wiki/Tupperware/UG/ref/langref/dir/).

---

**`user_limit`** *(optional, UserLimit)*
Set POSIX user limits (both hard and soft) prior to running the user process. Supported resources:

- `core_files_max_blocks`, `memory_locked_kb`, `stack_size_kb`, `open_file_descriptors`, `processes`, `virtual_memory_kb`

```python
user_limit = UserLimit(
    core_files_max_blocks=UserLimitValue(unlimited=True),
    open_file_descriptors=UserLimitValue(value=500000),
)

# To disable core dump:
user_limit = UserLimit(
    core_files_max_blocks=UserLimitValue(value=0),
)
```

---

**`image`** *(optional, FileSystemImage)*
Container image / chroot profile configuration. The supported chroot profile is `btrfs-based`; users are not expected to set this directly. Customization is exposed via the [image_setup CLI plugin](https://www.internalfb.com/intern/wiki/Tupperware/Reference/PluginReference/ImageSetup/).

---

**`resources`** *(optional, Resources)*
Resource requirements (CPU, RAM, disk) for each task. See [ResourceLimit](https://www.internalfb.com/intern/wiki/Tupperware/UG/ref/langref/resourcelimit/).

---

**`ports`** *(optional, map\<PortName, Port\>)*
Ports the binary binds to. See [Port](https://www.internalfb.com/intern/wiki/Tupperware/UG/ref/langref/port/).

---

**`security`** *(optional, ContainerSecurity)*
Container security configuration including capabilities and identity.

Linux capabilities allow the application to gain [Linux capabilities](http://man7.org/linux/man-pages/man7/capabilities.7.html) even when run by a non-root user. `CAP_SYS_BOOT` and `CAP_AUDIT_READ` are dropped by default.

⚠️ **Warning:** Setting capabilities also drops unused capabilities from the current and bounding sets. If a capability is not specified, your service cannot gain it back. `capabilities = []` drops **all** capabilities; `capabilities = None` uses defaults.

**Python/XAR note:** If your binary is Python and uses XAR, you must enable user namespaces and set `unix_user="root"`. If user namespaces are not possible, set `CAP_SYS_ADMIN`. Without either: `fusermount3: mount failed: Operation not permitted`.

```python
from facebook.tupperware.common.ttypes import LinuxCapability
job.capabilities = [LinuxCapability.NET_BIND_SERVICE]
```

---

**`network`** *(optional, Network)*
Network configuration. See [NetworkPolicy](https://www.internalfb.com/intern/wiki/Tupperware/Reference/LanguageReference/NetworkPolicy/).

**`net_counters_config`** (within Network): Network counters (`tw.net.*`) are enabled on the vast majority of the fleet. Some jobs with extremely high packet rates may see visible overhead. To disable:

```python
from facebook.tupperware.common.ttypes import NetCountersConfig
job.net_counters_config = NetCountersConfig(disableNetCounters=True)
```

> ℹ️ Before disabling, post in [Tupperware@FB](https://fb.facebook.com/groups/tw.cinc) to help the team understand your use case.

---

**`logging`** *(optional, Logging)*
How logs are handled. See [LogPolicy](https://www.internalfb.com/intern/wiki/Tupperware/UG/ref/langref/log/).

---

**`user_attributes`** *(optional, map\<string, string\>)*
Arbitrary data (must be unicode) passed into the task. Resides at `/etc/tw/api/user/metadata.json` as a JSON file with attributes under `userAttributes`.

```bash
cat /etc/tw/api/user/metadata.json | jq .userAttributes
{}
```

---

**`suicide_timeout`** *(optional, SuicideTimeout, default: 12 hours)*
Controls how long task containers survive when the agent is down. Built for the case when something takes down the agent, causing workloads that should be killed to keep running. Also controls how long the agent waits before considering a container lost and attempting restart.

> ⚠️ Do not confuse with `failover_timeout_ms` from [RestartPolicy](https://www.internalfb.com/intern/wiki/Tupperware/Reference/LanguageReference/restart/), which controls a similar aspect between scheduler and agent.

---

**`tmp_policy`** *(optional, TmpWatchPolicy)*
Configuration for tmpwatch, which deletes files in `/tmp` (inside the chroot) that haven't been modified. Default: not run at all.

> tmpwatch runs from **outside** the container, so `/usr/sbin/tmpwatch` is not needed inside. Cleanup will not happen if `/tmp` is a mount point (persistent or shared directory).

```python
tmp_policy = TmpWatchPolicy(
    invoke_seconds=3 * 60 * 60,         # invoke every 3 hours
    directory_threshold_hours=24,         # delete files unmodified for 1 day
)
```

---

**`scheduled_commands`** *(optional, list of ScheduledCommand)*
Commands to execute periodically. See [scheduled command plugin](https://www.internalfb.com/intern/wiki/Tupperware/Reference/PluginReference/Scheduled_Commands/).

---

**`lxc`** *(optional, restricted.Lxc)*
Disable parts of LXC containers. See [LxcConfig](https://www.internalfb.com/intern/wiki/Tupperware/Reference/LanguageReference/LxcConfig/).

---

**`mounts`** *(optional, list of Mount)*
Additional mount points in the container.

---

**`persistent_storage`** *(optional, LocalFlashStorage)*
Local flash storage configuration for persistent data.

---

**`container_run_mode`** *(optional, ContainerRunMode)*
Container runtime configuration. See [ContainerRunConfig](https://www.internalfb.com/intern/wiki/Tupperware/Reference/JobSpecReference/#container-run-config-con).

---

**`exit_files`** *(optional, list\<string\>)*
Files whose creation signals the container should exit.

---

**`tmp_cleanup`** *(optional, TmpCleanup)*
Configuration for automatic cleanup of the `/tmp` directory inside the container. Controls whether and how temporary files are periodically removed.

---

**`resource_enforcement`** *(ResourceEnforcement)*
Specifies how resource limits (CPU, memory) are enforced on the container. Controls whether limits are hard-enforced via cgroups or soft/advisory.

---

**`tags`** *(optional, list\<string\>)*
Free-form string tags attached to the container. Used for grouping, filtering, and organizational purposes.

---

**`staging_timeout`** *(optional, StagingTimeout)*
Maximum time allowed for the container staging phase (package download, image setup). If staging exceeds this timeout, the container is aborted. Replaces `staging_timeout_legacy`.

---

**`resource_control`** *(ResourceControl)*
Fine-grained resource control configuration. Controls CPU shares, memory limits, I/O weights, and other cgroup-level resource parameters for the container.

---

**`rootfs_ops`** *(optional, rootfs.RootfsOps)*
Root filesystem operations configuration. Controls how the container's root filesystem is set up — e.g., whether to use a read-only rootfs, overlay mounts, or custom rootfs layering.

---

**`tw_fw_config`** *(optional, TwFwConfig)* **[Deprecated]**
Tupperware firewall configuration for the container. Controls iptables/BPF-based network firewall rules, socket-level rules, and firewall logging. Deprecated — promoted for migration.

---

**`vm_net_config`** *(optional, VmNetConfig)*
VM networking configuration. Only applicable to VM-mode containers. Controls VM network interface setup, VM firewall, and VM count.

---

**`kill_timeout_legacy`** *(optional, i32)* **[Deprecated]**
Legacy kill timeout in seconds. Deprecated — use `kill_timeout` instead. Do not use this field directly.

---

**`resolution_metadata`** *(optional, ContainerResolutionMetadata)*
Metadata used to recreate unresolved job specs from resolved job specs. Primarily used when onboarding virtual jobs to diff job specs without dynamic information (e.g., package versions).

---

**`staging_timeout_legacy`** *(optional, i32)*
Legacy staging timeout in seconds. Superseded by the structured `staging_timeout` field.

---

**`xdp_config`** *(optional, XdpConfig)*
XDP (eXpress Data Path) configuration for high-performance packet processing. Controls BPF-based XDP programs attached to the container's network interface.

---

**`caps_allow_mknod`** *(optional, bool)*
When true, grants the container the `CAP_MKNOD` Linux capability, allowing creation of device special files. Disabled by default for security.

---

**`sandbox_spec`** *(optional, SandboxSpec)*
Sandbox configuration for the container. Controls isolation boundaries, sandbox mode, and security hardening settings.

---

**`mutable_config`** *(MutableUserConfig)*
User-mutable configuration that can be changed at runtime without redeploying the container. Includes settings that support live updates.

---

**`chroot_options`** *(set\<ChrootOption\>)*
Set of chroot options controlling the container's filesystem root setup. Options affect how the chroot environment is constructed and what host paths are visible.

---

**`set_timezone_in_env`** *(optional, bool)*
When true, sets the `TZ` environment variable inside the container to match the host's timezone.

---

**`make_stackable_config`** *(optional, MakeStackableConfig)*
Configuration for making the container "stackable" — allowing multiple container instances to share the same base image layers for efficient disk usage.

---

**`use_all_nics`** *(optional, bool)*
When true, the container is configured with all backend NICs available on the host, rather than just the primary interface.

---

**`vm_config`** *(optional, VMConfig)*
Virtual machine configuration. Controls VM mode (process VM, confidential VM, container VM), VM image configuration (rootfs, kernel, initrd, bootloader), and other VM-specific settings.

---

**`feature_rollout_reference`** *(optional, string)*
Reference identifier for feature rollout tracking. Links this container to a specific feature rollout configuration for gradual feature deployment.

---

## 3. schedulerSpec Field Reference

Low-level scheduler job specification. Defined in `tupperware/if/SchedulerService.thrift`. Most users should use the Spec 2.0 `userSpec` instead; this section documents the scheduler-level fields for completeness. Refer to the [Schema Diagram](#schema-diagram) for the full field hierarchy and `[Indexed]` annotations.

**`id`** *(required, Identifier)* **[Indexed ↓]**
Job identifier containing `(cluster, user, name)`. Sub-fields `cluster`, `name`, `user` are individually indexed.

---

**`command`** *(required, Command)* **[Indexed ↓]**
Main command to run. Sub-fields `command`, `arguments`, `capabilities` are individually indexed.

---

**`jobSize`** *(i32)* **[Indexed]**
Number of tasks (replicas) for the job. Directly searchable.

---

**`requirements`** *(Requirements)* **[Indexed ↓]**
Resource requirements (CPU, RAM, disk) for each task. See schema diagram for indexed leaf fields.

---

**`constraints`** *(Constraints)* **[Indexed ↓]**
Placement constraints for task scheduling. Sub-fields `smcTier`, `smcAllowEmptyTier`, `maxSmcTierShrinkLimitPercentage` are indexed.

---

**`deployment`** *(DeploymentPolicy)*
Speed and pace of update/restart operations. See [DeploymentPolicy](https://www.internalfb.com/intern/wiki/Tupperware/UG/ref/langref/deployment/).

---

**`restartPolicy`** *(RestartPolicy)*
What to do when a task terminates. See [RestartPolicy](https://www.internalfb.com/intern/wiki/Tupperware/UG/ref/langref/restart/).

---

**`killCommand`** *(optional, Command)*
See `kill_command` in [ClassicContainer Fields](#classiccontainer-fields) for full behavior details.

---

**`killTimeout`** *(i32, default: 30)*
Seconds before SIGKILL. See `kill_timeout` in [ClassicContainer Fields](#classiccontainer-fields).

---

**`preRunCommand`** *(optional, Command)* **[Deprecated]**
> Use `pre_run_steps` / `pre_run_commands` instead. Tupperware runs this through `bash -c`.

---

**`taskOverrides`** *(optional, map\<i32, TaskOverride\>)* **[Deprecated]**

> ℹ️ Task Overrides are being deprecated. Use Shard Manager if your service is sharded.

Override one or more fields in the job spec for specific tasks.

```python
job.task_overrides = {
    0: TaskOverride(env_variables={"FOO": "42"})
}
```

**Notes:**
- **packages**: Overridden packages **will** be auto-updated, but only permanent packages. Ephemeral packages are not.
- **resource_limit**: **Ignored**. Scheduler allocates based on the base job spec only.
- **ports**: Consider auto port assignment or IP-per-task instead — per-task port overrides are error-prone.

> ⚠️ Do **not** use Task Overrides to pass task IDs. Use the `TW_TASK_ID` environment variable.

---

**`envVariables`** *(optional, map\<string, string\>)* **[Indexed]**
See `environment_variables` in [ClassicContainer Fields](#classiccontainer-fields). Directly searchable.

---

**`suicideTimeoutMs`** *(i32, default: 12 hours)*
See `suicide_timeout` in [ClassicContainer Fields](#classiccontainer-fields).

---

**`runtimePolicy.unixUser`** *(optional, string)* **[Indexed]**
The unix user account the job runs under, defaults to `nobody`.

**Which `unix_user` should my job use?** Use **`fbnobody`**. If not set, defaults to `nobody`.

- **Why `fbnobody` over `nobody`?** During OS major-release migrations, `nobody` can map to different `uid` values, causing issues. `fbnobody` is the Facebook default with a stable `uid` guaranteed by the OS team.
- **Why not `root`?** Running as `root` inside the container is equivalent to `root` on the host. Even with user namespaces, allowing root capabilities is a potential security vulnerability.
- **Random/ephemeral users?** Adds nothing unless you believe the kernel has isolation bugs — Tupperware uses process, namespace, filesystem, and IPC isolation. You cannot see or touch anything belonging to any other `fbnobody` on the machine.

If you think you need root, post to `Tupperware@FB` and consider using [Linux capabilities](https://fburl.com/wiki/n6anvl5h) instead.

> ℹ️ `runtimePolicy.preRunUnixUser` runs `preRunCommand` (and `killCommand`) as a specific unix user, defaulting to `unixUser`.

---

**`cleanPackageVersionsOnly`** *(optional, bool)* **[Deprecated]**

> ℹ️ Deprecated in [TW Spec 2.0](https://www.internalfb.com/intern/wiki/Tupperware/Tupperware_Specs_2.0/).

If true, none of the fbpkgs listed in `packages` can be built with local changes or the job will fail to start or update. This is a security and compliance feature.

---

**`maintenancePolicy`** *(optional, MaintenancePolicy)* **[Indexed ↓]**
Controls job behavior during host maintenance. Sub-field `maxDownTime` is indexed.

- **`max_down_time`** (seconds): How long a maintenance interval the task can tolerate.
  - Planned maintenance > `max_down_time` → task is **preempted** to non-maintenance hosts.
  - Planned maintenance ≤ `max_down_time` → task stays. Whether it stops depends on `stop_tasks_during_maintenance`.
  - If not set or 0, `failover_timeout` is used.
- If maintenance exceeds its **planned end time**, any remaining task is preempted.

```python
maintenance_policy = MaintenancePolicy(
    max_down_time=3600,
    # stop_tasks_during_maintenance=True,
)
```

**⚠️ Warnings:**
1. `max_down_time` is **not respected** when the task uses Task Control (action depends on TaskController's decision) or the machine has SeRF Maintenance Status ≠ NONE.
2. If `stop_tasks_during_maintenance=True` and planned interval < `max_down_time`, the task begins stopping **immediately** even if maintenance hasn't started yet — tasks may be stopped longer than expected.

---

**`hotswapPolicy`** *(optional, HotswapPolicy)*
See `hot_swap` in [Section 2](#2-userspec-field-reference-spec-20).

---

**`allocationPolicy`** *(optional, AllocationPolicyHolder)* **[Indexed ↓]**
Advanced task placement (colocation, etc.). See [Allocation Policy](https://www.internalfb.com/intern/wiki/Tupperware/UG/ref/langref/allocation/). Multiple sub-fields are indexed — see schema diagram.

---

**`secrets`** *(optional, list of SecretSpec)*
Secrets uploaded to `install_path`. Supports keychain groups via `group` parameter. Default: 666 permissions, owned by `unix_user`.

You can customize ownership and permissions per secret:

```python
secrets = [
    Secret(
        install_path="/path/to/secret/secret_file_0",
        name="SECRET_0",
        unix_user="root",
        unix_group="root",
        mode=0o600,
    ),
    Secret(
        install_path="/tmp/ca_cert.pem",
        name="MY_CA_CERT",
        group="EC2_SECRETS",  # keychain secret group
    ),
]
```

---

**`autoTasksPerMachine`** *(optional, double, default: 1.0)*
When using `use_hosts_from_smc_tier` and `replicas=ALL`, acts as a multiplier. Reduce to avoid using all hosts; increase to invoke stacking.

---

**`expireTime`** *(optional, i64)* **[Indexed]**
Job lifetime in seconds. Stopped when `start_time + expire_time > current_time`. Value `0` means no expiry.

---

**`reservationHandle`** *(optional, string)* **[Indexed]**
Reference to an RBA reservation. Format: `'foo/A'` (Reservation ID / Container Name).

---

**`serviceMetadata`** *(optional, ServiceMetadata)* **[Indexed ↓]**
Service ID and Service Tags. Sub-fields `serviceID` and `tags` are indexed. Tupperware propagates these into task environment.

```python
service_metadata = ServiceMetadata(
    serviceID="cortex/cortex_sync",
    tags=["prod", "cortex_server", "sync"],
)
```

---

**`lbPools`** *(list of LBConfig)*
Load balancer pool configuration. See [LBConfig](https://www.internalfb.com/intern/wiki/Tupperware/Reference/LanguageReference/LBConfig/).

---

**`tmpfsSize`** *(optional, string)*
Size of `/dev/shm` tmpfs (e.g. `"512m"`). Default is half of physical RAM. Oversizing may cause deadlock as the OOM handler cannot free tmpfs memory.

---

**`networkPolicy`** *(optional, JobNetworkPolicy)* **[Indexed ↓]**
See [NetworkPolicy](https://www.internalfb.com/intern/wiki/Tupperware/Reference/LanguageReference/NetworkPolicy/). Deep sub-field `jobIpAllocationPolicy.jobVirtualIpPolicy.virtualIpPrefix` is indexed.

---

**`securityPolicy`** *(optional, SecurityPolicy)* **[Indexed ↓]**
TLS certificates and service identities. Sub-fields `serviceIdentities.name` and `reservationIdentity.id_data` are indexed.

---

**`logPolicy`** *(optional, LogPolicy)*
Log retention and upload configuration. Controls how long logs are kept, rotation size/rate, and whether logs are uploaded to Manifold.

---

**`commandLine`** *(string)* **[Indexed]**
The full command line as a single string. Directly searchable. This is the legacy way to specify the command; prefer `command` struct instead.

---

**`smc`** *(optional, SmcSpec)* **[Indexed ↓]**
SMC (Service Mesh Controller) tier configuration. Sub-fields `tiers.tierName`, `tiers.properties.extraServiceProperties.name`, `tiers.properties.extraServiceProperties.values`, and `tiers.properties.selectWeightProperties.svcSelectMode` are indexed.

---

**`authToken`** *(optional, AuthToken)*
Authentication token recording who performed the last action on the job. Contains `realUser`, `origin`, `fbid`, `requestUuid`, and `issuer`.

---

**`monitoringPolicy`** *(optional, MonitoringPolicy)* **[Indexed ↓]**
Health check and monitoring configuration. Sub-fields `healthCheckConfig` and `monitoringConfig` are indexed.

---

**`reliabilityPolicy`** *(optional, ReliabilityPolicy)*
Controls minimum reliability during machine repair. Field `minReliabilityOnMachineRepair` specifies the minimum percentage of tasks that must remain running.

---

**`jobSizeFixed`** *(bool)* **[Indexed]**
Whether the job size is fixed (cannot be auto-scaled). Directly searchable.

---

**`chrootProfile`** *(optional, string)*
Name of the chroot profile for the container filesystem.

---

**`ownership`** *(optional, Ownership)* **[Indexed ↓]**
Job ownership information. Sub-field `oncall_team` is indexed.

---

**`pushTags`** *(optional, list\<string\>)* **[Indexed]**
Push tags used for deployment tracking and filtering. Directly searchable.

---

**`lxcConfig`** *(optional, LxcConfig)* **[Indexed ↓]**
LXC container isolation configuration. Sub-field `user_isolation` (bool) is indexed.

---

**`migrationPolicy`** *(optional, MigrationPolicy)*
Controls how tasks are migrated. Field `migrationType` specifies the migration strategy (e.g., live migration vs restart).

---

**`expireTimeMode`** *(optional, ExpireTimeMode enum)*
How `expireTime` is interpreted. Controls whether the expiry is relative to job start or an absolute timestamp.

---

**`preRunSteps`** *(optional, list\<Command\>)* **[Indexed ↓]**
Pre-run commands executed before the main command. Sub-fields `command` and `unix_user` are indexed. See `pre_run_commands` in [ClassicContainer Fields](#classiccontainer-fields) for detailed behavior.

---

**`imageConfig`** *(optional, ImageConfig)* **[Indexed ↓]**
Container image configuration. Sub-field `fbpkg.version` is indexed.

---

**`userAttributes`** *(optional, map\<string, string\>)*
Arbitrary key-value metadata passed into the task. Available at `/etc/tw/api/user/metadata.json`.

---

**`localStorage`** *(optional, PersistentResource)*
Local flash storage configuration for persistent data. Contains `mountPoint`, `mountParams`, and `allowMissingMounts`.

---

**`targetSize`** *(optional, CapacityAmount)* **[Indexed ↓]**
Job target size. Union with `tasks` (containing indexed `taskCount`) and `rrus` (containing indexed `milliRRUs`).

---

**`containerManifest`** *(optional, TaskContainerManifest)* **[Indexed ↓]**
Container manifest handle. Sub-fields `handle.id` and `handle.name` are indexed.

---

**`minTaskId`** *(optional, i32)*
Minimum task ID for the job. Tasks are numbered starting from this value.

---

**`sandboxSpec`** *(optional, SandboxSpec)*
Sandbox specification defining custom package subvolumes, image subvolumes, and extra mounts for the container filesystem.

---

**`taskIdentifierAssignment`** *(optional, TaskIdentifierAssignment)* **[Indexed ↓]**
Strategy for assigning task identifiers. Sub-field `enable_ntid.allow_dynamic_upsizing_for_task_restarts_and_updates` is indexed.

---

**`vipConfigs`** *(optional, list\<VipConfig\>)*
Virtual IP configurations. Each VipConfig specifies a VIP type (BGP, DSR, or Geo), IP counts, and assignment options.

---

**`mutableConfig`** *(optional, MutableUserConfig)*
Mutable user configuration that can be updated without restarting tasks. Contains a `userConfig` map of key-value pairs.

---

**`isGarbageCollectedByJCP`** *(optional, bool)*
Whether this job is garbage-collected by JCP (Job Control Plane). When true, JCP manages the lifecycle of this job.

---

**`quorumBasedGang`** *(optional, QuorumBasedGang)*
Quorum-based gang scheduling configuration. Specifies `quorumSize`, `assemblyStrategy`, `scopeId`, timeouts, and retry limits.

---

**`exitFiles`** *(optional, list\<string\>)*
Files whose creation signals the container should exit. Same behavior as `exit_files` in ClassicContainer.

---

**`gangSchedulingConfig`** *(optional, GangSchedulingConfig)* **[Indexed ↓]**
Gang scheduling configuration. Sub-fields `gang_size` and `enable_multihost_topology` are indexed.

---

**`binary_flavor_rru`** *(optional, double)*
Binary flavor RRU (Relative Resource Unit) value for capacity accounting.

---

**`contHeapEnabledRatio`** *(optional, i32)*
Ratio (0-100) of tasks that have continuous heap profiling enabled.

---

**`memoryProfilingPolicy`** *(optional, MemoryProfilingPolicy)*
Memory profiling configuration. Contains `contHeapProfPrefix`, `contHeapLgProfSample`, and `contHeapLgProfInterval`.

---

**`staticSharding`** *(optional, StaticShardingConfig)*
Static sharding configuration. Contains `offset`, `shuffle`, and `groupSize` for deterministic shard assignment.

---

**`isJobSpecPatchedByJCP`** *(optional, bool)*
Whether the job spec has been patched by JCP. Read-only metadata field.

---

**`configStateConfiguration`** *(optional, ConfigStateConfiguration)*
Config state management. Field `requireDownloadingOverridesBeforeTaskStart` controls whether config overrides must be downloaded before task startup.

---

**`clientStaticShardingArgs`** *(optional, ShardingArguments)*
Client-side static sharding arguments. Opaque sharding configuration passed to the sharding framework.

---

**`configPartitionSpecifier`** *(optional, ConfigPartitionSpecifier)*
Config partition rollout specification. Controls `targetConfigPartitionLabel`, `configPartitionRolloutPpm`, and `configPartitionOffsetPpm`.

---

**`codePartitionSpecifier`** *(optional, PartitionSpecifier)*
Code partition rollout specification. Controls `targetPartitionLabel`, `partitionRolloutPpm`, and `partitionOffsetPpm`.

---

**`vmConfig`** *(optional, VMConfig)*
Virtual machine configuration. Specifies `vm_mode` (ProcessVM, ConfidentialVM, or ContainerVM) and `vm_image_config` for VM images.

---

**`canaryInfo`** *(optional)* **[Indexed ↓]**
Canary deployment information. Sub-field `expireTime` (i64) is indexed.

---

**`agentInternalConfig`** *(optional, AgentInternalConfig)* **[Indexed ↓]**
Agent-internal configuration. Sub-fields `config.enable_ipalloc_task` and `resourceControlConfig.swapConfig.swapType` are indexed.

---

**`configReferences`** *(optional, list)* **[Indexed ↓]**
Configuration references linking to packaged configs. Sub-field `packagedConfigSpec.name` is indexed.

---

**`jcpTwJobMetadata`** *(optional, JcpTwJobMetadata)* **[Indexed ↓]**
JCP (Job Control Plane) metadata. Sub-fields `virtualJobHandle` and `jcpJobRevisions.jobSpecRevision` are indexed.

---

**`templateSelectors`** *(optional, list)* **[Indexed ↓]**
Template selectors for container rollout templates. Deep indexed sub-fields include `containerRolloutTemplate.taskTemplate.containerTemplate.command.command`, `.lxcConfig.user_isolation`, and `.requirements.packages.uuid`.

---

**`userJobSpecLocation`** *(optional, UserJobSpecLocation)* **[Indexed ↓]**
Location of the user's job spec source. Sub-fields `raw_absolute_path`, `project_relative_path`, and deep field `config_reference.named_user_job_config.spec_file_source.spec_source_id` are indexed.

---

**Deprecated scheduler fields:**

| Field                | Status                                                     |
|----------------------|------------------------------------------------------------|
| `alertPolicy`        | No effect. Deprecated in TW Spec 2.0.                     |
| `enable_lxc`         | No effect. Deprecated in TW Spec 2.0.                     |
| `jobPriority`        | No effect. Never used. Deprecated in TW Spec 2.0.         |
| `serviceTags`        | Use `serviceMetadata` instead. Deprecated in TW Spec 2.0. |

---

## 4. Job Status

Runtime status of the job. Defined in `tupperware/api/if/Tupperware.thrift`. See the [Schema Diagram](#schema-diagram) for the field hierarchy.

- **`desiredState`**: The desired spec revisions and desired job state (ALLOCATED, STARTED, STOPPED, DELETED, PAUSED).
- **`observedJobRevision`**: Map from job spec revision to observed status per domain, showing how many tasks are running each revision.
- **`converged`** *(bool)*: Whether the job has fully converged to the desired state.
- **`errors`**: Map from region to error details — check this to diagnose job issues.
- **`containerDeploymentState`**: Per-container deployment state information.
- **`internalState`** *(optional)*: Internal scheduler state, for debugging.

### DesiredJobState Enum

```thrift
enum DesiredJobState {
  ALLOCATED = 1,   // Resources allocated but not running
  STARTED = 2,     // Job should be running
  STOPPED = 3,     // Job intentionally stopped
  DELETED = 4,     // Job deleted
  PAUSED = 5,      // Job paused
}
```

---

## 5. Job History

Historical records of actions taken on the job. Defined in `tupperware/if/SchedulerService.thrift`. See the [Schema Diagram](#schema-diagram) for the field hierarchy.

- **`timestamp`** *(i64)*: Unix timestamp of the action.
- **`action`** *(SchedulerJobActions)*: The action taken (start, stop, update, etc.).
- **`authToken`** *(optional)*: Who performed the action.
- **`commandLine`** *(optional)*: The CLI command used.
- **`reason`** / **`structuredReason`** *(optional)*: Human-readable or structured reason for the action.
- **`traceID`** *(string)*: Trace ID for debugging the action.
- **`jobSpecDiffURI`** *(optional)*: Link to the spec diff for update actions.
- **`stopType`** *(optional)*: Type of stop (graceful, forced, etc.).
- **`nujSpecSourceId`** *(i64)*: Named User Job spec source ID. This is the ID used to query the **NujSpec** (ObjectType `16`) to retrieve the full job specification snapshot at that point in time.

### Querying NujSpec from History

To inspect what a job's spec looked like at a specific historical event:

1. **Query the Job** to get its `history` records and find the `nujSpecSourceId` for the event of interest.
2. **Query NujSpec** (ObjectType `16`) using `idFilter` with the format `jobHandle:nujSpecSourceId`:

Query NujSpec (ObjectType `16`) using `idFilter` with the ID `tsp_prn/myteam/my_service:12345`, where `12345` is the `nujSpecSourceId` value from the history record. You can also query with just the numeric ID (`"12345"`), but the `jobHandle:specVersion` format is recommended. See universal-search-syntax.md for the runnable command.

> **Note:** NujSpec has no indexed fields and no association types — it only supports ID-based lookups. You must first get the `nujSpecSourceId` from the Job's history. See [query-nujspec.md](query-nujspec.md) for the full schema of the returned spec.

### SchedulerJobActions Enum

```thrift
enum SchedulerJobActions {
  ACTION_START_JOB = 1,
  ACTION_STOP_JOB = 2,
  ACTION_CONTROL_TASKS = 3,
  ACTION_RESTART_JOB = 4,
  ACTION_UPDATE_JOB = 5,
  ACTION_CANCEL_UPDATE = 6,
  ACTION_REVERT_UPDATE = 7,
  ACTION_FINISH_UPDATE = 8,
  ACTION_PAUSE_UPDATE = 9,
  ACTION_RESUME_UPDATE = 10,
  ACTION_START_CANARY = 11,                          // [Deprecated]
  ACTION_STOP_CANARY = 12,                           // [Deprecated]
  ACTION_INFO = 13,
  ACTION_COMMIT_JOB_CHANGES = 14,
  ACTION_TASK_OPERATION_APPLIED = 15,
  ACTION_TASK_OPERATION_MANUALLY_ACKED = 16,
  ACTION_TASK_OPERATION_MANUALLY_CANCELED = 17,
  ACTION_TASKS_PREEMPTED_BY_USER = 18,
  ACTION_UNAVAILABILITY_EVENT_MANUALLY_ACKED = 19,
  ACTION_INJECT_NET_FAULT = 20,
  ACTION_CLEAR_NET_FAULT = 21,
  ACTION_CONFIRM_JOB_ALLOCATION = 22,
  ACTION_FREEZE_JOB = 23,
  ACTION_UNFREEZE_JOB = 24,
  ACTION_DELETE_JOB = 25,
}
```

---

## 6. Indexed (Searchable) Fields & Supported Operations

Fields annotated with **`[Indexed]`** in the [Schema Diagram](#schema-diagram)
can be used in `jsonPathFilter` WHERE clauses. Non-indexed fields can still
appear in `SELECT` (projection) but NOT in `WHERE` (filtering).

> All 72 indexed fields (in JSONPath format) live under
> `$.schedulerSpec.*`. The `userSpec`, `status`, and `history` sub-trees
> are **not** indexed.

### Refreshing the Indexed-Fields List

The live source of truth is the `help()` API. Use the `help()` API with `objectType:1` and `format:2` to discover searchable fields. See universal-search-syntax.md for the runnable command.

### Supported Association Types

Jobs support association-based queries with these object types:

| Association Type | Description |
|-----------------|-------------|
| `Reservation` | Find jobs deployed on a specific reservation |
| `ServiceID` | Find jobs by service ID (GSI) |
| `Task` | Find jobs containing specific tasks |
| `SmcTopologyNode` | Find jobs by SMC topology node |
| `SdmTopologyNode` | Find jobs by SDM topology node |

For CompareOp values and query syntax, see [universal-search-syntax.md](universal-search-syntax.md).

---

## 7. References

- **Language Reference Wiki**: https://www.internalfb.com/wiki/Infra_Cloud/Service_Hosting/Tupperware/Tupperware_Reference/LanguageReference/Job
- **Ownership Wiki**: https://www.internalfb.com/wiki/Infra_Cloud/Service_Hosting/Tupperware/Tupperware_Reference/LanguageReference/Ownership
- **TW Spec 2.0**: https://www.internalfb.com/intern/wiki/Tupperware/Tupperware_Specs_2.0/
- **Resource.thrift**: `fbcode/tupperware/universal_search/if/Resource.thrift`
- **ResourceSearch.thrift**: `fbcode/tupperware/universal_search/if/ResourceSearch.thrift`
- **job_v2.thrift**: `fbcode/tupperware/api/experimental/job_v2.thrift`
- **SchedulerService.thrift**: `fbcode/tupperware/if/SchedulerService.thrift`
- **Tupperware.thrift**: `fbcode/tupperware/api/if/Tupperware.thrift`
- **container.thrift**: `fbcode/tupperware/api/experimental/container.thrift`
