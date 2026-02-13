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
  .shellrc \
  .shell_aliases \
  .shell_functions \
  .bashrc \
  .bash_profile \
  .zshrc \
  .zprofile \
  .screenrc \
  .inputrc \
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
link_one "$DOTFILES_DIR/claude_config/statusline.sh" "$HOME/.claude/statusline.sh"

# TODO look into using hammerspoon again
# link_one "$DOTFILES_DIR/hammerspoon.lua" "$HOME/.hammerspoon/init.lua"


if [[ "$(uname -s)" == Linux* ]]; then
    PLUG_VIM="${XDG_DATA_HOME:-$HOME/.local/share}/nvim/site/autoload/plug.vim"
    if [[ ! -e "$PLUG_VIM" ]]; then
        curl -fLo "$PLUG_VIM" --create-dirs \
            https://raw.githubusercontent.com/junegunn/vim-plug/master/plug.vim
        echo "installed vim-plug"
    fi
fi

if [[ ! -d "$HOME/.oh-my-zsh" ]]; then
    KEEP_ZSHRC=yes sh -c "$(curl -fsSL https://raw.githubusercontent.com/ohmyzsh/ohmyzsh/master/tools/install.sh)" "" --unattended
    echo "installed oh-my-zsh"
fi

if [[ ${#SKIPPED_FILES[@]} -gt 0 ]]; then
  echo ""
  echo "Skipped (already exist):"
  for f in "${SKIPPED_FILES[@]}"; do
    echo "  $f"
  done
fi

