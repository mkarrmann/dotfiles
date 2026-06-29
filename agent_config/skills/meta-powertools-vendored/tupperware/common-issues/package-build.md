# Package & Build

> 143 posts in TW Group FAQ | Primary Scuba: `tw_task_packages` | Primary CLI: `fbpkg`

## Debugging Playbook

**CLI**: `tw print <job_handle>` -- check the package version in the running spec. Pick the matching section:

| Error | Go to |
|-------|-------|
| `NO_SUCH_PACKAGE` / `NO_SUCH_VERSION` | [Package Not Found](#package-not-found) |
| Expired packages in Container Manifest | [Expired CM Packages](#expired-cm-packages) |
| `FBPKG_INTERNAL_ERROR` | [FBPKG Internal Error](#fbpkg-internal-error) |
| Exit code 127 (command not found) | [Command Not Found](#command-not-found) |
| Legocastle / Cogwheel CI failures | [CI Test Failures](#ci-test-failures) |
| `FETCHING_TIMEOUT` / slow fetch | [Package Fetch Timing](#package-fetch-timing) |
| Task stuck in FETCHING state | [Prefetching](#prefetching) |
| Package fetch blocking updates | [Package Fetch Blocking Updates](#package-fetch-blocking-updates) |
| Disk space failures during fetch | [Disk Space Failures](#disk-space-failures) |
| RPM install errors | [RPM Issues](#rpm-issues) |
| Stuck in "Creating Persistent Resource" | [Creating Persistent Resource](#creating-persistent-resource) |

---

### Package Not Found
**CLI**: `fbpkg versions <package_name> --show-deleted`
Check if the version is expired (ephemeral packages have a TTL). If the version was ephemeral and has expired, push a new non-ephemeral version. For `TW_PUSHED_VERSION` issues, ensure the env var is set when running `tw update`.
**CLI**: `TW_PUSHED_VERSION=<pkg>:<version> tw job update <tw-file> <job-handle>`
**Example posts**: [1](https://fb.workplace.com/groups/1473492212957333/permalink/3513100852329782), [2](https://fb.workplace.com/groups/1473492212957333/permalink/3511218775851323)

### Expired CM Packages
**CLI**: `tw validate <tw-file>`
TW validates ALL packages in the base job spec, including constituent packages in the Container Manifest. Even if your update does not change the CM, expired ephemeral fbpkgs inside the CM will cause validation failures. Fix by pushing a newly built CM or running a full job update to override the invalid package.
**Example posts**: [1](https://fb.workplace.com/groups/1473492212957333/permalink/3525280834445117), [2](https://fb.workplace.com/groups/1473492212957333/permalink/3527327580907109)

### FBPKG Internal Error
**Scuba**: `fbpkg_proxy_thrift_calls`
- Columns: `package_name`, `version`, `error_type`, `timestamp`
- Filter: `package_name = <pkg>`
Common cause: package labeled as both classic and CAF (Content-Addressable Files), causing a conflict. The TW agent requests the classic version for a CAF package. Investigate the `getStorageBackend` call. If the isCaf flag is wrong in the jobspec, onboard to JCP.
**Example posts**: [1](https://fb.workplace.com/groups/1473492212957333/permalink/3525435551096312)

### Command Not Found
**CLI**: `tw ssh <task_handle>` -- verify the binary exists at the expected path
Exit code 127 means the command specified in the TW spec was not found. Common causes: (a) package name mismatch between spec and deployed fbpkg; (b) binary path does not exist in the package; (c) wrong package is being fetched. Check that the `command` field in your .tw spec matches the actual binary location in the fbpkg.
**Example posts**: [1](https://fb.workplace.com/groups/1473492212957333/permalink/1930071364230436)

### CI Test Failures
If the failure is in `tupperware-legocastle-validation-tests`, `tupperware-legocastle-smoke-test_child`, or `tupperware-legocastle-scheduler-integration-tests`: check if your diff actually touches TW config files. If not, the failure is likely a known flaky test. Run `tw validate` locally to verify correctness; if local validation passes, the CI signal can be safely bypassed.
**Example posts**: [1](https://fb.workplace.com/groups/1473492212957333/permalink/3513214722318395), [2](https://fb.workplace.com/groups/1473492212957333/permalink/3513238995649301), [3](https://fb.workplace.com/groups/1473492212957333/permalink/3621435364829663)

---

## Package Fetch Timing

> Understanding fetch duration is critical for setting correct timeouts and diagnosing FETCHING_TIMEOUT failures.

### What happens during a package fetch

To complete a package fetch, Tupperware must perform three steps:

1. **Download** the compressed binary blob onto a host (network + I/O constrained)
2. **Decompress** the binary blob (CPU constrained, with potential memory constraint depending on params)
3. **Dearchive** the files (I/O constrained)

This is a very I/O intensive operation and in most cases I/O-constrained. The total data written to disk is at least `uncompressed_size + compressed_size`.

### Calculating total package size

**CLI**: `fbpkg info <package_name>:<version>`

Sum the `Size` (compressed) and `Pre-compressed size` (uncompressed) fields:

```
fbpkg info <my_package>:LATEST
...
    Size:                   420.91 MiB
    Pre-compressed size:    2.30 GiB
...
```

This output means the package requires at least ~2.7 GB to be written to disk.

### Write throughput estimation

Most Type I machines have relatively low-grade flash. Direct I/O throughput should be expected around **30-40 MB/s** assuming relatively low utilization from other binaries. Use 75% of throughput as a safe estimate to account for other workloads. Formula: `total_size / (expected_throughput * 0.75)`.

#### Estimation table

| Total size | Type I time (low I/O util.) | Type I time (high I/O util.) |
|------------|----------------------------|------------------------------|
| 1 GB       | 40 s                       | 5 min +                      |
| 2 GB       | 80 s (1m 20s)              | 10 min +                     |
| 5 GB       | 200 s (3m 20s)             | 25 min +                     |
| 10 GB      | 400 s (6m 40s)             | a long long time             |
| 50 GB      | 2000 s (30m +)             | ...                          |
| 100 GB     | 5000 s (60m +)             | ...                          |

> [!NOTE]
> Times are much larger on Type VI machines which have a spinning disk.

### Why devserver fetches are faster

Devservers differ from production machines in two important ways:

1. **Higher-grade flash**: Most devvms have much higher-grade flash storage that allows for higher throughputs.
2. **No direct I/O**: On devboxes, `fbpkg.fetch` does not use direct I/O. The kernel uses memory buffers, making writes appear faster. On production machines, direct I/O bypasses kernel-level memory buffers and writes directly to disk. This strictly limits memory consumption so the bare-metal daemon does not impact container workloads (especially important for `www` workloads).

### Prefetching

During updates, new packages are fetched by the host **before** the old task is stopped. This is visible as the **FETCHING** state in the UI. Because the old task is still running, only a subset of the host I/O is available for fetching, so fetching takes longer than normal.

**CLI**: `tw diag $task_handle` -- inspect the state of prefetching. See [Diagnosing fetch timeouts](#diagnosing-fetch-timeouts) for example output and diagnostic steps.

### Diagnosing fetch timeouts

**CLI**: `tw diag $task_handle`

Fetch timeouts appear as:

```
[State transition]: FETCHING->RESERVING_MACHINE,
[Event context]: Transition Type: FETCHING_TIMEOUT.
Reason: Fetching all packages is taking too long
```

A message like "Package fetch has started, but not completed yet" also indicates an in-progress fetch that has not finished.

**Steps to diagnose**:

1. Check package size: `fbpkg info <package_name>:<package_uuid>` -- sum `Size` + `Pre-compressed size`.
2. Compare against the [throughput reference](https://fburl.com/rwos5u0f) to estimate expected fetch time.
3. If the timeout is set below the estimated time, increase it [in your tw spec](https://fburl.com/wiki/6fdvl99u).

### Package Fetch Blocking Updates

**Symptom**: Tasks are running but the update is not making progress.

This occurs when a package fetch is in progress but has not completed, blocking the update from proceeding to the next step. The task appears healthy but the update is stalled.

**Resolution**: Use `tw changes commit` to force the update forward, or preempt the task.

**Prevention**: Set a prefetch timeout to avoid indefinite blocking. See [prefetch timeout configuration](https://fburl.com/wiki/dlqzy457).

### FNA Environment Fetch Timeouts

Jobs running outside Facebook datacenters (e.g., FNA environments) have slower network connectivity, which can cause package fetch timeouts even for reasonably sized packages.

**Diagnosis**: [Run a network speedtest](https://www.internalfb.com/intern/wiki/Traffic/Testing_Network_Speed/#curl-example) to verify network throughput at the remote location.

### Disk Space Failures

If a task fails to download packages but the reason is not a timeout, it is likely a disk space issue preventing download or extraction.

**Diagnosis**:

1. Review the host [dashboard](https://fburl.com/unidash/g8dsb6bh) of the failing machine.
2. Compare free disk space against the total package size (compressed + uncompressed).

**Solution**: Reduce the size of the package and/or reduce disk space usage from other jobs on the machine. Consider using [squashfs](https://fburl.com/9ea7dmqb) compression.

### I/O Load Reduction

If fetch timeouts persist despite appropriate timeout values, [check the I/O load](https://www.internalfb.com/intern/wiki/Infra_Cloud/Service_Hosting/Tupperware/Tupperware_Monitoring/Monitoring_Machine_and_Container_Resources/#i-o-consumption) on the host.

High I/O resource consumption means the machine does not have enough capacity left to download packages. Three remediation steps:

1. Reduce I/O throughput from other workloads on the machine, especially if you control the jobs already running there.
2. Reduce the total size of packages that need to be fetched (see [squashfs](https://fburl.com/9ea7dmqb) in Disk Space Failures above).
3. Adjust the fetch timeout upward as described above.

---

## RPM Issues

### RPM from manual fbpkgs

If you are fetching a manually constructed fbpkg that contains RPMs you want installed, **both** conditions must be met:

1. RPMs must be in the **root folder** of the fbpkg.
2. `rpm=True` must be specified on the package in the spec file.

If either condition is not met, the RPMs will not be installed. The files will be exposed within the container as if it were a normal package.

### RPM base image conflicts

If there are errors installing RPMs specified in the spec, check whether the RPM is already provided in the base image used by the task. Attempting to install an RPM that already exists in the base image will result in an install error.

**Scuba**: [`tw_rpm_installation`](https://fburl.com/scuba/tw_rpm_installation/8391l21j) -- check RPM install failures for your job.

### Staging timeout from slow RPM install

The top reason for slowness during task staging is slow RPM installation. Break down staging time in [Scuba: `tupperware_rootfs_preparation_time`](https://fburl.com/scuba/tupperware_rootfs_preparation_time/kj3swiw5).

---

## Creating Persistent Resource

If your container cannot move past the "Creating Persistent Resource" stage, review the [managed flash / local flash configuration](https://www.internalfb.com/wiki/Infra_Cloud/Service_Hosting/Tupperware/Tupperware_Task_Environments/TwsharedLocalFlash/#managed-flash-relocatabl). Using local flash needs to be carefully configured to match the job spec configuration with the correct host profile.

---

## Best Practices & How-To

### How to install third-party RPMs in TW containers
For RPMs distributed as part of the OS (e.g., CentOS), use them directly. For third-party RPMs, follow the RPMs/Adding_RPM_Packages wiki. For SmartPlatform, use `unreleased_settings.rpm_packages` in config. For docker specifically, consider `podman-docker` from CentOS repos. Do NOT use `feature` in Tupperware -- it is for dev environments only. Add dependencies as fbpkgs, Container Manifests, or RPMs instead.
**Example posts**: [1](https://fb.workplace.com/groups/1473492212957333/permalink/3518080868498447), [2](https://fb.workplace.com/groups/1473492212957333/permalink/2164236277423700)

### How to check which package version is actually running
`tw job print` shows the **target** version (what the scheduler wants all tasks to run). To see what each task is **actually** running, query per-task package versions via Universal Search:
```bash
thriftdbg sendRequest search '{"request":{"select":{"selectedJsonPaths":["$.taskHandle","$.spec.requirements.packages"]},"from":4,"where":{"assocFilter":{"assocObjectType":1,"assocObjectIds":["<JOB_HANDLE>"]}},"jsonResponseFormat":{}}}' --tier tupperware.universal_search.experiment.test --request_timeout_ms 90000 | jq
```
During a rolling restart, the target and actual versions will differ until all tasks have been cycled.

### How to find which packages a TW job is running
Use `tw search jobs -v 'requirements.packages.name =~ .*<pattern>.*'` to find jobs by package pattern. Or `tw search jobs --show 'jobSpec.requirements.packages.name' <handle>` to show packages for a specific job. The `tw_task_packages` Scuba table shows packages specified in the job handle, while `fbpkg_invocations` tracks runtime-fetched packages.
**Example posts**: [1](https://fb.workplace.com/groups/1473492212957333/permalink/3549352888704578)

### How to access the resolved package version at runtime
The resolved fbpkg version is available in the container's metadata. Check `/packages/<pkg_name>/` for the actual deployed version. For programmatic access during deployment, use the Conveyor pipeline's version injection mechanism.
**Example posts**: [1](https://fb.workplace.com/groups/1473492212957333/permalink/3550609001912300)

### How to automate package updates for TW jobs
Use `TW_PUSHED_VERSION=<pkg>:<version> tw job update <tw-file> <handle>` for manual updates. For automated updates, set up a Conveyor pipeline with proper push nodes. Do NOT deploy ephemeral packages to production jobs -- use `tw canary` for testing ephemeral builds, and ensure the production Conveyor pushes non-ephemeral versions.
**Example posts**: [1](https://fb.workplace.com/groups/1473492212957333/permalink/3527437957562738)

## Common Questions

### Q: Can fbpkg packages be preloaded on hosts to avoid slow in-container fetching?
**A:** Prefetching is possible but only works if the machine is known ahead of time and assigned to the same job. A better approach is to use Container Manifests which pre-fetch at container creation time. Check `fbpkg_proxy_thrift_calls` Scuba for fetch latency data.

### Q: Why is the /packages/ directory read-only, and can it be modified?
**A:** The /packages/ directory in TW containers is read-only by design and should not be modified. For MAST interactive jobs, overlayfs allows apparent deletion but cannot remove the deepest folders. Do not package files you do not need.

### Q: How to include a shell script in a TW container?
**A:** Use an fbpkg managed by a Conveyor for releasing code. The Container Manifest publish step supports `--build-remote-fbpkgs` which makes the package preservable. For simpler use cases, consider higher-level platforms like Async or Chronos.

### Q: Legocastle tests keep failing on my diff. Is it safe to bypass?
**A:** See the [CI Test Failures](#ci-test-failures) section in the Debugging Playbook above.

## Reference Tables

### Scuba Tables
| Table | When to Use | Key Columns |
|-------|------------|-------------|
| `tw_task_packages` | Find packages specified in job specs — see [datasets/tw_task_packages.md](../datasets/tw_task_packages.md) for full schema and queries | `initiating_job_handle`, `package_name`, `fetch_status`, `fetch_error`, `fetch_time` |
| `fbpkg_invocations` | Track runtime fbpkg fetch calls | `packages`, `version`, `caller` |
| `fbpkg_proxy_thrift_calls` | Debug package fetch failures/latency | `package_name`, `error_type`, `latency` |
| `tupperware_task_events` | Correlate package issues with task lifecycle | `job`, `event_name`, `exit_code` |
| `tw_rpm_installation` | Debug RPM install failures | `job_handle`, `rpm_name`, `error` |
| `tupperware_rootfs_preparation_time` | Break down task staging time (RPM install slowness) | `job_handle`, `stage`, `duration` |

### CLI Commands
| Command | When to Use |
|---------|------------|
| `fbpkg versions <pkg> --show-deleted` | Check if package version exists or is expired |
| `tw validate <tw-file>` | Validate spec including package references |
| `tw print <job_handle>` | Check running package version in job spec |
| `TW_PUSHED_VERSION=<pkg>:<ver> tw update` | Override package version during update |
| `tw search jobs -v 'requirements.packages.name =~ .*<pattern>.*'` | Find jobs by package name |
| `fbpkg info <pkg>:<version>` | Get detailed package metadata (size + pre-compressed size for fetch estimation) |
| `tw diag <task_handle>` | Diagnose fetch timeouts, prefetch state, and state transitions |
| `tw changes commit` | Force an update forward when blocked by package fetch |
