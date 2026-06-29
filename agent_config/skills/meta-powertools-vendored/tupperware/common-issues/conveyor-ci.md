# Conveyor & CI

> 35 posts in TW Group FAQ | Primary Scuba: `conveyor_push_event_logs` | Primary CLI: `conveyor`

## Debugging Playbook

Check the Conveyor UI for the specific error message, then pick the matching section:

| Symptom | Go to |
|---------|-------|
| NUJ creation failures | [NUJ Creation Failures](#nuj-creation-failures) |
| Push blocked by spec/package issues | [Spec/Package Blocks](#specpackage-blocks) |
| Push blocked by infrastructure errors | [Infrastructure Errors](#infrastructure-errors) |
| Legocastle CI signal failures | [CI Test Failures](#ci-test-failures) |
| Canary failures within Conveyor | [Conveyor Canary Failures](#conveyor-canary-failures) |

---

### NUJ Creation Failures
**Scuba**: `conveyor_push_event_logs`
- Columns: `push_identifier`, `message`, `conveyor_id`, `time`
- Filter: `message =~ ".*NUJ.*"` or `message =~ ".*regexes didn't match.*"`
Common causes: (a) `arcanist_project` is empty when NUJ is created with custom path under /tmp -- use standard paths; (b) Conveyor picks up unexpected local jobs referencing .tw files -- delete the unexpected job; (c) regex does not match any existing jobs -- verify the job exists with `tw job list`.
**Example posts**: [1](https://fb.workplace.com/groups/1473492212957333/permalink/3499122357060965), [2](https://fb.workplace.com/groups/1473492212957333/permalink/3539921569647710)

### Spec/Package Blocks
**CLI**: `tw validate <tw-file>` -- run local validation first
Common spec-related blockages:
- "Operations incompatible with TW Spec2.0 are locked down": custom push type sending `fast=True` (locked for Spec 2.0). Fix: pass `fast=False` in the `.update` call.
- "Package name is invalid" (slash in package name): validation rules changed, update the package reference.
- Expired ephemeral fbpkg in the running job: update with `TW_PUSHED_VERSION=<pkg>:<ver> tw job update`.
- "Tupperware config lives in unsupported repo": move TW config to a supported repo (fbsource).
**Example posts**: [1](https://fb.workplace.com/groups/1473492212957333/permalink/3538997189740148), [2](https://fb.workplace.com/groups/1473492212957333/permalink/3552941335012400), [3](https://fb.workplace.com/groups/1473492212957333/permalink/3603753296597870)

### Infrastructure Errors
**Scuba**: `conveyor_push_event_logs`
- Columns: `push_identifier`, `message`, `scuba_identifier`, `conveyor_id`
- Filter: time range around the failure
Infrastructure errors include: (a) "Scheduler was unavailable" -- transient, retry the push; (b) RECV_TIMEOUT contacting `tupperware.api.prod` -- check for ongoing SEVs; (c) "Couldn't find a XDB tier" for virtual job handles -- verify SMC tier configuration; (d) scheduler shard switch -- wait for migration to complete.
**Example posts**: [1](https://fb.workplace.com/groups/1473492212957333/permalink/3640394392933760), [2](https://fb.workplace.com/groups/1473492212957333/permalink/3559974007642466)

### CI Test Failures
If the failing tests are `tupperware-legocastle-validation-tests`, `tupperware-legocastle-smoke-test_child`, or `tupperware-legocastle-scheduler-integration-tests`:
1. Check if your diff actually modifies TW config files
2. Run `tw validate` locally -- if it passes, the CI signal can be bypassed
3. Check if the failure matches a known flaky test pattern (build failures, network errors, scheduler timeouts)
These tests are frequently flaky and are safe to bypass when local validation succeeds and the diff does not touch TW-specific code.
**Example posts**: [1](https://fb.workplace.com/groups/1473492212957333/permalink/3621435364829663), [2](https://fb.workplace.com/groups/1473492212957333/permalink/3513214722318395), [3](https://fb.workplace.com/groups/1473492212957333/permalink/3555545268085340)

### Conveyor Canary Failures
**Scuba**: `conveyor_canary_logs`
- Columns: `canary_id`, `tw_task_handle`, `log_type`, `message`
- Filter: `tw_task_handle = <handle>`
Common canary failures: (a) "canary tasks > jobSize" -- another full job canary is already running, consuming all available tasks; (b) canary stuck in Teardown Phase -- fixed teardown duration expired, need manual intervention; (c) allocation changes revert canaries by default -- switch to task-count-based canary; (d) canary on pending/stopped task -- TW selected an unavailable task.
**CLI**: `conveyor canary --tw-job <handle> create --num-tasks 1 --num-control-tasks 1 --duration 1h --version <version>`
**Example posts**: [1](https://fb.workplace.com/groups/1473492212957333/permalink/3612770959029437), [2](https://fb.workplace.com/groups/1473492212957333/permalink/27976122218676361), [3](https://fb.workplace.com/groups/1473492212957333/permalink/3607538079552725)

## Best Practices & How-To

> [!NOTE]
> Common Conveyor failure cases also include: jobs being turned up or turned down triggering code conditions, underlying reservation changes, and Conveyor attempting to update job-level fields after out-of-band user modifications. These may be covered in future updates.

### How to make a TW job pushable from a Conveyor pipeline
Ensure your Conveyor pipeline has a push node configured correctly. Use `tw service new` to register the service ID (not done by Conveyor). The Conveyor pipeline needs the correct push type configuration and NUJ creation node. Check the Conveyor documentation for TW push setup.
**Example posts**: [1](https://fb.workplace.com/groups/1473492212957333/permalink/3646098612363338)

### How to change oncall for a TW job without breaking Conveyor
Changing the oncall may change the job handle, which breaks Conveyor references. Update the Conveyor config to reference the new handle. If the old job still exists, delete it. Verify the NUJ regex matches the new handle pattern.
**Example posts**: [1](https://fb.workplace.com/groups/1473492212957333/permalink/3500894306883770)

### How to run parallel Conveyor phases
Conveyor pipeline phases can be configured to run in parallel. Update the pipeline configuration to specify which phases should run concurrently. This is a Conveyor configuration change, not a Tupperware change.
**Example posts**: [1](https://fb.workplace.com/groups/1473492212957333/permalink/3741318656174666)

## Common Questions

### Q: Conveyor is pushing with an old deleted config. Why?
**A:** JCP regional controller updates can clear the internal update cache and try to reclaim ownership, sending updates using the old tw push type instead of JCP push type. Enable the relevant JustKnobs setting and add the new conveyor to the JK config.

### Q: Can the Legocastle validation test be bypassed for thrift-fbcode-sync diffs?
**A:** Yes. If the diff only syncs thrift files and does not touch TW configuration, the Legocastle signals can be safely bypassed. Run `tw validate` locally to confirm the spec is valid.

### Q: What is the likelihood of maintenance trains taking down all tasks simultaneously with step_size_percent=100?
**A:** Estimated as very rare (~0.01%). The `step_size_percent=100` was used as a stop-gap. Fast update for Conveyor config is now supported, so removing `step_size_percent=100` should be revisited.

## Reference Tables

### Scuba Tables
| Table | When to Use | Key Columns |
|-------|------------|-------------|
| `conveyor_push_event_logs` | Debug push failures, track push history | `push_identifier`, `message`, `conveyor_id`, `scuba_identifier` |
| `conveyor_canary_logs` | Debug canary failures within Conveyor | `canary_id`, `tw_task_handle`, `log_type`, `message` |
| `tupperware_api_service_cpp` | Check API errors during Conveyor pushes | `method_name`, `error_type`, `client_id` |

### CLI Commands
| Command | When to Use |
|---------|------------|
| `tw validate <tw-file>` | Local validation before pushing |
| `tw job list <service>` | Verify job exists for NUJ creation |
| `tw update <tw-file> <handle>` | Manual update to unblock Conveyor |
| `conveyor canary --tw-job <handle> create` | Create a manual canary |
| `tw --version` | Check CLI version for compatibility |
| `tw service new` | Register service ID for Conveyor |
