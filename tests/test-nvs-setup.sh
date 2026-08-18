#!/usr/bin/env bash
set -euo pipefail

# Coverage for bin/nvs-setup: the env file it writes, and the stale-workdir
# warning. WORKDIR is only read at unit start, so an edit applied to a live
# session is silently inert until the next restart -- that is the bug the
# warning exists to surface.
#
# systemctl and pgrep are stubbed. pgrep especially: nvs-setup's legacy-squatter
# path kills whatever holds the session's port, and a session name that hashed
# onto a real port would take down an actual nvim server.

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
SETUP="$ROOT/bin/nvs-setup"
TMP="$(cd -- "$(mktemp -d)" && pwd -P)"
trap 'rm -rf "$TMP"' EXIT

mkdir "$TMP/bin"
# The single-quoted lines are the literal contents of the generated stubs.
# shellcheck disable=SC2016
printf '%s\n' \
  '#!/usr/bin/env bash' \
  'shift # --user' \
  'verb="$1"; shift' \
  'case "$verb" in' \
  '  is-active)' \
  '    if [[ "${1:-}" == "--quiet" ]]; then [[ "${FAKE_ACTIVE:-false}" == true ]]; exit; fi' \
  '    if [[ "${FAKE_ACTIVE:-false}" == true ]]; then echo active; else echo inactive; fi ;;' \
  '  show) echo "${FAKE_MAINPID:-0}" ;;' \
  '  *) : ;;' \
  'esac' >"$TMP/bin/systemctl"
printf '%s\n' '#!/usr/bin/env bash' 'exit 1' >"$TMP/bin/pgrep"
chmod +x "$TMP/bin/systemctl" "$TMP/bin/pgrep"

export PATH="$TMP/bin:$PATH"
export HOME="$TMP/home"
mkdir -p "$HOME"

run() { "$SETUP" "$@" >"$TMP/out" 2>"$TMP/err"; }

# ── Env file contents ────────────────────────────────────────────────────

# The unexpanded tilde is the input under test: init.sh passes WORKDIR through
# from the sessions file verbatim, so nvs-setup is what has to expand it.
# shellcheck disable=SC2088
run TEST-checkout9 '~/checkout9'
grep -Fx "WORKDIR=$HOME/checkout9" "$HOME/.config/nvs/TEST-checkout9.env" >/dev/null
if [[ -s "$TMP/err" ]]; then
  echo "fresh session warned unexpectedly: $(cat "$TMP/err")" >&2
  exit 1
fi

# An absolute path is passed through untouched; only a leading ~ expands.
run TEST-abs "$TMP/somewhere"
grep -Fx "WORKDIR=$TMP/somewhere" "$HOME/.config/nvs/TEST-abs.env" >/dev/null

# No workdir means no WORKDIR line at all, so nvim starts in $HOME.
run TEST-nowhere
[[ ! -s "$HOME/.config/nvs/TEST-nowhere.env" ]]

# ── Stale-workdir warning ────────────────────────────────────────────────
#
# Needs /proc to read a live server's argv. Everything above is portable.
if [[ ! -r /proc/self/cmdline ]]; then
  echo "nvs-setup tests passed (stale-workdir cases skipped: no /proc)"
  exit 0
fi

# Stand in for a headless server launched with `--cmd "cd <dir>"`. The two
# commands keep the shell from exec'ing sleep and replacing this argv.
sh -c 'sleep 30; :' nvs-fake --cmd "cd $TMP/live" &
live_pid=$!
trap 'kill "$live_pid" 2>/dev/null || true; rm -rf "$TMP"' EXIT
export FAKE_ACTIVE=true FAKE_MAINPID="$live_pid"

# Configured elsewhere than the running server: warn, and name the fix.
run TEST-stale "$TMP/wanted"
grep -F "is live in $TMP/live but configured for $TMP/wanted" "$TMP/err" >/dev/null
grep -F "nvs-restart TEST-stale" "$TMP/err" >/dev/null
# The env file is still updated -- the warning is about the process, not the file.
grep -Fx "WORKDIR=$TMP/wanted" "$HOME/.config/nvs/TEST-stale.env" >/dev/null

# Agreement between config and live server is silent.
run TEST-fresh "$TMP/live"
if [[ -s "$TMP/err" ]]; then
  echo "matching workdir warned unexpectedly: $(cat "$TMP/err")" >&2
  exit 1
fi

# Dropping WORKDIR from a session that is live in a directory is also a change.
run TEST-dropped
grep -F "is live in $TMP/live but configured for \$HOME" "$TMP/err" >/dev/null

# An inactive unit is never stale: enable --now is about to start it fresh.
FAKE_ACTIVE=false run TEST-inactive "$TMP/wanted"
if [[ -s "$TMP/err" ]]; then
  echo "inactive unit warned unexpectedly: $(cat "$TMP/err")" >&2
  exit 1
fi

echo "nvs-setup tests passed"
