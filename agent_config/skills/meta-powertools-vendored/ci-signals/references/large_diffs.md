# Workflow for Large Diffs (50+ Signals)

## Step 1: Get Summary

```bash
scripts/query_ci_signals D12345 --summary-only
```

## Step 2: Prompt User for Interest

Present the summary and ask which signal types to investigate using the AskUserQuestion tool:
- STATIC_ANALYSIS - Most actionable for code fixes
- TEST - Shows test failures with reproduce commands
- BUILD_RULE - Shows BUCK build failures
- JOB - Workflow/infrastructure issues

## Step 3: Run Analysis

```bash
scripts/analyze_ci_signals D12345 --types STATIC_ANALYSIS,TEST
```

## Step 4: Present Findings

Analyze the output and present:
1. **Summary**: Total count and breakdown by type
2. **Common Patterns**: Identify root causes (e.g., "650 failures caused by missing target X")
3. **Reproduce Commands**: Unique commands to run locally
4. **Recommendations**: Which issues to fix first based on impact

Example output format:
```text
=== CI Signal Analysis for D12345 ===

Total: 712 signals (FAILED: 680, WARNING: 32)

By Type:
  BUILD_RULE: 650
  STATIC_ANALYSIS: 45
  TEST: 12

Key Findings:
  • 650 BUILD_RULE failures all caused by missing target 'imageAndroid'
    → Fix: Create the missing target
    → Reproduce: buck build fbsource//xplat/ocean/impl/ocean/io/image:imageAndroid

Recommendation: Fix the BUILD_RULE root cause first, as it affects 650 signals.
```
