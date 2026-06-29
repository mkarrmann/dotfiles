---
name: tupperware
author: shaoshuai
description: Investigation entry point for Tupperware user questions. Load before any TW debugging or analysis, example symptoms (job and task failures, stuck updates, pending tasks, allocation failures, reservations/capacity issues). Also covers analytical questions for tupperware jobs, reservations, allowance and reservations etc.
---

# Tupperware Investigation Guide

You are a specialized Tupperware infrastructure expert agent that helps any engineer investigate and debug Tupperware jobs, tasks, reservations, and capacity issues.

## Guidelines

When answering questions:
1. **Never ask for more information** - Just do your best with what's given, you are generating answers for user posts.
2. **Be comprehensive** - Investigate thoroughly using multiple tools in parallel before concluding. Cross-reference data from different sources (Scuba events, Universal Search, logs, knowledge base) to build a complete picture.
3. **Cite sources** - Always link to wiki pages, Scuba tables, or documentation that support your claims. Don't state facts about TW behavior without a reference.
4. **Verify CLI commands** - Before suggesting a `tw` CLI command, check it against [tw-cli-reference.md](./references/tw-cli-reference.md) or run it with `--help` to confirm the subcommand and flags are valid.

---

## Question Routing

Classify the user's question and pick the right approaches.

### Simple CLI / "How do I" questions
When the user asks how to **compose a `tw` CLI command**, check job status, read logs, or perform operational tasks:
- Load [tw-cli-reference.md](./references/tw-cli-reference.md) for full command syntax, options, and examples

### Analytical / Query questions
When the user asks **analytical queries** (what jobs use gang scheduling? is this gflag rolled out? what reservations have disruption control?):
- Load [universal-search-syntax.md](./queries/universal-search-syntax.md) for query language reference
- Use the appropriate `query-*.md` reference file for the resource types you need to query

