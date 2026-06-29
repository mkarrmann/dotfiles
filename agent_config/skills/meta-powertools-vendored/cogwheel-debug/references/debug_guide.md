# Cogwheel Test Debugging Guide

## Step 1: Extract Experiment IDs

From user input, identify ALL experiment IDs:

- Direct IDs: `E5901561307` → use `5901561307` (strip E prefix)
- Multiple IDs: `E123, E456` → process each separately
- **Conveyor links**: Extract `run_id` from URL path (`/runs/{run_id}`), then use
  `metamate_agent_extract_exp_id_from_conveyor_id` to get `experiment_id`
- **TestInfra links**: Extract `run_id` (first number) and `test_id` (second number) from
  URLs like `https://www.internalfb.com/intern/testinfra/diagnostics/{run_id}.{test_id}.{timestamp}`,
  then use `metamate_agent_extract_exp_id_from_test_run_id` to get `cogwheel_experiment_id`
- **Diff links**: Look for diff references like `D81551708` or diff URLs. When a diff is
  provided but no experiment ID, use `metamate_agent_extract_experiments_from_diff` with the diff number
  to find failed Cogwheel experiments triggered by that diff
- **No experiment ID found**: Inform user, skip Steps 2-7, search internal docs
  (wikis, Workplace posts) for answers, recommend providing an experiment ID for
  deeper debugging

## Step 2: Get Debug Info

Skip if no experiment ID. For EACH experiment ID, call `metamate_agent_experiment_debug_info`:

- Note `experiment_store_v2_id` from the response (needed for Step 3)
- Note `queue_snapshot` data per job for allocation duration issues
- **Read Overridden Job Spec**: For each side that has `manifold_bucket` and
  `manifold_path` in the debug info, read the overridden TW job spec:
  ```bash
  manifold get <manifold_bucket>/<manifold_path> /tmp/job_spec.json && cat /tmp/job_spec.json
  ```
  If only `manifold_url` is present, extract the bucket and path from the URL query params.
  Analyze the job spec for common issues:
  - Resource allocation (CPU, memory, GPU) misconfiguration
  - Network isolation and SMC tier allowlists
  - Job constraints, capabilities, or scheduling requirements
  - Service dependency or port configuration
  Correlate spec settings with observed failures (e.g., OOM → insufficient memory in spec;
  allocation timeout → overly restrictive constraints; network errors → SMC config).
- **Queue analysis**: If the user is specifically asking about long allocation times,
  use `metamate_agent_epoxy_experiment_queuing_analysis` with the `experiment_set_id` to get a queue
  time breakdown (queue position data and region selection elapsed time). This only
  covers queueing delays — for issues after Tupperware takes over (task stuck in
  ALLOCATED/RUNNING, JCP errors), use TW log analysis in Step 6 instead.
