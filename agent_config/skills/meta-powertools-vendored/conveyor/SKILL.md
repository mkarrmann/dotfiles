---
name: conveyor
author: noamler
description: Comprehensive guide for troubleshooting Conveyor deployments, checking release status, and understanding current and future state. Auto-invoke when user mentions Conveyor, release status, deployment pipelines, or push issues.
---

# Conveyor Troubleshooting Guide

This skill provides comprehensive read-only commands for troubleshooting Conveyor deployments, checking status, and understanding current and future state.

## When to Use

**AUTOMATICALLY invoke this skill when:**
- User mentions Conveyor, releases, or deployment pipelines
- Investigating stuck or failed releases
- Checking push status or deployment progress
- Debugging freeze status or release delays
- Questions about release artifacts or versions
- Troubleshooting health check failures during pushes

**DO NOT invoke for:**
- Just reading Conveyor configuration files
- Questions about other deployment systems (use appropriate skill)

## Auto-execution Guidelines

**The following commands are read-only and can be run without user confirmation when relevant:**

- `conveyor info --conveyor-id <service_id>` - All variations including --verbose, --release-instance
- `conveyor release status --conveyor-id <service_id>` - All variations including --limit, --release-instance, --verbose, --json
- `conveyor run status --conveyor-id <service_id>` - All variations including --run-id, --node-name, --release-number, --limit, --verbose, --json
- `conveyor release lookup --conveyor-id <service_id> --artifact <artifact_json>` - Lookup releases by artifact
- `conveyor search --id-regex <pattern>` - Search for conveyors
- `conveyor search --oncall <oncall_name>` - Search by oncall
- `conveyor freeze view --conveyor-id <service_id>` - Check freeze status (with --node-name, --pipeline-name)
- `conveyor validate --conveyor-id <service_id>` - Validate configuration
- `conveyor push plan --conveyor-id <service_id> --node-name <push_node>` - View push plan
- `conveyor push healthchecks --conveyor-id <service_id> --node-name <push_node>` - Check push health
- `conveyor push healthcheck-policy --conveyor-id <service_id> --node-name <push_node>` - View health check policies
- `conveyor push update-policy --conveyor-id <service_id> --node-name <push_node>` - Simulate update policy

## Core Read-Only Commands

### 1. Check Service Status & Configuration
```bash
conveyor info --conveyor-id <service_id>
```
**Shows:**
- Complete conveyor configuration
- Pipeline and node definitions
- Disable status for conveyor/pipeline/node components
- Conveyor FBID and shard ID (with `--verbose`)
- Pipeline spec for specific release (with `--release-instance R123.1`)

**When to use:**
- Understanding conveyor structure
- Checking if components are disabled
- Getting pipeline and node names for further investigation

### 2. Check Release Status
```bash
conveyor release status --conveyor-id <service_id>
```
**Shows:**
- Recent release instances (e.g., R123.1)
- Release creation time
- Run status on each node in the pipeline
- Run IDs for each node

**Options:**
- `--limit N` - Show N most recent releases (default varies)
- `--release-instance R123.1` - Show specific release
- `--verbose` - Include release artifacts (fbpkg UUIDs, commits, etc.)
- `--json` - Output in JSON format

**When to use:**
- Checking if there's an ongoing release
- Seeing which nodes succeeded/failed
- Understanding release progression through pipeline

### 3. Check Run Details
```bash
conveyor run status --conveyor-id <service_id>
```
**Shows:**
- Run ID, node name, release number, release instance
- Run status (SCHEDULED, ONGOING, SUCCESSFUL, FAILED, etc.)
- Scheduled time, start time, end time

**Options:**
- `--run-id <id>` - Details for specific run
- `--node-name <node>` - Filter by node name
- `--release-number R123` - Filter by release
- `--limit N` - Limit number of results
- `--verbose` - Include run_data, artifacts, run_details, retry info
- `--json` - JSON output

**When to use:**
- Investigating why a node is stuck
- Checking timing of runs
- Understanding retry behavior

### 4. Lookup Releases by Artifact
```bash
conveyor release lookup --conveyor-id <service_id> --artifact <artifact_json>
```
**Shows:**
- Which release instances contain a specific artifact (fbpkg version, commit, etc.)

**Example:**
```bash
conveyor release lookup --conveyor-id edge_cloud/halagent \
  --artifact '{"artifact_type":"fbpkg","artifact_name":"edge_cloud.halagent","content":"{\"uuid\":\"abc123\"}"}'
```

**When to use:**
- Finding which release contains a specific fbpkg version
- Tracking down where a commit was released

