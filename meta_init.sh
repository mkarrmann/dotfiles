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

if [[ ${#SKIPPED_FILES[@]} -gt 0 ]]; then
  echo ""
  echo "Skipped (already exist):"
  for f in "${SKIPPED_FILES[@]}"; do
    echo "  $f"
  done
fi
