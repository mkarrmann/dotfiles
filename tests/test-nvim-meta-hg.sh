#!/usr/bin/env bash
set -euo pipefail

# Runs the nvim meta-hg specs against this checkout's config.
#
#   tests/test-nvim-meta-hg.sh            every spec
#   tests/test-nvim-meta-hg.sh diff-split only specs whose name contains "diff-split"
#
# Two kinds of spec:
# - tests/nvim-meta-hg/spec/*.lua load the full config (LazyVim, meta.nvim,
#   plugins) from a temp XDG_CONFIG_HOME that mirrors ~/.config/nvim's symlink
#   layout but points into this checkout, so a branch is tested as itself.
# - nvim/lua/lib/test/*-spec.lua are unit specs run with -u NONE.
#
# Hermetic against the live editor: XDG_STATE_HOME and XDG_CACHE_HOME are temp
# dirs, so persistence.nvim sessions, shada, swap and undo files never land in
# the real ones; lazy's update checker and installer are disabled. Plugins are
# read from the real ~/.local/share/nvim.

repo_root=$(cd "$(dirname "$0")/.." && pwd)
suite="$repo_root/tests/nvim-meta-hg"
tmp_dir=$(mktemp -d)
tmp_dir=$(cd "$tmp_dir" && pwd -P)
trap 'rm -rf "$tmp_dir"' EXIT

real_nvim=$(command -v nvim)
filter=${1:-}
jobs=${NVIM_META_HG_JOBS:-6}
spec_timeout=${NVIM_META_HG_TIMEOUT:-180}

config="$tmp_dir/config/nvim"
mkdir -p "$config/lua/config" "$config/lua/plugins"
ln -s "$repo_root/nvim/lua/lib" "$config/lua/lib"
for f in "$repo_root"/nvim/lua/config/*.lua "$repo_root"/nvim/local/config/*.lua; do
  ln -s "$f" "$config/lua/config/$(basename "$f")"
done
for f in "$repo_root"/nvim/lua/plugins/*.lua "$repo_root"/nvim/local/plugins/*.lua; do
  ln -s "$f" "$config/lua/plugins/$(basename "$f")"
done
echo 'require("config.meta")' > "$config/lua/config/local.lua"
for f in lazyvim.json lazy-lock.json; do
  if [[ -f $HOME/.config/nvim/$f ]]; then cp "$HOME/.config/nvim/$f" "$config/$f"; fi
done
cat > "$config/init.lua" <<LUA
vim.opt.rtp:prepend(vim.fn.stdpath("data") .. "/lazy/lazy.nvim")
local lazy = require("lazy")
local setup = lazy.setup
lazy.setup = function(spec, opts)
	local o = (type(spec) == "table" and spec.spec) and spec or opts or {}
	o.checker = { enabled = false }
	o.change_detection = { enabled = false }
	o.install = vim.tbl_extend("force", o.install or {}, { missing = false })
	return setup(spec, opts)
end
dofile("$repo_root/nvim_init.lua")
LUA

specs=()
for f in "$suite"/spec/*.lua "$repo_root"/nvim/lua/lib/test/*-spec.lua; do
  [[ -e $f ]] || continue
  [[ -z $filter || $(basename "$f") == *"$filter"* ]] && specs+=("$f")
done
[[ ${#specs[@]} -gt 0 ]] || { echo "test-nvim-meta-hg: no specs match '$filter'" >&2; exit 1; }

run_spec() {
  local spec=$1 name work
  name=$(basename "$spec" .lua)
  work="$tmp_dir/run/$name"
  mkdir -p "$work/state" "$work/cache" "$work/cwd"
  local -a cmd
  if [[ $spec == "$suite"/* ]]; then
    # Scheduled so the spec runs after VimEnter, like anything you'd type;
    # which-key, for one, creates no mappings until then. H.run quits on its
    # own, so the cquit only runs if the spec file itself fails to load.
    cmd=("$real_nvim" --headless -c "lua vim.schedule(function() local ok, err = pcall(dofile, '$spec') if not ok then io.stderr:write(tostring(err) .. '\\n') end vim.cmd.cquit(1) end)")
  else
    # A Lua error in -c doesn't change nvim's exit status; turn it into one.
    cmd=("$real_nvim" --headless -u NONE --cmd "set rtp+=$repo_root/nvim"
      -c "lua local ok, err = pcall(require('lib.test.$name').run) if not ok then io.stderr:write(tostring(err) .. '\\n') vim.cmd.cquit(1) end"
      -c "qa!")
  fi
  local status=0
  (
    cd "$work/cwd"
    env XDG_CONFIG_HOME="$tmp_dir/config" XDG_STATE_HOME="$work/state" XDG_CACHE_HOME="$work/cache" \
      HGUSER=test NVIM_META_HG_SUITE="$suite" NVIM_META_HG_LIB="$suite/lib.lua" NVIM_META_HG_TMP="$work" \
      timeout "$spec_timeout" "${cmd[@]}"
  ) > "$work/out.log" 2>&1 || status=$?
  if [[ $status -eq 0 ]]; then
    echo "ok   $name"
  else
    echo "FAIL $name (exit $status)"
    sed 's/^/     /' "$work/out.log"
  fi
  return $status
}

failed=0
running=0
for spec in "${specs[@]}"; do
  run_spec "$spec" > "$tmp_dir/$(basename "$spec").result" 2>&1 &
  running=$((running + 1))
  if [[ $running -ge $jobs ]]; then
    wait -n || failed=1
    running=$((running - 1))
  fi
done
while [[ $running -gt 0 ]]; do
  wait -n || failed=1
  running=$((running - 1))
done
for spec in "${specs[@]}"; do
  cat "$tmp_dir/$(basename "$spec").result"
done

if [[ $failed -ne 0 ]]; then
  echo "test-nvim-meta-hg: failures" >&2
  exit 1
fi
echo "test-nvim-meta-hg: all ${#specs[@]} specs passed"
