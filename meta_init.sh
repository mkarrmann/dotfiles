#!/usr/bin/env bash
set -uo pipefail

DOTFILES_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

SKIPPED_FILES=()

link_one() {
  local src="$1"
  local dst="$2"

  if [[ ! -e "$src" ]]; then
    echo "ERROR: source missing: $src" >&2
    exit 1
  fi

  if [[ -e "$dst" || -L "$dst" ]]; then
    SKIPPED_FILES+=("$dst")
    return 0
  fi

  ln -s "$src" "$dst"
  echo "linked $dst -> $src"
}

sync_link_dir() {
  local src_dir="$1"
  local dst_dir="$2"
  local pattern="$3"
  local target

  mkdir -p "$dst_dir"

  shopt -s nullglob
  for src in "$src_dir"/$pattern; do
    link_one "$src" "$dst_dir/$(basename "$src")"
  done
  shopt -u nullglob

  # Remove stale links previously created from this managed source directory.
  shopt -s nullglob
  for dst in "$dst_dir"/$pattern; do
    if [[ -L "$dst" ]]; then
      target="$(readlink "$dst")"
      if [[ "$target" == "$src_dir/"* ]] && [[ ! -e "$target" ]]; then
        rm "$dst"
        echo "removed stale link $dst -> $target"
      fi
    fi
  done
  shopt -u nullglob
}

# Shell local overrides (Meta macOS-specific)
link_one "$DOTFILES_DIR/zshenv.local.meta-macos" "$HOME/.zshenv.local"

# Symlink local config templates into the nvim runtime
mkdir -p "$HOME/.config/nvim/lua/config" "$HOME/.config/nvim/lua/plugins"
sync_link_dir "$DOTFILES_DIR/nvim/local/config" "$HOME/.config/nvim/lua/config" "*.lua"
sync_link_dir "$DOTFILES_DIR/nvim/local/plugins" "$HOME/.config/nvim/lua/plugins" "*.lua"

# Create config/local.lua opt-in if it doesn't already exist
LOCAL_LUA="$HOME/.config/nvim/lua/config/local.lua"
if [[ ! -e "$LOCAL_LUA" && ! -L "$LOCAL_LUA" ]]; then
  cat > "$LOCAL_LUA" <<'EOF'
require("config.meta")
EOF
  echo "created $LOCAL_LUA"
fi

# dvsc-core-acp deps (out-of-tree node_modules for the ACP wrapper)
if [[ "$(uname -s)" == "Linux" ]]; then
  echo ""
  echo "--- dvsc-core-acp deps ---"
  DVSC_DEPS="$HOME/dvsc-core-acp-deps"
  DVSC_DIST_REL="users/mk/mkarrmann/dvsc-core-acp/packages/acp-wrapper"

  # Find the dotslash Node's bundled npm (bypasses system npm block)
  _dotslash_npm=""
  for _candidate in "$HOME"/.cache/dotslash/obj/manifold/*/extract/*/node-*/bin/npm; do
    if [[ -x "$_candidate" ]]; then
      _dotslash_npm="$_candidate"
      break
    fi
  done

  if [[ -z "$_dotslash_npm" ]]; then
    echo "WARNING: dotslash node not cached yet — skipping dvsc-core-acp deps install."
    echo "  (Will be available after first dvsc-core run; re-run meta_init.sh then)" >&2
  else
    # Install deps if node_modules is missing or stale
    if [[ ! -d "$DVSC_DEPS/node_modules" ]] || \
       ! diff -q "$DOTFILES_DIR/dvsc-core-acp-deps/package-lock.json" "$DVSC_DEPS/package-lock.json" &>/dev/null; then
      mkdir -p "$DVSC_DEPS"
      cp "$DOTFILES_DIR/dvsc-core-acp-deps/package.json" "$DOTFILES_DIR/dvsc-core-acp-deps/package-lock.json" "$DVSC_DEPS/"
      (cd "$DVSC_DEPS" && "$_dotslash_npm" ci --ignore-scripts 2>&1 | tail -3)
      echo "installed dvsc-core-acp deps via dotslash npm"
    fi

    # Symlink node_modules + build for each checkout that has the project
    for _root in "$HOME"/checkout{1,2,3}/fbsource "$HOME/fbsource"; do
      _proj="$_root/$DVSC_DIST_REL"
      if [[ -d "$_proj/src" ]]; then
        if [[ ! -e "$(dirname "$_proj")/../../node_modules" ]]; then
          ln -sfn "$DVSC_DEPS/node_modules" "$(dirname "$_proj")/../../node_modules"
          echo "symlinked node_modules -> $DVSC_DEPS/node_modules for $_root"
        fi
        # Build if dist is missing or older than source
        if [[ ! -f "$_proj/dist/index.js" ]] || \
           [[ "$_proj/src/agent.ts" -nt "$_proj/dist/agent.js" ]]; then
          (cd "$_proj" && "$DVSC_DEPS/node_modules/.bin/tsc" -b 2>&1 | tail -5)
          echo "built dvsc-core-acp wrapper in $_proj"
        fi
      fi
    done
  fi
fi

# Obsidian headless sync (Linux devservers only)
if [[ "$(uname -s)" == "Linux" ]]; then
  echo ""
  echo "--- Obsidian headless sync ---"
  bash "$DOTFILES_DIR/ob-headless/setup.sh"
fi

if [[ ${#SKIPPED_FILES[@]} -gt 0 ]]; then
  echo ""
  echo "Skipped (already exist):"
  for f in "${SKIPPED_FILES[@]}"; do
    echo "  $f"
  done
fi
#
# Ensure key Codex marketplace components are present.
if command -v agent-market >/dev/null 2>&1; then
  if [[ ! -f "$HOME/.codex/agents/vsp-code-search.toml" ]]; then
    agent-market agent vsp-code-search install --agent codex --scope user --quiet || true
  fi
  if [[ ! -f "$HOME/.codex/agents/meta-knowledge.toml" ]]; then
    agent-market agent meta-knowledge install --agent codex --scope user --quiet || true
  fi
  if ! grep -Fq '[plugins."meta-search-cli@claude-templates"]' "$HOME/.codex/config.toml" 2>/dev/null; then
    agent-market plugin meta-search-cli install --agent codex --scope user --quiet || true
  fi
fi

