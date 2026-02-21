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

# Screenshots (for Claude Code image sharing via VS Code Explorer drag-and-drop)
mkdir -p "$HOME/screenshots"
# Symlink into VS Code workspace roots so the folder is visible in the Explorer sidebar
for ws_root in "$HOME/fbsource" "$HOME/fbsource2"; do
  if [[ -d "$ws_root" ]]; then
    link_one "$HOME/screenshots" "$ws_root/screenshots"
  fi
done

# wofi
mkdir -p "$HOME/.config/wofi"
link_one "$DOTFILES_DIR/wofi_config" "$HOME/.config/wofi/config"

# sway
mkdir -p "$HOME/.config/sway"
link_one "$DOTFILES_DIR/sway_config" "$HOME/.config/sway/config"

# Claude Code
mkdir -p "$HOME/.claude/projects" "$HOME/.claude/rules" "$HOME/.claude/hooks"
link_one "$DOTFILES_DIR/claude_config/CLAUDE.md" "$HOME/.claude/CLAUDE.md"
link_one "$DOTFILES_DIR/agent_config/global-development-preferences.md" "$HOME/.claude/rules/global-development-preferences.md"
link_one "$DOTFILES_DIR/claude_config/statusline.sh" "$HOME/.claude/statusline.sh"
# Agent Manager
mkdir -p "$HOME/.claude/agent-manager/bin" "$HOME/.claude/statusline.d"
for src in "$DOTFILES_DIR/claude_config/agent-manager/"*.sh; do
  base="$(basename "$src")"
  link_one "$src" "$HOME/.claude/agent-manager/bin/$base"
done
link_one "$DOTFILES_DIR/claude_config/agent-manager/statusline-ext.sh" "$HOME/.claude/statusline.d/agent-manager.sh"
# Hooks
shopt -s nullglob
for src in "$DOTFILES_DIR/claude_config/hooks/"*; do
  base="$(basename "$src")"
  link_one "$src" "$HOME/.claude/hooks/$base"
done
shopt -u nullglob
# Skills (shared between Claude Code and Codex)
mkdir -p "$HOME/.claude/skills"
shopt -s nullglob
for src in "$DOTFILES_DIR/agent_config/skills/"*/; do
  base="$(basename "$src")"
  link_one "$src" "$HOME/.claude/skills/$base"
done
shopt -u nullglob
# Ensure settings.json has the statusline command configured (preserving other settings)
CLAUDE_SETTINGS="$HOME/.claude/settings.json"
if [[ ! -f "$CLAUDE_SETTINGS" ]]; then
  echo '{}' > "$CLAUDE_SETTINGS"
fi
tmp=$(jq '
  .statusLine = {"type": "command", "command": "~/.claude/statusline.sh"} |
  .hooks.PreToolUse = [
    {
      "matcher": "Edit|Write",
      "hooks": [
        {
          "type": "command",
          "command": "python3 ~/.claude/hooks/accept-source-controlled-edits.py"
        }
      ]
    }
  ] |
  .hooks.Stop = [
    {
      "hooks": [
        {
          "type": "command",
          "command": "bash ~/.claude/hooks/tmux-notify.sh"
        }
      ]
    },
    {
      "hooks": [
        {
          "type": "command",
          "command": "[ -f ~/.claude/agent-manager/bin/agent-tracker.sh ] && cat | bash ~/.claude/agent-manager/bin/agent-tracker.sh idle || cat > /dev/null",
          "timeout": 10
        }
      ]
    }
  ] |
  .hooks.Notification = [
    {
      "hooks": [
        {
          "type": "command",
          "command": "bash ~/.claude/hooks/tmux-notify.sh"
        }
      ]
    },
    {
      "hooks": [
        {
          "type": "command",
          "command": "[ -f ~/.claude/agent-manager/bin/agent-tracker.sh ] && cat | bash ~/.claude/agent-manager/bin/agent-tracker.sh idle || cat > /dev/null",
          "timeout": 5
        }
      ]
    }
  ] |
  .hooks.UserPromptSubmit = [
    {
      "hooks": [
        {
          "type": "command",
          "command": "bash ~/.claude/hooks/tmux-notify.sh"
        }
      ]
    },
    {
      "hooks": [
        {
          "type": "command",
          "command": "[ -f ~/.claude/agent-manager/bin/agent-tracker.sh ] && cat | bash ~/.claude/agent-manager/bin/agent-tracker.sh active || cat > /dev/null",
          "timeout": 5
        }
      ]
    }
  ] |
  .hooks.SessionStart = [
    {
      "hooks": [
        {
          "type": "command",
          "command": "bash ~/.claude/hooks/tmux-notify.sh"
        }
      ]
    },
    {
      "hooks": [
        {
          "type": "command",
          "command": "[ -f ~/.claude/agent-manager/bin/agent-tracker.sh ] && cat | bash ~/.claude/agent-manager/bin/agent-tracker.sh register || cat > /dev/null",
          "timeout": 10
        }
      ]
    }
  ] |
  .hooks.SessionEnd = [
    {
      "hooks": [
        {
          "type": "command",
          "command": "bash ~/.claude/hooks/tmux-notify.sh"
        }
      ]
    }
  ]
' "$CLAUDE_SETTINGS") \
  && echo "$tmp" > "$CLAUDE_SETTINGS" \
  && echo "configured statusLine and hooks in $CLAUDE_SETTINGS"

# Codex
mkdir -p "$HOME/.codex/rules"

# Portable settings (templated) + machine-local overrides (config.local.toml)
codex_config="$HOME/.codex/config.toml"
codex_existed=$([[ -f "$codex_config" ]] && echo true || echo false)
sed "s|__HOME__|$HOME|g" "$DOTFILES_DIR/codex_config/config.toml" > "$codex_config"
if [[ -f "$HOME/.codex/config.local.toml" ]]; then
  echo "" >> "$codex_config"
  cat "$HOME/.codex/config.local.toml" >> "$codex_config"
fi
if $codex_existed; then
  echo "updated $codex_config"
else
  echo "generated $codex_config"
fi

# Shared development rules
link_one "$DOTFILES_DIR/agent_config/global-development-preferences.md" "$HOME/.codex/rules/global-development-preferences.md"

# default.rules is machine-specific — managed by Codex itself

# TODO look into using hammerspoon again
# link_one "$DOTFILES_DIR/hammerspoon.lua" "$HOME/.hammerspoon/init.lua"


if [[ ! -d "$HOME/.oh-my-zsh" ]]; then
    KEEP_ZSHRC=yes sh -c "$(curl -fsSL https://raw.githubusercontent.com/ohmyzsh/ohmyzsh/master/tools/install.sh)" "" --unattended
    echo "installed oh-my-zsh"
fi

if [[ ! -d "$HOME/.tmux/plugins/tpm" ]]; then
    git clone https://github.com/tmux-plugins/tpm "$HOME/.tmux/plugins/tpm"
    echo "installed tpm"
fi

if [[ -x "$HOME/.tmux/plugins/tpm/bin/install_plugins" ]]; then
    if command -v tmux &>/dev/null; then
        "$HOME/.tmux/plugins/tpm/bin/install_plugins" && echo "installed tmux plugins" \
            || echo "WARNING: tmux plugin install failed" >&2
    else
        echo "WARNING: tmux not found, skipping plugin install (run prefix + I in tmux later)" >&2
    fi
fi

if [[ ${#SKIPPED_FILES[@]} -gt 0 ]]; then
  echo ""
  echo "Skipped (already exist):"
  for f in "${SKIPPED_FILES[@]}"; do
    echo "  $f"
  done
fi
