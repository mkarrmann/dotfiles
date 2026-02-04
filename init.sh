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

# Top-level dotfiles
for f in \
  .bash_aliases \
  .bashrc \
  .screenrc \
  .inputrc \
  .bash_profile \
  .tmux.conf \
  .git-prompt.sh
do
  link_one "$DOTFILES_DIR/$f" "$HOME/$f"
done

# Neovim
mkdir -p "$HOME/.config/nvim"
link_one "$DOTFILES_DIR/nvim_init.lua" "$HOME/.config/nvim/init.lua"

# ~/bin (link each file individually; fail if any target exists)
mkdir -p "$HOME/bin"
shopt -s nullglob
for src in "$DOTFILES_DIR/bin/"*; do
  base="$(basename "$src")"
  link_one "$src" "$HOME/bin/$base"
done
shopt -u nullglob

# wofi
mkdir -p "$HOME/.config/wofi"
link_one "$DOTFILES_DIR/wofi_config" "$HOME/.config/wofi/config"

# Claude Code
mkdir -p "$HOME/.claude/projects" "$HOME/.claude/rules"
link_one "$DOTFILES_DIR/claude_config/CLAUDE.md" "$HOME/.claude/CLAUDE.md"
link_one "$DOTFILES_DIR/claude_config/rules/personal-style.md" "$HOME/.claude/rules/personal-style.md"

# TODO look into using hammerspoon again
# link_one "$DOTFILES_DIR/hammerspoon.lua" "$HOME/.hammerspoon/init.lua"


if [ "$(expr substr $(uname -s) 1 5)" == "Linux" ]; then
    # Installs vim plug (need to install manually on other platforms)
    # Still need to call :PlugInstall inside of nvim to get all dependencies
    sh -c 'curl -fLo "${XDG_DATA_HOME:-$HOME/.local/share}"/nvim/site/autoload/plug.vim --create-dirs \
           https://raw.githubusercontent.com/junegunn/vim-plug/master/plug.vim'
fi

if [[ ${#SKIPPED_FILES[@]} -gt 0 ]]; then
  echo ""
  echo "Skipped (already exist):"
  for f in "${SKIPPED_FILES[@]}"; do
    echo "  $f"
  done
fi

