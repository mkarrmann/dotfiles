---
name: testx-debug
description: Debugs failing, broken, or flaky tests by analyzing TestX run results. Use when given a TestX URL (https://www.internalfb.com/intern/test/...) or test ID, or when investigating why a test is failing.
---

# Test Debugging via TestX

## Quick Start

Given a TestX URL like `https://www.internalfb.com/intern/test/1234567890`:

1. Determine which failure type(s) to investigate (from arguments or by asking the user)
2. Extract test ID (`1234567890`) and query TestX with selected statuses
3. Analyze the `detail` field for error messages and stack traces
4. Fetch and compare logs between a recent PASSED and FAILED run
5. Read the test source code to understand the failure
6. Apply the fix, then run `run_cmd` and `stress_run_cmd` to verify

## Step 1: Identify the Problem

Determine which failure status(es) to investigate.

**If `$ARGUMENTS` includes statuses** (e.g., `--statuses FAILED,FATAL`), parse the comma-separated list and use those statuses directly. Do NOT use `AskUserQuestion` in this case.

**Otherwise**, use the `AskUserQuestion` tool to ask:

```json
{
  "questions": [{
    "header": "Status",
    "question": "Which test failure status(es) are you investigating?",
    "multiSelect": true,
    "options": [
      {"label": "FAILED", "description": "Test assertion failed"},
      {"label": "TIMEOUT", "description": "Test exceeded time limit"},
      {"label": "FATAL", "description": "Test crashed or had a fatal error"},
      {"label": "INFRA_FAILURE", "description": "Infrastructure failure (not the test's fault)"}
    ]
  }]
}
```

The selected status(es) will be used in the GraphQL query's `statuses` filter.

**Available statuses:**

| Status | Description |
|--------|-------------|
| `FAILED` | Test assertion failed |
| `TIMEOUT` | Test exceeded time limit |
| `FATAL` | Test crashed or had a fatal error |
| `INFRA_FAILURE` | Infrastructure failure (not the test's fault) |
| `SKIPPED` | Test was skipped |
| `OMITTED` | Test was omitted |

## Step 2: Fetch Test Data and Identify Runs

1. **Fetch test details and recent runs** - Query the test metadata using the primary query below to get both PASSED and FAILED results
2. **Fetch details for failed results** - The primary query uses `run_results_without_details`, so `detail` is only available in `previous_attempts` (retried failures). For top-level failed results, fetch `detail` separately via `testx --caller testx-debug --as-json results get <RESULT_ID>`. Look for patterns in repeated failures

## Step 3: Compare PASSED vs FAILED Logs

If a recent PASSED run is available in the results, compare its logs against the FAILED run. The failure `detail` often only shows the symptom; diffing logs between a passing and failing run reveals the actual cause.

If no PASSED results appear in the primary query, fetch some:

```bash
meta graphql.query execute --query 'query FetchPassedResults($test_id: ID!) {
  fetch__TestInfraTest(id: $test_id) {
    run_results(filter: { statuses: [PASSED] }, first: 3) {
      edges { node { result_id status } }
    }
  }
}' --variables "{\"test_id\": \"$TEST_ID\"}"
```

### Fetch metadata for both runs

```bash
testx --caller testx-debug --as-json results get <RESULT_ID>
```

Compare the JSON output between the two runs — look for differences in revisions, build versions, app handles, or config that explain the regression.

### Download and compare logs

Use `meta testinfra.artifact` to list and download logs for both runs:

```bash
# List artifacts (extract run_id = first segment of result_id before the first dot)
meta testinfra.artifact list --test-id=<TEST_ID> --run-id=<RUN_ID> --output=json

# Download to separate directories for easy comparison
mkdir -p /tmp/testx_artifacts/passed /tmp/testx_artifacts/failed
meta testinfra.artifact download --handle=<HANDLE> --output-dir=/tmp/testx_artifacts/passed
meta testinfra.artifact download --handle=<HANDLE> --output-dir=/tmp/testx_artifacts/failed
```

Always download **test logs** (`Runner Logs`, `RE Action Stdout/Stderr`).

### Device test logs

For device/emulator tests (detected by `config.device`, `config.isOSTest`, artifact titles containing `logcat`, or test paths with `__e2e__`/device names), also fetch **device logs** via the diagnostics report:

```bash
DIAG_HANDLE=$(testx --caller testx-debug --as-json results get <RESULT_ID> | \
  python3 -c "import sys,json; print(json.load(sys.stdin).get('result_metadata',{}).get('diagnostics_report',''))")

[ -n "$DIAG_HANDLE" ] && clowder get "$DIAG_HANDLE" - 2>/dev/null | python3 -c "
import sys, json
data = json.load(sys.stdin)
for log in data.get('logs', []):
    print(f\"{log.get('title','')} -> {log.get('handle','')}\")
for f in data.get('file_artifacts', []):
    print(f\"FILE: {f.get('title','')} -> {f.get('handle','')}\")
if 'failure' in data and 'screenshot' in data['failure']:
    print(f\"FAILURE_SCREENSHOT: {data['failure']['screenshot'].get('handle','')}\")
"
```

**Frame/device logs** to fetch: `logcat-log.txt`, `logcat.entireLogBuffer_*.txt`, `boot_logcat_log.log`, `emulator_log.log`

**Phone logs** — also fetch when a companion phone is involved (multi-device tests, pairing, tests mentioning `phone`/`mwa`/`companion`): look for logcat and `bluetooth_dumpsys` artifacts with the phone's device serial.

### Search and compare

Search both PASSED and FAILED logs for the error patterns from the `detail` field. Look for differences: services that started in one but not the other, different code paths, different build versions, error messages present only in the failing run.

## Step 4: Read Test Source and Identify Root Cause

Read the test source code to understand the test logic. Correlate what the test expects with what the logs show actually happened.

## Step 5: Apply Fix and Verify

1. **Run the test**: Execute the `run_cmd` from the query to verify the fix
2. **Stress test**: Execute the `stress_run_cmd` to verify stability

## Input Parsing

Extract the test ID from input:
- URL `https://www.internalfb.com/intern/test/1233456789?...` → extract `1233456789`
- Already numeric → use directly

## TestX CLI

The `testx` command may not be on PATH. If not found, use the path relative to fbsource:

```bash
# Try testx first, fall back to relative path from fbsource
testx --caller testx-debug --help 2>/dev/null || FBSOURCE/fbcode/tae/testx/scripts/testx --caller testx-debug --help
```

Replace `FBSOURCE` with the actual fbsource directory path (e.g., `/data/sandcastle/boxes/fbsource`).

### Useful testx Commands

```bash
# Get run summary
testx --caller testx-debug runs get <RUN_ID>

# List all results for a run
testx --caller testx-debug results list <RUN_ID>

# Get detailed result info (table format)
testx --caller testx-debug results get <RESULT_ID>

# Get detailed result info as JSON (includes diagnostics handles)
testx --caller testx-debug --as-json results get <RESULT_ID>
```

The `--as-json` flag is particularly useful as it returns:
- `details` - Full error message with stack trace
- `result_metadata.diagnostics_report` - Everstore handle for diagnostics
- `config` - Test configuration details (device, osFlavor, etc.)

## Fetching Test Data via GraphQL

Use `meta graphql.query execute` to fetch test details and failure information.

### Important: Retried (Flaky) Tests

The `run_results` and `run_results_without_details` connections return **only one result per run — the final/canonical one**. For retried tests that eventually pass, the top-level result will be `PASSED`, hiding the intermediate failures. The failed attempts are nested under the **`previous_attempts`** field on each result.

**Always query `previous_attempts` to catch flaky failures.** If you only filter by `statuses: [FAILED]`, you will miss failures from runs that were retried and eventually passed.

### Primary Query (recommended — catches both direct failures and retried failures)

```bash
TEST_ID="your_test_id_here"

meta graphql.query execute --query 'query FetchTestRunResults($test_id: ID!) {
  fetch__TestInfraTest(id: $test_id) {
    name
    run_cmd
    stress_run_cmd
    run_results_without_details(first: 20) {
      nodes {
        result_id
        status
        previous_attempts {
          result_id
          status
          detail
        }
      }
    }
  }
}' --variables "{\"test_id\": \"$TEST_ID\"}"
```

Then check for failures in both the top-level results AND `previous_attempts`. A test is flaky if top-level results are `PASSED` but `previous_attempts` contain `FAILED` entries.

### Filtered Query (for tests with direct, non-retried failures)

Replace `$STATUSES` with the user's selected status(es) as a JSON array (e.g., `["FAILED"]` or `["TIMEOUT", "FATAL"]`):

```bash
TEST_ID="your_test_id_here"
STATUSES='["FAILED"]'  # Replace with user's selection

meta graphql.query execute --query 'query FetchTestRunResults($test_id: ID!, $statuses: [TestInfraRunResultStatus!]!) {
  fetch__TestInfraTest(id: $test_id) {
    name
    run_cmd
    stress_run_cmd
    run_results(filter: { statuses: $statuses }, first: 5) {
      edges {
        node {
          result_id
          status
          detail
        }
      }
    }
  }
}' --variables "{\"test_id\": \"$TEST_ID\", \"statuses\": $STATUSES}"
```

**Note:** This filtered query will NOT return failures from retried runs that eventually passed. Use the primary query above for flaky test investigations.

**Key fields:**
- `name`: Full test name (e.g., `fbcode//path/to:target - TestClass.testMethod`)
- `run_cmd`: Command to run the test locally
- `stress_run_cmd`: Command to stress test for flakiness
- `detail`: **Full failure details including stack trace and error messages**
- `previous_attempts`: Array of earlier attempts for the same run (contains retried failures)

## Fetching Specific Result (Optional)

For more details on a specific result, use the `result_id` (format: `{run_id}.{test_id}.{timestamp}`):

```bash
RESULT_ID="9851624324801523.844425191991706.1767689792"

meta graphql.query execute --query 'query TestResultDiagnostics($result_id: String!) {
  fetch__TestInfraRunResult(id: $result_id) {
    status
    result_id
    detail
    test {
      name
    }
  }
}' --variables "{\"result_id\": \"$RESULT_ID\"}"
```

## Downloading Diagnostics and Artifacts

Test results often have associated diagnostics stored in Everstore. Use `clowder` to download them:

```bash
# 1. Get the result with diagnostics handle
testx --caller testx-debug --as-json results get <RESULT_ID>

# 2. Extract the diagnostics_report handle from result_metadata and download
clowder get <DIAGNOSTICS_HANDLE> -

# 3. The diagnostics JSON contains handles to specific logs, e.g.:
# {"logs":[{"title":"Runner Logs","handle":"<HANDLE>","data_type":"PLAIN_TEXT"}]}

# 4. Download specific logs
clowder get <LOG_HANDLE> -
```

### Quick Diagnostics Workflow

```bash
# One-liner to get diagnostics report
testx --caller testx-debug --as-json results get <RESULT_ID> | jq -r '.result_metadata.diagnostics_report' | xargs clowder get -
```

## GraphQL Schema Reference

For additional fields, see the schema at `xplat/graphql/server-schemas/relay/intern/`:

| Type | File |
|------|------|
| `TestInfraTest` | `0042.graphql` |
| `TestInfraRunResult` | `0042.graphql` |
| `fetch__TestInfraRunResult` | `009a.graphql` |
| `fetch__TestInfraTest` | `009a.graphql` |

**Valid statuses (TestInfraRunResultStatus):** `PASSED`, `FAILED`, `SKIPPED`, `FATAL`, `TIMEOUT`, `OMITTED`, `INFRA_FAILURE`

**Test states:** `good_quality`, `bad_quality`, `flaky`, `suspected_bad`, `disabled`
