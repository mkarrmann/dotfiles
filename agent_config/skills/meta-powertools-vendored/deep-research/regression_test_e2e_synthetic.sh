#!/bin/bash
# regression_test_e2e_synthetic.sh — e2e smoke test of trust_gate + render pipeline.
set -uo pipefail

TEAM=/tmp/e2e_test_team_$$
REPORT=/tmp/e2e_test_report_$$.md
RENDERED=/tmp/e2e_test_rendered_$$.md
rm -rf "$TEAM"; mkdir -p "$TEAM"
export TRUST_GATE_TEAM_DIR="$TEAM"

cleanup() { rm -rf "$TEAM" "$REPORT" "$RENDERED" /tmp/gate_decision_e2e_test_report_*.json; }
trap cleanup EXIT

# Synthetic verification: 5 findings, 4 verified, 1 stale (above 0.85 hard-block, below 0.95 → soft block)
cat > "$TEAM/verification.jsonl" <<EOF
{"finding_id":"F-1","tag":"[VERIFIED]","reasons":["sha_match"],"source_kind":"native"}
{"finding_id":"F-2","tag":"[VERIFIED]","reasons":["sha_match"],"source_kind":"native"}
{"finding_id":"F-3","tag":"[VERIFIED]","reasons":["sha_match"],"source_kind":"extracted"}
{"finding_id":"F-4","tag":"[VERIFIED]","reasons":["sha_match"],"source_kind":"native"}
{"finding_id":"F-5","tag":"[STALE]","reasons":["snippet_drift"],"source_kind":"native"}
EOF

cat > "$REPORT" <<EOF
## Findings

Token storage uses Configerator[^F1] introduced via D101234[^F2].
Cache TTL hard-coded[^F3]. Auth handler uses fallback[^F4]. Old comment refers to deprecated module[^F5].

[^F1]: [VERIFIED] handler.py:85
[^F2]: [LANDED] D101234
[^F3]: [VERIFIED] cache.py:42
[^F4]: [VERIFIED] handler.py:120
[^F5]: [STALE] deprecated_module.py:1
EOF

# Step 1: Run gate (eval mode so it doesn't exit 1)
bash ~/.claude/skills/deep-research/references/scripts/trust_gate.sh "$REPORT" --eval-mode > /tmp/e2e_gate_out.txt 2>&1 || true
decision_path=$(cat /tmp/e2e_gate_out.txt | tail -c 200 | grep -oE '/tmp/gate_decision_[^[:space:]"]*\.json' | tail -1)
[[ -z "$decision_path" ]] && decision_path=$(ls -t /tmp/gate_decision_e2e_test_report_*.json 2>/dev/null | head -1)
if [[ ! -f "$decision_path" ]]; then
  echo "FAIL: gate did not emit gate_decision JSON"
  cat /tmp/e2e_gate_out.txt
  rm -f /tmp/e2e_gate_out.txt
  exit 1
fi

verified_pct=$(jq -r '.verified_pct' "$decision_path")
blocked=$(jq -r '.blocked' "$decision_path")

# 4/5 = 0.8 → below 0.85 hard-block threshold → blocked=true
if [[ "$blocked" != "true" ]]; then
  echo "FAIL: expected blocked=true (4/5=0.80 < 0.85) got blocked=$blocked verified_pct=$verified_pct"
  cat "$decision_path"
  rm -f /tmp/e2e_gate_out.txt
  exit 1
fi
echo "OK gate verdict: blocked=$blocked verified_pct=$verified_pct"

# Step 2: Render footnotes (regardless of block — for testing purposes)
python3 ~/.claude/skills/deep-research/references/scripts/render_footnotes.py "$REPORT" > "$RENDERED"

# Verify rendered output: HTML sup + Citations + colorized + NO green
for required in '<sup>' '## Citations' '<a id="cite-1">' 'color: #0066cc' 'color: #d9a600'; do
  if ! grep -q "$required" "$RENDERED"; then
    echo "FAIL: rendered output missing '$required'"
    cat "$RENDERED"
    rm -f /tmp/e2e_gate_out.txt
    exit 1
  fi
done

if grep -iE 'green|#00[8a-f][0-9a-f]00|#0f0|#00ff00' "$RENDERED" >/dev/null 2>&1; then
  echo "FAIL: COLORBLIND VIOLATION in rendered output"
  grep -inE 'green|#00[8a-f][0-9a-f]00|#0f0|#00ff00' "$RENDERED"
  rm -f /tmp/e2e_gate_out.txt
  exit 1
fi

rm -f /tmp/e2e_gate_out.txt
echo "PASS: e2e_synthetic full pipeline"
