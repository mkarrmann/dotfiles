# Cogwheel Replay Test Debugging Guide

Supplements the main [Cogwheel Test Debugging Guide](debug_guide.md).

## Architecture

Replay tests have **3 jobs** (vs regular cogwheel's 2):

| Role | Job name | Purpose |
|------|----------|---------|
| **SUT** | Varies (e.g., `shots`) | Service under test |
| **Treadmill** | `treadmill` | Replays recorded prod traffic |
| **Crash checker** | `cogwheel_test` | Monitors for crashes |

Identify these from `trials[].sides[].jobs[]` in experiment debug info.

## Triage Flow

### 1. Did jobs launch?

If `trials` is empty or jobs have no tasks → **pre-deployment failure**. Check the
experiment error message for spec validation, build, or infra errors. No TW logs exist.

### 2. Which job failed?

This is the key question. The error message alone is often misleading:
- Crash checker says "SUT might have crashed" → could be treadmill that died
- Error message has shutdown noise → real cause is in SUT stderr
- OOM shows as generic failure → crash checker doesn't detect `TASK_STATE_RESOURCE_ERROR`

**Always check TW logs for the failed job** using handles from experiment debug info:

```bash
tw log <tw_handle>/<task_id> --start-time <submission_time> --end-time <current_time>
```

### 3. Interpret by job role

**Treadmill failures**: Check stderr for data loading errors (`"0 requests loaded"`,
hive/hdfs errors) or OOM (`"Exceeded memory limit"` — default 28G/26G limit).
If treadmill died, crash checker reports are false positives.

**SUT failures**: Check stderr for the actual error. For ASAN tests (workload name
contains `asan`), look for `AddressSanitizer` reports here — these are real memory
bugs in service code, not test issues.

**Crash checker**: Reports task state changes. If it has no logs, the experiment
failed at infra level before it could start.

## Replay Data

Traffic comes from a Hive table (with Scribe ptail fallback). Find the table name in:
1. The workload's `.cinc` config (`hive_table` or `scribe_category`)
2. The `.recording.cconf` in Configerator (`scribeCategory` field)
3. Treadmill stderr (logged during data loading)

## Quick Reference

| Indicator | Likely cause | Action |
|-----------|-------------|--------|
| No jobs launched | Spec/build/infra error | Fix config or retry |
| Treadmill: "0 requests loaded" | Empty Hive table | Check recording pipeline |
| Crash checker: "Test returned early" | Treadmill crashed (false positive) | Check treadmill logs |
| SUT stderr: `AddressSanitizer` | Real memory bug | Fix service code |
| `killed by OOMD` / `RESOURCE_ERROR` | OOM | Increase memory in `.cinc` |
| Task restarted / preemption | TW infra | Transient — retry |
