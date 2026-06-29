#!/bin/bash
# regression_test_color_palette.sh — anti-regression for the colorblind HARD RULE.
# (a) Verifies the palette spec is structurally sound (required hex codes,
#     required sections present).
# (b) Verifies lint_rule.sh colorblind correctly clears the skill itself AND
#     correctly catches a seeded green leak in a synthetic directory.
set -uo pipefail

PALETTE=~/.claude/skills/deep-research/references/color_palette.md
LINT=~/.claude/skills/deep-research/references/scripts/lint_rule.sh
SKILL=~/.claude/skills/deep-research

# (a) Spec structural assertions
if [[ ! -f "$PALETTE" ]]; then
  echo "FAIL: color_palette.md missing"; exit 1
fi
for hex in '#0066cc' '#d9a600' '#d96600' '#d90000' '#8c8c8c'; do
  if ! grep -q "$hex" "$PALETTE"; then
    echo "FAIL: missing required hex $hex in palette"; exit 1
  fi
done
if ! grep -q '^## NEVER USE' "$PALETTE"; then
  echo "FAIL: '## NEVER USE' section missing from palette"; exit 1
fi
if ! grep -q '^## Validation regex' "$PALETTE"; then
  echo "FAIL: '## Validation regex' section missing from palette"; exit 1
fi

# (b) Lint script behavior
if [[ ! -x "$LINT" ]]; then
  chmod +x "$LINT"
fi

# Positive case: the deep-research skill itself must lint clean.
if ! "$LINT" colorblind "$SKILL" >/dev/null 2>&1; then
  echo "FAIL: lint_rule.sh reported violations on the deep-research skill itself:"
  "$LINT" colorblind "$SKILL"
  exit 1
fi

# Negative case: a synthetic green leak in a temp dir must be caught.
TMPDIR=$(mktemp -d /tmp/cb-lint-test.XXXXXX)
trap 'rm -rf "$TMPDIR"' EXIT
cat > "$TMPDIR/fake_renderer.py" <<'EOF'
PASS_COLOR = "#00ff00"
SPAN = '<span style="color: green; font-weight: bold;">[PASS]</span>'
EOF
if "$LINT" colorblind "$TMPDIR" >/dev/null 2>&1; then
  echo "FAIL: lint_rule.sh did NOT catch the seeded green leak in $TMPDIR"
  ls -la "$TMPDIR"
  exit 1
fi

echo "PASS: color_palette compliant + lint_rule.sh behavior verified"
