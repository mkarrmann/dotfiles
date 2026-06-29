# Tupperware CLI Reference

Comprehensive reference for `tw` CLI commands. These are read-only investigation commands that complement Universal Search queries. When a user asks "how do I check X with CLI" or needs to compose a `tw` command, use this reference.

---

## Auto-execution Guidelines

**The following commands are read-only and can be run without user confirmation:**

- `tw log <task_handle>` - All variations including --file, --pattern, time ranges (NOT -f/--tail)
- `tw search <regex>` - All search variations with filters
- `tw job print <handle>` - Including --json, --previous-jobspecs, --previous-user-job-specs
- `tw resolve <job_or_task_handle>` - Show hosts and task states
- `tw changes show <job_handle>` - View pending changes
- `tw changes show-unhealthy <job_handle>` - View unhealthy tasks
- `tw job diff <path_to_tw_file>` - Compare specs
- `tw job history <job_handle>` - View job history
- `tw task-control show-status <job_handle>` - View task control status
- `tw task-control show-task-ops <job_handle>` - View pending operations

---

## CLI Quirks and Gotchas

- **`tw diag` is deprecated — do not use.** It queries schemaless Scuba tables via RFE and XDB, most of which no longer have data or accessible columns. It also frequently hangs or times out. Use direct Scuba queries via `meta scuba.dataset query` instead — see the dataset reference files in `datasets/` for the correct table names, columns, and query examples.
- **`tw allocation explain`** fails with `ERR_ALLOCATOR_ERROR` for jobs using the `DeploymentAllocator`. Skip it and continue with other diagnostic tools.
- **`tw allocation preempt`** does not work on stopped tasks. If you see `Unable to retrieve host and port information. Task state: TASK_STATE_STOPPED`, the task is already stopped.
- **Authentication:** If any `tw` command fails with an auth error (`Unauthorized`, `authentication required`), tell the user to check their authentication (e.g., run `kinit` or refresh their certificate).

---

## 1. View Logs (tw log)

```bash
tw log <task_handle> [OPTIONS]
```

**Shows:**
- Task stdout/stderr logs
- Console logs
- Custom log files from task log directory

**Options:**
- `--file <filename>` - Specific log file (`stderr`, `stdout`, `both`, `all`, `console`, or custom filename)
- `-s, --start-time <time>` - Start time (e.g., "10 minutes ago")
- `-e, --end-time <time>` - End time (e.g., "now")
- `-f, --tail` - Stream logs in real-time
- `-n <num>` - Number of lines (default: 100, unlimited if time range or pattern specified)
- `--pattern <regex>` - Filter logs by regex pattern
- `-A <num>` - Lines of context after match
- `-B <num>` - Lines of context before match
- `-C <num>` - Lines of context before and after match

**When to use:**
- Investigating task failures — application logs often have the real error
- Checking application stdout/stderr output
- Searching for specific error patterns across log files

**Examples:**
```bash
# View recent stderr
tw log tsp_atn/edge_cloud/halagent/0 --file stderr -s "10 minutes ago"

# Search for errors with context
tw log tsp_atn/edge_cloud/halagent/0 --file stderr --pattern "ERROR|FATAL" -C 5

# View all log files
tw log tsp_atn/edge_cloud/halagent/0 --file all -s "30 minutes ago"

# View console logs
tw log tsp_atn/edge_cloud/halagent/0 --file console -s "1 hour ago"
```

**Gotchas:**
- Logs are garbage collected after ~3 days. Older logs may be unavailable.
- `tw log-describe` (to discover available log files) requires a task handle (ending in `/<number>`), not a job handle.

### Log Storage Locations on Host

If you need to access logs directly on a host (via `tw ssh`):
```
stderr/stdout: /var/facebook/tupperware/agent/data/$team/$job/$tasknum/persist-dirs/logs
packages: /var/facebook/tupperware/agent/packages
```

---

## 2. Search for Jobs (tw search)

```bash
tw search [REGEX] [OPTIONS]
```

**Shows:**
- Jobs matching search criteria
- Job properties and values

**Options:**
- `REGEX` - Regex to match job handles (e.g., `tsp_atn/edge_cloud/.*`)
- `--prefix` - Switch to prefix search instead of regex
- `--value <property> <operator> <value>` - Filter by property
- `--size <property> <operator> <value>` - Filter by size properties
- `--list-props` - Show all searchable properties
- `--list-values <property>` - Show all values for a property
- `--show-distribution` - Show percentage breakdown

