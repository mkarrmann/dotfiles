# Logging & Debugging

> 115 posts in TW Group FAQ | Primary Scuba: `tupperware_task_events` | Primary CLI: `tw ssh`

## Debugging Playbook

Step-by-step workflow for diagnosing log access and debugging issues in Tupperware.

### Step 1: Determine if the task is still running
**CLI**: `tw resolve <job_handle>`
Look for the task state (Running, Stopped, Creating, Failed). If the task is stopped or gone, logs on the host are lost once the container leaves. Proceed to Step 2 if running, Step 5 if stopped.

### Step 2: Access task logs directly
**CLI**: `tw ssh <task_handle>`
Read stdout/stderr logs inside the container. Task logs are the most useful source for debugging specific tasks. Use Logarithm (tab to the right of "Task logs" in TW UI) as an alternative if `tw ssh` is unavailable.
If `tw ssh` fails with "Permission denied" or hangs, go to Step 3.

### Step 3: Troubleshoot SSH access issues
**CLI**: `SSH_AUTH_SOCK= tw ssh <task_handle> --verbose`
Common SSH issues include: (a) conflicting SSH_AUTH_SOCK from ET forwarding -- fix by unsetting it; (b) Rust tw ssh CLI bugs -- use `tw.real ssh` as workaround; (c) permission issues -- request access via TW job security page (`bunnylol tw <handle>`, Advanced->Security); (d) full `/tmp/` directory on devserver can cause SSH to hang ([ref](https://fb.workplace.com/groups/tw.cinc/permalink/2476431732663371/)) -- free space in `/tmp/` or run `fixmyserver`.

**Alternative SSH path via `sush` + `twac`**: If `tw ssh` is unavailable, you can SSH to the host directly, then enter the container namespace:
1. `sush $host` -- gives non-privileged host access. For `twshared` hosts, you can only `sush` to hosts where you have a privileged job running or was very recently running. See `fb_twpool/recipes/facebook_user.rb` for available sudo commands.
2. `twac list` -- shows tasks on the host (e.g., `tsp_prn/foo/bar/4`)
3. `twac enter $task` -- nsenter into the container

Note: entering the container this way may not have the environment set up as expected.

**RPM proxy resolution error**: If you see `ProxyError(MaxRetryError("HTTPSConnectionPool(host='yum', port=443)...ConnectionResetError(104, 'Connection reset by peer')))`, this is caused by a bad proxy config on your devserver. Fix: `unset HTTP_PROXY HTTPS_PROXY http_proxy https_proxy`, then run `fixmyserver`.

**Example posts**: [1](https://fb.workplace.com/groups/1473492212957333/permalink/3532503383722862), [2](https://fb.workplace.com/groups/1473492212957333/permalink/3505017953138072)

### Step 4: Enable debug mode for crash investigation
**CLI**: `tw job debug-mode <job_handle> --seconds 3600`
Debug mode keeps the container alive after the main process crashes, allowing manual investigation via `tw ssh`. Note: debug mode can mask crashes -- the TASK_EXIT_REPORT may not be written, and Conveyor updates may register as clean exits. Also note that debug mode does **not** disable SMC or otherwise prevent the task from receiving production traffic. For SMC-bridged services, use `tw job disable-smc-bridge $TASK_HANDLE` to stop traffic during debugging.
**CLI**: `tw ssh <task_handle> --mode debug-mode` (access gdb at `/opt/gdb-dev/bin/gdb`)

**Debug-mode tool freeze policy**: No new tools are added to the default `--debug-mode` list. All new debug tools must be specified via `--debug-mode-tools`. Tools accumulate across sessions: exiting and re-entering with new `--debug-mode-tools` keeps previously mounted tools alongside the new ones.

**Requesting new debug tools**: Post in [Tupperware@Meta](https://fb.workplace.com/groups/1473492212957333) and tag the TW Agent [oncall](https://www.internalfb.com/omh/view/tupperware_agent/oncall_profile). Provide: (1) what tool you need, (2) your use case, (3) where you want the tool in your container (dest path).

**Example posts**: [1](https://fb.workplace.com/groups/1473492212957333/permalink/3504208606552340), [2](https://fb.workplace.com/groups/1473492212957333/permalink/3292732034366666)

### Step 5: Retrieve logs for stopped/previous tasks
**Scuba**: `tupperware_task_events`
- Columns: `job`, `task`, `event_name`, `time`
- Filter: `job = <handle>`, time range around the issue
Off-host log upload via Logarithm is available for twshared hosts but NOT for twstorage hosts. Once the container leaves the host, on-host logs are lost.

**Log retention**: Logs persist for approximately 3 days after the container exits, barring a host reimage (which causes log loss). Use `wth` in the TW CLI to check if a host was recently reprovisioned, which would explain missing logs.

**Log types taxonomy**:
- **Service Logs** -- Rotates and compresses stdout and stderr for each task. Standard general-purpose logging.
- **Off-Host Logs** -- Task logs stored in external storage; retention is not bound by the lifespan of the task. Available for twshared tenants.
- **WDS/WDB Logs** -- Tupperware provides access to wds (wdb) logs for additional diagnostics.

**CLI**: `tw log <task_handle>` (if log upload was enabled)
**Example posts**: [1](https://fb.workplace.com/groups/1473492212957333/permalink/3518051308501403), [2](https://fb.workplace.com/groups/1473492212957333/permalink/3494021104237757)

### Step 6: Correlate log gaps with task events
**Scuba**: `tupperware_task_events`
- Columns: `event_name`, `task`, `time`, `event_detail`
- Filter: `job = <handle>`
Missing logs often correlate with health check failures during startup, OOM kills, or task moves. Cross-reference with `tupperware_health_check_results` if health check timeouts are suspected.

**`kill_timeout` for missing logs**: If logs are frequently missing or truncated during crashes, increase the job's [`kill_timeout`](https://www.internalfb.com/intern/wiki/Infra_Cloud/Service_Hosting/Tupperware/Tupperware_Reference/LanguageReference/job/#kill-timeout-optional-in) to give the host more time to flush logs under I/O pressure. Caveat: a long `kill_timeout` slows down job updates. Also consider writing less data to disk as an alternative resolution for log truncation.

### Step 7: Use `tw diag` for timeline-based diagnosis
**CLI**: `tw diag --start-time <time> [--end-time <time>] <entity>`
`tw diag` presents a timeline of events relevant to a job, task, or host. It uses regexes to match known problems to messages in the timeline, helping find potential root causes for events like host reboots or chroot setup failures. You can filter by `--host` to narrow results to a specific host. **Tip**: provide a representative task handle instead of a job handle to reduce the amount of data output.

### Step 8: Use Hypershell as a last resort
**CLI**: `hsh exec -T ".*pattern.*" 'command'`
[Hypershell](https://www.internalfb.com/intern/wiki/Hypershell/) runs commands inside containers matching a job regex. This should only be used as a last resort -- most information is available from Scuba or other sources without the risk Hypershell poses.

## Logs visible via `tw ssh` but missing from `tw log` / TW UI / Logarithm

**Telltale symptom:** You `tw ssh` into the container and `tail /logs/stderr` (or `/logs/stdout`) shows your lines, but `tw log` / `tw log --tail` / `tw tail -f` show nothing or a stale tail, the **Task logs** tab in the TW UI is empty or frozen, and Logarithm has no recent lines -- even though the process is clearly running.

**Cause: the process is stamping log timestamps in UTC instead of the host's local system timezone.** The on-host raw files (`/logs/stderr`, `/logs/stdout`) are an append-only byte stream and always contain everything, which is why `tw ssh` + `tail` looks fine. But `tw log`, the TW UI, and Logarithm build a *time-ordered* view by parsing each line's leading timestamp **as the host's local zone** (glog stamps carry no zone) and binary-searching over the result. A UTC stamp on a US-Pacific host reads ~7-8h ahead, so its lines land outside the tool's default window (the last few hours) and break the binary search -- they get skipped, dropped, or rendered wildly out of order. DST and non-Pacific regions change the size of the gap, not the failure.

**Rule: logs on Meta servers must be stamped in the host's local system timezone, never UTC.** This is the convention `tw log` / Logarithm were built to parse (C++ glog defaults to local time). A UTC logger is the single most common reason logs are present on the host yet missing from the tooling.

**Confirm it:** `tw ssh <task_handle>`, then compare the timestamps in `tail /logs/stderr` against the host's `date`. If the log stamps are hours *ahead* of `date`, this is the bug.

**Fix -- make the logger stamp local time:**
- **C++ glog**: local time is the default. Do NOT set `--log_utc_time` / `FLAGS_log_utc_time=true`.
- **Rust `tracing` + `tracing-glog`**: the `Glog` timer defaults to **UTC**. Override it -- `Glog::default().with_timer(tracing_glog::LocalTime::default())` (chrono-backed local time, not the `time` crate's `now_local()`). For a plain `tracing_subscriber` fmt layer, use a local timer such as `tracing_subscriber::fmt::time::ChronoLocal`.
- **Python `logging`**: the stdlib `Formatter` already uses local time. Do NOT set `logging.Formatter.converter = time.gmtime`.
- **Any logger**: render timestamps in the host's local zone. A line with no parseable leading timestamp can also be dropped or mis-ordered by the time-ordered views.

After fixing, redeploy (restarting the old binary won't rewrite already-stamped lines) and re-check `tw log --tail`.

## Best Practices & How-To

### How to access logs for short-lived jobs with deleted TIER_ACL
The ACL checked for log access is stored at ingestion time in a `remDataAuth` structure, not from the current spec. If the TIER_ACL is recreated, log access should work. Consider delaying ACL cleanup or using a dual-identity solution where a permanent open TIER_ACL coexists with per-tenant ACLs.
**Example posts**: [1](https://fb.workplace.com/groups/1473492212957333/permalink/3494021104237757)

### How to run and monitor daemon processes in TW containers
Use `systemd-run --user --unit my-daemon --remain-after-exit` and check status with `systemctl --user status my-daemon`. Source `/etc/twenv.sh` for environment variables since systemd-run does not inherit the calling process environment. Check `ExecMainCode` and `ExecMainStatus` for process exit state. TW redirects systemd messages to the console log, not stderr.
**Example posts**: [1](https://fb.workplace.com/groups/1473492212957333/permalink/3504208606552340)

### How to customize crash Logview categorization
The Tupperware Task Crashes Logview uses a dedicated categorizer (`TUPPERWARE_TASK_CRASHES`). Custom categorizers can be created following the "Creating MIDs" wiki. The categorizer code lives in `tupperware_task_crashes_categorizer.py`. This Logview is not actively maintained by the TW team.
**Example posts**: [1](https://fb.workplace.com/groups/1473492212957333/permalink/3504311666542034)

### How to copy files to/from TW tasks
Use `tw scp` instead of `scutil scp`. If `tw scp` hangs, the common cause is a conflicting `SSH_AUTH_SOCK` from ET forwarding. Fix: `eval "$(ssh-agent -s)"` to reset the SSH agent. Use `tw scp --verbose` to see the underlying command. Compression can be added: `tw scp --scp-options '-C' <handle>:<path> <dest>`.
**Example posts**: [1](https://fb.workplace.com/groups/1473492212957333/permalink/3526344594338741), [2](https://fb.workplace.com/groups/1473492212957333/permalink/3512266512413216)

## Common Questions

### Q: Why are log lines printed twice with different header formats?
**A:** This is typically a Python logging configuration problem, not a TW issue. Duplicate logging is caused by extra logging handlers added in application code. The TW environment has different logging initialization than local environments, making this visible only in production.

### Q: Can NVIDIA Nsight Compute (ncu) profiling be enabled inside containers?
**A:** It requires changes in the TW agent to bind mount the tool into the container, similar to what was done for cuda-gdb. Currently, `tw ssh --debug-mode` only supports cuda-gdb at `/opt/cuda-toolkit/`. File a request with the hm_hardware_enablement team.

### Q: Can TW support SSH with limited privileges (read-only log access)?
**A:** This is not currently supported. SSH access grants full container access including machine certificates. Restricted access for specific use cases (e.g., WhatsApp security requirements) is being explored but has no timeline.

### Q: What is the /logs cleanup policy inside containers?
**A:** TW cleans up old log files to reclaim disk space. The policy may delete entire nested directories under /logs rather than just individual files. For critical logs (e.g., MySQL binlogs), use a separate persist-dir rather than the default /logs directory.

### Q: Is there a Python API to query task crashes and get logs?
**A:** Use the `getTaskUpdateHistory` API on endpoint `tupperware.api.prod` and check `exitTrigger` to get crashes. For logs, use the Logarithm team's APIs.

### Q: How do I SSH between Tupperware tasks (task-to-task)?
**A:** You need to set up your job to handle this. Use the Python SSH library:
```python
import tupperware.lib.py.cli.ssh as ssh
ssh.setup_ssh(...)
```

## Reference Tables

### Scuba Tables
| Table | When to Use | Key Columns |
|-------|------------|-------------|
| `tupperware_task_events` | Correlate log gaps with task lifecycle events | `job`, `task`, `event_name`, `time` |
| `tupperware_task_control_operations` | Track task control operations and their outcomes | `job`, `operationType`, `operationStatus` |
| `coredumper` | Investigate crash dumps and stack traces | `job_handle`, `stack_trace`, `signal` |
| `tupperware_crashes_logview` | View categorized crash information | `job_handle`, `crash_category` |

### CLI Commands
| Command | When to Use |
|---------|------------|
| `tw ssh <task_handle>` | SSH into a running task to read logs |
| `tw ssh <task_handle> --mode debug-mode` | Access debug tools (gdb) inside container |
| `tw.real ssh <task_handle>` | Workaround for Rust tw ssh CLI issues |
| `tw log <task_handle>` | Access uploaded logs for stopped tasks |
| `tw scp <handle>:<path> <dest>` | Copy files (logs, dumps) from a task |
| `tw job debug-mode <job> --seconds <N>` | Keep container alive after crash for investigation |
| `tw job disable-smc-bridge $TASK_HANDLE` | Stop production traffic for SMC-bridged services during debugging |
| `tw diag --start-time <time> <entity>` | Timeline-based diagnosis for jobs, tasks, or hosts |
| `tw task-control list-events <task_handle>` | List task lifecycle events |
| `tw print <job_handle>` | Check job spec including log upload policy |
| `wth <hostname>` | Check if a host was recently reprovisioned (explains missing logs) |
| `sush $host` | SSH to host as non-privileged user (alternative to `tw ssh`) |
| `twac list` | List tasks on a host (used after `sush`) |
| `twac enter <task_handle>` | Escalation: nsenter into container when `tw ssh` fails (local-only, must be on host) |
| `twac mlv -t <handle> -p /logs -s <host>` | Escalation: map container path to host filesystem when `tw log` and `tw ssh` both fail |
| `twac memlog -s <hostname>` | Escalation: dump agent's in-memory log when agent-level issues are suspected |
| `hsh exec -T ".*pattern.*" 'command'` | Last resort: run commands inside containers matching a job regex via Hypershell |
