# Advanced: Raw GraphQL Queries

Most users should use the scripts. For custom queries:

```bash
# Get version ID
VERSION_ID=$(jf diff-properties D12345 | jq -r '.latest_phabricator_version.id')

# Query signals (note: version_id must be unquoted number)
meta graphql.query execute --query 'query ($version_id: ID!) {
  signalview_signals(phabricator_version_fbid: $version_id) {
    signals(filters: {status: [FAILED, WARNING]}) {
      count
      nodes {
        name
        status
        slp_functional_type
        debugger_slp_signal {
          expensive_signal_details {
            detail {
              __typename
              ... on CISignalBoxStaticAnalysisDetail {
                lint_issues { code name path line description severity }
              }
              ... on CISignalBoxTestDetail {
                relevant_execution_test_run_result { local_repro_run_cmd detail }
              }
              ... on CISignalBoxCitadelBuildRuleDetail {
                description
                repro_command
              }
            }
          }
        }
      }
    }
  }
}' --variables "{\"version_id\": $VERSION_ID}"
```

## Signal Count Query (using aliases)

Get counts by status without fetching full signal details:

```bash
VERSION_ID=$(jf diff-properties D12345 | jq -r '.latest_phabricator_version.id')

# NOTE: signals() requires the filters argument — use filters: {} for unfiltered total
meta graphql.query execute --query 'query ($version_id: ID!) {
  signalview_signals(phabricator_version_fbid: $version_id) {
    all: signals(filters: {}) { count }
    failed: signals(filters: {status: [FAILED]}) { count }
    warning: signals(filters: {status: [WARNING]}) { count }
    passed: signals(filters: {status: [PASSED]}) { count }
    pending: signals(filters: {status: [PENDING]}) { count }
    info: signals(filters: {status: [INFO]}) { count }
  }
}' --variables "{\"version_id\": \"$VERSION_ID\"}"
```

## GraphQL Schema Reference

**Signal Types:** `STATIC_ANALYSIS`, `TEST`, `BUILD_RULE`, `BUILD`, `JOB`

**Signal Statuses (CIResultStatus enum):** `PASSED`, `FAILED`, `WARNING`, `PENDING`, `INFO`

**NOTE:** The `signals()` field requires a `filters` argument. Use `filters: {}` for an unfiltered query.

**Filter Options:**
```graphql
filters: {
  status: [FAILED, WARNING]           # Status filter
  slp_functional_types: [TEST]        # Type filter (plural)
}
```

**Detail Fragments:**
- `CISignalBoxStaticAnalysisDetail` → `lint_issues[]`
- `CISignalBoxTestDetail` → `relevant_execution_test_run_result.detail` (full untruncated output) and `.local_repro_run_cmd`
- `CISignalBoxCitadelBuildRuleDetail` → `description` (full error) and `repro_command`

**IMPORTANT: Use `detail` not `detail_short`.** The `detail_short` field truncates at ~2KB, cutting off the actual error assertion and stack trace. The `detail` field returns the complete untruncated test output. The MCP tool `include_failing_ci_signals` uses `detail_short` internally, so always use direct GraphQL with `detail` when you need full error context.
