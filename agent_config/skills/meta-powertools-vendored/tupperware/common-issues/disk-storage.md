# Disk & Storage

> 21 posts in TW Group FAQ | Primary Scuba: `tupperware_task_events` | Primary CLI: `tw task-control`

## Debugging Playbook

**CLI**: `tw ssh <task_handle>` and check the error message, then pick the matching section:

| Error | Go to |
|-------|-------|
| "No space left on device" | [No Space Left](#no-space-left) |
| "Failed to reformat storage device" | [Storage Reformat Failures](#storage-reformat-failures) |
| BTRFS corruption / read-only filesystem | [BTRFS Corruption](#btrfs-corruption) |

---

### No Space Left
**CLI**: `tw ssh <task_handle>` then run `df -h` and `du -sh /*` to find what is consuming space
- Check for open-but-deleted files: `find /proc/*/fd -ls 2>/dev/null | grep deleted`
- The "No space left" error can be misleading -- it may be caused by exceeding semaphore or shared memory limits, not actual disk space
- For CVM containers: increase `read_write_overlay_size` (default 2G), which uses CVM memory
- For root filesystem full: check log spam filling up the disk
**Scuba**: `tupperware_task_events`
- Columns: `job_handle`, `event_type`, `host`, `reason`
- Filter: `job_handle = <your_job_handle>`, look for disk-related events
- ODS metric: `system.disk.used_percentage` and `tw.disk.chroot` for task-level usage
**Example posts**: [1](https://fb.workplace.com/groups/1473492212957333/permalink/3554938558146011), [2](https://fb.workplace.com/groups/1473492212957333/permalink/3803196363320228)

### Storage Reformat Failures
- Often caused by agent-helper still running on the host when the next container tries to format the drive
- Self-recovers after ~2 hours in many cases
**CLI**: `tw allocation preempt <task_handle>` to move to a different host
- If attached storage is not needed: set `opt_out_attached_storage` in the spec
- For persistent volume errors ("taskSpec expects persistent volumes but allotment doesn't have any unmanaged storage"): check that the reservation's host profile includes storage support
**Example posts**: [1](https://fb.workplace.com/groups/1473492212957333/permalink/3540080992965101), [2](https://fb.workplace.com/groups/1473492212957333/permalink/3796177170688814)

### BTRFS Corruption
**CLI**: SSH to host (if possible) and run `dmesg` to check for BTRFS errors
- Remediation: (1) `rm -f /var/facebook/tupperware/agent/env_root_path`, (2) reboot, (3) `umount /data/device00/mount_point`, (4) `mkfs.btrfs /dev/md1p1 -f`, (5) retry provisioning
- If SSH is not possible due to crashloop: `tw bad-host <hostname>` to move tasks to another host
- For btrfs stalls during IO pressure: consider using CAF images to reduce IO, or make packages smaller
**Example posts**: [1](https://fb.workplace.com/groups/1473492212957333/permalink/3498310530475481), [2](https://fb.workplace.com/groups/1473492212957333/permalink/3796320560674475)

## Best Practices & How-To

### How to configure local flash storage correctly
Understand the difference between managed and unmanaged flash. With managed btrfs (BTRFS_MANAGED_*), the data volume becomes the container root -- do not try to mount it again. With unmanaged flash (BTRFS_FULL_V2), use `local_flash.mount_local_flash(job, "/mnt/path")` in the spec. Ensure the reservation's host profile matches the storage type in the job spec.
**Example posts**: [1](https://fb.workplace.com/groups/1473492212957333/permalink/3758435694462962), [2](https://fb.workplace.com/groups/1473492212957333/permalink/3726505364322662)

### How to monitor disk usage
Use ODS metric `tw.disk.chroot` for the main filesystem (everything not a persistent dir, package, or local flash). Use `tw.disk.persistent` for persistent directories. The `storage_flash_used_percentage` ODS key is host-level flash utilization reported under the task handle. For historical analysis, use Scuba table `tupperware_btrfs_disk_usage`.
**Example posts**: [1](https://fb.workplace.com/groups/1473492212957333/permalink/3712416759064856)

### How to recover from a crashlooping task due to disk issues
Use `tw job debug-mode` to pause the task on next crash, then SSH in to manually clean up files or investigate. No dedicated CLI exists for wiping persistent flash directly.
**Example posts**: [1](https://fb.workplace.com/groups/1473492212957333/permalink/3600414876931712), [2](https://fb.workplace.com/groups/1473492212957333/permalink/3918229391820406)

## Common Questions

### Q: Why does my T2 host only show half the expected disk space?
**A:** T2 hosts have a 2TB root disk (nvme1) and 2x2TB data drives (nvme0, nvme2). The job only sees the root disk by default. For unmanaged flash, add `mount_local_flash` in the job spec. For managed flash, the container is moved to the data drive.

### Q: Why does flash storage change after a task restart?
**A:** Tasks can be moved between different LSST types (e.g., T3_SPR with 12TB vs T3_CPL with 6TB). TW does not prioritize between LSSTs. To control flash size, set a preference on flash capacity in the allocation policy.

### Q: Where are previous task logs after host reallocation?
**A:** Once a host is reallocated, on-host logs are deleted. Check TW logs via the TW UI, persistent directories (if configured), and off-host destinations (Manifold/Logarithm). Configure log upload policies in the job spec for future access.

## Reference Tables

### Scuba Tables
| Table | When to Use | Key Columns |
|-------|------------|-------------|
| `tupperware_task_events` | Track disk-related task events | `job`, `event_name`, `host`, `event_detail` |
| `tupperware_btrfs_disk_usage` | Monitor BTRFS disk usage over time | `hostname`, `total_disk_used_pct` |
| `fbpkg_proxy_thrift_calls` | Debug package fetch/install failures | `hostname`, `Package Name`, `error` |

### CLI Commands
| Command | When to Use |
|---------|------------|
| `tw ssh <task_handle>` | SSH into task to check disk usage |
| `tw allocation preempt <task_handle>` | Move task to different host |
| `tw bad-host <hostname>` | Mark host as bad for disk issues |
| `tw job debug-mode <job_handle>` | Pause crashlooping task for investigation |
| `du -sh <path>` | Check directory sizes inside container |
| `df -h` | Check filesystem usage inside container |
