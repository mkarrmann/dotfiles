#!/bin/bash
# regression_test_gate_decision.sh — verify gate decision matrix.
set -uo pipefail

SCRIPT=~/.claude/skills/deep-research/references/scripts/trust_gate.sh
TEAM=/tmp/trust_gate_test_team
rm -rf "$TEAM"; mkdir -p "$TEAM"
export TRUST_GATE_TEAM_DIR="$TEAM"

fail=0
pass=0

run_scenario() {
  local desc="$1"; local report_anchors="$2"; local verification="$3"; local expected_blocked="$4"
  cat > /tmp/gate_test_report.md <<EOF
## Findings
$report_anchors
EOF
  echo "$verification" > "$TEAM/verification.jsonl"

  bash "$SCRIPT" /tmp/gate_test_report.md --eval-mode > /tmp/gate_test_out.txt 2>&1 || true
  out_path=$(cat /tmp/gate_test_out.txt | tr -d '\n' | tail -c 200 | grep -oE '/tmp/gate_decision_[^"[:space:]]*\.json' | tail -1)
  [[ -z "$out_path" ]] && out_path=$(ls -t /tmp/gate_decision_*.json 2>/dev/null | head -1)
  if [[ ! -f "$out_path" ]]; then
    echo "FAIL: $desc no gate_decision file produced"; fail=$((fail + 1)); return
  fi
  actual_blocked=$(jq -r '.blocked' "$out_path")
  if [[ "$actual_blocked" == "$expected_blocked" ]]; then
    echo "PASS: $desc (blocked=$actual_blocked)"; pass=$((pass + 1))
  else
    echo "FAIL: $desc expected blocked=$expected_blocked got blocked=$actual_blocked"
    cat "$out_path"
    fail=$((fail + 1))
  fi
  rm -f "$out_path"
}

# Scenario 1: 100% verified → not blocked
run_scenario "100pct verified PASS" '[F-1] [F-2]' \
  '{"finding_id":"F-1","tag":"[VERIFIED]","reasons":[]}
{"finding_id":"F-2","tag":"[VERIFIED]","reasons":[]}' "false"

# Scenario 2: 50% verified → HARD BLOCK
run_scenario "50pct hard block" '[F-1] [F-2]' \
  '{"finding_id":"F-1","tag":"[VERIFIED]","reasons":[]}
{"finding_id":"F-2","tag":"[UNVERIFIED]","reasons":["no_evidence"]}' "true"

# Scenario 3: 1 hallucinated ref → HARD BLOCK regardless of percent
run_scenario "hallucinated hard block" '[F-1] [F-2] [F-3] [F-4] [F-5]' \
  '{"finding_id":"F-1","tag":"[VERIFIED]","reasons":[]}
{"finding_id":"F-2","tag":"[VERIFIED]","reasons":[]}
{"finding_id":"F-3","tag":"[VERIFIED]","reasons":[]}
{"finding_id":"F-4","tag":"[VERIFIED]","reasons":[]}
{"finding_id":"F-5","tag":"[UNVERIFIED]","reasons":["hallucinated_ref"]}' "true"

# Scenario 4: 90% verified (in soft-block band) → blocked (soft block still blocks delivery)
run_scenario "90pct soft block" '[F-1] [F-2] [F-3] [F-4] [F-5] [F-6] [F-7] [F-8] [F-9] [F-10]' \
  '{"finding_id":"F-1","tag":"[VERIFIED]","reasons":[]}
{"finding_id":"F-2","tag":"[VERIFIED]","reasons":[]}
{"finding_id":"F-3","tag":"[VERIFIED]","reasons":[]}
{"finding_id":"F-4","tag":"[VERIFIED]","reasons":[]}
{"finding_id":"F-5","tag":"[VERIFIED]","reasons":[]}
{"finding_id":"F-6","tag":"[VERIFIED]","reasons":[]}
{"finding_id":"F-7","tag":"[VERIFIED]","reasons":[]}
{"finding_id":"F-8","tag":"[VERIFIED]","reasons":[]}
{"finding_id":"F-9","tag":"[VERIFIED]","reasons":[]}
{"finding_id":"F-10","tag":"[STALE]","reasons":["snippet_drift"]}' "true"

rm -rf "$TEAM" /tmp/gate_test_report.md /tmp/gate_test_out.txt
if [[ $fail -eq 0 ]]; then
  echo "PASS: gate_decision $pass/$((pass + fail)) cases"
  exit 0
else
  echo "FAIL: gate_decision $fail failures, $pass passes"
  exit 1
fi
