# Job Start & Creation

> 66 posts in TW Group FAQ | Primary Scuba: `tupperware_api_service_cpp` | Primary CLI: `tw start`

## Debugging Playbook

### Step 1: Validate the spec before starting
**CLI**: `tw validate <spec_file>` to catch spec errors early.
**CLI**: `tw validate --full-lint-results <spec_file>` for comprehensive lint output including UTMOST checks.
-> If validation fails, see [spec-config.md](./spec-config.md). If validation passes but start fails, continue below.

Pick the matching section:

| Symptom | Go to |
|---------|-------|
| `tw start` returns an error | [Start Errors](#start-errors) |
| Job started but tasks not running | [Tasks Not Running](#tasks-not-running) |
| Container creation failures | [Container Creation Failures](#container-creation-failures) |
| FixedStartupDurationExpired | [Startup Duration Expired](#startup-duration-expired) |

---

### Start Errors
**CLI**: `tw start <spec_file> <job_handle>` to start a specific job from a multi-job spec.
-> If you see "Error: Invalid value for 'SPEC'": verify the file path exists and is correct. The CLI expects one of: job handle, named jobs, spec file, or task handle. This error also appears on OnDemand machines -- use a devvm instead.
-> If you see `PERMISSION DENIED` on `CAPACITY_RESERVATION`: the short-term reservation auto-grant may have expired. Request access manually.
-> If you see "Proxy shard selector can't find a shard": the reservation may not have a scheduler provisioned. Contact scheduler extensions oncall.
**Example posts**: [1](https://fb.workplace.com/groups/1473492212957333/permalink/2462274000745811), [2](https://fb.workplace.com/groups/1473492212957333/permalink/3632047113768488)

### Tasks Not Running
**Scuba**: `tupperware_task_events`
- Columns: `job_handle`, `task_id`, `event_type`, `error_message`, `host`
- Filter: `job_handle = <your_handle>`
-> If tasks are stuck in CREATING: check for "Host profile swap in progress" or "Container creation timed out". Preempt with `tw allocation preempt <handle>/<task_id>`.
-> If tasks are stuck in ALLOCATED: check for IPPT assignment issues or host hardware problems.
**CLI**: `tw task-control show-status <job_handle>` to check for pending operations.
**Example posts**: [1](https://fb.workplace.com/groups/1473492212957333/permalink/3513323275640873), [2](https://fb.workplace.com/groups/1473492212957333/permalink/3498310530475481)

### Container Creation Failures
**Scuba**: `tupperware_api_service_cpp`
- Columns: `job_handle`, `error`, `method`
- Filter: `job_handle = <your_handle>`
-> `PACKAGE_FETCHING_FAILURE`: package fetch timed out (common on T6 hosts with spinning disks and large packages). Increase `fetch_timeout_in_sec` in the package spec.
-> `ERR_SPEC_VALIDATION`: the spec passes `tw validate` but fails agent-side validation. Check for incompatible container manifest versions.
-> `CAT verification failed` / `Failed to cache credential data`: certificate issues on the host. Use `tw bad-host` to cycle the host.
-> `Container creation timed out`: agent may be load-shedding due to max request limits. Typically auto-resolves within 1-2 hours.
**Example posts**: [1](https://fb.workplace.com/groups/1473492212957333/permalink/3502585990047935), [2](https://fb.workplace.com/groups/1473492212957333/permalink/3644374669202399)

### Startup Duration Expired
> This error occurs when a canary or update exceeds the configured fixed startup duration threshold. It is a Conveyor/canary-specific timeout, not a general job start issue.

**CLI**: `tw task-control show-status <job_handle>` to check update progress.
-> Either increase `fixed_startup_duration` in the canary/Conveyor config, or investigate why tasks are slow to become healthy (e.g., slow health checks, LegacyExclusion machine drains coinciding with canary operations).
**Example posts**: [1](https://fb.workplace.com/groups/1473492212957333/permalink/3545481365758397), [2](https://fb.workplace.com/groups/1473492212957333/permalink/3671657116474154)

## Best Practices & How-To

### How to start a specific job from a multi-job spec
Use: `tw start <spec_file> <job_handle>`. For example: `tw start fbcode/tupperware/config/myteam/service.tw tsp_pci/myteam/service_name`. If `--interactive` fails because another job in the file has lint errors, specifying the job handle directly bypasses those checks.
**Example posts**: [1](https://fb.workplace.com/groups/1473492212957333/permalink/3519902284982972)

### How to create a new job in a new region
Use `tw start` to explicitly create new jobs in new regions. `tw update --all-jobs` and `tw validate` expect the job to already exist. For virtual jobs, use `tw job2 set-size --task-count=<region>:<count>` instead.
**Example posts**: [1](https://fb.workplace.com/groups/1473492212957333/permalink/3519902284982972)

### How to test locally before starting production jobs
Use `tw sandbox2` (not the old `tw sandbox`) for testing. Sandbox2 uses the full scheduling stack and is closer to prod behavior. It bridges to SMC and works with `tw resolve`. The old `tw sandbox` has subtle differences and is no longer invested in.
**Example posts**: [1](https://fb.workplace.com/groups/1473492212957333/permalink/3665726500400549)

### How to handle expired ephemeral packages
Preserve the package with `fbpkg preserve <name>:<hash>` and use `ephemeral_package_id` instead of version ID. For ongoing deployments, set up a TW push node in Conveyor with PUSHED_VERSION. Use `tw update` instead of `tw start` for stopped jobs that need a package refresh.
**Example posts**: [1](https://fb.workplace.com/groups/1473492212957333/permalink/3641036022869597)

## Common Questions

### Q: Does landing a TW config automatically start the job?
**A:** No. Landing the config alone does not start the job. You must explicitly run `tw start` after landing. The config landing only makes the spec available; it does not trigger job creation.

### Q: Can TW_RUNNING_TASK_COUNT be made mandatory in tw start?
**A:** Making it mandatory would break existing workflows. Set explicit task counts in the spec to avoid accidental low-count starts. Remove default values so users are prompted.

### Q: Why does tw start succeed but the job is not created?
**A:** If `tw start` succeeds repeatedly but the job does not appear, it may be a transient scheduler issue. Jobs can take up to 30 minutes to appear. Check `tupperware_jcp_tickers` for processing state.

### Q: Is tw sandbox2 enforced on resource limits?
**A:** No. Sandbox2 uses NO_ENFORCEMENT, meaning containers are not capped on memory/CPU at runtime. However, the allocation system still checks resource availability on the devvm.

## Reference Tables

### Scuba Tables
| Table | When to Use | Key Columns |
|-------|------------|-------------|
| `tupperware_api_service_cpp` | Debug API errors during start | `job_handle`, `error`, `method` |
| `tupperware_task_events` | Track task creation events | `job`, `task`, `event_name`, `host` |
| `tupperware_jcp_tickers` | Track JCP processing state | `tw_job_handle`, `ticker_type` |
| `tw_task_packages` | Verify package deployment | `job_handle`, `package_name`, `version` |

### CLI Commands
| Command | When to Use |
|---------|------------|
| `tw start <spec_file> <job_handle>` | Start a specific job from spec |
| `tw validate <spec_file>` | Validate spec before starting |
| `tw validate --full-lint-results <spec_file>` | Full lint with UTMOST checks |
| `tw sandbox2 create --tw-file <spec>` | Test in sandbox before prod |
| `tw print <job_handle>` | Inspect running job spec after start |
| `tw task-control show-status <handle>` | Check task states after start |
| `TW_PUSHED_VERSION=<pkg>:<ver> tw start <spec>` | Start with explicit package version |