**Operators:** `!=`, `==`, `>`, `<`, `<=`, `>=`, `=~` (regex), `=^` (prefix)

**Gotchas:**
- Queries an index service — may not include very recently created/destroyed jobs.
- Use `--format json` for JSON output (NOT `--json`).

**Examples:**
```bash
# Find all edge_cloud jobs in tsp_atn
tw search 'tsp_atn/edge_cloud/.*'

# Find jobs with < 5 tasks
tw search --value 'jobSize < 5'

# Find jobs by service tags
tw search -v 'serviceTags.application == edge_cloud' -v 'serviceTags.tags == prod'

# Find tasks on a specific host
tw search --value "hostname == <hostname>"

# List all searchable properties
tw search --list-props
```

---

## 3. Print Job/Task Spec (tw job print)

```bash
tw job print <handle> [OPTIONS]
```

**Shows:**
- Current job specification as stored in scheduler
- Task specification for specific tasks

**Options:**
- `--json` - Export as JSON (NOT `--format json` — that flag is for `tw search` and `tw allocation explain`)
- `--previous-jobspecs` - Show history of job spec changes
- `--previous-user-job-specs` - Show last 10 user spec updates

**Examples:**
```bash
# Print current job spec
tw job print tsp_atn/edge_cloud/halagent

# Print specific task spec
tw job print tsp_atn/edge_cloud/halagent/0

# Export as JSON for field inspection
tw job print tsp_atn/edge_cloud/halagent --json

# Print spec history
tw job print tsp_atn/edge_cloud/halagent --previous-jobspecs
```

---

## 4. List Hosts for Jobs/Tasks (tw resolve)

```bash
tw resolve <job_or_task_handle>
```

**Shows:**
- Which hosts tasks are running on
- Task states
- Port information

**Example output:**
```
tsp_atn/edge_cloud/halagent/0  twshared123.05.atn1  RUNNING  thrift:8100
tsp_atn/edge_cloud/halagent/1  twshared456.07.atn1  RUNNING  thrift:8100
```

---

## 5. Show Pending Changes (tw changes show)

```bash
tw changes show <job_handle>
```

**Shows:**
- Pending changes scheduler will apply
- Changes that are rate-limited by deployment policy
- What will happen next during an update

---

## 6. Show Unhealthy Tasks (tw changes show-unhealthy)

```bash
tw changes show-unhealthy <job_handle>
```

**Shows:**
- Which tasks are unhealthy
- Reasons why scheduler considers them unhealthy

---

## 7. Diff Local vs Running Spec (tw job diff)

```bash
tw job diff <path_to_tw_file> [job_regex]
```

**Shows:**
- Differences between local .tw file and running job spec
- What would change if you ran an update

**Options:**
- `--external` - Use external diff tool (set EXTDIFF env var)

---

## 8. View Job History (tw job history)

```bash
tw job history <job_handle>
```

**Shows:**
- Past actions taken on the job
- Who performed actions and when
- Command history

**Gotcha:** `--show full-exit-message` and `--show exit-code` only work with task handles (ending in `/<number>`), not job handles.

---

## 9. Task Control Status (tw task-control)

```bash
tw task-control show-status <job_handle>
tw task-control show-task-ops <job_handle>
```

