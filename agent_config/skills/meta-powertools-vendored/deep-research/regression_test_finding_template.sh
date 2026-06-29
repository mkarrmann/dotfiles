#!/bin/bash
# regression_test_finding_template.sh — verify Finding JSON skeleton is valid.
set -euo pipefail

TEMPLATE=~/.claude/skills/deep-research/references/scripts/finding_template.sh
if ! command -v jq >/dev/null; then
  echo "FAIL: jq missing"; exit 1
fi
if [[ ! -x "$TEMPLATE" ]]; then
  chmod +x "$TEMPLATE"
fi

out=$(bash "$TEMPLATE" F-99 researcher-test "Test claim with spaces" extracted)

echo "$out" | jq -e '.id == "F-99"' >/dev/null || { echo "FAIL: id mismatch in: $out"; exit 1; }
echo "$out" | jq -e '.agent == "researcher-test"' >/dev/null || { echo "FAIL: agent mismatch"; exit 1; }
echo "$out" | jq -e '.source_kind == "extracted"' >/dev/null || { echo "FAIL: source_kind mismatch"; exit 1; }
echo "$out" | jq -e '.evidence | type == "array"' >/dev/null || { echo "FAIL: evidence not array"; exit 1; }

echo "PASS: finding_template emits valid JSON"
