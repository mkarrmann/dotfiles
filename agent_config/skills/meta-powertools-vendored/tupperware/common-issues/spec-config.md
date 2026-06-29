# Spec & Config Issues

> 70 posts in TW Group FAQ | Primary Scuba: `tupperware_api_service_cpp` | Primary CLI: `tw validate`

## Debugging Playbook

**CLI**: `tw validate <spec_file.tw>` — then pick the matching section:

| Error | Go to |
|-------|-------|
| `NO_SUCH_PACKAGE` / `NO_SUCH_VERSION` | [Package Version Errors](#package-version-errors) |
| "unable to find source file" (thrift) | [Thrift Import Errors](#thrift-import-errors) |
| "scheduler shard switch" | [scheduling-preemption.md § Scheduler Shard Switch](./scheduling-preemption.md) |
| `ModuleNotFoundError` | [Module Not Found](#module-not-found) |
| UTMOST lint errors | [UTMOST Lint Errors](#utmost-lint-errors) |
| `tw validate` hangs or times out | [Validate Hangs](#validate-hangs) |
| `tw validate` passes but `tw start` fails | [Validate Passes Start Fails](#validate-passes-start-fails) |

---

### Package Version Errors
**CLI**: `fbpkg versions <package_name>` to verify the package exists
- If using TW_PUSHED_VERSION, ensure the version is preserved (not ephemeral/expired)
- For expired ephemeral packages on dev jobs: `TW_PUSHED_VERSION=<pkg>:<ver> tw update <spec> <job_handle>`
- For "Not a classic package" errors: verify the package type matches the TW deployment method
**CLI**: `tw validate --full-lint-results <spec_file>` for complete diagnostic output
**Example posts**: [1](https://fb.workplace.com/groups/1473492212957333/permalink/3511218775851323), [2](https://fb.workplace.com/groups/1473492212957333/permalink/3651570108482855)

### Thrift Import Errors
- `.tw` files and `tw_config_library` TARGETs can only access files under `fbcode/tupperware/config/` and `fbcode/configerator/structs/`
- Thrift imports from outside these paths (e.g., `core_systems/if/server/device.thrift`) will fail
- Fix: Move the thrift dependency into an allowed path, or restructure the import chain
**Example posts**: [1](https://fb.workplace.com/groups/1473492212957333/permalink/3514160018890532), [2](https://fb.workplace.com/groups/1473492212957333/permalink/3651507075155825)

### Module Not Found
- Check if the Python module import path has changed (e.g., `ti.fna` module renamed)
- For newly created cinc folders: verify the TARGETS/BUCK file includes the correct dependencies
- If local `tw validate` passes but CI fails: the CI environment may lack a dependency; bypass if local validation succeeds
**CLI**: `tw validate <spec_file>` locally to confirm
**Example posts**: [1](https://fb.workplace.com/groups/1473492212957333/permalink/3540123246294209), [2](https://fb.workplace.com/groups/1473492212957333/permalink/3564489697190897)

### UTMOST Lint Errors
- BIND_MOUNTS: User directories cannot contain bind mounts in twshared; storage jobs may need exemption
- BPF_TOKEN_ALLOW: Add the service identity to the `tw_allow_can_use_bpf_token` group (adding to the config allowlist alone is not sufficient)
- Root user warnings: Scheduled commands running as root generate lint warnings from TWFW
**Scuba**: `tw_lint_results`
- Columns: `job_handle`, `lint_rule`, `severity`, `message`
**Example posts**: [1](https://fb.workplace.com/groups/1473492212957333/permalink/3538865843086616), [2](https://fb.workplace.com/groups/1473492212957333/permalink/3621476258158907)

### Validate Hangs
- SmartPlatform specs with many jobs can cause validate to export all jobs; scope the spec to just your service's jobs
- RECV_TIMEOUT errors: transient scheduler proxy issues; if `tw lint` works locally, the diff is safe to ship
**CLI**: `TW_GATE_API_SERVICE_TW_JOB_VALIDATE=0 tw validate <spec_file>` to bypass API-level validation
**Example posts**: [1](https://fb.workplace.com/groups/1473492212957333/permalink/3781470415492823), [2](https://fb.workplace.com/groups/1473492212957333/permalink/3625054394467760)

### Validate Passes Start Fails
- Check if the ACL is configured for the service identity: "No ACL configured for some service identity"
**CLI**: `tw validate <spec_file>` may not run all ACL checks that `tw start` does
- Fix: Create a hipster ACL for the service identity
- If "PermissionChecker: job spec not found": the ACL or permissions may need time to propagate
**Example posts**: [1](https://fb.workplace.com/groups/1473492212957333/permalink/3746332872339911), [2](https://fb.workplace.com/groups/1473492212957333/permalink/3809726862667178)

## Best Practices & How-To

### How to convert Global Virtual Jobs to regular .tw 2.0 job specs
Use `tw job2 convert --virtualize --tw-file <spec_file>` to generate an unresolved job spec for comparison. Match your spec generator output to that command's output. You cannot call external APIs from within .tw files.
**Example posts**: [1](https://fb.workplace.com/groups/1473492212957333/permalink/3554473868192480)

### How to create a new job in a new region
Use `tw start` to explicitly create new jobs: `tw start fbcode/tupperware/config/<path>.tw tsp_<region>/<oncall>/<service>`. The `tw update` and `tw validate` commands expect the job to already exist.
**Example posts**: [1](https://fb.workplace.com/groups/1473492212957333/permalink/3525526011087266)

### How to handle spec v1 vs v2
Use spec v1 for .tw files; let TW handle internal v1-to-v2 conversion. `tupperware.api.experimental.job_v2` is not currently supported in .tw files. Spec generator is being deprecated; use spec1 files.
**Example posts**: [1](https://fb.workplace.com/groups/1473492212957333/permalink/3632106730429193)

### How to check differences between local and running specs
Use `tw diff <job_handle>` to compare. Unexpected differences like `contHeapEnabledRatio` may be controlled by Feature Rollout Config (FRC). Task override differences in Spec 2.0 are scheduler-generated and should be ignored.
**Example posts**: [1](https://fb.workplace.com/groups/1473492212957333/permalink/3494097910896743)

## Common Questions

### Q: Can side effects like stop_running_job be put in .tw files?
**A:** No, this is strictly prohibited. Running `tw print` or `tw lint` on such a spec would stop running jobs. Side effects must be moved out of the .tw file immediately.

### Q: Does tw validate catch all issues before deployment?
**A:** No. `tw validate` does not run all ACL checks, and some transient errors (scheduler proxy timeouts) can cause false failures. Always run locally first; if local validation passes, CI failures are often transient and can be bypassed.

### Q: How to set region-specific flags in a global virtual job spec generator?
**A:** Use conditional logic in the spec generator based on region. There is no built-in mechanism for per-region overrides in .tw files; this requires spec generator code.

## Reference Tables

### Scuba Tables
| Table | When to Use | Key Columns |
|-------|------------|-------------|
| `tupperware_api_service_cpp` | Debug API-level validation errors | `job_handle`, `validation_error_type` |
| `tw_lint_results` | Check lint validation results | `job_handle`, `lint_rule`, `severity` |
| `tw_cli_usage` | Track CLI validation commands | `command`, `user` |
| `tupperware_job_request_history` | Check request processing details | `job`, `method_name` |

### CLI Commands
| Command | When to Use |
|---------|------------|
| `tw validate <spec_file>` | Validate spec before deployment |
| `tw validate --full-lint-results <spec_file>` | Get complete lint output |
| `tw print <spec_file or job_handle>` | Print the resolved spec |
| `tw diff <job_handle>` | Compare local spec vs running spec |
| `tw job2 convert --virtualize --tw-file <spec>` | Convert to GVJ format for comparison |
| `tw start <spec_file> <job_handle>` | Create a new job from spec |
| `TW_PUSHED_VERSION=<pkg>:<ver> tw update <spec> <handle>` | Override package version |