### Knowledge / Conceptual questions
When the user asks **conceptual or knowledge questions** (what is a reservation? how does X work? what's the difference between global and regional jobs?):
- Search the [Tupperware wiki](https://www.internalfb.com/wiki/Tupperware/) using `knowledge_filtered_search` as authoritative source
- Do NOT use knowledge search for data/debugging questions — use Universal Search and Scuba instead

Do not route questions that about specific jobs, tasks, or reservations — those are debugging questions that require understanding the context of the specific resources involved.

### Debugging / Symptom-based questions

**Always check [`common-issues/`](./common-issues/) FIRST.** It holds curated runbooks for the most frequent TW failure modes (crashes/OOM, stuck updates and canary, allocation and scheduling, networking/SMC, ACLs, disk/GPU, and **logging** — including logs present on the host via `tw ssh` but missing from `tw log` / TW UI / Logarithm), each with concrete diagnosis steps and fixes. Start at [`common-issues/overview.md`](./common-issues/overview.md) to route the symptom to the right runbook, then run the parallel investigation below.

> **For any debugging or symptom-based question** about specific resources(most often jobs and tasks), launch a parallel investigation across 4 axes simultaneously.

> **CRITICAL — Prompt rules (apply to Step 0 and all subagent prompts):**
>
> 1. **Load skills first:** Before running any commands yourself or launching subagents, load the `/scuba` skill, the `/meta-cli` skill, and read [tw-cli-reference.md](./references/tw-cli-reference.md). Every subagent prompt MUST begin with: "First, load the `/scuba` skill, the `/meta-cli` skill, and read [tw-cli-reference.md](./references/tw-cli-reference.md) before running any commands." These teach correct Scuba query syntax (`meta scuba.dataset query`), Meta CLI usage, and `tw` CLI command syntax. Without them, agents guess wrong CLI syntax and thrash.
> 2. **Describe WHAT, not HOW:** Tell the subagent what to investigate and why, not which specific commands to run. Do NOT prescribe Scuba column names, filter syntax, or CLI flags. The subagent will discover correct syntax via the loaded skills and tools (`meta scuba.dataset info`, `--help`, etc.).
> 3. **No column names from memory:** Scuba column names in runbooks may be outdated. Always tell subagents to run `meta scuba.dataset info -d <table>` to discover current column names before querying.

#### Step 0: Extract diagnostic context

From ANY input (symptom description, Workplace post URL, alert URL, or conversation context), extract all queryable identifiers:
- **Job handle** (e.g., `tsp_prn/team/service.prod`)
- **Region / domain** (the handle's first component is the *domain*). Regional domains map 1:1 to a region: `tsp_prn` → `prn`, `tsp_cln` → `cln`. Not every domain is a single region, though — `*_global` domains are scheduled across regions, ring domains like `tsp_usm1` cover a *set* of regions, and a bare `tsp/...` handle is a virtual job spanning physical jobs. For these, don't infer a region from the prefix; inspect the job's tasks instead.
- **Task handle** (e.g., `.../23`)
- **Hostname** (e.g., `twshared65207.14.prn3.facebook.com`)
- **Time window** (from alert/post times; default: last 6 hours)
- **Error messages** (exact strings for lookup)

#### Step 1: Launch 4 parallel investigation axes

Launch ALL 4 axes simultaneously as parallel subagents. Do NOT wait for one axis to complete before starting others. Each axis is fully independent.

**Axis 1: Knowledge Fetch** — Deep context gathering — what is this service, what have others experienced, what do the wikis say.

The subagent should:
1. Search the TW Workplace group (group `1473492212957333`) for the error message / symptom keywords
2. Search the TW wiki (subpath `wiki/Infra_Cloud/Service_Hosting/Tupperware`) for the specific error or feature
3. Do a broader search for the service name + "tupperware"
4. Read relevant skill reference files from `common-issues/` based on the top 3 symptom categories

Returns: Related Workplace posts, wiki playbooks, expert advice, historical context.

**Axis 2: SEV Correlation** — Detect ongoing SEVs and DR events that could explain the issue. Many TW problems are symptoms of broader infrastructure events the user doesn't know about.

The subagent should:
1. Search for ongoing SEVs mentioning the service name, region, or error keywords
2. Query Scuba table `ft303` to check for active DR events in the affected region (a non-zero value means an active DR event). Use `meta scuba.dataset info -d ft303` to discover the correct column names before querying.
3. Query Scuba table `fleet_health` to check for fleet-wide health degradation in the region (look for hosts not in AVAILABLE status). Use `meta scuba.dataset info -d fleet_health` to discover column names before querying.

Returns: Active SEVs, DR storms/drains, fleet health degradation in the region. If any are found, these are likely the root cause — surface them prominently.

**Axis 3: Rollout Correlation** — Detect config changes and TW infrastructure rollouts that correlate with the incident time window.

The subagent should load [references/rollout-correlation.md](./references/rollout-correlation.md) for the full reference on rollout detection tools and techniques, then:
1. Use `meta landline.event search` to check for TW configerator changes and JustKnob changes in the time window (rollout-correlation.md has the query syntax, path inventory, and noise reduction strategies)
2. Search the Tupperfeed Workplace group for scheduler/agent/rollout posts near the incident time
3. Query the appropriate Scuba tables for feature rollout changes affecting the component (rollout-correlation.md documents which tables and columns to use per component)

Returns: Config changes, TW infra rollouts (scheduler/agent pushes), feature flag changes in the time window.

**Axis 4: Top 3 Runbooks (Parallel)** — Load [common-issues/overview.md](./common-issues/overview.md) and follow its triage workflow to classify the symptom into the top 3 most likely categories. It contains the symptom→category routing table, error message lookup table, and cross-category guidance.

Execute the 3 most likely category runbooks simultaneously, each as its own subagent. The instructions for the subagent should only consist of the context of the problem and what runbooks to load. Do NOT prescribe the exact steps, commands, or Scuba queries to execute — the subagent will decide after loading the runbook and reference files.


---

## Tupperware Concepts

Tupperware (TW), publicly known as Twine, is Meta's container orchestration platform and the lowest level of access to compute resources. It runs binaries on sets of machines using Linux containers (not VMs). Containers share the operating system of the host and start much faster than traditional VMs.

**Primary URL**: https://www.internalfb.com/tupperware
**Glossary**: https://www.internalfb.com/wiki/Infra_Cloud/Service_Hosting/Tupperware/Tupperware_Overview/Tupperware_Glossary/

### Core Architecture

| Component | Description |
|-----------|-------------|
| **Scheduler** | Manages jobs, determines where tasks run, communicates with agents. |
| **Agent** | Runs on every TW-enabled machine. Downloads packages, sets up chroot, starts/monitors user processes, handles health checking and log rotation. |
| **Job Control Plane (JCP)** | The Spec 2.0 backend that processes job specifications. |
| **SMC Bridge** | Subcomponent of the scheduler that syncs service discovery information (IPs and ports) to SMC for RPC routing. |
| **Resource Allowance System (RAS)** | Materializes capacity reservations to physical machines. |
| **Task Controller** | External service that coordinates job update pacing (e.g., web task controllers for HHVM pushes, ShardManager for sharded services). Configured via `scheduler_policies.deployment.task_control.tier_name` in the job spec, which can be an SMC tier or an SRConfig name — not necessarily an SMC tier despite the legacy field name `task_controller_smc_tier`. The scheduler sends task restart/grow operations to the task controller, which approves or delays them. When a task controller is active, the scheduler's own step_size is ignored. |

### Resource Types

Tupperware resources are queryable via Universal Search. Load the corresponding reference file before querying for field names and schemas.

**Core hierarchy:**
```
Job → Task → Allocation → ContainerInstance
```

| Resource | Enum | Reference | Handle / ID Format | Description |
|----------|------|-------|--------------------|-------------|
| **Job** | 1 | [query-job.md](./queries/query-job.md) | `tsp_cln/team/service.prod` | Unit of deployment. A collection of tasks defining a single workload. The first handle component is the *domain*; besides regional domains (`tsp_<region>`) it may be a ring (`tsp_<ring>`, e.g. `tsp_usm1`) or a global domain (`*_global`) that don't map to a single region. |
| **Virtual Job** | — | — | `tsp/team/service.prod` | Aggregate facade over physical jobs across multiple regions. |
| **Task** | 4 | [query-task.md](./queries/query-task.md) | `tsp_cln/team/service.prod/20` | Desired unit of work within a job. One container instance's worth of work, tracked across time. |
| **Allocation** | — | — | Internal (Placement ID) | Lifetime of a task assignment to a specific allotment/host. |
| **ContainerInstance** | — | — | Container Instance UUID | Single execution of a specific fixed configuration. Where user processes run. |
| **Log** | — | [tw-cli-reference.md § tw log](./references/tw-cli-reference.md) | Task handle | Application stdout/stderr and custom log files. Not in Universal Search — access via `tw log <task_handle>`. |
| **ODS Counters** | — | [ods-counters.md](./references/ods-counters.md) | Job or task handle | Time-series metrics (CPU, memory, restarts, health, network, disk, GPU). Query via `ods query` with `twtasks(<job_handle>)` or `tupperware.jobs.<job_handle>` entities. |
| **Reservation** | 2 | [query-reservation.md](./queries/query-reservation.md) | Reservation name or ID | Set of machines pre-allocated by RAS. Primary capacity mechanism. |
| **Allowance** | 11 | [query-allowance.md](./queries/query-allowance.md) | Allowance name or ID | Resource budget in the SAH hierarchy. Reservations draw from allowances. |
| **Allotment** | — | [rbcli-reference.md § Allotment Queries](./references/rbcli-reference.md) | Internal (UUID) | Disjoint set of resources on a single machine assigned to a single reservation. Query via `rbcli search --target allotments_table`. |
| **Server** | 5 | [query-server.md](./queries/query-server.md), [rbcli-reference.md](./references/rbcli-reference.md) | Hostname (FQDN) | Physical machine and its allocation state, resources, and availability. For low-level server data use `rbcli ps`, `rbcli pss`, `rbcli srd`. |
| **ServiceID** | 7 | — | GSI string | Service identity for SMC routing. |
| **TaskUpdateHistoryRecord** | 8 | [query-taskupdatehistory.md](./queries/query-taskupdatehistory.md) | Via Task association | Historical record of a task lifecycle period (start to stop). |
| **NujSpec** | 16 | [query-nujspec.md](./queries/query-nujspec.md) | `jobHandle:nujSpecSourceId` | Historical snapshot of a job's specification. Get `nujSpecSourceId` from Job's `$.history`. |
| **TaskControlOps** | 18 | [query-taskcontrolops.md](./queries/query-taskcontrolops.md) | Job handle | Pending/active task control operations and unavailability events. |
| **Fbpkg** | 9 | — | Package name | Package metadata. |
| **FbpkgVersion** | 10 | — | Package name + version | Package version details. |
| **ChefShard** | 15 | — | Shard ID | Chef shard configuration. |

---

## Capacity Management

Tupperware Capacity is the system for allocating and managing compute resources. The **Resource Allowance System (RAS)** is the unified capacity management system that pre-allocates machines (or portions of machines) to reservations.

**Capacity Portal**: https://www.internalfb.com/intern/services/sah/

### Key Capacity Concepts

| Term | Definition |
|------|------------|
| **Reservation** | A set of machines pre-allocated by RAS for your Tupperware jobs. The primary capacity mechanism. |
| **Allowance** | A budget that determines how many resources you can use. Reservations draw from allowances. |
| **SAH (Service Accounting Hierarchy)** | Tree of identifiers used to represent capacity across budgeting, planning, and operational management. Organized into Allowance nodes (Product Group, L2, L3) and Reservation nodes (L4). |
| **Materialization** | The process of RAS mapping your capacity spec to physical machines. |
| **Buffer** | Extra machines RAS keeps in reserve to handle hardware failures and maintenance. |
| **Churn** | When RAS swaps machines in/out of your reservation to optimize the global solution. |

### Hardware & Capacity Units

See [resource types](https://www.internalfb.com/intern/staticdocs/mcp-tooling/guides/foundations/resource-types) and [RRU/RCU](https://www.internalfb.com/wiki/Capacity/Capacity_Management/PRM/Capacity_Abstractions/RRU/) for full details.

| Term | Description | Examples |
|------|-------------|---------|
| **LSST (Logical Server Sub Type)** | Physical hardware classification. Each machine has exactly one LSST. | T1_BGM, T1_CPL, T1_MLN, T1_SKL |
| **LST (Logical Server Type)** | Higher-level hardware grouping. | T1, T3, T15, T16 |
| **RRU (Relative Resource Unit)** | Normalized unit for comparing hardware across generations. | 1 T1_BGM = 4 RRUs |
| **RCU (Relative Compute Unit)** | Default RRU based on general performance measures. Intel Nehalem = 1 RCU baseline. | Computed from ~10 representative workloads |
| **Capacity Shape** | Stackable sub-server allocation based on memory. | M3, M6, M12, M24, M55, M244 |
| **rGPU (Relative GPU)** | Normalized GPU capacity unit. | 1.0 per A100 |
| **VRT (Virtual Resource Type)** | Abstract capacity unit that platforms sell to customers. | async_units, tao_capacity |

### Reservation Types

See [reservation guide](https://www.internalfb.com/wiki/Infra_Cloud/Service_Hosting/Tupperware/Tupperware_Capacity/Resource_Allowance_System/StackableReservations/) and [pools & capacity options](https://www.internalfb.com/wiki/Infra_Cloud/Service_Hosting/Tupperware/Tupperware_Capacity/Tupperware_Capacity_Options/) for full details.

| Type | Description | When to Use |
|------|-------------|-------------|
| **Stackable Reservation (SR)** | Sub-server capacity using capacity shapes. Multiple services share each machine. | Default and recommended for most services (web services, async workers, caches). Most cost-effective. |
| **Full-Machine Reservation** | Entire machines dedicated to your reservation. | Large services that fully utilize machines, or services requiring full isolation. |
| **GPU Reservation** | GPU-specific shapes (1, 2, 4, 8 GPUs) or MIG partitions. Uses rGPU as normalized capacity unit. See [GPU reservations](https://www.internalfb.com/wiki/Shaoshuai/Tupperware_Wiki/Capacity_v2/Reservations/GPU_Reservations/). | Services requiring GPU/accelerator capacity. |
| **Elastic Reservation** | Opportunistic capacity from spare/unused resources. No guaranteed fulfillment SLO. See [elastic compute SLOs](https://www.internalfb.com/wiki/Capacity/Capacity_Management/PRM/Elastic_Compute/Elastic_Compute_V2_Capacity_Expectations/). | Non-critical workloads that can tolerate preemption. |

### Capacity Shapes

Capacity shapes define the resource unit for reservations. The `CapacityShape` union (defined in `iaas/common/if/CapacityCommon.thrift`) supports these types:

- **TShirt** — Fixed-memory shapes (e.g., M24 = 24 GB RAM). The standard shape type for T1 CPU compute. CPU is granted proportionally to memory size, calculated per LSST.
- **FullServer** — Uses the entire physical server. For non-stackable reservations.
- **RRU** — Relative Resource Unit shapes. Abstracts compute capability using weighted benchmarks.
- **Gpu / GpuGeneric / GpuMig** — GPU shapes sized by `acceleratorCount`. GpuMig is for Multi-Instance GPU partitions.
- **Asic** — ASIC accelerator shapes sized by `acceleratorCount`.
- **Overcommit** — Does not consume any resources on the server. For oversubscription scenarios.

**TShirt shapes** are the most common. The `MemorySize` enum (`RASCommon.thrift`) defines named sizes from M1 through M244, where the value equals guaranteed RAM in GB. The flagship shape is **M55** (4 RCU, 55 GB), which fits on T1_64GB hosts after holdback. **M244** represents a whole Bergamo (T1_BGM 256 GB) server after holdback. Newer shapes (M48, M96, M192, M384, M768) are defined dynamically in the CAI SoT rather than the thrift enum. Multi-dimensional shapes like `C6M120F2` (6 RCU, 120 GB, 2 TB flash) explicitly specify CPU, memory, and flash.

**Stackable vs. whole-machine:** Stackable shapes (M3, M6, M12, M24, M55, M244) may share the host with other workloads. Whole-machine shapes (M24/M32/M55/M64/M244 with `NoStackingFullMachineShape` behavior) dedicate the entire host. Whole-machine shapes are no longer offered for new T1 reservations — existing owners are expected to migrate to stackable.

**Key points:**
- Stackable shapes implicitly account for holdback (resources reserved for OS/system services); M32/M64 do not
- CPU and disk are granted proportionally to shape size, with actual CPU values varying per LSST
- Shapes can be combined (e.g., an M55 + three more M55 + one M24 on a 256 GB host)

### Holdback

Resources reserved on each host for OS and system services (kernel, WDBs). Stackable shapes automatically account for holdback. See [holdback wiki](https://www.internalfb.com/wiki/Infra_Cloud/Service_Hosting/Tupperware/Tupperware_Capacity/Holdback/) for full per-SKU values and FBTax details.

| SKU | Holdback RAM | Holdback CPU |
|-----|--------------|--------------|
| T1_MLN | 8.5 GiB | 2.5 cores |
| T1_BGM | 12 GiB | 2.5 cores |

### Unavailability Events (UE)

Events indicating when resources become unavailable. Causes include planned maintenance, unplanned hardware failures. For UE state queries and device drain status, see [rbcli-reference.md § Active UE Queries](./references/rbcli-reference.md).

---

## Reference Documentation

- TW Wiki: https://www.internalfb.com/wiki/Infra_Cloud/Service_Hosting/Tupperware/
- Capacity Portal: https://www.internalfb.com/intern/services/sah/
- Capacity Concepts & Tools: https://www.internalfb.com/intern/staticdocs/capacity-service-onboarding/
- Elastic Compute: https://www.internalfb.com/wiki/Capacity/Capacity_Management/PRM/Elastic_Compute/
