#!/bin/bash
# regression_test_symbol_in_code.sh — heuristic test for symbol_in_code.py
set -uo pipefail

SCRIPT=~/.claude/skills/deep-research/references/scripts/symbol_in_code.py
TMP=/tmp/sic_$$.tmp

fail=0
pass=0

run_case() {
  local desc="$1"; local file_content="$2"; local line="$3"; local symbol="$4"; local expected="$5"
  local ext="${6:-.py}"
  echo -n "$file_content" > "${TMP}${ext}"
  python3 "$SCRIPT" "${TMP}${ext}" "$line" "$symbol" >/dev/null 2>&1
  rc=$?
  if [[ "$rc" == "$expected" ]]; then
    echo "PASS: $desc (rc=$rc)"
    pass=$((pass + 1))
  else
    echo "FAIL: $desc expected $expected got $rc"
    fail=$((fail + 1))
  fi
  rm -f "${TMP}${ext}"
}

# Case 1: bare code definition → exit 0
run_case "py def line is code" 'def get_token():' 1 get_token 0 .py
# Case 2: python comment → exit 1
run_case "py comment line" '# get_token is here' 1 get_token 1 .py
# Case 3: in string literal → exit 1
run_case "py string literal only" 'x = "get_token name"' 1 get_token 1 .py
# Case 4: in string AND in code → exit 0 (over-permissive by design)
run_case "py mixed string + call" 'get_token("the get_token call")' 1 get_token 0 .py
# Case 5: cpp comment → exit 1
run_case "cpp // comment" '// call get_token here' 1 get_token 1 .cpp
# Case 6: cpp code → exit 0
run_case "cpp function def" 'int get_token() { return 0; }' 1 get_token 0 .cpp

if [[ $fail -eq 0 ]]; then
  echo "PASS: symbol_in_code $pass/$((pass + fail)) cases"
  exit 0
else
  echo "FAIL: symbol_in_code $fail failures, $pass passes"
  exit 1
fi
