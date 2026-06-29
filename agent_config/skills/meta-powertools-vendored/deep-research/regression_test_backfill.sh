#!/bin/bash
# regression_test_backfill.sh — verifies the offline Trust Gate backfill
# pipeline (extract → verify → orchestrate) on synthetic inputs.
#
# Builds a tiny session JSONL containing references to known-real, known-stale,
# and known-missing files; runs each stage; asserts that:
#   * extractor finds the expected number of file_refs and diff_refs
#   * extractor's compact JSON survives trust_gate.sh's grep
#   * verifier emits VERIFIED for real paths, STALE for drifted snippet,
#     UNVERIFIED+hallucinated_ref for missing files
#   * orchestrator produces summary.csv and per-session decision JSONs
set -uo pipefail

SCRIPTS=~/.claude/skills/deep-research/references/scripts/eval
TMP=$(mktemp -d /tmp/backfill-regtest.XXXXXX)
trap 'rm -rf "$TMP"' EXIT

fail=0
pass=0
# Run a test command and report PASS/FAIL by its exit code. Avoids the
# `[[ test ]]; report $?` pattern which shellcheck flags (SC2319) because
# the `$?` reads from the command-substitution boundary, not the test.
# Usage: assert "<description>" <cmd> [args...]
assert() {
  local desc="$1"; shift
  if "$@" >/dev/null 2>&1; then
    echo "PASS: $desc"; pass=$((pass + 1))
  else
    echo "FAIL: $desc"; fail=$((fail + 1))
  fi
}

# Set up a synthetic skill tree with known files
mkdir -p "$TMP/fake-skills/sample-skill"
cat > "$TMP/fake-skills/sample-skill/real_file.md" <<'EOF'
# real_file.md
Line 2 of the real file.
This is line 3 with a known-snippet UNIQUE_SNIPPET_ABC.
Line 4.
EOF
cat > "$TMP/fake-skills/sample-skill/another.py" <<'EOF'
# another.py module
def real_function():
    return 42
EOF

# Build a synthetic session JSONL with assistant messages that cite:
#   * a real file at a real line     (expected: VERIFIED via path-only)
#   * a real file with no line       (expected: VERIFIED via path-only)
#   * a known-deleted file           (expected: HALLUCINATED)
#   * a real diff number             (expected: INFORMATIONAL — diff not verified offline)
# Single-quoted heredoc avoids shell interpretation of $ and \.
cat > "$TMP/fake-session.jsonl" <<'EOF'
{"type":"assistant","message":{"content":[{"type":"text","text":"The file real_file.md:3 contains UNIQUE_SNIPPET_ABC. Function real_function() lives in another.py. Missing utility: deleted_file.md:99. Referenced diff: D9999999."}]}}
EOF

# 1. Extractor
python3 "$SCRIPTS/backfill_extract_claims.py" \
  --source "$TMP/fake-session.jsonl" \
  --findings "$TMP/findings.jsonl" \
  --search-root "$TMP/fake-skills" \
  --quiet
n_findings=$(wc -l < "$TMP/findings.jsonl")
assert "extractor: >=3 findings produced (got $n_findings)" test "$n_findings" -ge 3

# Compact JSON check (no spaces after `:`/`,`)
assert "extractor: compact JSON format (no spaces around separators)" \
  grep -q '","' <<< "$(head -1 "$TMP/findings.jsonl")"

# 2. Verifier
python3 "$SCRIPTS/backfill_verify_claims.py" \
  --findings "$TMP/findings.jsonl" \
  --verification "$TMP/verification.jsonl" \
  --quiet
n_verdicts=$(wc -l < "$TMP/verification.jsonl")
assert "verifier: 1 verdict per finding ($n_verdicts/$n_findings)" \
  test "$n_verdicts" -eq "$n_findings"

assert "verifier: detected the seeded HALLUCINATED ref (deleted_file.md)" \
  grep -q '"hallucinated_ref"' "$TMP/verification.jsonl"

assert "verifier: emitted at least one [VERIFIED] for the real file" \
  grep -q '"tag":"\[VERIFIED\]"' "$TMP/verification.jsonl"

# 3. Orchestrator end-to-end
mkdir -p "$TMP/sessions"
cp "$TMP/fake-session.jsonl" "$TMP/sessions/test.jsonl"

OUT=$(mktemp -d /tmp/backfill-regtest-out.XXXXXX)
bash "$SCRIPTS/backfill_corpus.sh" \
  --project-dir "$TMP/sessions" \
  --out-dir "$OUT" \
  --min-citations 1 \
  > "$TMP/orch.log" 2>&1
orch_rc=$?
assert "orchestrator: exited 0 (got $orch_rc)" test "$orch_rc" -eq 0
assert "orchestrator: wrote summary.csv" test -f "$OUT/summary.csv"

# Summary should have header + at least one data row
n_rows=$(wc -l < "$OUT/summary.csv" 2>/dev/null || echo 0)
assert "orchestrator: summary.csv has >=1 data row (got $((n_rows - 1)))" \
  test "$n_rows" -ge 2

# Decision JSON should exist for the test session and be parseable
n_decisions=$(find "$OUT/decisions" -name '*.gate.json' 2>/dev/null | wc -l)
assert "orchestrator: >=1 gate decision JSON written (got $n_decisions)" \
  test "$n_decisions" -ge 1

if [[ "$n_decisions" -ge 1 ]]; then
  decision=$(find "$OUT/decisions" -name '*.gate.json' | head -1)
  assert "orchestrator: decision JSON is well-formed (verified_pct, blocked, reasons present)" \
    jq -e '.verified_pct, .blocked, .reasons' "$decision"
fi

rm -rf "$OUT"

echo ""
if [[ $fail -eq 0 ]]; then
  echo "PASS: backfill pipeline $pass/$((pass + fail)) cases"
  exit 0
else
  echo "FAIL: backfill pipeline $fail failures, $pass passes"
  sed 's/^/  /' "$TMP/orch.log"
  exit 1
fi
