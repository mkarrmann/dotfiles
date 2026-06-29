# Tupperware NujSpec Query Skill

## Overview

This skill provides the complete schema reference and query patterns for **NujSpec** objects in Tupperware Universal Search. NujSpec contains historical snapshots of job specifications, useful for understanding how a job's configuration has changed over time.

### When to Use This Skill
- Investigating historical job configurations
- Understanding spec changes over time
- Diffing job spec versions
- Debugging configuration-related issues
- Auditing job changes

## Quick Reference

**Object Type Enum:** `16` (NujSpec)

**Query Language:** See [universal-search-syntax.md](universal-search-syntax.md) for how to run queries (thriftdbg command syntax, single-line rule, quick-start examples).

### ID Format

> ⚠️ NujSpec has **no indexed fields** and **no association types** — queries are limited to ID-based lookups only.

The ID is **not** the job handle. It uses the `nujSpecSourceId` (Named User Job spec source ID), an i64 found in the Job object's `$.history[*].nujSpecSourceId` field. You must first query the Job to get its history records, then use the `nujSpecSourceId` from the relevant record.

Two ID formats are accepted:
- **`jobHandle:nujSpecSourceId`** (recommended) — e.g., `"tsp_prn/myteam/my_service:12345"`
- **`nujSpecSourceId`** (numeric only) — e.g., `"12345"`

### Common Query Pattern

**Step 1:** Query the Job (ObjectType `1`) to get history records with `nujSpecSourceId` values. Use `selectedJsonPaths:["$.history"]` and `idFilter` with the job handle (e.g. `tsp_prn/myteam/my_service`). See universal-search-syntax.md for the runnable command.

**Step 2:** Use the `nujSpecSourceId` from the desired history record to query the full spec. Query NujSpec (ObjectType `16`) using `idFilter` with the format `jobHandle:nujSpecSourceId` (e.g. `tsp_prn/myteam/my_service:12345`). See universal-search-syntax.md for the runnable command.

## Schema Reference

