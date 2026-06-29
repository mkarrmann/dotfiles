#!/bin/bash
# trust_gate.sh — Compute Trust Gate verdict for a draft research report.
# Usage: trust_gate.sh <draft_report.md> [--eval-mode] [--no-agent] [--best-effort]
#
# Inputs:
#   $1               draft report markdown (must contain [F-N] anchors)
#   --eval-mode      no actual block; just write gate_decision.json
#   --no-agent       bash-only checks (degraded mode if verifier crashed)
#   --best-effort    proceed even if verification.jsonl is incomplete
#
# Outputs:
#   /tmp/gate_decision_<basename>.json
#     {verified_pct, blocked, reasons[], failed_findings[], coverage, timestamp}
#
# Threshold: TRUST_GATE_THRESHOLD env var (default 0.95).
# Hallucinated refs → HARD BLOCK regardless of percent.
set -euo pipefail

REPORT="${1:?usage: $0 <draft_report.md> [--eval-mode] [--no-agent] [--best-effort]}"
shift || true
EVAL_MODE=0
NO_AGENT=0
BEST_EFFORT=0
for arg in "$@"; do
  case "$arg" in
    --eval-mode)   EVAL_MODE=1 ;;
    --no-agent)    NO_AGENT=1 ;;
    --best-effort) BEST_EFFORT=1 ;;
    *) echo "unknown flag: $arg" >&2; exit 2 ;;
  esac
done

# --no-agent is reserved for a future bash-only verification fallback (used when
# the trust-gate-verifier subagent is unavailable). The plumbing is in place;
# the bash-only path itself is not yet implemented. Warn loudly if a caller
# expects it to do something today.
if [[ $NO_AGENT -eq 1 ]]; then
  echo "WARN: --no-agent accepted but bash-only verifier not yet implemented; running standard path" >&2
fi

THRESHOLD="${TRUST_GATE_THRESHOLD:-0.95}"
HARD_BLOCK_THRESHOLD="0.85"

# Find the team directory by reading the report's front-matter or env var
TEAM_DIR="${TRUST_GATE_TEAM_DIR:-}"
if [[ -z "$TEAM_DIR" ]]; then
  # Try to derive from report filename: meta_research_report_<ts>.draft.md → look for matching team
  TEAM_DIR="$(ls -dt ~/.claude/teams/deep-research-* 2>/dev/null | head -1 || true)"
fi
if [[ -z "$TEAM_DIR" || ! -d "$TEAM_DIR" ]]; then
  echo "ERROR: cannot find team dir; set TRUST_GATE_TEAM_DIR" >&2
  exit 3
fi

VERIFICATION_JSONL="$TEAM_DIR/verification.jsonl"
if [[ ! -f "$VERIFICATION_JSONL" ]]; then
  if [[ $BEST_EFFORT -eq 1 ]]; then
    echo "WARN: verification.jsonl missing; emitting empty verdict" >&2
    VERIFICATION_JSONL=/dev/null
  else
    echo "ERROR: verification.jsonl missing at $VERIFICATION_JSONL" >&2
    exit 4
  fi
fi

# Extract referenced finding IDs from the report (anchors like [^F1], [^F-3], or [F-N])
REFS=$(grep -oE '\[F-?[0-9]+\]|\[\^F-?[0-9]+\]' "$REPORT" 2>/dev/null | \
       sed -E 's/\[?\^?F-?([0-9]+)\]?/F-\1/g' | sort -u)

if [[ -z "$REFS" ]]; then
  echo "WARN: no [F-N] anchors found in report" >&2
  BLOCKED=true
  VERIFIED_PCT="0"
  REASONS='["no_anchors_in_report"]'
  FAILED_FINDINGS='[]'
  COVERAGE="0"
