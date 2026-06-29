# Querying CI Signals for Previous Diff Versions

CI signal results for ALL previous versions of a diff are preserved and queryable. This is critical when working with diff stacks — after resubmitting, diffs above the changed one get new versions with fresh CI runs, but the previous version's completed CI results remain accessible.

## Step 1: List all version IDs for a diff (including draft sub-versions)

**IMPORTANT:** Use `author_relevant_phabricator_versions` — NOT `phabricator_versions`. The `phabricator_versions` field only returns published versions and misses draft sub-versions (e.g., V2.1, V2.2, V2.3). The `author_relevant_phabricator_versions` field returns ALL versions — published and draft — matching what the Phabricator Signals UI shows.

```bash
meta graphql.query execute -o json --query '{
  phabricator_diff_query(query_params: {numbers: [12345]}) {
    results { nodes {
      author_relevant_phabricator_versions(limit: 100) {
        id
        number
        ordinal_label { abbreviated }
      }
    }}
  }
}' | jq -r '.phabricator_diff_query[0].results.nodes[0].author_relevant_phabricator_versions[] | "\(.ordinal_label.abbreviated): id=\(.id)"'
```

Returns all versions with human-readable labels (V1, V2, V2.1 Draft, V2.2 Draft, etc.).

## Step 2: Find which version had failures

```bash
for vid in <VERSION_ID_1> <VERSION_ID_2> ...; do
  count=$(meta graphql.query execute -o json --query "query {
    signalview_signals(phabricator_version_fbid: \"$vid\") {
      signals(filters: {status: [FAILED]}) { count }
    }
  }" | jq -r '.signalview_signals.signals.count // 0')
  echo "Version $vid: $count failures"
done
```

## Step 3: Get full failure details for a specific version

Use the same `signalview_signals` query from [raw_graphql.md](raw_graphql.md), substituting the old version's ID. **Always use the `detail` field (not `detail_short`) for complete tracebacks.**

## Alternative: MCP with version number

The `mcp__plugin_meta_mux__get_phabricator_diff_details` tool accepts a `phabricator_version_number` parameter (the `number` field from the version listing above):

```bash
phabricator_diff_number: "D12345"
phabricator_version_number: 354654186
include_failing_ci_signals: true
include_ci_overall_status: true
```

**Note:** MCP `include_failing_ci_signals` uses truncated `detail_short`. For full tracebacks, use direct GraphQL with the `detail` field.