See the full [Language Reference](#language-reference) below for complete field documentation.

### Indexed Fields

> ⚠️ NujSpec has **no indexed fields** — `jsonPathFilter` queries are not supported. Use `idFilter` only.

### NujSpec Structure Overview

```yaml
JobHistoricalSpec:
  job: schema.Job — Full historical job specification (128+ fields)
    # Key sub-sections:
    name: string — Job name
    cluster: string — Cluster name
    user: string — Job user
    ownership: Ownership — Team and oncall ownership
    packages: list<Package> — Deployed packages
    command: string — Main command
    scheduling: Scheduling — Replica count and placement
    resource_limit: ResourceLimit — CPU, RAM, disk, GPU limits
    allocation_policy: AllocationPolicy — Placement constraints
    restart_policy: RestartPolicy — Restart behavior
    deployment_policy: DeploymentPolicy — Rolling update config
    # ... see Language Reference for full schema
```

For CompareOp values, see [universal-search-syntax.md](universal-search-syntax.md#compareop-values).

## Discover Available Fields

Use the `help()` API with `objectType:16` and `format:2` to discover available fields. See universal-search-syntax.md for the runnable command.

---

# Language Reference


# Tupperware NujSpec — Language Reference (Agent Prompt)

This document describes the **NujSpec** resource as defined in the Tupperware Universal Search system. It is intended to be used as an agent prompt so that an LLM can answer questions about historical job specifications, their schema, and field semantics.

---

## 1. Overview

A **NujSpec** represents a historical snapshot of a Tupperware job's specification at a point in time. It captures the full job configuration as it existed when a particular version was deployed, allowing users to inspect previous job specs, compare configurations across deployments, and audit changes.

In Universal Search, the NujSpec object (`ObjectType.JobHistoricalSpec`, value `16`) is the `Resource.JobHistoricalSpec` struct from `tupperware/universal_search/if/Resource.thrift`. It wraps a single field — `schema.Job` from `tupperware/twdeploy/config/schema.thrift` — which contains the full 128-field historical job specification.

**NujSpec identity format:** `jobHandle:nujSpecSourceId` (e.g. `tsp_prn/myteam/my_service:12345`) or just the numeric `nujSpecSourceId` (e.g. `12345`). The `nujSpecSourceId` is found in the Job object's `$.history[*].nujSpecSourceId` field.

### Schema Diagram

The diagram below is the **authoritative schema reference** for the NujSpec resource. All fields, types, and `[Deprecated]` annotations are shown here. Sections 2–3 provide **field-level documentation only** (behavioral details, caveats, code examples) — they do not repeat the schema.

> **Legend:** `[Indexed]` = field is searchable via `jsonPathFilter`.
> `[Deprecated]` = field is deprecated.

> ⚠️ NujSpec has no indexed fields and no association types — queries are limited to ID-based lookups.

```yaml
Resource.thrift::JobHistoricalSpec:
  job: schema.Job
    # --- Identity & Ownership ---
    name: string (required)
    cluster: string (required)
    user: string (required)
    ownership: Ownership
      org_team: string
      oncall_team: string
    unix_user: string
    unix_group: string

    # --- Packages & Commands ---
    packages: list<Package> (required)
      name: string (required)
      version: i32
      files: list<string>
      rpm: bool
      ephemeral_package_id: string
      tag: string
      fetch_timeout_in_sec: i32
      install_prefix: string
      rpms: list<string>
      paths: list<string>
      auto_update: bool
      fetch_direct_io: bool
      install_timeout_in_sec: i32
      tw_internal_read_only: bool
    command: string (required)
    kill_command: string
    kill_timeout: i32
    pre_run_command: string
    pre_run_unix_user: string
    pre_run_unix_group: string
    pre_run_steps: list<Command>
      name: string
      command: string
      timeout_in_sec: i32
      arguments: list<Argument>
      unix_user: string
      restart_policy: RestartPolicy (see below)
      env_variables: map<string, string>
      resource_limit: ResourceLimit (see below)
      retry_policy: RetryPolicy
        max_retries: i32
        retry_interval_ms: i64
    arguments: list<Argument>
      name: string (required)
      value: string (required)
      task_id_range: IntRange
        begin: i32 (required)
        end: i32 (required)

    # --- Scheduling ---
    scheduling: Scheduling (required)
      replicas: i32 (required)
      machines: list<string> [Deprecated]
      use_hosts_from_smc_tier: string
      use_hosts_from_smc_tiers: list<string>
      enable_service_mesh: bool
      placement_preferences: list<PlacementPreference>
        key: string
        value: string
      gang_scheduling: GangSchedulingCommon.GangSchedulingJobConfig
      idle: bool
      disabled_tasks: set<i32>

    # --- Restart & Deployment ---
    restart_policy: RestartPolicy
      daemon: bool
      max_instance_restarts: i32
      max_total_failures: i32 [Deprecated]
      max_task_failures: i32
      restart_interval: i32
      exponential_delay: bool
      max_restart_interval: i32
      failover_timeout_ms: i64
      min_running_interval: i32
      preempt_after_max_instance_restarts: bool
      enable_power_loss_siren_task_stop: bool
      keep_running_on_power_loss_siren: bool
      system_failure_restart_policy: SystemFailureRestartPolicy
        max_system_failure_restarts: i32
    deployment_policy: DeploymentPolicy
      step_size: i32
      restart_period_ms: i64
      cancellation_threshold: i32
      bad_update_threshold: i32
      staging_timeout_ms: i32
      randomize: bool
      step_size_percent: i32
      cancellation_threshold_percent: i32
      task_control: TaskControl
        tier_name: string (the task controller tier — can be an SMC tier or SRConfig name, e.g., "webtaskcontrol.vcn.instagram.c2" or "shardmanager.global")
        context: map<string, string> (e.g., {"pushphase": "c2", "region": "vcn", "tenant": "instagram"})
        restart_period_ms: i64 (optional, delay between deployment steps when task controller is active)

    # --- Resource Limits ---
    resource_limit: ResourceLimit
      ram: string
      cpu: i32
      disk: string
      flash: string
      network_mbps: i32
      gpu: i32
      shared_memory: string
      additional_memory: string
      io_bps: i64
      io_ops: i64
      disk_bps_read: i64
      disk_bps_write: i64
      disk_iops_read: i64
      disk_iops_write: i64
      resource_control_config: ResourceControlConfig (see below)

    # --- Allocation ---
    allocation_policy: AllocationPolicy
      name: string
      machine_pool: string
      jobs: list<string>
      exclusions: list<Exclusion>
        name: string (required)
        jobs: list<string>
        tier: string
        task_range: IntRange
        exclusion_type: ExclusionType enum
      exclusion_rules: map<string, ExclusionRules>
      colocations: list<Colocation>
      server_restrictions: list<string>
      use_allocation_policy: string
      locality_constraints: LocalityConstraints
      entitlement_name: string
      user_preferences: list<Preference>
      security_domain: string
      machine_task_allocation_policies: map<string, MachineTaskAllocationPolicy>
      allow_zero_hosts: bool
    reservation_handle: string

    # --- Networking ---
    ports: list<Port>
      name: string (required)
      port: i32 (required)
      protocol: Protocol enum
    smc_bridges: list<SmcBridge>
      smc_tier: string (required)
      port_name: string (required)
      port_map: map<string, string>
    lb_pools: list<LBConfig>
      type: LBType enum (required)
      lb: string (required)
      pool: string (required)
      port_name: string (required)
    network_policy: NetworkPolicy
      ip_allocation_policy: IPAllocationPolicy
      transparent_tls: TransparentTls
    netns_config: NetnsConfig
    net_counters_config: NetCountersConfig
    vm_net_config: VmNetConfig
    bgp_vip_config: BgpVipConfig
    virtual_link_config: VirtualLinkConfig
    gue_port_config: GuePortConfig
    gue_port_configs: list<GuePortConfig>
    vip_configs: list<common.VipConfig>

    # --- Security ---
    security_policy: SecurityPolicy
      serviceIdentities: list<ServiceIdentity>
      sshCertificates: list<SSHCertParams>
      soxComplianceParams: SOXComplianceParams
      bpfJailerParams: common.BpfJailerParams
    kerberos_tier: string [Deprecated]
    support_kerberos: bool
    enable_acl_check: bool
    secrets: list<Secret>

    # --- Monitoring & Alerts ---
    alert_policy: AlertPolicy
    monitoring: MonitoringPolicy
    monitoring_config: string [Deprecated]

    # --- Container & Image ---
    image: ImageConfig
      fbpkg: Fbpkg
        name: string
        version: string
      min_rootfs_size: i64
      filename: string
      rootfs_system_layer: chroot_config.ChrootConfig
      use_tw_managed_image: bool
    chroot_profile: string
    enable_lxc: bool
    lxc_config: LxcConfig
    container_run_config: ContainerRunConfig
    container_manifest: ContainerManifest
    private_container_mixins: list<PrivateContainerMixinRef>
    container_rootfs_ops: ContainerRootfsOps
    sandbox_spec: common.SandboxSpec

    # --- Storage & Filesystem ---
    user_directories: list<Directory>
      name: string (required)
      owner: string
      permissions: i32
    system_files: list<SystemFile>
    file_system_mounts: map<string, FileSystemMount>
    persistent_storage: LocalFlashStorage
    tmpfs_size: string

    # --- Logging & Operations ---
    log_policy: LogPolicy
    tmp_policy: TmpWatchPolicy
    opsstream_types: list<i64>
    opsstream_expiration_days: i64

    # --- Environment & Configuration ---
    env_variables: map<string, string>
    user_attributes: map<string, string>
    task_overrides: map<i32, TaskOverride>
    configerator_canary_configs: list<string>
    mutable_config: common.MutableUserConfig
    config_sequence: common.ConfigSequence
    code_sequence: common.CodeSequence
    feature_rollout_reference: string

    # --- Canary & Deployment Strategies ---
    canaries: list<Canary>
    push_tags: list<string>
    hotswap_policy: HotswapPolicy
    clean_package_versions_only: bool

    # --- Policies ---
    maintenance_policy: MaintenancePolicy
    preemption_policy: PreemptionPolicy
    migration_policy: MigrationPolicy
    job_priority: JobPriority

    # --- Task Configuration ---
    min_task_id: i32
    auto_tasks_per_machine: double
    scheduled_commands: list<ScheduledCommand>
    exit_files: list<string>
    capabilities: list<common.LinuxCapability>
    user_limit: UserLimit

    # --- Tags & Metadata ---
    service_tags: ServiceTags
    service_metadata: ServiceMetadata
    prevent_nontrunk_update: bool
    shadowing_config: ShadowingConfig

    # --- Networking (Advanced) ---
    xdp_config: XdpConfig
    tw_fw_config: TwFwConfig
    enable_sockets_hook: bool
    use_all_nics: bool

    # --- Resource Control ---
    resource_control_config: ResourceControlConfig
    caps_allow_mknod: bool
    make_stackable_config: MakeStackableConfig
    binary_flavor_rru: double
    vm_config: common.VMConfig

    # --- Timers ---
    suicide_timeout_ms: i32
    expire_time: i64
    bind_mount_fbcode_dir: bool
    set_timezone_in_env: bool

    # --- Internal Fields (do not set manually) ---
    _user_job_spec_hash: string
    _raw_spec_abs_file_path: string
    _agentInternalConfig: map<string, string>
    _setup_tls_status: SetupTlsStatus enum
    _setup_ssh_status: map<string, SetupSshStatus enum>
    _compilationMetadata: TwCliCompilationMetadata
    _unresolved_fields: set<string>
    _compilation_error_messages: list<string>
    _applied_transforms: list<JobTransform>
```

---

## 2. Field Reference

### `job` *(schema.Job)*

The full historical job specification, defined in `tupperware/twdeploy/config/schema.thrift`. This is the same `schema.Job` struct used by `twcli` and `twdeploy` to define and deploy Tupperware jobs. It contains 128 fields organized into the categories below.

---

### Identity & Ownership

- **`name`** *(string, required)*: Name of the job.
- **`cluster`** *(string, required)*: Name of the cluster (scheduler) the job runs on.
- **`user`** *(string, required)*: User for running the job.
- **`ownership`** *(Ownership)*: Team and oncall ownership.
  - `org_team`: Name of a team listed in the Teams directory.
  - `oncall_team`: Name of an oncall rotation.
- **`unix_user`** *(string)*: Unix user for the job process.
- **`unix_group`** *(string)*: Unix group for the job process.

---

### Packages & Commands

- **`packages`** *(list\<Package\>, required)*: List of packages to deploy with the job. Each package has:
  - `name` *(required)*: Package name.
  - `version`: Package version. If not specified, resolves to latest.
  - `tag`: Package tag.
  - `auto_update`: If true, Tupperware auto-updates the job to always use the latest package version.
  - `rpm`: Whether this is an RPM package.
  - `install_prefix`: Location to install the package inside the chroot.
  - `fetch_timeout_in_sec`: Timeout for fetching the package (default 0 = no timeout).
  - `tw_internal_read_only` *(default true)*: Whether the package is mounted read-only. Making a package read-write is expensive (increased disk usage and start time).
- **`command`** *(string, required)*: The main command to run.
- **`kill_command`** *(string)*: Command to run when stopping the task.
- **`kill_timeout`** *(i32)*: Timeout for the kill command in seconds.
- **`pre_run_command`** *(string)*: Command to run before the main command.
- **`pre_run_steps`** *(list\<Command\>)*: List of commands to run at pre_run, before `pre_run_command`.

---

### Scheduling

- **`scheduling`** *(Scheduling, required)*: Scheduling configuration.
  - `replicas` *(i32, required)*: Number of tasks to run. Each task is assigned an ID from 0 to `replicas - 1`. Use `ALL_CAPACITY = -1` to use all capacity from a tier or reservation, or `SRM_RUNNING_TASK_COUNT = -10` to match the size of a running counterpart.
  - `machines` **[Deprecated]**: Explicit list of hostnames. Use an SMC tier instead.
  - `use_hosts_from_smc_tier` *(string)*: Run tasks on machines from this SMC tier.
  - `use_hosts_from_smc_tiers` *(list\<string\>)*: Run tasks on machines from multiple SMC tiers.
  - `idle` *(bool)*: If true, the job is idle (no tasks scheduled).
  - `disabled_tasks` *(set\<i32\>)*: Set of task IDs that should not be scheduled.
  - `gang_scheduling`: Gang scheduling configuration for coordinated multi-task scheduling.

---

### Restart & Deployment

- **`restart_policy`** *(RestartPolicy)*: Controls task restart behavior.
  - `daemon` *(bool)*: If true, the task restarts automatically on exit.
  - `max_instance_restarts` *(i32)*: Maximum restarts per task instance.
  - `max_task_failures` *(i32)*: Maximum task failures before stopping.
  - `restart_interval` *(i32)*: Delay between restarts in seconds.
  - `exponential_delay` *(bool)*: Use exponential backoff for restart delays.
  - `max_restart_interval` *(i32)*: Maximum restart delay when using exponential backoff.
  - `preempt_after_max_instance_restarts` *(bool)*: Preempt the task (move to new host) after max restarts.
  - `enable_power_loss_siren_task_stop` *(bool)*: If true, task is stopped (not killed) prior to power outage.
  - `keep_running_on_power_loss_siren` *(bool)*: If true, task is not stopped on power loss maintenance.
- **`deployment_policy`** *(DeploymentPolicy)*: Controls rolling update behavior.
  - `step_size` / `step_size_percent`: Number or percentage of tasks to update per step.
  - `restart_period_ms` *(i64)*: Delay between deployment steps.
  - `cancellation_threshold` / `cancellation_threshold_percent`: Failure threshold to cancel deployment.
  - `bad_update_threshold` *(i32)*: Threshold for bad updates.
  - `staging_timeout_ms` *(i32)*: Timeout for staging phase.
  - `randomize` *(bool)*: Randomize update order.
  - `task_control` *(TaskControl)*: When specified, the job uses task control for coordinating updates with an external controller. All throttling is handled externally. Contains `tier_name` (the task controller tier — can be an SMC tier like `shardmanager.global` or an SRConfig like `webtaskcontrol.vcn.instagram.c2`), `context` (map of key-value pairs for the controller), and optional `restart_period_ms`. Note: the legacy twdeploy schema uses `task_controller_tier` and `task_controller_smc_tier` for this same concept.

---

### Resource Limits

- **`resource_limit`** *(ResourceLimit)*: Resource constraints for each task.
  - `ram` *(string)*: Total memory (e.g. `"1024M"`, `"4G"`). RSS + Cache. Format: `^[0-9]+[KMG][Bb]?$`.
  - `cpu` *(i32)*: Number of logical CPU cores. Default 0 means no CPU limit.
  - `disk` *(string)*: Total disk space (e.g. `"50G"`). Format: `^[0-9]+[KMGT][Bb]?$`.
  - `flash` *(string)*: Flash storage.
  - `network_mbps` *(i32)*: Network bandwidth in Mbps.
  - `gpu` *(i32)*: Number of GPUs.
  - `shared_memory` *(string)*: Shared memory size.
  - `io_bps`, `io_ops`: I/O bandwidth and operations limits.
  - `disk_bps_read`, `disk_bps_write`, `disk_iops_read`, `disk_iops_write`: Per-direction disk I/O limits.

---

### Allocation

- **`allocation_policy`** *(AllocationPolicy)*: Controls where tasks are placed.
  - `entitlement_name` *(string)*: Name of the RAS reservation/entitlement to use.
  - `machine_pool` *(string)*: Machine pool for allocation.
  - `exclusions` / `exclusion_rules`: Rules for preventing colocation with other jobs.
  - `colocations`: Rules for forcing colocation.
  - `server_restrictions`: Attribute-based server restrictions (e.g. `"server.type=II-MC"`).
  - `locality_constraints`: Geographic placement constraints.
  - `security_domain`: Security domain for the job.
- **`reservation_handle`** *(string)*: Handle of a specific reservation to use.

---

### Networking

- **`ports`** *(list\<Port\>)*: Named port declarations. Each has `name` (required), `port` (required), and `protocol`.
- **`smc_bridges`** *(list\<SmcBridge\>)*: SMC tier bridge configurations.
- **`lb_pools`** *(list\<LBConfig\>)*: Load balancer pool configurations with `type` (L4_DSR or L4_SNAT), `lb`, `pool`, and `port_name`.
- **`network_policy`** *(NetworkPolicy)*: IP allocation and transparent TLS settings.
- **`netns_config`** *(NetnsConfig)*: Network namespace configuration.
- **`bgp_vip_config`** *(BgpVipConfig)*: BGP VIP setup.
- **`vip_configs`** *(list\<VipConfig\>)*: VIP configurations.
- **`gue_port_config`** / **`gue_port_configs`**: GUE (Generic UDP Encapsulation) port configurations.

---

### Security

- **`security_policy`** *(SecurityPolicy)*: Security configuration.
  - `serviceIdentities`: TLS service identities.
  - `sshCertificates`: SSH certificate parameters.
  - `soxComplianceParams`: SOX compliance parameters.
  - `bpfJailerParams`: BPF jailer parameters.
- **`kerberos_tier`** *(string)* **[Deprecated]**: Use `security_policy.serviceIdentities` instead.
- **`secrets`** *(list\<Secret\>)*: Secrets to inject into the task.
- **`enable_acl_check`** *(bool)*: Enable ACL checking.

---

### Container & Image

- **`image`** *(ImageConfig)*: Container image configuration. Used when `chroot_profile == 'tupperware.image'`.
  - `fbpkg`: Package reference (name + version).
  - `min_rootfs_size` *(i64)*: Minimum root filesystem size.
  - `filename`: Image filename.
  - `use_tw_managed_image` *(bool)*: Use Tupperware-managed image.
- **`chroot_profile`** *(string)*: Chroot profile name.
- **`lxc_config`** *(LxcConfig)*: LXC-specific configuration.
- **`container_manifest`** *(ContainerManifest)*: Container manifest definition.
- **`private_container_mixins`** *(list\<PrivateContainerMixinRef\>)*: Container mixin references. Use `add_container_mixin()` from `tupperware.lib.py.cli.container_manifest`.
- **`sandbox_spec`** *(common.SandboxSpec)*: Sandbox specification.

---

### Storage & Filesystem

- **`user_directories`** *(list\<Directory\>)*: User-defined directories inside the chroot.
- **`system_files`** *(list\<SystemFile\>)*: System files to provision.
- **`file_system_mounts`** *(map\<string, FileSystemMount\>)*: Named filesystem mount configurations.
- **`persistent_storage`** *(LocalFlashStorage)*: Local flash storage configuration.
- **`tmpfs_size`** *(string)*: Size of tmpfs.

---

### Environment & Configuration

- **`env_variables`** *(map\<string, string\>)*: Environment variables set in the task.
- **`user_attributes`** *(map\<string, string\>)*: User-defined key-value attributes.
- **`task_overrides`** *(map\<i32, TaskOverride\>)*: Per-task ID configuration overrides.
- **`configerator_canary_configs`** *(list\<string\>)*: Absolute paths to canaried configerator configs.
- **`mutable_config`** *(common.MutableUserConfig)*: Mutable configuration that can be updated without restarting.
- **`config_sequence`** *(common.ConfigSequence)*: Config partition structure. **Experimental — under development.**
- **`code_sequence`** *(common.CodeSequence)*: Code sequence configuration.

---

### Policies

- **`maintenance_policy`** *(MaintenancePolicy)*: How the job handles machine maintenance events.
- **`preemption_policy`** *(PreemptionPolicy)*: Preemption behavior.
- **`migration_policy`** *(MigrationPolicy)*: Migration behavior during capacity changes.
- **`job_priority`** *(JobPriority)*: Job priority level.
- **`hotswap_policy`** *(HotswapPolicy)*: Hot-swap update policy.

---

### Timers & Lifecycle

- **`suicide_timeout_ms`** *(i32)*: Timeout after which the task kills itself if unresponsive.
- **`expire_time`** *(i64)*: Job will be stopped after `start_time + expire_time`.
- **`kill_timeout`** *(i32)*: Timeout for kill operations.

---

### Internal Fields

Fields prefixed with `_` are for internal Tupperware use and should not be set manually:

- `_user_job_spec_hash`: Hash of the input user schema for deployment comparison.
- `_raw_spec_abs_file_path`: Original `.tw` file path.
- `_agentInternalConfig`: Internal testing config. Do not set.
- `_setup_tls_status` / `_setup_ssh_status`: TLS/SSH setup status annotations.
- `_compilationMetadata`: Metadata from `twcli` compilation.
- `_unresolved_fields`: Fields that couldn't be resolved during compilation. Non-empty means conversion to scheduler spec will fail.
- `_compilation_error_messages`: Exceptions caught during compilation.
- `_applied_transforms`: List of transforms applied to the job.

---

## 3. Indexed (Searchable) Fields & Supported Operations

> ⚠️ **Note:** NujSpec has no indexed searchable keys and no association types. Queries are limited to ID-based lookups only.

### Refreshing the indexed-fields list

Use the `help()` API with `objectType:16` and `format:2` to discover searchable fields. See universal-search-syntax.md for the runnable command.

For CompareOp values and query syntax, see [universal-search-syntax.md](universal-search-syntax.md).

---

## 4. References

- **Resource.thrift**: `fbcode/tupperware/universal_search/if/Resource.thrift`
- **schema.thrift**: `fbcode/tupperware/twdeploy/config/schema.thrift` (schema.Job — 128-field historical job spec)
- **ResourceSearch.thrift**: `fbcode/tupperware/universal_search/if/ResourceSearch.thrift`
- **Job Language Reference**: `fbcode/tupperware/universal_search/if/job_language_reference.md` (related — covers the live Job resource with scheduler status)
