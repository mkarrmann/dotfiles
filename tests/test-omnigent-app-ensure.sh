#!/usr/bin/env bash
set -euo pipefail

# Coverage for bin-macos/omnigent-app-ensure. The properties under test are the
# two that let the desktop app rot silently for two months: that a stale app is
# reported as stale (0.1.0 vs 0.10.0, where a string compare says 0.9.0 > 0.10.0
# and gets it backwards), and that the arm64 dmg is paired with ITS sha512 and
# not with the zip's or the feed's trailing top-level one.
#
# pgrep/osascript are stubbed throughout: a bug that reached the quit step must
# never be able to kill the developer's running Omnigent.

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
ENSURE="$ROOT/bin-macos/omnigent-app-ensure"
TMP="$(cd -- "$(mktemp -d)" && pwd -P)"
trap 'rm -rf "$TMP"' EXIT

mkdir -p "$TMP/bin"
printf '%s\n' '#!/usr/bin/env bash' 'exit 1' >"$TMP/bin/pgrep"
printf '%s\n' '#!/usr/bin/env bash' 'exit 0' >"$TMP/bin/osascript"
# shellcheck disable=SC2016
printf '%s\n' '#!/usr/bin/env bash' 'if [[ "${1-}" == "-m" ]]; then echo arm64; else exec /usr/bin/uname "$@"; fi' \
  >"$TMP/bin/uname"
chmod +x "$TMP/bin/pgrep" "$TMP/bin/osascript" "$TMP/bin/uname"
export PATH="$TMP/bin:$PATH"

fake_app() { # $1 = version, or empty for "not installed"
  rm -rf "$TMP/Omnigent.app"
  [[ -n "$1" ]] || return 0
  mkdir -p "$TMP/Omnigent.app/Contents"
  cat >"$TMP/Omnigent.app/Contents/Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleShortVersionString</key><string>$1</string>
</dict></plist>
EOF
}

write_feed() { # $1 = version, $2 = arm64 dmg sha512
  cat >"$TMP/latest-mac.yml" <<EOF
version: $1
files:
  - url: Omnigent-$1-arm64-mac.zip
    sha512: ZIP_SHA_MUST_NOT_BE_USED
    size: 121676280
  - url: Omnigent-$1-arm64.dmg
    sha512: $2
    size: 123120513
path: Omnigent-$1-arm64-mac.zip
sha512: TOP_LEVEL_SHA_MUST_NOT_BE_USED
releaseDate: '2026-08-21T03:13:29.000Z'
EOF
}

run() {
  OMNIGENT_APP_PATH="$TMP/Omnigent.app" \
    OMNIGENT_APP_FEED="file://$TMP/" \
    "$ENSURE" "$@"
}

write_feed "0.10.0" "DMG_SHA_IS_THE_ONE_TO_USE"

# Behind: 0.1.0 < 0.10.0. A string compare ranks "0.1.0" and "0.9.0" above
# "0.10.0"; sort -V is the reason this script does not repeat that mistake.
fake_app "0.1.0"
run --check && { echo "0.1.0 was not reported as behind 0.10.0" >&2; exit 1; }
[[ "$(run --check; echo $?)" == 1 ]]

fake_app "0.9.0"
[[ "$(run --check; echo $?)" == 1 ]] || { echo "0.9.0 must be behind 0.10.0" >&2; exit 1; }

# Current, and ahead: both are satisfied, so a deliberately-forward build is
# never dragged backwards.
fake_app "0.10.0"
run --check || { echo "0.10.0 must be current against 0.10.0" >&2; exit 1; }
fake_app "0.11.0"
run --check || { echo "0.11.0 must satisfy a 0.10.0 feed" >&2; exit 1; }

# Not installed at all is "behind", so a fresh Mac bootstraps.
fake_app ""
[[ "$(run --check; echo $?)" == 1 ]] || { echo "missing app must report behind" >&2; exit 1; }

# Unreachable feed is undetermined (2), never "behind". startup-windows only
# warns on exactly 1, so an offline login stays quiet instead of crying wolf.
fake_app "0.1.0"
rc=0
OMNIGENT_APP_PATH="$TMP/Omnigent.app" OMNIGENT_APP_FEED="file://$TMP/nope/" \
  "$ENSURE" --check || rc=$?
[[ "$rc" == 2 ]] || { echo "unreachable feed must exit 2, got $rc" >&2; exit 1; }

# Report mode always exits 0 -- it is advisory, and startup-windows must not
# treat a stale app as a startup failure.
run >"$TMP/out" 2>"$TMP/err"
grep -q "behind the feed's 0.10.0" "$TMP/err" || { echo "report mode said nothing" >&2; exit 1; }

# The dmg entry must pair with its own sha512. Point the feed at a real file
# whose digest cannot match, and assert the EXPECTED value quoted back is the
# dmg's -- not the zip's, and not the trailing top-level one.
: >"$TMP/Omnigent-0.10.0-arm64.dmg"
rc=0
run --install >"$TMP/out" 2>"$TMP/err" || rc=$?
[[ "$rc" == 1 ]] || { echo "sha mismatch must fail, got $rc" >&2; exit 1; }
grep -q "expected: DMG_SHA_IS_THE_ONE_TO_USE" "$TMP/err" || {
  echo "wrong sha512 paired with the dmg:" >&2; cat "$TMP/err" >&2; exit 1
}
if grep -q "MUST_NOT_BE_USED" "$TMP/err"; then
  echo "picked the zip or the top-level sha instead of the dmg's" >&2
  exit 1
fi

# A feed with no dmg for this arch fails loudly rather than installing nothing.
sed -i.bak 's/-arm64\.dmg/-x64.dmg/' "$TMP/latest-mac.yml"
rc=0
run --install >"$TMP/out" 2>"$TMP/err" || rc=$?
[[ "$rc" == 1 ]] || { echo "missing arch dmg must fail, got $rc" >&2; exit 1; }
grep -q "no arm64 dmg" "$TMP/err" || { echo "unhelpful error for missing arch" >&2; cat "$TMP/err" >&2; exit 1; }

# Bad usage is a usage error, not a silent no-op.
rc=0
run --bogus >/dev/null 2>&1 || rc=$?
[[ "$rc" == 2 ]] || { echo "bad flag must exit 2, got $rc" >&2; exit 1; }

echo "omnigent-app-ensure tests passed"