else
  # Join refs against verification.jsonl using jq
  TOTAL=$(echo "$REFS" | wc -l)
  VERIFIED_COUNT=0
  HALLUCINATED_COUNT=0
  STALE_COUNT=0
  UNVERIFIED_COUNT=0
  FAILED=()

  for fid in $REFS; do
    verdict=$(grep "\"finding_id\":\"$fid\"" "$VERIFICATION_JSONL" 2>/dev/null | tail -1 || true)
    if [[ -z "$verdict" ]]; then
      UNVERIFIED_COUNT=$((UNVERIFIED_COUNT + 1))
      FAILED+=("$fid:no_verdict")
      continue
    fi
    tag=$(echo "$verdict" | jq -r '.tag')
    case "$tag" in
      '[VERIFIED]')     VERIFIED_COUNT=$((VERIFIED_COUNT + 1)) ;;
      '[STALE]')        STALE_COUNT=$((STALE_COUNT + 1));     FAILED+=("$fid:stale") ;;
      '[UNVERIFIED]')
        reason=$(echo "$verdict" | jq -r '.reasons[0] // "no_evidence"')
        UNVERIFIED_COUNT=$((UNVERIFIED_COUNT + 1))
        FAILED+=("$fid:$reason")
        if [[ "$reason" == "hallucinated_ref" ]]; then
          HALLUCINATED_COUNT=$((HALLUCINATED_COUNT + 1))
        fi
        ;;
      *) UNVERIFIED_COUNT=$((UNVERIFIED_COUNT + 1)); FAILED+=("$fid:unknown_tag:$tag") ;;
    esac
  done

  VERIFIED_PCT=$(awk -v v=$VERIFIED_COUNT -v t=$TOTAL 'BEGIN{ if (t==0) print "0"; else printf "%.4f", v/t }')

  # Decision logic
  REASONS_ARR=()
  if [[ $HALLUCINATED_COUNT -gt 0 ]]; then
    BLOCKED=true
    REASONS_ARR+=("hard_block_hallucinated_refs:$HALLUCINATED_COUNT")
  elif awk "BEGIN{exit !($VERIFIED_PCT < $HARD_BLOCK_THRESHOLD)}"; then
    BLOCKED=true
    REASONS_ARR+=("hard_block_below_${HARD_BLOCK_THRESHOLD}")
  elif awk "BEGIN{exit !($VERIFIED_PCT < $THRESHOLD)}"; then
    BLOCKED=true
    REASONS_ARR+=("soft_block_below_${THRESHOLD}")
  else
    BLOCKED=false
    REASONS_ARR+=("pass")
  fi

  REASONS=$(printf '%s\n' "${REASONS_ARR[@]}" | jq -Rsc 'split("\n") | map(select(length > 0))')
  FAILED_FINDINGS=$(printf '%s\n' "${FAILED[@]}" | jq -Rsc 'split("\n") | map(select(length > 0))')

  # Coverage = verified + stale (i.e., findings the verifier processed) / total refs
  PROCESSED=$((VERIFIED_COUNT + STALE_COUNT + UNVERIFIED_COUNT))
  COVERAGE=$(awk -v p=$PROCESSED -v t=$TOTAL 'BEGIN{ if (t==0) print "0"; else printf "%.4f", p/t }')
fi

TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
OUTPUT="/tmp/gate_decision_$(basename "$REPORT" .md).json"

jq -n \
  --argjson verified_pct "$VERIFIED_PCT" \
  --argjson blocked "$BLOCKED" \
  --argjson reasons "$REASONS" \
  --argjson failed_findings "$FAILED_FINDINGS" \
  --argjson coverage "$COVERAGE" \
  --arg timestamp "$TIMESTAMP" \
  --arg threshold "$THRESHOLD" \
  --arg report "$REPORT" \
  '{verified_pct: $verified_pct, blocked: $blocked, reasons: $reasons,
    failed_findings: $failed_findings, coverage: $coverage,
    threshold: ($threshold|tonumber), timestamp: $timestamp, report: $report}' \
  > "$OUTPUT"

if [[ $EVAL_MODE -eq 0 ]]; then
  cat "$OUTPUT"
  if [[ "$BLOCKED" == "true" ]]; then
    exit 1
  fi
else
  echo "$OUTPUT"
fi
