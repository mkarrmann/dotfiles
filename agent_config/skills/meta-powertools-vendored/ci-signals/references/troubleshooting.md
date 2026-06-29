# Common Mistakes, Troubleshooting & Limitations

## Common Mistakes

### GraphQL Variable Formatting

When using `meta graphql.query execute` directly, version IDs must be unquoted numbers:

```bash
# WRONG - quoted string causes error
--variables '{"version_id": "1409390620889384"}'

# CORRECT - unquoted number
--variables '{"version_id": 1409390620889384}'
```

### Piping JSON Output

```bash
# WRONG - progress messages break parsing
scripts/query_ci_signals D12345 --json 2>&1 | jq '.summary'

# CORRECT - redirect stderr separately
scripts/query_ci_signals D12345 --json 2>/dev/null | jq '.summary'
```

### Fetching Too Many Artifacts

```bash
# RISKY - can timeout
scripts/fetch_test_details D12345

# CORRECT - start small
scripts/fetch_test_details D12345 --limit 5
```

### Truncating Output

```bash
# WRONG - kills script mid-download
scripts/fetch_test_details D12345 | head -100

# CORRECT - use --limit flag
scripts/fetch_test_details D12345 --limit 5
```

### Assuming Zero Failures Means CI Passed

```bash
# WRONG - does not account for pending signals
scripts/query_ci_signals D12345 --summary-only | jq '.signalview_signals.signals.count'
# If count is 0, it only means 0 FAILED+WARNING. Signals may still be running!

# CORRECT - check ci_state AND counts
scripts/check_ci_state D12345 | jq '{state: .ci_state, pending: .signal_counts.pending, failed: .signal_counts.failed}'
```

## Troubleshooting

| Problem | Cause | Solution |
|---------|-------|----------|
| "0 sub results" in detail_short | Test harness crashed or timed out (TSAN adds 10-15x overhead) | Check JOB signals for infrastructure issues |
| Error message doesn't end with a traceback or assertion | `detail_short` truncated at ~2KB, actual error cut off | Use direct GraphQL with `detail` field instead of `detail_short` (see Advanced section) |
| "sanitize_report" in detail_short | Sanitizer error; details are in stdout.log, not summary | `fetch_test_details --status FAILED --limit 5` then grep stdout.log |
| No artifacts available | Expired (~2 weeks), harness crash, or infra killed job | Re-run tests if recent; check Sandcastle UI |
| Generic reproduce command | Runs entire suite, not specific test | Extract test name from signal, run `buck2 test <target>` directly |
| "Found N tests" but no artifacts | Output truncated or artifacts expired | Don't pipe through `head`; use `--limit 5` instead |
| All tests "failed to download" | TestX CLI missing or artifacts expired | Check CLI at `~/fbsource/fbcode/tae/testx/scripts/testx` |
| "Argument list too long" | Too many artifacts fetched at once | Use `--limit 10` to fetch in smaller batches |
| "CI tests are DEFERRED" | Diff is in unpublished draft mode with deferred CI | Tests are auto-started; re-run the command shortly to see results |
| 0 failures but CI not done | Signals still pending/running | Use `check_ci_state` to see `ci_state` and `pending` count; wait and re-check |
| `pending == 0` but state is IN_PROGRESS | Signal jobs not yet registered in signalview | Wait 30-60s for signals to appear, then re-check |
| JOB signal shows "no detail" or "CISignalBoxBuildDetail" | The signalview GraphQL API doesn't expose error messages for this signal type | Use `get_phabricator_diff_details` MCP with `include_failing_ci_signals=true` to get the full error message (e.g., rebase conflicts, patch failures) |

## Limitations

- **CI signals via MCP truncate test logs** - Use direct GraphQL with the `detail` field (not `detail_short`) for full untruncated output. Use `fetch_test_details` for TSAN/ASAN artifacts.
- **Reproduce commands may be generic** - Some run entire suites; extract specific test name
- **Artifact retention:** ~2 weeks. Older artifacts cannot be fetched
- **Large diffs (500+ signals):** May need pagination using `after` cursor in raw GraphQL
- **Signal counts may lag behind CI state** - When `ci_state` says IN_PROGRESS but `pending == 0`, signals haven't been registered yet
- **JOB/Build signals may lack inline detail** - Signals with `CISignalBoxBuildDetail` type (e.g., `fbsource-target-determinator`, app build jobs) don't expose error messages via the signalview GraphQL API. Use `get_phabricator_diff_details` MCP to get the actual error (rebase conflicts, build errors, etc.)
