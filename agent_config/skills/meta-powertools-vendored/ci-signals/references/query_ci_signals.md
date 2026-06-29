# query_ci_signals - Raw Data Access

Get raw CI signal JSON for custom processing:

```bash
# Summary only (fast)
scripts/query_ci_signals D12345 --summary-only

# Full details (up to 100 signals)
scripts/query_ci_signals D12345

# Filter by type(s)
scripts/query_ci_signals D12345 --types STATIC_ANALYSIS
scripts/query_ci_signals D12345 --types TEST
scripts/query_ci_signals D12345 --types TEST,BUILD_RULE

# Custom limit
scripts/query_ci_signals D12345 --limit 50

# ALL statuses (not just FAILED+WARNING) — includes PASSED signals
scripts/query_ci_signals D12345 --all-statuses
scripts/query_ci_signals D12345 --all-statuses --summary-only

# Specific status filter
scripts/query_ci_signals D12345 --status PASSED
scripts/query_ci_signals D12345 --status FAILED,PASSED
```

**Status flags:**
- Default (no flag): Returns only `FAILED` and `WARNING` signals
- `--all-statuses`: Returns ALL signals regardless of status (PASSED, FAILED, WARNING, etc.)
- `--status STATUS1,STATUS2`: Returns only signals matching the specified statuses (valid: `PASSED`, `FAILED`, `WARNING`, `PENDING`, `INFO`)

**Filter output with jq:**
```bash
# Static analysis issues
scripts/query_ci_signals D12345 | jq '.signalview_signals.signals.nodes[] | select(.slp_functional_type == "STATIC_ANALYSIS")'

# Test failures with reproduce commands
scripts/query_ci_signals D12345 | jq -r '.signalview_signals.signals.nodes[] | select(.slp_functional_type == "TEST") | "Test: \(.name)\nReproduce: \(.debugger_slp_signal.expensive_signal_details.detail.relevant_execution_test_run_result.local_repro_run_cmd)\n"'

# Count by type
scripts/query_ci_signals D12345 | jq '[.signalview_signals.signals.nodes[] | .slp_functional_type] | group_by(.) | map({type: .[0], count: length})'

# Count by status (with --all-statuses)
scripts/query_ci_signals D12345 --all-statuses --summary-only | jq '[.signalview_signals.signals.nodes[] | .status] | group_by(.) | map({status: .[0], count: length})'
```
