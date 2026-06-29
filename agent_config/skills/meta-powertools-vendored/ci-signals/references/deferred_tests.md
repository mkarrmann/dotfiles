# start_deferred_tests - Start Deferred CI Tests

When a diff is in unpublished draft mode, CI tests may be deferred and not started automatically. This script detects that state and triggers them.

> **⚠️ Capacity & Fairness Warning:** This script directly triggers CI tests via GraphQL mutation, bypassing the platform's deferred testing controls. It is intended for **manual, interactive use on individual diffs only**. Do NOT call this script in automated loops, cron jobs, or polling scripts that monitor multiple diffs — doing so defeats the CI platform's capacity and fairness mechanisms. For automated workflows, use `jf submit --draft --publish-when-ready` which goes through the proper submission flow.

```bash
# Start deferred tests for a draft diff
scripts/start_deferred_tests D12345
```

**Note:** You typically don't need to call this directly. Both `query_ci_signals` and `analyze_ci_signals` automatically detect deferred state and trigger tests. This script is available for explicit manual use if needed.
