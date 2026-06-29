#!/bin/bash
# regression_test_symbol_patterns.sh — verify language-aware ripgrep patterns.
set -uo pipefail

SCRIPT=~/.claude/skills/deep-research/references/scripts/symbol_patterns.sh
fail=0
pass=0

expect() {
  local desc="$1"; local kind="$2"; local lang="$3"; local sym="$4"; local exp_pattern="$5"; local exp_rc="$6"
  actual=$(bash "$SCRIPT" "$kind" "$lang" "$sym" 2>/dev/null)
  rc=$?
  if [[ "$rc" == "$exp_rc" && "$actual" == "$exp_pattern" ]]; then
    echo "PASS: $desc"; pass=$((pass + 1))
  else
    echo "FAIL: $desc"; echo "  expected: '$exp_pattern' rc=$exp_rc"; echo "  actual:   '$actual' rc=$rc"; fail=$((fail + 1))
  fi
}

expect "py function" function python get_token '^\s*(async\s+)?def\s+get_token\b' 0
expect "cpp function" function cpp myFunc '\b\w[\w\s\*&<>:]*\bmyFunc\s*\(' 0
expect "ts function" function ts foo '(function\s+foo\b|const\s+foo\s*=\s*(async\s*)?\(.*?\)\s*=>|foo\s*:\s*(async\s*)?function)' 0
expect "swift function" function swift bar '\bfunc\s+bar\b' 0
expect "rust function" function rust baz '\bfn\s+baz\b' 0
expect "py class" class python AuthHandler '^\s*class\s+AuthHandler\b' 0
expect "py const" const python TIMEOUT '^TIMEOUT\s*=' 0
expect "unknown lang exits 1" function tla someFunc '' 1

if [[ $fail -eq 0 ]]; then
  echo "PASS: symbol_patterns $pass/$((pass + fail)) cases"
  exit 0
else
  echo "FAIL: symbol_patterns $fail failures, $pass passes"
  exit 1
fi
