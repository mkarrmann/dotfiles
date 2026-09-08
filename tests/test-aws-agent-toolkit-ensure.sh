#!/usr/bin/env bash
set -euo pipefail

# Coverage for bin/aws-agent-toolkit-ensure. The properties that matter: a
# machine with no AWS session must exit 0 having touched nothing (init.sh runs
# unattended, and "not logged in yet" is the expected state of a fresh box, not
# a failure); the installer is invoked only when `aws` is absent; and the
# toolkit command is pinned to us-east-1 with the requested profile.
#
# `aws` and `curl` are stubbed throughout: this must never reach the network or
# the developer's real ~/.aws.

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
ENSURE="$ROOT/bin/aws-agent-toolkit-ensure"
TMP="$(cd -- "$(mktemp -d)" && pwd -P)"
trap 'rm -rf "$TMP"' EXIT

export HOME="$TMP/home"
export TEST_CALLS="$TMP/calls.log"
mkdir -p "$HOME" "$TMP/bin"

# Fake aws: records every invocation; `sts` succeeds only when TEST_AWS_LOGGED_IN=1.
FAKE_AWS='#!/usr/bin/env bash
printf "aws %s\n" "$*" >> "$TEST_CALLS"
case "${1:-}" in
  --version) echo "aws-cli/0.0.0-fake"; exit 0 ;;
  sts)       [[ "${TEST_AWS_LOGGED_IN:-0}" == 1 ]] ;;
  *)         exit 0 ;;
esac'

# Fake curl: stands in for the AWS installer pipeline by emitting a script that
# "installs" the fake aws into ~/.local/bin, exactly where the real one lands.
cat >"$TMP/bin/curl" <<EOS
#!/usr/bin/env bash
printf "curl %s\n" "\$*" >> "\$TEST_CALLS"
cat <<'INSTALLER'
mkdir -p "\$HOME/.local/bin"
cat >"\$HOME/.local/bin/aws" <<'AWS'
$FAKE_AWS
AWS
chmod +x "\$HOME/.local/bin/aws"
INSTALLER
EOS
chmod +x "$TMP/bin/curl"
export PATH="$TMP/bin:/usr/bin:/bin"

install_fake_aws() {
  printf '%s\n' "$FAKE_AWS" >"$TMP/bin/aws"
  chmod +x "$TMP/bin/aws"
}
reset() {
  rm -f "$TMP/bin/aws" "$TEST_CALLS"
  rm -rf "$HOME/.local"
}
calls() { [[ -f "$TEST_CALLS" ]] && cat "$TEST_CALLS" || true; }
fail() { echo "FAIL: $*" >&2; echo "--- calls ---" >&2; calls >&2; exit 1; }

# 1. aws absent, not logged in: installer runs, toolkit does not, exit 0.
reset
TEST_AWS_LOGGED_IN=0 "$ENSURE" >"$TMP/out" || fail "absent+logged-out should exit 0"
grep -q '^curl .*install\.sh' <(calls) || fail "installer was not fetched when aws was absent"
[[ -x "$HOME/.local/bin/aws" ]] || fail "installer output was not executed"
grep -q '^aws sts get-caller-identity --profile default' <(calls) || fail "session was not probed"
grep -q 'configure agent-toolkit' <(calls) && fail "toolkit ran without a session"
grep -q 'aws login --profile default' "$TMP/out" || fail "no login instructions printed"

# 2. aws present, not logged in: no installer, no toolkit, exit 0.
reset; install_fake_aws
TEST_AWS_LOGGED_IN=0 "$ENSURE" >/dev/null || fail "present+logged-out should exit 0"
grep -q '^curl' <(calls) && fail "installer fetched although aws was present"
grep -q 'configure agent-toolkit' <(calls) && fail "toolkit ran without a session"

# 3. aws present, logged in: toolkit runs, pinned to us-east-1, default profile.
reset; install_fake_aws
TEST_AWS_LOGGED_IN=1 "$ENSURE" >/dev/null || fail "present+logged-in should exit 0"
grep -qx 'aws configure agent-toolkit --yes --region us-east-1 --profile default' <(calls) \
  || fail "toolkit command not invoked as expected"

# 4. AWS_TOOLKIT_PROFILE reaches both the probe and the toolkit; region stays pinned.
reset; install_fake_aws
AWS_TOOLKIT_PROFILE=sandbox TEST_AWS_LOGGED_IN=1 "$ENSURE" >/dev/null || fail "profile override should exit 0"
grep -qx 'aws sts get-caller-identity --profile sandbox' <(calls) || fail "probe ignored AWS_TOOLKIT_PROFILE"
grep -qx 'aws configure agent-toolkit --yes --region us-east-1 --profile sandbox' <(calls) \
  || fail "toolkit ignored AWS_TOOLKIT_PROFILE or changed region"

echo "PASS: test-aws-agent-toolkit-ensure"
