# GPU & Accelerator Issues

> Primary CLI: `tw job print`, `tw log` | Primary Scuba: `tupperware_task_events`, `gpu_dyno_stats`
>
> Wiki: [Accelerators in TW](https://www.internalfb.com/wiki/Infra_Cloud/Service_Hosting/Tupperware/Tupperware_Reference/Accelerators_%28GPU%2C_ASIC%29_in_Tupperware/), [GPU Reservations](https://www.internalfb.com/wiki/Shaoshuai/Tupperware_Wiki/Capacity_v2/Reservations/GPU_Reservations/)

## Debugging Playbook

### Step 1: Identify the GPU issue category

| Symptom | Go to |
|---------|-------|
| Task pending / can't allocate GPUs | [Allocation Failures](#gpu-allocation-failures) |
| Wrong GPU count in container | [Device Count Mismatch](#device-count-mismatch) |
| `GPU mode mismatch` crash (CVM/TEE) | [CVM/TEE Issues](#cvmtee-gpu-issues) |
| GPU OOM / memory errors | [GPU Memory Issues](#gpu-memory-issues) |
| CUDA init failure / driver error | [CUDA & Driver Issues](#cuda--driver-issues) |
| Accelerator bundle error on host | [Bundle Generation Failures](#accelerator-bundle-failures) |
| GPU perf regression / NVLink down | [Topology & Performance](#topology--performance) |

### Step 2: Gather GPU allocation state

**Check job spec:**
```bash
tw job print <job_handle> --json | jq '.[] | {numAccelerators: .requirements.requirements.accelerator.numAccelerators, jobSize: .jobSize}'
```

**Check task assignment via Universal Search:**
```bash
thriftdbg sendRequest search '{"request":{"select":{"selectedJsonPaths":["$.taskHandle.handle","$.schedulerState","$.latestTaskInfo.assignedAcceleratorIDs","$.allocation.resourceAllotment.acceleratorCount","$.allocation.resourceAllotment.id.shapeName","$.allocation.machine.name.hostname","$.allocation.machine.logicalServerSubType"]},"from":4,"where":{"assocFilter":{"assocObjectType":1,"assocObjectIds":["<JOB_HANDLE>"]}},"jsonResponseFormat":{}}}' --tier tupperware.universal_search.experiment.test --request_timeout_ms 90000 | jq
```

**Key fields:**
- `numAccelerators` — requested GPU count
- `assignedAcceleratorIDs` — actual GPU IDs assigned (array length = real count)
- `resourceAllotment.acceleratorCount` — total GPUs on the allotment (**not** the task's share)
- `resourceAllotment.id.shapeName` — `M0` = full machine; GPU shapes = 1/2/4/8 GPUs

> **Common misconception:** `acceleratorCount` in the allotment shows the **machine's total**, not the task's share. On a full-machine allotment (M0) on T16_GRAND_TETON, this is always 8 even for 1-GPU tasks.

**Check what the container actually sees:**
```bash
tw ssh <task_handle>
jq '.devices.acceleratorDevices | length' /etc/tw/api/metadata.json
jq '.devices.acceleratorDevices' /etc/tw/api/metadata.json
```

For CVM/TEE tasks (can't SSH directly), search logs for VFIO passthrough:
```bash
tw log <task_handle> --file stderr --pattern "vfio-pci|pcie_root_port|acceleratorDevices" -C 3 -s "24 hours ago" -n 200
```

**Query the TW agent directly (most authoritative):** Use `twac export-task-spec -t <task_handle> -s <hostname>` to see the agent's live GPU assignment, including `assignedAcceleratorIDs`. Use this when Universal Search data seems stale.

### Step 3: Check other tasks on the same host

Multiple tasks can share GPUs on a full-machine allotment. Verify no double-assignment:
```bash
thriftdbg sendRequest search '{"request":{"select":{"selectedJsonPaths":["$.taskHandle.handle","$.schedulerState","$.latestTaskInfo.assignedAcceleratorIDs"]},"from":4,"where":{"assocFilter":{"assocObjectType":5,"assocObjectIds":["<HOSTNAME>"]}},"jsonResponseFormat":{}}}' --tier tupperware.universal_search.experiment.test --request_timeout_ms 90000 | jq
```

---

## GPU Allocation Failures

Tasks pending because GPUs can't be allocated.

**Common errors:**
- `ERR_COULD_NOT_ALLOCATE_RESOURCE: Could not find accelerators to allocate`
- `Failed to reserve accelerators NICs`

**Root causes and fixes:**

| Cause | Diagnostic | Fix |
|-------|-----------|-----|
| All GPUs on host already taken | Check host colocated tasks (Step 3 above) | Wait for capacity or add hosts to reservation |
| Accelerator bundle missing on host | `ls /run/facebook/tw_ha_accelerator_bundle.conf` via `tw ssh` | `systemctl restart host_agent_accelerator_bundle_oneshot.service` |
| Topology mismatch (requesting 3 GPUs; only 1/2/4/8 supported) | Check `numAccelerators` value | Use valid topology-aware counts: 1, 2, 4, or 8 |
| GPU capacity fragmentation | Check if host has free GPUs but no contiguous bundle | Enable **StackUp** on reservation; or preempt with `tw allocation preempt` |
| Mixing different AcceleratorSpec sizes on same reservation | TW adds `uniform_gpu_stacking_X` labels preventing mixing | Use separate reservations per GPU shape |

---

## Device Count Mismatch

Container sees a different number of GPUs than expected.

**Three layers to check — they can disagree:**

| Layer | Source of truth | How to check |
|-------|----------------|-------------|
| Job spec | `numAccelerators` | `tw job print --json` |
| Scheduler | `assignedAcceleratorIDs` | Universal Search or `twac export-task-spec` |
| Container | `acceleratorDevices` in metadata.json | `jq '.devices.acceleratorDevices' /etc/tw/api/metadata.json` |

The TW agent is designed to expose **only** the task's assigned GPUs in metadata.json. If the counts disagree, check:

1. **ResourceAssignment from scheduler** — query `$.spec.resourceAssignment` via Universal Search to verify what IDs the scheduler sent
2. **Agent state** — use `twac export-task-spec -t <handle> -s <host>` to see what it actually reserved
3. **Stale Universal Search data** — for tasks with many hotswaps, Universal Search may lag; `twac` reflects current agent state

---

## CVM/TEE GPU Issues

### GPU mode mismatch crash

```
RuntimeError: GPU mode mismatch. Expected GpuMode.SINGLE, but got GpuMode.MULTI from TW metadata file
```

The TEE Task Manager derives `GpuMode` from metadata device count (0=NONE, 1=SINGLE, 8=MULTI) and compares against `build_config.json` (set at BUCK build time). A mismatch causes a crash loop.

**Check:**
```bash
tw log <task_handle> --file stderr --pattern "GPU mode mismatch" -C 5 -s "24 hours ago"
```

**Relevant env vars:** `TEE_TM_GPUMODE` (SINGLE/MULTI), `TEE_TM_ENABLE_GPU` (0/1).

### VFIO passthrough architecture

CVM workloads use VFIO (`/dev/vfio/*`) instead of native NVIDIA drivers. The processvm agent (QEMU) passes through all `acceleratorDevices` listed in the task's metadata.json — which already contains only this task's assigned GPUs (scoped by the TW agent's `reserveDevices()` logic in `AcceleratorDeviceManagerNew.cpp`). GPU count enforcement happens at the TW agent layer, not QEMU.

### GPU CC mode

GPUs in TEE require Confidential Compute mode. Check and set via:
```bash
nvidia_gpu_admin_tools --query-cc-mode --gpu-bdf <BDF>
```

> [!WARNING]
> Never enable/disable CC mode while the nvidia driver is loaded — the GPU will fall off the bus.

**Host profiles:** Single GPU TEE uses `NVIDIA_CONF_COMPUTE_CC_ON`; multi-GPU TEE on H100 uses `NVIDIA_CONF_COMPUTE_PPCIE_ON`.

---

## GPU Memory Issues

**Common errors:**
- `CUDA error: CUDA-capable device(s) is/are busy or unavailable`
- `HIP out of memory. Tried to allocate X MiB`
- `Free memory on device cuda:0 ... is less than desired GPU memory utilization`

**Debugging:**
1. Check ODS metrics: `tw.accelerator.mem-used-pct`, `tw.accelerator.mem-used-pct-max`
2. SSH with debug mode to inspect: `tw job debug-mode <job_handle>`, then `tw ssh <task_handle>`
3. On host: `fuser /dev/nvidia*` to find processes holding GPU memory

**Fix patterns:**
- **GB200 page cache leak:** `sudo sh -c 'echo 3 > /proc/sys/vm/drop_caches'` on affected host
- **Stale GPU memory after crash:** Preempt (not just restart) the task to force full GPU cleanup
- **NUMA misalignment:** Use `nvidia_gpu_numa_bind(job, max_nodes_to_bind=1)` in job spec
- **Model too large:** Reduce `VLLM_GPU_MEMORY_UTILIZATION` or use gang scheduling for multi-host

---

## CUDA & Driver Issues

**Common errors:**
- `CUDA initialization: CUDA unknown error`
- `c10::cuda::device_count()` returns 0
- `stat: cannot statx '/dev/nvidia[0-9]*': No such file or directory`

**Root causes:**
- CUDA library version mismatch between container image and host driver
- `nvidia-smi` is a host tool, not included in containers by default
- VFIO hosts run the nvidia driver **inside** the VM, not on the host
- Race condition during CUDA init (low probability per restart, eventually succeeds)

**Fix patterns:**
- Use `tw ssh --debug-mode` for live debugging
- Check GPU visibility: `ls /dev/nvidia*` (NVIDIA) or `ls /dev/dri/renderD*` (AMD)
- For CVM: ensure CUDA libraries in the CVM image match the GPU driver version
- Monitor without `nvidia-smi`: use ODS counters `tw.accelerator.util-pct`, `tw.accelerator.mem-used-pct`

---

## Accelerator Bundle Failures

See [host-machine.md § Accelerator Bundle Failures](./host-machine.md#accelerator-bundle-failures-gpu-hosts) for full debugging of accelerator bundle generation failures (`Setup accelerator configuration failed`, `Failed to find Nvidia device with minor`, etc.). These are host-level issues — if the bundle fails, no GPU tasks can start on the host.

---

## Topology & Performance

**GPU topology awareness:** TW allocates GPUs in topology-aware bundles (1/2/4/8). A 2-GPU allocation picks an NVLink-connected pair, not arbitrary GPUs.

**NVLink bandwidth:** NVLink >> PCIe. Wrong GPU pairing can reduce performance by >50%.

**Check topology:** `nvidia-smi topo -m` (from host, not container)

**NVSwitch issues:** NVSwitches enable all-to-all GPU communication on 8-GPU servers. If NVSwitches fail, only direct NVLink pairs work, degrading multi-GPU performance significantly.

**Fragmentation:** If a reservation has scattered 1-GPU tasks across hosts, no contiguous 2/4/8-GPU bundles may be available despite free GPUs. Enable **StackUp** on the reservation to pack tasks onto partially filled hosts first.

---

## Key Concepts

### GPU Shapes and Stacking

| GPU Shape | GPUs | Stacking on 8-GPU server |
|-----------|------|-------------------------|
| 1 GPU | 1 | Up to 8 tasks |
| 2 GPU | 2 | Up to 4 tasks |
| 4 GPU | 4 | Up to 2 tasks |
| 8 GPU | 8 | 1 task (whole machine) |

### Device Types

| Type | Path | Used by |
|------|------|---------|
| Native NVIDIA | `/dev/nvidia0` ... `/dev/nvidia7` | Regular GPU containers |
| VFIO passthrough | `/dev/vfio/<iommu_group>` | CVM/TEE workloads |
| AMD | `/dev/dri/renderD*` | AMD accelerator containers |

### Metadata API

`/etc/tw/api/metadata.json` reports only the task's assigned GPUs. See [container-environment.md](./container-environment.md) for full metadata inspection (fields, jq commands, programmatic libraries).

### GPU Monitoring (ODS)

| Counter | Description | Note |
|---------|-------------|------|
| `tw.accelerator.util-pct` | SUM compute utilization | Divide by `num-devices` for average |
| `tw.accelerator.mem-used-pct` | SUM memory utilization | Divide by `num-devices` for average |
| `tw.accelerator.num-devices` | Allocated device count | |

For MIG workloads, `tw.accelerator.util_pct` does **not** work — use dynolog counters instead.

For VFIO workloads (CVM), host-level `rgpu` has no NVML access — zero GPU metrics for VM workloads (known gap).

## Escalation

| Issue | Oncall |
|-------|--------|
| TW Agent / container GPU issues | `tupperware_agent` |
| Host accelerator bundle / hardware | `hm_hardware_enablement` |
| GPU hardware failure detection | `cfhs_failure_detection` |
| Scheduler / allocation | `tupperware_scheduler_core` |
| TEE/CVM GPU passthrough | `tee_llm_inference` |
| GPU capacity / reservations | `tw_capacity` |

## Reference Tables

### Scuba Tables
| Table | When to Use | Key Columns |
|-------|------------|-------------|
| `tupperware_task_events` | Task lifecycle events — see [dataset reference](../datasets/tupperware_task_events.md) | `job`, `task`, `event_name` |
| `gpu_dyno_stats` | GPU performance metrics (10s) | `tw_job_handle`, `gpu_instance_id` |
| `tw_allocator_v2_allocation_failures` | GPU allocation failures | `job_handle`, `failures` |
| `tw_rebalancer_executor_stats` | GPU defragmentation actions | `reservation`, `operation_type` |

### CLI Commands (twac — escalation only, when scheduler view seems inconsistent)
| Command | When to Use |
|---------|------------|
| `twac export-task-spec -t <handle> -s <host>` | Verify `assignedAcceleratorIDs` when GPU count mismatch between scheduler and container |
| `twac get-resource-usage -t <handle> -s <host>` | Check GPU resource usage when ODS metrics seem inconsistent |
| `twac list-allotments -s <hostname>` | Verify GPU shape and bundle assignments when allocation looks wrong |
| `twac show-cpu-topology -s <hostname>` | Verify NUMA-aware GPU pinning when performance suggests incorrect topology |
