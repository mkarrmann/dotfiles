#!/usr/bin/env bash
set -euo pipefail

# Regression coverage for HACK(omnigent-sdk-codex-cwd). Remove the workspace
# validation/chdir cases when the cleanup checklist in
# docs/omnigent-codecompanion-adapter.md is completed.

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
WRAPPER="$ROOT/bin/codex"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# The production wrapper ultimately execs /usr/local/bin/codex. Exercise its
# workspace validation without starting a real app-server; every failure below
# occurs before that exec boundary.
if OMNIGENT_RUNNER_WORKSPACE=relative "$WRAPPER" app-server >"$TMP/out" 2>"$TMP/err"; then
  echo "relative runner workspace unexpectedly succeeded" >&2
  exit 1
fi
grep -F "must be absolute" "$TMP/err" >/dev/null

if OMNIGENT_RUNNER_WORKSPACE="$TMP/missing" "$WRAPPER" app-server >"$TMP/out" 2>"$TMP/err"; then
  echo "missing runner workspace unexpectedly succeeded" >&2
  exit 1
fi
grep -F "is not a directory" "$TMP/err" >/dev/null

mkdir "$TMP/workspace"
# The single-quoted lines are the literal contents of the generated fake.
# shellcheck disable=SC2016
printf '%s\n' \
  '#!/usr/bin/env bash' \
  'printf "cwd=%s\n" "$PWD"' \
  'printf "arg=%s\n" "$@"' >"$TMP/fake-codex"
chmod +x "$TMP/fake-codex"
OMNIGENT_RUNNER_WORKSPACE="$TMP/workspace" \
  OMNIGENT_CODEX_REAL_PATH="$TMP/fake-codex" \
  "$WRAPPER" app-server -c 'model="test"' --flag >"$TMP/out"
grep -Fx "cwd=$TMP/workspace" "$TMP/out" >/dev/null
# The wrapper keeps config overrides in global scope for Meta's launcher.
grep -Fx 'arg=-c' "$TMP/out" >/dev/null
grep -Fx 'arg=model="test"' "$TMP/out" >/dev/null
grep -Fx 'arg=app-server' "$TMP/out" >/dev/null
grep -Fx 'arg=--flag' "$TMP/out" >/dev/null

# Non-app-server invocations must remain usable outside Omnigent.
"$WRAPPER" --version | grep -E '^codex-cli [0-9]+' >/dev/null

echo "codex wrapper tests passed"
