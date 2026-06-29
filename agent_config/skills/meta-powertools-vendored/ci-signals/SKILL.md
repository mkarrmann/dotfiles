---
name: ci-signals
description: "ACTIVATE THIS SKILL when the user asks about CI status, CI signals, failing tests, lint issues, whether a diff is green/red, or the AI-review findings on a diff (Confucius/Devmate review insights and Arctic spotlight/drift insights). This skill provides efficient bash scripts that are MUCH faster and use far less context than get_phabricator_diff_details. DO NOT use get_phabricator_diff_details or get_diff_details as a first step for CI checks — activate this skill instead."
allowed-tools: Bash(**/ci-signals/scripts/**), Bash(grep:*), Bash(meta graphql.query:*), Bash(meta phabricator.diff:*), Bash(jf diff-properties:*), Read
---

# CI Signals Query Skill

Query failing CI signals and their detailed issues for any Phabricator diff.

**Info:** Scripts live next to this SKILL.md in the `scripts/` subdirectory. Depending on how the skill was installed, that resolves to either `~/.claude/skills/ci-signals/scripts/` (Claude Code) or `~/.llms/skills/claude-templates/ci-signals/scripts/` (Devmate / `llms` install). Run them using the absolute path that exists on your machine (e.g. `~/.llms/skills/claude-templates/ci-signals/scripts/check_ci_state D12345`).

## TL;DR - Quick Decision Guide

| What You Need | Command | Time |
|---------------|---------|------|
| Quick triage (FAILED only) | `scripts/analyze_ci_signals D12345 --triage` | 10-20s |
| Full analysis with reproduce commands | `scripts/analyze_ci_signals D12345` | 10-30s |
| Just counts | `scripts/query_ci_signals D12345 --summary-only` | <5s |
| Full TSAN/ASAN logs | `scripts/fetch_test_details D12345 --status FAILED --limit 5` | 30-60s |
| Start deferred tests (draft diffs) | `scripts/start_deferred_tests D12345` | 5-10s |
| CI state + pending signal counts | `scripts/check_ci_state D12345` | <5s |
| Review Insights (Confucius) | `scripts/query_review_insights D12345` | <10s |
| Arctic AI-review insights | `scripts/query_arctic_insights D12345` | <10s |

**Quick workflow:**
```bash
# Step 1: See what's blocking
scripts/analyze_ci_signals D12345 --triage

# Step 2: If test failures need more detail, fetch artifacts
scripts/fetch_test_details D12345 --status FAILED --limit 5

# Step 3: Search for specific errors
grep -A 50 "WARNING: ThreadSanitizer" /tmp/ci-debug-D12345-*/*/stdout.log
```

## Signal Types

| Type | Description | Examples |
|------|-------------|----------|
| **STATIC_ANALYSIS** | Lint, type errors | `type` (Hack), `arc-lint`, `lint-infer-www-hack` |
| **TEST** | Test failures | Unit tests, integration tests, E2E tests |
| **BUILD_RULE** | Build graph issues | Dependency checks, target validation |
| **JOB** | Workflow/infrastructure | Sandcastle orchestrators |

## IMPORTANT: JOB/Build Signal Error Messages

The signalview GraphQL API does NOT expose error messages for `CISignalBoxBuildDetail` signals (e.g., `fbsource-target-determinator`, app build jobs). When `analyze_ci_signals` shows "Build/infra failure (no inline detail)", you MUST call `get_phabricator_diff_details` with `include_failing_ci_signals=true` to get the actual error message. Common errors include:
- **"Failed to apply patch. Conflict when rebasing onto master"** — the diff needs rebasing
- **Build compilation errors** — a file in the diff doesn't compile
- **Missing dependencies** — BUCK target issues

Do NOT assume JOB failures are "infrastructure issues" or "not actionable" — always fetch the full error message.

## Scripts Reference

### `analyze_ci_signals` - Start Here (Most Common)

Intelligent analysis with pattern detection and reproduce commands. Shows CI state and pending signal counts at the top.

```bash
# Quick triage: FAILED signals only (recommended starting point)
scripts/analyze_ci_signals D12345 --triage

# Full analysis with all signal types
scripts/analyze_ci_signals D12345

# Specific signal types only
scripts/analyze_ci_signals D12345 --types STATIC_ANALYSIS,TEST
```

**Output includes:** CI state, signal counts (total/passed/failed/warning/pending), summary statistics, affected files, unique reproduce commands, pattern detection.

### `check_ci_state` - CI State + Signal Counts (Best for Polling)

Returns `core_ci_signals_state` and signal counts as structured JSON. Fastest way to check if CI is complete.

```bash
scripts/check_ci_state D12345
```

**Output:** JSON with `ci_state` and `signal_counts` (total/failed/warning/passed/pending/info).

**Common ci_state values:**
- `TEST_DEFERRED` — tests haven't started (unpublished draft)
- `TEST_IN_PROGRESS_WITH_NO_FAILURES` — tests running, none failed yet
- `TEST_IN_PROGRESS_WITH_FAILURES` — tests running, some already failed
- `TEST_FINISHED_ALL_PASSED` — all tests passed
- `TEST_FINISHED_WITH_FAILURES` — tests completed with failures

**Interpreting:** `pending > 0` means CI is not complete. `pending == 0` with `*FINISHED*` state means CI is done.

### `query_arctic_insights` - Arctic AI-Review Insights

Arctic insights are AI code-review findings (NOT CI signals), so they don't show up in the CI scripts above. Use this when the user asks to address the AI-review findings / issues on a diff, or specifically about spotlight or drift. It reads the persisted Arctic insights via `meta phabricator.diff arctic`.

```bash
# All Arctic insights (intent, drift, spotlight, evidence, category) as a table
scripts/query_arctic_insights D12345

# Only spotlight (code regions flagged for review) and drift
scripts/query_arctic_insights D12345 --insight-type spotlight,drift

# Raw JSON for scripting
scripts/query_arctic_insights D12345 --json
```

**Output includes:** spotlight regions (file/line, severity, category, reason, review guidance), drift (score 0-100 + justification + mitigation), author intent, evidence/test-plan gaps, and a `resolution` column. **Skip any finding whose `resolution` is already set** — the author already addressed it in Preflight.

## Additional Resources

For advanced workflows, read the relevant reference file:

- **[query_ci_signals](references/query_ci_signals.md)** — Raw signal data access, jq filtering, status flags
- **[fetch_test_details](references/fetch_test_details.md)** — TSAN/ASAN artifact download, stdout.log grep patterns, TestX CLI
- **[start_deferred_tests](references/deferred_tests.md)** — Triggering CI on unpublished draft diffs
- **[query_review_insights](references/review_insights.md)** — Confucius Comments, AI Test Quality
- **[Polling / eval loop workflow](references/polling_workflow.md)** — Automated CI polling, decision logic, sleep intervals
- **[Large diffs (50+ signals)](references/large_diffs.md)** — Pagination, type-filtered analysis, summary presentation
- **[Stack fixing](references/stack_fixing.md)** — Multi-diff stack CI fixing, version dynamics, bottom-up methodology
- **[Previous diff versions](references/previous_versions.md)** — Querying CI for older versions, version listing
- **[Raw GraphQL](references/raw_graphql.md)** — Direct signalview queries, schema reference, detail vs detail_short
- **[Troubleshooting](references/troubleshooting.md)** — Common mistakes, error resolution, known limitations

## Related Tools

- **Signalview UI**: https://www.internalfb.com/diff/D{diff_number}
- **`jf diff-properties`**: Get diff metadata including version IDs
- [CI-Signals Experience wiki](https://www.internalfb.com/wiki/CI-Signals_Experience/)
