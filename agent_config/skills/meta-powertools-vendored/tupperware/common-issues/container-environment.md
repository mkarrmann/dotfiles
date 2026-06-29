# Container Environment

> 58 posts in TW Group FAQ | Primary Scuba: `tupperware_task_events` | Primary CLI: `tw ssh`

## Debugging Playbook

Minimal playbook for container environment issues. Most posts in this category are informational (57%), so the Common Questions section below is the primary reference.

**CLI**: `tw ssh <task_handle>` -- enter the container and investigate. Check the task's environment variables, available mounts, and device nodes.

Pick the section matching the symptom:

| Symptom | Go to |
|---------|-------|
| `EPERM` on BPF operations | [BPF Token Failures](#bpf-token-failures) |
| Container creation failed / nspawn error | [Container Creation Failures](#container-creation-failures) |
| TLS cert fetch failure during container creation | [TLS Certificate Fetch Failures](#tls-certificate-fetch-failures) |
| `libjvm.so: cannot open shared object file` / missing shared libs | [Missing Shared Libraries](#missing-shared-libraries) |
| Service broken after IP-per-task migration | [IP-per-Task and Network Namespace](#ip-per-task-and-network-namespace) |
| Need to inspect metadata (devices, ports, IPs) | [Task Metadata Inspection](#how-to-inspect-task-metadata-devices-network-ports) |

---

### BPF Token Failures
**CLI**: `tw print <job_handle>` -- verify `bpf_token: true` and `user_isolation: true`
Even with BPF token enabled, you MUST also grant `CAP_BPF` (and optionally `CAP_PERFMON`, `CAP_NET_ADMIN`) capabilities in the job spec. If running on twshared hosts with kernel 5.19, BPF tokens may not work -- there is no way to enforce minimum kernel version without host pinning.
**Example posts**: [1](https://fb.workplace.com/groups/1473492212957333/permalink/2396983910675138), [2](https://fb.workplace.com/groups/1473492212957333/permalink/3511159582523909)

### Container Creation Failures
**Scuba**: `tupperware_task_events`
- Columns: `job`, `task`, `event_name`, `event_detail`
- Filter: `event_name = CONTAINER_CREATION_FAILED`
Container creation failures can be host-specific or architecture-specific. Check the exact error message in `tupperware_task_events`. Report host issues via `tw bad-host`.
**Example posts**: [1](https://fb.workplace.com/groups/1473492212957333/permalink/1867518237492785), [2](https://fb.workplace.com/groups/1473492212957333/permalink/2504621026538252)

### TLS Certificate Fetch Failures
During container creation, the TW Agent makes a network request to ProdCA (the production certificate authority) to fetch TLS certificates for the service. Failure to fetch certificates is usually caused by a degraded network connection or other host-level issue, not by Tupperware itself.

**Host debugging steps:**
1. Check for fbar alarms (e.g. SSH/network alarms) on the host:
   ```
   fbar log <hostname> --start=-3h
   ```
2. Run machinechecker for hardware issues:
   ```
   machinechecker <hostname>
   ```
3. For traffic/edge/FNA hosts only, run fixmyedge:
   ```
   fixmyedge check-host <hostname>
   ```
4. If the host is a lemon, flag it as bad to preempt tasks off:
   ```
   tw bad-host <hostname> --reason <reason>
   ```
   > [!NOTE]
   > All tasks on the bad host will be moved off the machine, so only use `tw bad-host` when you are confident the host is a lemon.

**Escalation:** If the host appears healthy (or you are unsure), reach out to the [tupperware@](https://fb.workplace.com/groups/tw.cinc) Workplace group with an investigation post providing context from the debug steps above.

### Missing Shared Libraries
**CLI**: `tw ssh <task_handle>` -- enter the container and check for missing `.so` files

The most common case is `libjvm.so: cannot open shared object file: No such file or directory`. Java is **not** installed in Tupperware containers by default. Devservers have JDKs pre-installed, which masks this issue during development -- a binary that works on your devserver may fail in a TW container.

**Resolution:**
1. Confirm whether the Java dependency was intended.
2. Bisect changes since the last known working version to find where the dependency was introduced. The change may come from a transitive dependency, not your own code.
3. For `libjvm.so` specifically, see the [Workplace post on investigating missing libjvm.so](https://fb.workplace.com/groups/java.eng/permalink/4568361689879077/?comment_id=4577173745664538) for installation guidance.

### IP-per-Task and Network Namespace
**CLI**: `tw print <job_handle>` -- check for NetNS/IPPT configuration
If services break after IPPT migration: (a) binding to both `::` and `::1` will conflict with NetNS -- set `useIP6Loopback=false`; (b) hostname format changes to `XXXX-XXXX-XXXX-XXXX.<hostname>.tw.fbinfra.net`; (c) localhost services (like Scribed on port 1456) may become unreachable from inside the container.
**Example posts**: [1](https://fb.workplace.com/groups/1473492212957333/permalink/3520975904875610), [2](https://fb.workplace.com/groups/1473492212957333/permalink/3517786275194573)

## Best Practices & How-To

### How to enable hostname namespacing with DNS
Add `override_hostname` to the job spec along with NetNS (IP-per-Task). The new shorter format complies with the Linux 64-byte hostname limit. DNS is pushed to Meta DNS and resolvable, with propagation taking up to 5 minutes. For faster resolution, use local DNS. Documentation is at the IP-per-Task Guide wiki.
**Example posts**: [1](https://fb.workplace.com/groups/1473492212957333/permalink/3462244447415423)

### How to get backend NIC (beth) IP addresses inside containers
With NETNS enabled, beth devices are not visible inside the container. Use `cat /etc/fbwhoami | grep "NICS_BETH"` to get beth info. NCCL accesses backend NICs through `/sys/class/infiniband/mlx5_*` which is available inside the container. Disabling NETNS to access beth is possible but considered an anti-pattern.
**Example posts**: [1](https://fb.workplace.com/groups/1473492212957333/permalink/3499195993720268)

### How to add Linux capabilities to containers
> [!NOTE]
> Capability allowlisting behavior may have changed. Verify current requirements with the TW Agent team documentation before relying on these steps.

Add capabilities using the `capabilities` field in the job spec. For `user_isolation=True`, capabilities are restricted -- to add `cap_sys_admin`, add the job name pattern to the allowlist in `whitelist.cconf`. Limit caps per `preRun` when possible rather than giving caps to all task commands. SYS_PTRACE is in the AlwaysDrop list for Boxman containers and requires special allowlisting.
**Example posts**: [1](https://fb.workplace.com/groups/1473492212957333/permalink/1231906042116670), [2](https://fb.workplace.com/groups/1473492212957333/permalink/2396983910675138)

### How to include text files as resources in a Container Manifest
Use the Container Manifest's resource inclusion mechanism for Python projects. The CM publish step supports `--build-remote-fbpkgs` for packaging resources alongside code. For fetch timeouts, specify `fetch_timeout` in the container manifest spec under the `container.thrift` definition.
**Example posts**: [1](https://fb.workplace.com/groups/1473492212957333/permalink/3569923439980856), [2](https://fb.workplace.com/groups/1473492212957333/permalink/3521170718189462)

### How to install debugging tools via DNF inside containers
Most common debugging tools are available through `tw ssh --debug-mode-tools` ([see the list](https://fburl.com/code/8fblse1i)). To install additional RPMs not in the default image, point `dnf` to a valid configuration. Note that `dnf` requires root, so SSH into your task with `--root`:

```bash
# Standard containers -- use the OS version ID to select the right config
. /etc/os-release && dnf install --config /etc/dnf/dnf.conf.$VERSION_ID.stable fb-your-rpm

# Debug-mode containers
dnf install --config /etc/dnf/dnf.conf.9.x fb-your-rpm
```

This is useful for installing one-off debugging tools (e.g. `strace`, `perf`, custom profilers) that are not part of the default container image.

### How to inspect task metadata (devices, network, ports)

`/etc/tw/api/metadata.json` is written by the TW agent before container startup and is immutable during the container's lifetime. It is the authoritative source for what devices, ports, and network config the container was assigned.

**Key fields:**
```bash
# GPU/accelerator devices assigned to this task
jq '.devices.acceleratorDevices' /etc/tw/api/metadata.json

# Device count
jq '.devices.acceleratorDevices | length' /etc/tw/api/metadata.json

# Assigned ports
jq '.ports' /etc/tw/api/metadata.json

# Allotment shape and UUID
jq '{shape: .allotmentShapeName, uuid: .allotmentUuid}' /etc/tw/api/metadata.json

# Network config (NICs, task IP)
jq '{taskIp: .taskIp, taskFQDN: .taskFQDN, nics: .nics}' /etc/tw/api/metadata.json
```

**Libraries for programmatic access:**
- C++: `fbcode/tupperware/agent/api/TaskMetadata.h`
- Python: `fbcode/libfb/py/tw/local_task.py`
- Rust: `fbcode/common/rust/tupperware/task_metadata/src/lib.rs`

The metadata only contains the task's own devices — not all devices on the host. For GPU workloads, `acceleratorDevices` lists only the GPUs assigned to this task. See [gpu-accelerator.md](./gpu-accelerator.md) for GPU-specific debugging.

## Common Questions

### Q: Why does `/sys/class/net/eth0/speed` show 10G instead of 100G inside a container?
**A:** With NetNS enabled, the speed shown is for the virtual eth device, not the physical NIC. Linux kernel hardcodes veth device speed to 10G. With `SHARED_NIC_FACTOR=4`, available bandwidth is 2.5G per container. Check `taskMetadata` for actual available bandwidth limit, or use `grep SHARED_NIC_FACTOR /etc/fbwhoami`.

### Q: Is AUTO port guaranteed unique at the host level with IP-per-Task?
**A:** AUTO port allocation is designed to provide unique ports at the physical host level when IP-per-Task is enabled, even for stacked tasks on the same host.

### Q: How to get host-level network stats from inside a NetNS container?
**A:** `/proc/net/dev` shows namespace-level stats, not host-level. Options: read ODS metrics like `dyno.network.eth0.rx_bytes`, mount host procfs into the container, or create a service outside the container that reads host Tx/Rx.

### Q: Can sysctl values be tuned inside TW containers?
**A:** Sysctls like `net.core.somaxconn`, `net.ipv4.tcp_max_syn_backlog`, and `net.ipv4.ip_local_port_range` can be set via the job spec. For host-level sysctls, a host profile change may be required. With NetNS, network namespace sysctls are independently configurable.

### Q: Does the $HOSTNAME environment variable change with IP-per-Task?
**A:** Yes. The hostname format changes from `twshared*.facebook.com` to `XXXX-XXXX-XXXX-XXXX.<physical-hostname>.tw.fbinfra.net`. Use `socket.gethostname()` in code as a reliable alternative.

### Q: How to disable cgroup memory protection?
**A:** Use `MEM_PROT_DISABLED` in the job spec. In Spec 2.0, `memoryProtectionConfig` support was added later. Cgroup memory protection can cause thrashing after restarts when shmem from the previous container retains memory protection.

### Q: Is the FB_SERVICE_ID env var always set inside containers?
**A:** It is set most of the time for "normal" containers but may not be set for internal tests. Anyone exec-ing into the container without passing env vars will not have it. Log when the env var is missing for defensive coding.

## Reference Tables

### Scuba Tables
| Table | When to Use | Key Columns |
|-------|------------|-------------|
| `tupperware_task_events` | Track container creation/destruction events | `job`, `task`, `event_name` |
| `tupperware_spec_linter` | Check spec lint results for capability issues | `job_handle`, `lint_result` |
| `logconsolidator_child_usage` | Debug log consolidation with new hostname format | `hostname`, `error` |

### CLI Commands
| Command | When to Use |
|---------|------------|
| `tw ssh <task_handle>` | Enter container to inspect environment |
| `tw print <job_handle>` | Check job spec for capabilities, NetNS, IPPT config |
| `tw resolve <job_handle>` | Get task IPs, hostnames, and port assignments |
| `tw bad-host <hostname> --reason <reason>` | Flag a lemon host to preempt all tasks off it |
| `fbar log <hostname> --start=-3h` | Check for fbar alarms (network, SSH) on a host |
| `machinechecker <hostname>` | Check for hardware issues on a host |
| `fixmyedge check-host <hostname>` | Check network/host issues on traffic/edge/FNA hosts |
| `cat /etc/fbwhoami` | Check host-level info (NICs, NIC factor) from inside container |
| `dig +short AAAA <hostname>.tw.fbinfra.net` | Verify DNS resolution for namespaced hostname |
| `twac net -s <hostname>` | Escalation: show per-container network devices when scheduler tools don't explain network issues |
| `twac show-cpu-topology -s <hostname>` | Escalation: visualize CPU pinning when performance suggests incorrect NUMA placement |
| `twac list-allotments -s <hostname>` | Escalation: verify actual resource allotments when allocation seems wrong |
| `twac export-task-spec -t <handle> -s <host>` | Escalation: check agent's spec when runtime behavior contradicts `tw job print` |