**Shows:**
- Requested operations (what's being asked of Task Control)
- Acknowledged operations (what Task Control has accepted)
- Pending task operations
- RPC status and timing

---

## 10. Check Job Update Dry Run

```bash
tw job update <path_to_tw_file> --dry-run
```

**Shows:**
- Simulation of what would happen during update
- How many tasks would restart
- Whether tasks would allocate successfully

**Note:** Dry run doesn't guarantee success, but shows likely outcome.

---

## 11. Updating Package Versions (TW_PUSHED_VERSION)

Most jobs use `PUSHED_VERSION` in their package spec (e.g., `Package(name="<package_name>", version=PUSHED_VERSION)`). For these jobs, set the `TW_PUSHED_VERSION` environment variable when running the update. There is no `--package` CLI flag on `tw job update` — the env var is the only way to specify the version.

```bash
TW_PUSHED_VERSION=<package_name>:<package_version> tw job update <tw_file> <tw_job>
```

**Find the latest persisted fbpkg version:**
```bash
fbpkg list <package_name> --latest
```

---

## 12. Resize Job Task Count (tw resize)

```bash
tw resize --task-count <count> <job_handle>
```

**Changes:**
- The number of tasks (replicas) for a running job
- Takes effect immediately without requiring a `.tw` file update

**Important:**
- This is a runtime change only. To persist across deploys, also update the `.tw` config file.
- `tw job update` with a `.tw` file does NOT change task count — use `tw resize` for that.

---

## 13. Check Per-Task Package Versions

Use Universal Search to query per-task package versions:

```bash
thriftdbg sendRequest search '{"request":{"select":{"selectedJsonPaths":["$.taskHandle","$.spec.requirements.packages"]},"from":4,"where":{"assocFilter":{"assocObjectType":1,"assocObjectIds":["<JOB_HANDLE>"]}},"jsonResponseFormat":{}}}' --tier tupperware.universal_search.experiment.test --request_timeout_ms 90000 | jq
```

**Shows:**
- Actual package version running on each task
- Same data as the TW UI's tasks page

**Key distinction:** `tw job print` shows the *target* version (what the scheduler wants all tasks to run). The Universal Search query above shows what each task is *actually* running. During a rolling restart these will differ until all tasks have been cycled.

---

## 14. Live Container Inspection (tw ssh, debug-mode)

```bash
tw ssh <task_handle>
```

SSH into a running container for live inspection — check processes, filesystem, environment variables, network state. Avoid running `find` or `grep` on large directory trees inside the container — they can time out.

```bash
tw job debug-mode <job_handle>
```

Disable health checks so a crashlooping container stays alive instead of being killed and restarted. Use this when you need to SSH into a container that keeps crashing before you can inspect it.

**Typical workflow:**
1. Enable debug mode: `tw job debug-mode <job_handle>`
2. Wait for the task to restart and stay up
3. SSH in: `tw ssh <task_handle>`
4. Inspect logs, processes, memory, etc.
5. Disable debug mode when done

---

## Understanding Task States

**Common task states:**
- `TASK_STATE_RUNNING` - Task is running and healthy
- `TASK_STATE_RUNNING_NOT_HEALTHY` - Running but failing health checks
- `TASK_STATE_STOPPED` - Task is stopped
- `TASK_STATE_STAGING` - Task is being set up (downloading packages, etc.)
- `TASK_STATE_FETCHING` - Downloading package
- `TASK_STATE_STARTING` - Container starting
- `TASK_STATE_PENDING` - Waiting for allocation
- `TASK_STATE_UNINITIALIZED` - Initial state

**State transitions to watch for:**
- `RUNNING -> STOPPED` - Task crashed or was stopped
- `STAGING -> STOPPED` - Staging failure (package issues, setup problems)
- `RUNNING_NOT_HEALTHY -> *` - Health check failures

---

## Key Concepts

### Job Handle Format
```
<cluster>/<user>/<jobname>[/<task_id>]
```
Examples:
- Job: `tsp_atn/edge_cloud/halagent`
- Task: `tsp_atn/edge_cloud/halagent/0`

A task handle ends in `/<number>` (e.g., `/0`, `/40`). Handles with dots, hashes, or other non-numeric suffixes are job handles.

### Common Clusters
- `tsp_*` - Twshared clusters (e.g., `tsp_atn`, `tsp_frc`, `tsp_prn`)
- `cg-*` - Cloud Gaming clusters
- `fbcanary_*` - Canary clusters

### Time Specifications
Valid formats for `-s`/`-e`/`--start-time`/`--end-time`:
- Absolute: `"2024/11/04-14:30:00"` or `"11/4 2PM"`
- Relative: `"10 minutes ago"`, `"2 hours ago"`, `"3 days ago"`
- Special: `"now"`

Never use `$(date)` or command substitution in timestamp arguments — it triggers permission prompts. Always use literal strings.

---

## Related Resources

- **Wiki**: https://www.internalfb.com/wiki/Tupperware/
- **Troubleshooting 101**: https://www.internalfb.com/wiki/Infra_Cloud/Service_Hosting/Tupperware/Tupperware_Help_and_Troubleshooting/How_to_Troubleshoot_Tupperware_101/
- **Cheat Sheet**: https://www.internalfb.com/wiki/Infra_Cloud/Service_Hosting/Tupperware/Tupperware_Get_Started/Quick_Ref%3A_Managing_Jobs_in_Tupperware/
- **Tupperware Users Group**: https://fb.workplace.com/groups/tw.cinc/
- **UI Access**: https://www.internalfb.com/tupperware/
