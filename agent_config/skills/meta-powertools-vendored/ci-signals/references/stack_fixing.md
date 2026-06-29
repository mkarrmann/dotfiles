# Fixing CI Errors Across a Diff Stack

When asked to fix CI errors across a stack of diffs, follow this methodology. The key insight is that **CI results persist across all versions** — you never lose error context, but you must know which version to look at.

## Understanding stack version dynamics

When a stack of diffs is resubmitted (e.g., after fixing one diff):
- The **changed diff** gets a new version with new CI
- All **diffs above it** also get new versions (bumped) even though their code didn't change
- All **diffs below it** keep their existing versions unchanged
- The **previous version's CI results for every diff remain queryable** via GraphQL

## Step-by-step methodology

**1. Survey all diffs in the stack**

For each diff, check whether the latest version has completed CI:
```bash
scripts/check_ci_state D<DIFF_NUMBER>
```

**2. For each diff, determine if it was "bumped" or actually changed**

A "bumped" diff has a new version only because a diff lower in the stack was updated — its own code is unchanged. To identify bumped diffs:
- The diff's latest version was created at the same time as the resubmit
- The diff's raw patch is identical to its previous version

For bumped diffs whose new CI is still `IN_PROGRESS` or `PENDING`, **check the previous version's CI instead** — it already completed and reflects the same code:

```bash
# List all versions (including draft sub-versions)
meta graphql.query execute -o json --query '{
  phabricator_diff_query(query_params: {numbers: [DIFF_NUM]}) {
    results { nodes {
      author_relevant_phabricator_versions(limit: 100) {
        id ordinal_label { abbreviated }
      }
    }}
  }
}' | jq -r '.phabricator_diff_query[0].results.nodes[0].author_relevant_phabricator_versions[] | "\(.ordinal_label.abbreviated): \(.id)"'

# Check the second-to-last version (previous)
meta graphql.query execute --query 'query {
  signalview_signals(phabricator_version_fbid: "<PREVIOUS_VERSION_ID>") {
    signals(filters: {status: [FAILED]}) {
      count
      nodes { name debugger_slp_signal { expensive_signal_details { detail {
        ... on CISignalBoxTestDetail {
          relevant_execution_test_run_result { detail local_repro_run_cmd }
        }
        ... on CISignalBoxCitadelBuildRuleDetail { description repro_command }
      }}}}
    }
  }
}'
```

**3. Get full error details — never dismiss failures without reading the traceback**

For every failure:
- Use the `detail` field (not `detail_short`) to get the **full untruncated** error output
- Read the complete traceback before classifying as pre-existing vs. caused by your changes
- If the error message doesn't end with a clear traceback or assertion, the output is truncated — switch to direct GraphQL with the `detail` field

**4. Fix bottom-up**

Fix the **lowest failing diff** in the stack first:
- Lower diffs' fixes cascade upward through the stack on rebase
- After fixing and resubmitting (`jf submit --stack --draft`), diffs above get bumped with new versions
- Immediately check those bumped diffs' **previous version CI** (already completed) rather than waiting for new CI to run
- Only the diff you actually changed needs its new version's CI verified

**5. After each fix-and-resubmit cycle**

Do NOT wait for all new CI runs to complete. Instead:
- For the **changed diff**: wait for its new CI to complete (this is the one with new code)
- For **bumped diffs above**: query the **previous version's** CI immediately — same code, results already available
- For **diffs below the change**: unchanged, check their existing CI

## Common mistakes to avoid

| Mistake | Why it's wrong | Correct approach |
|---------|---------------|-----------------|
| Dismissing failures as "pre-existing" without reading the traceback | Many CI failures are caused by your changes but appear in downstream test targets | Always read the full `detail` field before classifying |
| Waiting for all bumped diffs' new CI to complete | Wastes time — same code, results already exist on previous version | Query previous version CI for bumped diffs |
| Only checking the latest version's CI after resubmitting | Bumped diffs' new CI may be IN_PROGRESS with 0 failures simply because tests haven't run yet | Check previous version CI for completed results |
| Fixing multiple diffs simultaneously then resubmitting | Makes it hard to attribute which fix resolved which failure | Fix one diff at a time, verify, then move to next |
