# Tupperware Help and Troubleshooting

This guide helps you find the right troubleshooting runbook for your Tupperware issue. Many service problems are not actually caused by Tupperware — Tupperware is often just the messenger. Part of troubleshooting is learning whether the issue is with Tupperware, an external dependency, or your own service.

---

## Before You Start

### Getting Help

| Channel | Link | When to Use |
|---------|------|-------------|
| Tupperware & TW Shared Users | [tw.cinc](https://fb.workplace.com/groups/tw.cinc) | Questions about containers, TWShared hosts, hardware, and migrations |
| Tupperware FYI | [tw.fyi](https://fb.workplace.com/groups/tw.fyi/) | Announcements and updates |
| Issue form | [Butterfly form](https://www.internalfb.com/butterfly/form/248980716396027) | Structured form for posting to the TW support group |

### SEV Escalation

For emergencies, contact the [Tupperware oncall](https://fburl.com/tw_oncall) directly. Have the following ready as **copyable text** (not screenshots):

1. Full job handle, task IDs, and/or affected hostnames
2. The `tw` commands you ran and their output (in a paste)
3. What you see vs. what you expect, and when it started
4. Steps to reproduce

---

## Find Your Issue

### Tasks Crashing or Restarting

**[Task Restarts & Crashes](./task-restarts.md)** — Your tasks keep crashing, restarting unexpectedly, or getting stuck in a broken state. Includes a comprehensive exit code reference to interpret why a task died (out of memory, segfault, missing binary, etc.), patterns for identifying different types of crash loops, and procedures for collecting diagnostic data like core dumps and stack traces before a crashing container recycles.

**[OOM & Memory Pressure](./oom-memory.md)** — Your tasks are being killed for exceeding memory limits. Covers how to identify why tasks are being OOM-killed, how to distinguish real memory usage from file cache, how to handle memory spikes after restarts, and how to configure memory limits to prevent future problems.

**[Health Check Failures](./health-checks.md)** — Your tasks appear unhealthy, fail health checks, or get incorrectly removed from serving traffic. Covers why tasks might show as unhealthy even when they are actually running fine (especially for Python services), how to debug deadlocked or hung processes, and how to properly configure health checks to avoid false positives during high-load events or SEVs.

### Deployments and Updates

**[Canary & Deployment](./canary-deployment.md)** — Your canary deployment got stuck, failed to start, or partially reverted. Covers common pitfalls like stale canaries blocking new pushes, spec validation errors, and how to choose between placement-based and count-based canary modes.

**[Job Updates](./job-updates.md)** — Your job update is stuck, progressing too slowly, or behaving unexpectedly. Covers diagnosing why an update is stalled — rate-limited by concurrent host maintenance, blocked by SMC permission errors, throttled by step-size policies, or waiting on package downloads — and how to unblock stuck task operations.

**[Shared Buffer Manager & Dynamic Upsizing](./sbm-dynamic-upsizing.md)** — Your job update is stuck and SBM (Shared Buffer Manager) is enabled for the job. SBM manages the Dynamic Upsizing (DU) capacity buffer across jobs sharing a reservation. Covers diagnosing whether the reservation is capacity-exhausted, whether the SBM buffer is fully utilized, and whether another job's DU task allocation failure is blocking buffer reclamation.

**[Conveyor & CI](./conveyor-ci.md)** — Your Conveyor deployment pipeline is failing — whether it is a push that cannot create the right job, a spec or package validation error blocking the deploy, a flaky CI test signal, or a canary that is stuck. Covers how to determine if CI test failures are safe to bypass, how to unblock stuck pushes, and how to configure canary deployments.

### Capacity and Allocation

**[Allocation & Capacity](./allocation-capacity.md)** — Your tasks are stuck in "pending" because Tupperware cannot find a machine to run them on. Covers a systematic workflow for diagnosing why allocation is failing — whether the reservation is full, machines are under maintenance, resource requests are too large for the available hardware, or elastic capacity is being reclaimed.

**[Scheduling & Preemption](./scheduling-preemption.md)** — Your tasks are being unexpectedly moved or stopped, getting stuck in limbo states, or showing as "lost." Covers step-by-step guidance for diagnosing why tasks were preempted, how to unstick tasks that are frozen in transitional states, and how to control the rate at which tasks are moved during updates.

### Networking and Service Discovery

**[Networking & SMC](./networking-smc.md)** — Your tasks cannot register with or are being dropped from SMC tiers. Covers fixing permission errors that block SMC registration, resolving port configuration mismatches between your job spec and SMC bridges, and understanding why tasks get temporarily disabled in SMC during health check failures.

**[ACL & Permissions](./acl-permissions.md)** — You are getting "permission denied" or authorization errors when trying to SSH into a container, start or delete a job, or sync with service discovery. Covers figuring out exactly which identity and which permission is missing, setting up ACLs correctly for new jobs, and transferring permissions when job ownership changes.

### Job Lifecycle

**[Job Start & Creation](./job-start.md)** — Your job start command is returning errors, tasks are getting stuck and never actually running, or containers are failing to be created. Covers practical advice on testing in a sandbox, handling expired packages, and starting jobs in new regions.

**[Job Stop & Deletion](./job-stop.md)** — You cannot stop or delete a job — deletion is rejected, times out, or the job keeps coming back. Covers safely shutting down jobs, handling sticky host allocations, and understanding which operations are reversible versus permanent.

**[Spec & Config](./spec-config.md)** — Your job spec is failing validation — missing or expired packages, broken thrift imports, module-not-found errors, or lint failures. Covers converting between spec formats, comparing your local spec against what is running in production, and working around transient validation timeouts.

### Infrastructure and Environment

**[Host & Machine Issues](./host-machine.md)** — You suspect a problem with the physical or virtual host — hardware failures (especially GPUs), hosts stuck in a profile migration, or tasks failing only on specific machines. Note: host-level problems are rare — exhaust other categories first. Covers diagnosing whether a problem is host-specific, reporting bad hosts for repair, and moving tasks to healthy machines.

**[Container Environment](./container-environment.md)** — Something is wrong with the runtime environment inside your container — missing shared libraries (e.g., Java), container creation failures, networking changes after IP-per-task migration, or permission errors when using advanced Linux features like BPF. Covers how to inspect what devices, ports, and network configuration your container was actually assigned.

**[Disk & Storage](./disk-storage.md)** — You are seeing "no space left on device" errors, storage reformat failures when a new container starts, or filesystem corruption. Covers how to diagnose what is consuming disk space, how to configure local flash storage, how to monitor disk usage over time, and how to recover from disk-related crash loops.

**[GPU & Accelerator](./gpu-accelerator.md)** — Your GPU workloads are not working correctly — tasks stuck pending because GPUs cannot be allocated, containers seeing the wrong number of GPUs, CUDA initialization failures, GPU out-of-memory errors, or performance problems from incorrect GPU topology. Covers both standard GPU containers and confidential computing (CVM/TEE) workloads.

### Operations and Debugging

**[Logging & Debugging](./logging-debugging.md)** — You cannot access, find, or read logs from your containers — whether the task is still running, has already stopped, or you cannot SSH in at all. Covers a progressive debugging workflow from basic log access through SSH workarounds, debug mode for crash investigation, retrieving logs from dead containers, and copying files out of running tasks.

**[Package & Build](./package-build.md)** — Your package deployment is failing — "package not found" errors, expired packages blocking updates, slow or timed-out downloads, or disk space failures during deployment. Covers how to estimate download times, diagnose why an update is stalled on a package, and understand why packages behave differently on devservers versus production.

**[API & Monitoring](./api-monitoring.md)** — You are getting errors from the Tupperware API, your monitoring metrics or ODS counters are missing, or your requests are being rate-limited. Covers setting up and managing alerts, tracking task restarts and job size over time, and understanding rate limit configuration.

**[DR & Storm Tests](./dr-storm.md)** — You need to prepare for or recover from a disaster recovery or regional storm-test exercise. Covers configuring region distribution policies, understanding failover timelines (~20 minutes), and the important caveat that there is no way to opt out of regional storm drills.

---

## Error Message Quick Reference

If you have a specific error message, find it below to jump to the right runbook.

### Memory and Crashes
- `OOM` / `OOM kill` / `OOM-kill` / `SIGKILL (exit code 137)` / `memory pressure` — see [OOM & Memory](./oom-memory.md)
- `unexpected exit` / `crashloop` / `SIGSEGV` / `SIGABRT` — see [Task Restarts](./task-restarts.md)
- `health check fail` / `readiness check` / `Cannot connect to ptail port` — see [Health Checks](./health-checks.md)

### Deployments
- `canary failed` / `FixedStartupDurationExpired` / `Undefined port` — see [Canary & Deployment](./canary-deployment.md)
- `UpdateNoProgress` / `update stuck` / `rate limited (max: 0)` / `STATUS_STEP_SIZE_LIMITED` — see [Job Updates](./job-updates.md)
- `dynamic step sizing` / `SBM` / `DU buffer` / `emptyAllotments == 0` (with DU enabled) / step size 0 on DU-enabled job — see [Shared Buffer Manager & Dynamic Upsizing](./sbm-dynamic-upsizing.md)
- `conveyor push failed` / `NUJ` error / `Legocastle` / `CI signal` failure — see [Conveyor & CI](./conveyor-ci.md)

### Allocation and Scheduling
- `allocation fail` / `no hosts available` / `pending tasks` / `SELECTION_NO_HOST_IN_SMC` — see [Allocation & Capacity](./allocation-capacity.md)
- `preempt` / `task preempted` / `scheduler shard switch` / `TW_BAD_SCHEDULER_DOMAIN` — see [Scheduling & Preemption](./scheduling-preemption.md)

### Networking and Permissions
- `smcbridge error` / `undefined port in smc_bridges` / `CONNECT_TIMEOUT` — see [Networking & SMC](./networking-smc.md)
- `PERMISSION DENIED` / `not authorized` / `access denied` / `CREDENTIAL_FETCHING_FAILURE` — see [ACL & Permissions](./acl-permissions.md)

### Job Lifecycle
- `container creation failed` / `ERR_SPEC_VALIDATION` / `job not found` / `NOT_FOUND` — see [Job Start](./job-start.md)
- `cannot delete` / `cannot stop` — see [Job Stop](./job-stop.md)
- `validation failed` / `INVALID_TUPPERWARE_SPEC_ERROR` — see [Spec & Config](./spec-config.md)

### Infrastructure
- `bad host` / `plannedMaintenance` / `Failed to reformat storage device` — see [Host & Machine](./host-machine.md)
- `No space` / `disk full` / `FBPKG_INTERNAL_ERROR during storage provisioning` — see [Disk & Storage](./disk-storage.md)
- `BPF token` / `EPERM` on BPF / `nspawn` / `container creation` error — see [Container Environment](./container-environment.md)
- `GPU mode mismatch` / `wrong GPU count` / `vfio` error — see [GPU & Accelerator](./gpu-accelerator.md)

### Packages and Debugging
- `NO_SUCH_PACKAGE` / `NO_SUCH_VERSION` / `FBPKG_INTERNAL_ERROR` / exit code 127 — see [Package & Build](./package-build.md)
- `tw ssh` hangs / `log not found` — see [Logging & Debugging](./logging-debugging.md)
- `ODS counter missing` / `port 0` returned / `TW_BAD_SCHEDULER_DOMAIN` (API) — see [API & Monitoring](./api-monitoring.md)

---

## Issues That Span Multiple Areas

Some problems involve more than one category. Common overlaps:

- **OOM + Task Restarts** — OOM kills cause unexpected task exits. Check memory first, then task events.
- **Canary + Health Checks** — Canary failures often result from health check failures during the rollout.
- **Networking + Health Checks** — SMC bridge errors can manifest as health check failures.
- **Allocation + Scheduling** — Allocation failures may be caused by preemption removing capacity.
- **Package + Canary** — Package fetch failures during canary cause deployments to stall.
- **Conveyor + Canary** — Conveyor pipelines include canary steps; check Conveyor first, then canary.
- **Spec + Job Start** — Spec validation errors prevent job creation. Validate before starting.
- **Host + Allocation** — Bad hosts reduce available capacity and trigger pending tasks.
- **Container + Task Restarts** — Container creation failures manifest as task restart loops.
- **Logging + ACL** — SSH access failures are often ACL permission issues.
- **GPU + Task Restarts** — GPU mode mismatches in CVM/TEE can cause crash loops.
- **GPU + Allocation** — Full-machine allotments show 8 GPUs even for 1-GPU tasks; check assigned IDs, not allotment count.
- **SBM + Allocation** — SBM buffer exhaustion is often caused by a DU task failing to allocate, holding the buffer and blocking other DU-enabled jobs. Check allocation failures for the buffer-holding job.
- **SBM + Job Updates** — Stuck updates on DU-enabled jobs may be SBM throttling (expected) or an allocation failure blocking buffer reclamation. Check SBM status before debugging the update itself.