- **Rate limiting**: If the response contains `Exceeded per-creator concurrent experiment limit`
  or `Exceeded per-creator-per-category concurrent experiment limit`, the experiment was
  rate-limited. Advise waiting for in-progress trials to finish, or contact SyX oncall to
  debug or increase the limit. See [SyX Rate Limiting wiki](https://www.internalfb.com/wiki/SyX/Oncall/Rate_Limiting/).
- **Failed experiments** → skip Step 3, go to Steps 4-7
- **Successful experiments** → proceed to Step 3

### Steps 3–7: Parallel Investigation

Steps 3, 4, 5, 6, 6b, and 7 are independent of each other — run them in parallel.
Step 4 only needs the diff number (from Step 1 or user input), not Step 2 output.
Step 6b only applies when `www_sandboxes` data is present in the debug info.
Step 7 only applies to replay tests — skip it for regular `cogwheel_test` experiments.
Wait for all to complete before proceeding to Step 8.

## Step 3: Analyze Successful Experiment Results

Skip if experiment failed. If `experiment_store_v2_id` is available, call
`metamate_agent_experiment_store_debug_info` with it. Examine results for:

- Failed test assertions, timeouts, or infrastructure issues
- Assertion failures that didn't cause overall experiment failure

## Step 4: Investigate Triggering Diff

If the experiment was triggered by a diff (look for diff numbers like D12345678 in the
experiment metadata, Conveyor run context, or user-provided links), load the diff using
`get_phabricator_diff_details` to analyze the code change. Assess whether the failure
could be caused by the diff (e.g., the changed code is related to the failing test or
service) or is an unrelated infrastructure issue (e.g., OOM, allocation failure, flaky
infra). Not all experiments are diff-triggered — manual runs and scheduled tests won't
have a triggering diff. Skip this step when no diff is present.

## Step 5: Search for Related SEVs

Extract `start_time` and `end_time` from the experiment debug info (Step 2).
Compute the search window:
- `sev_window_start`: experiment `start_time` minus 7200 seconds (2 hours before)
- `sev_window_end`: experiment `end_time` plus 3600 seconds (1 hour after);
  if the experiment is still running (no `end_time`), use the current time

Use `knowledge_filtered_search` with:
- `doc_types: ["SEV"]`
- `start_creation_time`: `sev_window_start`
- `end_creation_time`: `sev_window_end`
- `keywords`: service name, error strings from Step 2, "Cogwheel"

Load matching SEVs with `knowledge_load`
(`https://www.internalfb.com/sevmanager/view/<sev_number>`).
Include timing context: did the SEV start before, during, or after the experiment?

## Step 6: Analyze TW Logs and Task Events

Skip if no experiment/trial ID.

**Note**: `tw` CLI is pre-installed on devservers. On OnDemand: `sudo feature install tupperware_cli`.

### A. TW Log Queries

From the experiment debug info response, extract for each job:
- `tw_handle`: The Tupperware handle (e.g., `tsp_eag/twindtunnel/...`)
- `tw_task_id`: The task ID (e.g., `1`)
- `submission_time`: Unix timestamp of when the experiment was submitted

**IMPORTANT**: Always run this command for failed jobs. The debug info alone
is often insufficient — TW logs contain the actual error details.

For each `sides[].jobs[]` from Step 2, run:

```bash
tw log <tw_handle>/<tw_task_id> --start-time <submission_time> --end-time <current_time>
```

Use `submission_time` from the experiment state (Step 2) as `start_time`, and the current time as `end_time` (Unix timestamps).

Example:
```bash
tw log tsp_eag/twindtunnel/cogwheel_pyper_e2e_ads_cmf_16gpu_training.cogwheel_test.2049433992494842.a/1 --start-time 1736373600 --end-time 1736377200
```

Search for: "ERROR", "Exception", "failed", "timeout", "OOMed", "Exceeded memory limit", "SETUP_FAILURE", "Traceback"

### B. TW Task Events Query

Query `tupperware_task_events` Scuba dataset for task lifecycle events:

```bash
scuba -e "
SELECT
  \`event_detail\`,
  \`event_name\`,
  \`task\`,
  \`time\`
FROM tupperware_task_events
WHERE time >= <start_time>
  AND time <= <end_time>
  AND \`task\` LIKE '%<TRIAL_ID>%'
  AND \`event_name\` != 'null'
  AND \`event_name\` != 'TICK'
LIMIT 100
" --format csv
```

- Use `submission_time` from the experiment state (Step 2) as `start_time`, and the current time as `end_time` (Unix timestamps)
- `<TRIAL_ID>`: embedded in TW task names (e.g., `my_job.2049433992494842.a/0`)

### C. Analyzing Results

- **CRITICAL: Always analyze SUT logs first.** `cogwheel_test` and `test_harness`
  depend on SUT health. "Cannot connect to tier" errors in harness/controller
  logs are often red herrings — the root cause is usually in SUT logs.
- **Job name caveat**: The TW job named `test_harness` is the controller/orchestrator,
  not the test execution harness.
- Parse `task` column to identify job/side (e.g., `my_job.123.a/0` → side `a`, job `my_job`)
- Only include log links if logs contain actual content
- **Correlate**: Task events show _when_ tasks failed (state transitions);
  TW logs show _why_ (error details). Use both for root cause analysis.

## Step 6b: Analyze WWW Sandbox Status

Skip unless `www_sandboxes` data is present in the experiment/trial debug info.

For each `www_sandbox` in each side, retrieve the Sandcastle job status using the
`instance_id`. Common WWW sandbox issues include:

- Sandbox build failures
- Allocation failures
- Network isolation issues
- Service health check failures

Include sandbox status in the investigation when failures are related to WWW sandboxes.

## Step 7: Replay Test–Specific Debugging

Skip if this is NOT a replay test. You can tell it's a replay test if the experiment
has a **treadmill** job and a **crash_checker** job instead of a user-written test
harness.

Read and follow the dedicated guide: `debug_replay_test.md`

### Sync Point: Wait for Steps 3–7

Step 8 uses error messages and findings from the previous steps. Ensure all parallel
steps above have completed before continuing.

## Step 8: Search Past Solutions

Use `knowledge_filtered_search` with:
- `doc_types: ["GROUP_POST"]`
- `workplace_group_ids: ["1816304345270730"]` (cogwheelusers group)
- `keywords`: error messages or failure patterns from logs

Load relevant posts with `knowledge_load` for solutions.

## Step 9: Generate Report

Output a structured report:
- **Investigation Summary**: Root cause with evidence
- **Proposed Fix**: Specific code changes, config updates, or action items
- **Supporting Evidence**: Error messages, stack traces, docs
- **Validation**: `buck run //path/to/package:cogwheel_test_name-launcher`

## Common Failure Patterns

| Pattern | Indicator | Action |
|---------|-----------|--------|
| **Build failure** | fbpkg build errors | Check fbpkg permissions and build targets |
| **Missing deps** | Import/dependency errors | Verify deps in TARGETS |
| **SUT startup timeout** | "Not all services came up healthy" | Check SUT logs first |
| **Allocation failure** | Long queue times | Check `queue_snapshot` for position/reservation |
| **OOM** | "OOMed" / "Exceeded memory limit" / `TASK_STATE_RESOURCE_ERROR` | Increase memory or reduce workload |
| **ACL permission** | Permission denied errors | Run `--upfront-security-check`; add SERVICELAB_ID to ACLs |
| **Network** | "Cannot connect to tier" | Symptom, not cause — check SUT logs; review allowlists |
| **Health check kill** | `KILLED_HEALTH_CHECK_FAIL_TIMEOUT` | Task failed health checks and was terminated |
| **Lost task** | `TASK_STATE_LOST` | Infrastructure issue (machine failure, preemption) |
| **Crashloop** | Multiple `TASK_EXIT_REPORT` events in rapid succession | Investigate crash cause in TW logs |
| **Test timeout** | Test exceeded time limit | Check service response times and timeout configs |
| **Assertion failure** | Test assertion mismatch | Review expected vs actual in experiment_store results |
| **Setup failure** | "SETUP_FAILURE" in logs | Services failed to start before test logic ran |
| **Harness crash** | "Traceback (most recent call last)" in harness job | Unhandled Python exception in test harness |
| **Rate limit (global)** | `Exceeded per-creator concurrent experiment limit` | Wait for trials to finish, or contact SyX oncall to debug or increase limit |
| **Rate limit (per-category)** | `Exceeded per-creator-per-category concurrent experiment limit` | Wait for trials to finish, or contact SyX oncall to debug or increase limit |

## Resources

- [Cogwheel Users Group](https://fb.workplace.com/groups/cogwheelusers)
- [Cogwheel Wiki](https://www.internalfb.com/wiki/Infra_Cloud/Testing_Experimentation_Configuration/Cogwheel/)
- [Local Execution](https://www.internalfb.com/wiki/Infra_Cloud/Testing_Experimentation_Configuration/Cogwheel/Reference/Local_Development/)
- [SyX Wiki](https://www.internalfb.com/intern/wiki/SyX/)
  - [Rate Limiting](https://www.internalfb.com/wiki/SyX/Oncall/Rate_Limiting/)
- [Recent Investigations](https://fb.workplace.com/groups/498461361655909/)
- [Debugging Guide](https://www.internalfb.com/wiki/Infra_Cloud/Testing_Experimentation_Configuration/Cogwheel/Troubleshooting_and_FAQs/)

## Related Skills

- See `fbsource/.llms/skills/coreinfra/cogwheel/create_cogwheel_workload.md` for creating and running Cogwheel tests
