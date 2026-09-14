#!/usr/bin/env bash
set -euo pipefail

# Coverage for bin/aws-agent-toolkit-ensure. The properties that matter: a
# machine with no AWS session must exit 0 having run neither the installer nor
# the wizard (init.sh runs unattended, and "not logged in yet" is the expected
# state of a fresh box, not a failure); the installer is invoked only when
# `aws` is absent; the toolkit command is pinned to us-east-1 with the requested
# profile; and installed skills are reconciled against aws-skills.list on every
# run -- removal even without a session, addition only with one.
#
# `aws` and `curl` are stubbed throughout: this must never reach the network or
# the developer's real ~/.aws.

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
ENSURE="$ROOT/bin/aws-agent-toolkit-ensure"
TMP="$(cd -- "$(mktemp -d)" && pwd -P)"
trap 'rm -rf "$TMP"' EXIT

export HOME="$TMP/home"
export TEST_CALLS="$TMP/calls.log"
export AWS_SKILLS_LIST="$TMP/aws-skills.list"
mkdir -p "$HOME" "$TMP/bin"

# Fake aws: records every invocation; `sts` succeeds only when
# TEST_AWS_LOGGED_IN=1; `list-installed-skills` reports the space-separated
# names in TEST_AWS_INSTALLED, each once per fake agent.
FAKE_AWS='#!/usr/bin/env bash
printf "aws %s\n" "$*" >> "$TEST_CALLS"
case "${1:-}" in
  --version) echo "aws-cli/0.0.0-fake"; exit 0 ;;
  sts)       [[ "${TEST_AWS_LOGGED_IN:-0}" == 1 ]] ;;
  agent-toolkit)
    [[ "${TEST_AWS_NO_TOOLKIT:-0}" == 1 ]] && { echo "usage: aws [options] <command>" >&2; exit 252; }
    if [[ "${2:-}" == add-skill ]]; then
      for bad in ${TEST_AWS_ADD_FAILS:-}; do
        [[ "${4:-}" == "$bad" ]] && { echo "Skill \"$bad\" not found" >&2; exit 252; }
      done
    fi
    if [[ "${2:-}" == list-installed-skills ]]; then
      python3 -c "
import json, os
names = os.environ.get(\"TEST_AWS_INSTALLED\", \"\").split()
skills = [{\"agent\": agent, \"name\": n, \"path\": f\"/x/{agent}/{n}/SKILL.md\"}
          for agent in (\"Universal\", \"Claude Code\") for n in names]
print(json.dumps({\"skills\": skills}))"
    fi
    exit 0 ;;
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
  printf '# none\n' >"$AWS_SKILLS_LIST"
  export TEST_AWS_INSTALLED=""
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

# 5. Empty list, skills installed by the wizard: every one is removed exactly
#    once (names are unioned across agents), nothing is added.
reset; install_fake_aws
TEST_AWS_INSTALLED="aws-iam aws-cdk" TEST_AWS_LOGGED_IN=1 "$ENSURE" >/dev/null || fail "reconcile should exit 0"
[[ "$(grep -c '^aws agent-toolkit remove-skill' <(calls))" == 2 ]] || fail "expected exactly two removals"
grep -qx 'aws agent-toolkit remove-skill --skill-name aws-iam' <(calls) || fail "aws-iam not removed"
grep -qx 'aws agent-toolkit remove-skill --skill-name aws-cdk' <(calls) || fail "aws-cdk not removed"
grep -q 'add-skill' <(calls) && fail "add-skill ran with an empty list"

# 6. Listed skills are kept; an unlisted one goes; a listed-but-missing one is
#    added even without a session (the catalog is public), pinned to region
#    and profile. Comments and blank lines in the list are ignored.
reset; install_fake_aws
printf '# keep these\naws-cdk  # comment\n\naws-serverless\n' >"$AWS_SKILLS_LIST"
TEST_AWS_INSTALLED="aws-iam aws-cdk" TEST_AWS_LOGGED_IN=0 "$ENSURE" >/dev/null || fail "logged-out reconcile should exit 0"
grep -qx 'aws agent-toolkit remove-skill --skill-name aws-iam' <(calls) || fail "unlisted aws-iam not removed"
grep -q 'remove-skill --skill-name aws-cdk' <(calls) && fail "listed aws-cdk was removed"
grep -qx 'aws agent-toolkit add-skill --skill-name aws-serverless --region us-east-1 --profile default' <(calls) \
  || fail "missing listed skill not added"

# 7. A listed name the catalog rejects is reported and fails the run, after
#    the other additions have still been attempted.
reset; install_fake_aws
printf 'aws-bogus\naws-serverless\n' >"$AWS_SKILLS_LIST"
TEST_AWS_ADD_FAILS="aws-bogus" TEST_AWS_LOGGED_IN=1 "$ENSURE" >"$TMP/out" 2>"$TMP/err" && fail "rejected skill should fail the run"
grep -q 'could not add skill aws-bogus' "$TMP/err" || fail "rejected skill not reported"
grep -q 'add-skill --skill-name aws-serverless' <(calls) || fail "later addition skipped after a failure"

# 8. No list file at all behaves as an empty list rather than failing.
reset; install_fake_aws; rm -f "$AWS_SKILLS_LIST"
TEST_AWS_INSTALLED="aws-iam" TEST_AWS_LOGGED_IN=0 "$ENSURE" >/dev/null || fail "missing list should exit 0"
grep -qx 'aws agent-toolkit remove-skill --skill-name aws-iam' <(calls) || fail "missing list did not remove skills"

# 9. A preinstalled aws too old for `agent-toolkit` (distro package) must not
#    turn a logged-out run into a failure: exit 0, no removals attempted.
reset; install_fake_aws
TEST_AWS_NO_TOOLKIT=1 TEST_AWS_LOGGED_IN=0 "$ENSURE" >"$TMP/out" || fail "old aws should exit 0"
grep -q 'remove-skill\|add-skill' <(calls) && fail "reconcile ran against an aws without agent-toolkit"
grep -q 'no working agent-toolkit' "$TMP/out" || fail "old aws was not reported"

echo "PASS: test-aws-agent-toolkit-ensure"
