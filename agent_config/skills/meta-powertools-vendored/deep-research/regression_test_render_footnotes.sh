#!/bin/bash
# regression_test_render_footnotes.sh — render_footnotes.py functional + colorblind tests.
set -uo pipefail

SCRIPT=~/.claude/skills/deep-research/references/scripts/render_footnotes.py
fail=0
pass=0

TMP=$(mktemp /tmp/rftest.XXXXXX.md)

cleanup() { rm -f "$TMP" /tmp/rftest_out.txt; }
trap cleanup EXIT

cat > "$TMP" <<EOF
## Findings

Token storage uses Configerator[^F1], introduced via D101234[^F2].
Cache TTL is hard-coded[^F3].

[^F1]: [VERIFIED] handler.py:85
[^F2]: [LANDED] D101234
[^F3]: [UNVERIFIED] no constant found
EOF

python3 "$SCRIPT" "$TMP" > /tmp/rftest_out.txt

# Case 1: HTML <sup> tags created
if grep -q '<sup>\[<a href="#cite-1">1</a>\]</sup>' /tmp/rftest_out.txt; then
  echo "PASS: case 1 sup tags created"; pass=$((pass + 1))
else
  echo "FAIL: case 1 missing sup tags"; fail=$((fail + 1))
fi

# Case 2: Citations section appended
if grep -q '## Citations' /tmp/rftest_out.txt && grep -q '<a id="cite-1"></a>' /tmp/rftest_out.txt; then
  echo "PASS: case 2 Citations section + anchors"; pass=$((pass + 1))
else
  echo "FAIL: case 2 missing Citations or anchors"; fail=$((fail + 1))
fi

# Case 3: VERIFIED chip colorized to blue (HEX #0066cc)
if grep -q 'color: #0066cc' /tmp/rftest_out.txt; then
  echo "PASS: case 3 VERIFIED is #0066cc blue"; pass=$((pass + 1))
else
  echo "FAIL: case 3 missing #0066cc"; fail=$((fail + 1))
fi

# Case 4: COLORBLIND HARD RULE — NO green anywhere
if grep -iE 'green|#00[8a-f][0-9a-f]00|#0f0|#00ff00' /tmp/rftest_out.txt >/dev/null 2>&1; then
  echo "FAIL: case 4 COLORBLIND VIOLATION — green found in output:"
  grep -inE 'green|#00[8a-f][0-9a-f]00|#0f0|#00ff00' /tmp/rftest_out.txt
  fail=$((fail + 1))
else
  echo "PASS: case 4 NO green in output (colorblind compliant)"; pass=$((pass + 1))
fi

# Case 5: --no-footnotes fallback path: no Citations section, but tags still colorized
python3 "$SCRIPT" "$TMP" --no-footnotes > /tmp/rftest_out.txt
if ! grep -q '## Citations' /tmp/rftest_out.txt && grep -q 'color: #0066cc' /tmp/rftest_out.txt; then
  echo "PASS: case 5 --no-footnotes degrades correctly"; pass=$((pass + 1))
else
  echo "FAIL: case 5 fallback wrong"; fail=$((fail + 1))
fi

if [[ $fail -eq 0 ]]; then
  echo "PASS: render_footnotes $pass/$((pass + fail)) cases"
  exit 0
else
  echo "FAIL: render_footnotes $fail failures, $pass passes"
  exit 1
fi