### 5. Search for Conveyors
```bash
conveyor search --id-regex "pattern"
conveyor search --oncall "oncall_name"
```

**IMPORTANT:** The `--id-regex` flag accepts a **regular expression**, not a literal string. For partial matches, you must use regex patterns:
- `.*servicename.*` - Find conveyors containing "servicename" anywhere in the ID
- `edge_cloud/.*` - Find all conveyors starting with "edge_cloud/"
- `.*halagent` - Find conveyors ending with "halagent"
- `edge_cloud/halagent` - Exact match only (no wildcards = exact match)

**Examples:**
```bash
# Find all conveyors containing "agent" in the ID
conveyor search --id-regex ".*agent.*"

# Find all conveyors in the edge_cloud namespace
conveyor search --id-regex "edge_cloud/.*"

# Find conveyors by oncall team
conveyor search --oncall "edge_cloud_oncall"
```

**Shows:**
- List of conveyor IDs matching the regex pattern or oncall

**When to use:**
- Finding all conveyors for your team
- Discovering conveyor ID from partial service name (use `.*partial_name.*`)

### 6. Check Freeze Status
```bash
conveyor freeze view --conveyor-id <service_id> --node-name <node> --pipeline-name <pipeline>
```
**Shows:**
- Whether the node is currently frozen
- Reason for the freeze (global freeze, custom freeze, etc.)

**When to use:**
- Understanding why pushes aren't happening
- Checking if there's an infra freeze

### 7. Validate Conveyor Configuration
```bash
conveyor validate --conveyor-id <service_id>
conveyor validate --conveyor-config-path <path_to_materialized_json>
```
**Shows:**
- Configuration validation errors/warnings
- Preview URL for the config

**Options:**
- `--print-config` - Print the config being validated

**When to use:**
- Testing config changes before deploying
- Understanding config errors

## Push Node Troubleshooting Commands

### 8. View Push Plan
```bash
conveyor push plan --conveyor-id <service_id> --node-name <push_node>
```
**Shows:**
- Complete push plan with phases and steps
- How many hosts/jobs in each phase
- Bake times, health check windows
- Update policy details
- Which units will be updated

**When to use:**
- Understanding what will happen during a push
- Checking push phases and percentages
- Verifying push configuration

### 9. Check Push Health
```bash
conveyor push healthchecks --conveyor-id <service_id> --node-name <push_node>
```
**Shows:**
- Health check results for all units in the push
- Which health checks are passing/failing
- Current health status

**When to use:**
- Understanding why a push is paused
- Checking if services are healthy before/during push

### 10. View Health Check Policies
```bash
conveyor push healthcheck-policy --conveyor-id <service_id> --node-name <push_node>
```
**Shows:**
- All health check policies configured for the push node
- Policy details and thresholds

**When to use:**
- Understanding what health checks will be evaluated
- Debugging health check failures

### 11. Simulate Update Policy
```bash
conveyor push update-policy --conveyor-id <service_id> --node-name <push_node> --phase <phase_num>
```
**Shows:**
- Which units would be updated in a specific phase
- Update policy evaluation results

**When to use:**
- Understanding why certain units are/aren't being updated
- Debugging update policy logic

## Troubleshooting Workflows

### Workflow 1: "Why isn't my release progressing?"

```bash
# Step 1: Check overall release status
conveyor release status --conveyor-id <service_id> --limit 3

# Step 2: Identify stuck/failed nodes from output above
# Step 3: Get detailed run info for the stuck node
conveyor run status --conveyor-id <service_id> --run-id <run_id> --verbose

# Step 4: Check if the node is frozen
conveyor freeze view --conveyor-id <service_id> --node-name <node> --pipeline-name <pipeline>

# Step 5: Check conveyor/node disable status
conveyor info --conveyor-id <service_id>
```

### Workflow 2: "When will my release reach production?"

```bash
# Step 1: Check current release status
conveyor release status --conveyor-id <service_id> --release-instance R123.1

# Step 2: Check push plan to understand timeline
conveyor push plan --conveyor-id <service_id> --node-name "Production Push"

# Step 3: Check scheduled runs
conveyor run status --conveyor-id <service_id> --release-number R123 --node-name "Production Push"

# Step 4: Verify no freezes blocking progress
conveyor freeze view --conveyor-id <service_id> --node-name "Production Push" --pipeline-name "main"
```

### Workflow 3: "Which fbpkg version is in production?"

```bash
# Step 1: Find the most recent successful push
conveyor run status --conveyor-id <service_id> --node-name "Production Push" --limit 5

# Step 2: Get release details with artifacts
conveyor release status --conveyor-id <service_id> --release-instance <R123.1> --verbose

# Step 3: Look for fbpkg UUIDs in the output
```

