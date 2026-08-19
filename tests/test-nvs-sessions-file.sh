#!/usr/bin/env bash
set -euo pipefail

# Coverage for bin/nvs-sessions-file. The property under test is that two
# devservers sharing one dotsync2-managed ~/.config/nvs never read each
# other's session list -- the failure that had FTW-* servers running on the
# CCO devvm.

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
RESOLVE="$ROOT/bin/nvs-sessions-file"
TMP="$(cd -- "$(mktemp -d)" && pwd -P)"
trap 'rm -rf "$TMP"' EXIT

mkdir "$TMP/bin"
# `hostname -s` is the only host input, so stubbing it lets one machine stand
# in for both devservers.
# shellcheck disable=SC2016
printf '%s\n' '#!/usr/bin/env bash' 'echo "$FAKE_HOST"' >"$TMP/bin/hostname"
chmod +x "$TMP/bin/hostname"
export PATH="$TMP/bin:$PATH"
export HOME="$TMP/home"
mkdir -p "$HOME/.config/nvs"
nvs="$HOME/.config/nvs"

# No list at all: exit 1 and print nothing, so init.sh simply declares nothing.
FAKE_HOST=devvm20365 "$RESOLVE" >"$TMP/out" 2>&1 && { echo "empty dir unexpectedly resolved" >&2; exit 1; }
[[ ! -s "$TMP/out" ]]

# Only the shared fallback exists: every host reads it.
echo "# fallback" >"$nvs/sessions"
[[ "$(FAKE_HOST=devvm20365 "$RESOLVE")" == "$nvs/sessions" ]]
[[ "$(FAKE_HOST=devvm36111 "$RESOLVE")" == "$nvs/sessions" ]]

# A host-scoped list wins for its own host and is invisible to the other --
# this is the whole point.
echo "CCO-checkout1 ~/checkout1" >"$nvs/sessions.devvm20365"
[[ "$(FAKE_HOST=devvm20365 "$RESOLVE")" == "$nvs/sessions.devvm20365" ]]
[[ "$(FAKE_HOST=devvm36111 "$RESOLVE")" == "$nvs/sessions" ]]

echo "FTW-checkout1 ~/checkout1" >"$nvs/sessions.devvm36111"
[[ "$(FAKE_HOST=devvm20365 "$RESOLVE")" == "$nvs/sessions.devvm20365" ]]
[[ "$(FAKE_HOST=devvm36111 "$RESOLVE")" == "$nvs/sessions.devvm36111" ]]

# A third devserver with no list of its own falls back rather than adopting
# either neighbour's sessions.
[[ "$(FAKE_HOST=devvm99999 "$RESOLVE")" == "$nvs/sessions" ]]

# Removing the fallback leaves the host-scoped lists working and the unknown
# host with nothing.
rm "$nvs/sessions"
[[ "$(FAKE_HOST=devvm20365 "$RESOLVE")" == "$nvs/sessions.devvm20365" ]]
FAKE_HOST=devvm99999 "$RESOLVE" >/dev/null 2>&1 && { echo "unknown host unexpectedly resolved" >&2; exit 1; }

# A directory named like a list is not a list.
rm "$nvs/sessions.devvm20365"
mkdir "$nvs/sessions.devvm20365"
FAKE_HOST=devvm20365 "$RESOLVE" >/dev/null 2>&1 && { echo "directory unexpectedly resolved" >&2; exit 1; }

echo "nvs-sessions-file tests passed"
