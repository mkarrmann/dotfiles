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
mkdir -p "$HOME/.config/nvim" "$HOME/.config/nvim/lua/config" "$HOME/.config/nvim/lua/plugins"
link_one "$DOTFILES_DIR/nvim_init.lua" "$HOME/.config/nvim/init.lua"
shopt -s nullglob
for f in "$DOTFILES_DIR/nvim/lua/config/"*.lua; do
  link_one "$f" "$HOME/.config/nvim/lua/config/$(basename "$f")"
done
for f in "$DOTFILES_DIR/nvim/lua/plugins/"*.lua; do
  link_one "$f" "$HOME/.config/nvim/lua/plugins/$(basename "$f")"
done
shopt -u nullglob

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

# sway
mkdir -p "$HOME/.config/sway"
link_one "$DOTFILES_DIR/sway_config" "$HOME/.config/sway/config"

# Claude Code
mkdir -p "$HOME/.claude/projects" "$HOME/.claude/rules"
link_one "$DOTFILES_DIR/claude_config/CLAUDE.md" "$HOME/.claude/CLAUDE.md"
link_one "$DOTFILES_DIR/agent_config/global-development-preferences.md" "$HOME/.claude/rules/global-development-preferences.md"
link_one "$DOTFILES_DIR/claude_config/statusline.sh" "$HOME/.claude/statusline.sh"
# Ensure settings.json has the statusline command configured (preserving other settings)
CLAUDE_SETTINGS="$HOME/.claude/settings.json"
if [[ ! -f "$CLAUDE_SETTINGS" ]]; then
  echo '{}' > "$CLAUDE_SETTINGS"
fi
tmp=$(jq '.statusLine = {"type": "command", "command": "~/.claude/statusline.sh"}' "$CLAUDE_SETTINGS") \
  && echo "$tmp" > "$CLAUDE_SETTINGS" \
  && echo "set statusLine.command in $CLAUDE_SETTINGS"

# Codex
mkdir -p "$HOME/.codex/rules"

# Portable settings (templated) + machine-local overrides (config.local.toml)
sed "s|__HOME__|$HOME|g" "$DOTFILES_DIR/codex_config/config.toml" > "$HOME/.codex/config.toml"
if [[ -f "$HOME/.codex/config.local.toml" ]]; then
  echo "" >> "$HOME/.codex/config.toml"
  cat "$HOME/.codex/config.local.toml" >> "$HOME/.codex/config.toml"
fi
echo "generated $HOME/.codex/config.toml"

# Shared development rules
link_one "$DOTFILES_DIR/agent_config/global-development-preferences.md" "$HOME/.codex/rules/global-development-preferences.md"

# default.rules is machine-specific — managed by Codex itself

# TODO look into using hammerspoon again
# link_one "$DOTFILES_DIR/hammerspoon.lua" "$HOME/.hammerspoon/init.lua"


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
