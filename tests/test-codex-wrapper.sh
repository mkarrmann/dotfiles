#!/usr/bin/env bash
set -euo pipefail

# Regression coverage for HACK(omnigent-sdk-codex-cwd). Remove the workspace
# validation/chdir cases when the cleanup checklist in
# docs/omnigent-codecompanion-adapter.md is completed.

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
WRAPPER="$ROOT/bin/codex"
# Resolved physically, like ROOT above. The wrapper enters the workspace with
# `cd -P`, so the cwd it reports is the physical path; on macOS mktemp hands
# back /var/... while /var is a symlink to /private/var, and the exact-match
# assertion below compares the two.
TMP="$(cd -- "$(mktemp -d)" && pwd -P)"
trap 'rm -rf "$TMP"' EXIT

# The production wrapper ultimately execs a resolved launcher (see the
# resolution cases below). Exercise its workspace validation without starting a
# real app-server; every failure below occurs before that exec boundary.
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

# Launcher resolution. CODEX_LAUNCHER_CANDIDATES stands in for the built-in
# system list so these run identically on a Meta devserver (where
# /usr/local/bin/codex exists) and on a machine where it does not.
mkdir "$TMP/first" "$TMP/second" "$TMP/empty"
for slot in first second; do
  # shellcheck disable=SC2016
  printf '%s\n' '#!/usr/bin/env bash' "printf 'launcher=$slot\\n'" >"$TMP/$slot/codex"
  chmod +x "$TMP/$slot/codex"
done

# Earlier candidates win, which is what keeps Meta's provisioned
# /usr/local/bin/codex ahead of any npm-global install on work machines.
CODEX_LAUNCHER_CANDIDATES="$TMP/first/codex:$TMP/second/codex" \
  "$WRAPPER" --version >"$TMP/out"
grep -Fx 'launcher=first' "$TMP/out" >/dev/null

# Missing candidates are skipped rather than fatal, so one layout's absence
# falls through to the next.
CODEX_LAUNCHER_CANDIDATES="$TMP/empty/codex:$TMP/second/codex" \
  "$WRAPPER" --version >"$TMP/out"
grep -Fx 'launcher=second' "$TMP/out" >/dev/null

# With no candidate present, PATH supplies the launcher.
PATH="$TMP/second:$PATH" CODEX_LAUNCHER_CANDIDATES="$TMP/empty/codex" \
  "$WRAPPER" --version >"$TMP/out"
grep -Fx 'launcher=second' "$TMP/out" >/dev/null

# The PATH fallback must never exec the wrapper itself, directly or through the
# ~/bin symlink, or codex would fork-bomb instead of starting. The wrapper is
# first on PATH here, so resolving past it to the fake is the guard working.
mkdir "$TMP/self"
ln -s "$WRAPPER" "$TMP/self/codex"
PATH="$TMP/self:$TMP/second:$PATH" CODEX_LAUNCHER_CANDIDATES="$TMP/empty/codex" \
  "$WRAPPER" --version >"$TMP/out"
grep -Fx 'launcher=second' "$TMP/out" >/dev/null

# Non-app-server invocations must remain usable outside Omnigent.
"$WRAPPER" --version | grep -E '^codex-cli [0-9]+' >/dev/null

echo "codex wrapper tests passed"