### Workflow 4: "Why is my push paused?"

```bash
# Step 1: Check push health
conveyor push healthchecks --conveyor-id <service_id> --node-name <push_node>

# Step 2: Check current run status
conveyor run status --conveyor-id <service_id> --node-name <push_node> --limit 1 --verbose

# Step 3: Verify push plan matches expectations
conveyor push plan --conveyor-id <service_id> --node-name <push_node>

# Step 4: Check freeze status
conveyor freeze view --conveyor-id <service_id> --node-name <push_node> --pipeline-name <pipeline>
```

### Workflow 5: "When was the last successful deployment?"

```bash
# Check recent runs for production push node
conveyor run status --conveyor-id <service_id> --node-name "Production Push" --limit 10

# Look for most recent SUCCESSFUL status with completion time
```

### Workflow 6: "What's in the current release?"

```bash
# Get verbose details of latest release
conveyor release status --conveyor-id <service_id> --limit 1 --verbose

# This shows:
# - Source commit hash (scm:grafted_commit)
# - Fbpkg UUIDs for all packages
# - Diffs that were grafted (if any)
```

## Understanding Output

### Release Status Output
- **SCHEDULED**: Run is scheduled but hasn't started yet (shows scheduled time in future)
- **ONGOING**: Run is currently executing
- **SUCCESSFUL**: Run completed successfully
- **FAILED**: Run failed (check run details for error)
- **APPLICATION_FAILURE**: Application-level failure (build failed, tests failed, etc.)
- **SKIPPED**: Node was skipped (conditional execution)

### Timing Information
All times shown in the conveyor's configured timezone (check conveyor config for timezone setting).

### Artifact Format
Artifacts are shown as `artifact_type:artifact_name`:
- `fbpkg:package.name` - fbpkg package
- `scm:grafted_commit` - source code commit
- `diff_list:diffs_to_graft` - list of diffs
- `nuj:nujs` - Tupperware job specs

## Key Concepts

### Release vs Release Instance
- **Release Number** (e.g., R123): Increments with each new release created
- **Release Instance** (e.g., R123.1): Specific instance of that release
  - Usually .1 for first attempt
  - .2, .3, etc. if release is retried or re-run

### Node Types & Typical Flow
1. **Graft Node**: Combines diffs into a single commit
2. **Contbuild Tracking Node**: Waits for CI builds to complete
3. **fbpkg Build Node**: Builds fbpkg packages
4. **Preserve Node**: Tags and preserves packages (creates "stable" versions)
5. **TW Spec Snapshot**: Captures Tupperware job specs
6. **Push Nodes**: Deploy to environments (dev → canary → prod)

### Node Scheduling
- **Continuous nodes**: Run immediately when dependencies are met (builds, tests)
- **Scheduled nodes**: Run at specific times (pushes often scheduled daily)
- Check run status to see scheduled time vs actual start time

## Common Patterns

### Finding Push Node Names
```bash
# Get conveyor info and look for node names containing "Push"
conveyor info --conveyor-id <service_id> | grep -i push
```

### Monitoring Active Releases
```bash
# Check for ONGOING runs
conveyor run status --conveyor-id <service_id> --limit 20 | grep ONGOING
```

### Getting All Conveyors for a Team
```bash
conveyor search --oncall "your_oncall_name"
```

## Tips

1. **Use JSON output for scripting**: Most commands support `--json` flag
2. **Timezone awareness**: Times shown in conveyor's timezone (often PST or IST)
3. **Bunnylol shortcuts**:
   - `conveyor <service_id>` → Opens UI
   - Easier than typing full URL
4. **Finding node names**: Use `conveyor info` first to see all node names
5. **Pipeline names**: Usually "main" unless multi-pipeline setup
6. **Run IDs are unique**: Can be used to track specific execution across nodes
7. **Pipeline creation**: Conveyor configs (`.contbuild/`) must live in the **configerator** repo, not fbsource. Placing them in fbsource creates dormant configs that Conveyor never reads.

## Related Resources

- **Wiki**: https://www.internalfb.com/wiki/Conveyor/
- **CLI Docs**: https://www.internalfb.com/wiki/CLI_man_pages/conveyor/
- **Conveyor Users Group**: https://fb.workplace.com/groups/servicefoundry
- **UI Access**: `https://www.internalfb.com/conveyor/<conveyor_id>/releases`

## Example Conveyor IDs

- `edge_cloud/halagent`
- `edge_cloud/ondemand`
- `admarket/prospector_v2`
- `fboss/forwarding_stack`
- `ai_infra/unified_feature_release/<pipeline_name>`
