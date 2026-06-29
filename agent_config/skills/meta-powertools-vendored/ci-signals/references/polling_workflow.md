# Polling / Eval Loop Workflow

When waiting for CI to complete (e.g., in an automated fix-and-verify loop), use `check_ci_state` for efficient polling. **Do not guess whether CI is complete — always check.**

## Recommended Polling Pattern

```bash
# Step 1: Check CI state (fast, <5s)
CI_JSON=$(scripts/check_ci_state D12345)
CI_STATE=$(echo "$CI_JSON" | jq -r '.ci_state')
PENDING=$(echo "$CI_JSON" | jq -r '.signal_counts.pending')
FAILED=$(echo "$CI_JSON" | jq -r '.signal_counts.failed')

# Step 2: Decide what to do
# If CI is complete with no failures → success
# If CI is complete with failures → investigate and fix
# If CI is still running → sleep and re-check
```

## Decision Logic for Automated Loops

| ci_state | pending | failed | Action |
|----------|---------|--------|--------|
| `*FINISHED*` or `*PASSED*` | 0 | 0 | **Success** — all signals passed |
| `*FINISHED*` | 0 | > 0 | **Fix needed** — run `analyze_ci_signals --triage` |
| `*IN_PROGRESS*` or similar | > 0 | any | **Wait** — sleep 60-120s, then re-check |
| `*IN_PROGRESS*` | 0 | any | **Wait** — signals not yet registered, sleep 30-60s |
| `TEST_DEFERRED` | any | any | **Trigger** — run `start_deferred_tests`, then wait |

## Example: Full Polling Loop

```bash
#!/bin/bash
DIFF="D12345"
MAX_ATTEMPTS=30
SLEEP_SECONDS=120

for i in $(seq 1 $MAX_ATTEMPTS); do
  echo "=== Check $i/$MAX_ATTEMPTS ==="
  CI_JSON=$(scripts/check_ci_state "$DIFF" 2>/dev/null)
  CI_STATE=$(echo "$CI_JSON" | jq -r '.ci_state')
  PENDING=$(echo "$CI_JSON" | jq -r '.signal_counts.pending')
  FAILED=$(echo "$CI_JSON" | jq -r '.signal_counts.failed')
  TOTAL=$(echo "$CI_JSON" | jq -r '.signal_counts.total')

  echo "State: $CI_STATE | Total: $TOTAL | Pending: $PENDING | Failed: $FAILED"

  # Check if CI is complete
  case "$CI_STATE" in
    *FINISHED*|*PASSED*|*ALL_CLEAR*|*COMPLETED*)
      if [ "$FAILED" -gt 0 ]; then
        echo "CI complete with $FAILED failure(s). Investigating..."
        scripts/analyze_ci_signals "$DIFF" --triage
        exit 1
      else
        echo "CI complete. All $TOTAL signals passed!"
        exit 0
      fi
      ;;
    TEST_DEFERRED)
      echo "Tests deferred. Triggering..."
      scripts/start_deferred_tests "$DIFF" 2>&1
      ;;
  esac

  echo "CI still running. Sleeping ${SLEEP_SECONDS}s..."
  sleep "$SLEEP_SECONDS"
done

echo "Timed out after $MAX_ATTEMPTS checks."
exit 2
```

## Key Guidelines for Agents in Eval Loops

1. **Always check `ci_state` AND `signal_counts.pending` before declaring success or failure.** Zero failures does NOT mean CI passed — it may mean signals haven't arrived yet.
2. **Use `check_ci_state` for polling** (fastest, <5s). Only use `analyze_ci_signals` when you need failure details.
3. **Sleep 60-120 seconds between checks.** CI signals take time to complete. Checking more frequently wastes resources.
4. **Match on `ci_state` patterns, not exact values.** Use `*FINISHED*`, `*PASSED*` patterns since exact enum values may vary.
5. **When `pending == 0` and `total == 0` with a non-DEFERRED state**, the signal system may still be initializing. Wait and re-check.
6. **`info` signals are informational and non-blocking.** They don't indicate failures. Focus on `failed` and `warning` for actionable issues.
