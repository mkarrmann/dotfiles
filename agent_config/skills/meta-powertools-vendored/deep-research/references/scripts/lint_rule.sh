#!/bin/bash
# lint_rule.sh — Multi-rule HARD-RULE lint dispatcher for the deep-research skill.
#
# Today supports rule_name = "colorblind" (colorblind-accessible palette; PASS=blue,
# NEVER green). Future rules ("no_auto_publish", "symbol_safety", ...) slot in
# as additional case branches without a second script.
#
# Single source of truth for: forbid patterns, file-level allowlist, line-level
# narrative-context keyword exclusions, scan extensions. Consumed by
# regression_test_color_palette.sh AND the §8 build gate so they cannot drift.
#
# Usage: lint_rule.sh <rule_name> <skill_root>
#   rule_name:   currently "colorblind" (only supported value)
#   skill_root:  directory to scan (e.g. ~/.claude/skills/deep-research)
# Exit:
#   0  PASS, no violations.
#   1  FAIL, one or more violations found.
#   2  Usage error.
#   3  Internal error (skill_root not a directory).
#
# Output: "VIOLATION: <relpath>:<lineno>:<text>" per match, then PASS/FAIL summary.

set -uo pipefail

RULE="${1:-}"
SKILL_ROOT_ARG="${2:-}"
if [[ -z "$RULE" || -z "$SKILL_ROOT_ARG" ]]; then
  echo "usage: $0 <rule_name> <skill_root>" >&2
  exit 2
fi
if [[ ! -d "$SKILL_ROOT_ARG" ]]; then
  echo "ERROR: not a directory: $SKILL_ROOT_ARG" >&2
  exit 3
fi
# Canonicalize: resolve symlinks so allowlist comparison is on real paths.
SKILL_ROOT="$(cd "$SKILL_ROOT_ARG" && pwd -P)"

case "$RULE" in
  colorblind)
    # Forbidden green-related values. Extend here, not in callers.
    # Catches: bare names (green/lime/forestgreen/seagreen/darkgreen), hex shades
    # in the green band, and rgb(0,255|128,0).
    FORBID='\b(green|lime|forestgreen|seagreen|darkgreen)\b|#(00[8a-f][0-9a-f]00|0f0\b|00ff00\b)|rgb\([[:space:]]*0[[:space:]]*,[[:space:]]*(255|128)[[:space:]]*,[[:space:]]*0[[:space:]]*\)'
    # Files exempt from scan ENTIRELY (rule spec, lint script itself, colorblind tests).
    # Match is exact relpath from canonicalized skill root.
    ALLOWLIST_FILES=(
      "references/color_palette.md"
      "references/scripts/lint_rule.sh"
      "regression_test_color_palette.sh"
      "regression_test_render_footnotes.sh"
      "regression_test_e2e_synthetic.sh"
    )
    # Line-level keywords: narrative mentions in production files pass when present.
    # Catches "PASS=blue, NEVER green" in SKILL.md and "(NEVER green)" doc comments.
    LINE_EXCLUDE='NEVER|forbid|colorblind|auto-fail|MUST NOT'
    # Extensions to scan. Add new leak-surface extensions here.
    EXTENSIONS=("md" "sh" "py" "html" "css" "json" "yaml" "yml" "toml" "txt")
    ;;
  *)
    echo "ERROR: unknown rule '$RULE'. Supported: colorblind" >&2
    exit 2
    ;;
esac

# Build grep --include args from EXTENSIONS
INCLUDES=()
for ext in "${EXTENSIONS[@]}"; do INCLUDES+=("--include=*.${ext}"); done

is_allowlisted() {
  local rel="$1"
  local af
  for af in "${ALLOWLIST_FILES[@]}"; do
    [[ "$rel" == "$af" ]] && return 0
  done
  return 1
}

violations=0
# grep -rEni: recursive, ERE, line-numbered, case-insensitive
while IFS= read -r match; do
  # match format: <abspath>:<lineno>:<text>
  fp="${match%%:*}"
  rel="${fp#$SKILL_ROOT/}"
  if is_allowlisted "$rel"; then
    continue
  fi
  rest="${match#*:}"          # <lineno>:<text>
  text_only="${rest#*:}"      # <text> only — used for keyword check
  if echo "$text_only" | grep -iEq -- "$LINE_EXCLUDE"; then
    continue
  fi
  echo "VIOLATION: $rel:$rest"
  violations=$((violations + 1))
done < <(grep -rEni "$FORBID" "$SKILL_ROOT" "${INCLUDES[@]}" 2>/dev/null || true)

if [[ $violations -gt 0 ]]; then
  echo "FAIL: $violations $RULE violation(s)"
  exit 1
fi
echo "PASS: 0 $RULE violations"
exit 0
