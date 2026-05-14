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

sync_launchd_plist() {
  local src="$1"
  local dst="$HOME/Library/LaunchAgents/$(basename "$src")"
  local label="$(basename "$src" .plist)"

  if [[ ! -f "$src" ]]; then
    echo "ERROR: launchd plist source missing: $src" >&2
    return 1
  fi

  # Up-to-date copy: nothing to do.
  if [[ -f "$dst" ]] && cmp -s "$src" "$dst"; then
    return 0
  fi

  cp "$src" "$dst"
  echo "synced $dst"

  # bootout is harmless if the job isn't loaded; bootstrap (re)loads it
  # under the user's GUI domain so it runs at next login automatically.
  launchctl bootout "gui/$UID/$label" 2>/dev/null || true
  if launchctl bootstrap "gui/$UID" "$dst" 2>/dev/null; then
    echo "loaded $label"
  else
    echo "WARNING: launchctl bootstrap $label failed" >&2
  fi
}

sync_link_subdirs() {
  local src_parent="$1"
  local dst_parent="$2"
  local required_file="${3:-}"
  local target

  mkdir -p "$dst_parent"

  shopt -s nullglob
  for src in "$src_parent"/*/; do
    if [[ -n "$required_file" ]] && [[ ! -e "$src$required_file" ]]; then
      echo "skipped $src (missing $required_file)"
      continue
    fi
    link_one "$src" "$dst_parent/$(basename "$src")"
  done
  shopt -u nullglob

  # Remove stale links previously created from this managed source parent.
  shopt -s nullglob
  for dst in "$dst_parent"/*; do
    if [[ -L "$dst" ]]; then
      target="$(readlink "$dst")"
      if [[ "$target" == "$src_parent/"* ]]; then
        if [[ ! -e "$target" ]] || { [[ -n "$required_file" ]] && [[ ! -e "$target/$required_file" ]]; }; then
          rm "$dst"
          echo "removed stale link $dst -> $target"
        fi
      fi
    fi
  done
  shopt -u nullglob
}

# Top-level dotfiles
for f in \
  .shell_env \
  .shellrc \
  .shell_aliases \
  .shell_functions \
  .bashrc \
  .bash_profile \
  .zshrc \
  .zprofile \
  .zshenv \
  .screenrc \
  .inputrc \
  .tmux.conf \
  .git-prompt.sh
do
  link_one "$DOTFILES_DIR/$f" "$HOME/$f"
done

# Neovim
mkdir -p "$HOME/.config/nvim" "$HOME/.config/nvim/lua/config" "$HOME/.config/nvim/lua/plugins" "$HOME/.config/nvim/lua/lib"
link_one "$DOTFILES_DIR/nvim_init.lua" "$HOME/.config/nvim/init.lua"
sync_link_dir "$DOTFILES_DIR/nvim/lua/config" "$HOME/.config/nvim/lua/config" "*.lua"
sync_link_dir "$DOTFILES_DIR/nvim/lua/plugins" "$HOME/.config/nvim/lua/plugins" "*.lua"
sync_link_dir "$DOTFILES_DIR/nvim/lua/lib" "$HOME/.config/nvim/lua/lib" "*.lua"

# ~/bin (platform-specific takes priority over cross-platform)
mkdir -p "$HOME/bin"
case "$(uname -s)" in
  Darwin) platform_bin="$DOTFILES_DIR/bin-macos" ;;
  Linux)  platform_bin="$DOTFILES_DIR/bin-linux" ;;
  *)      platform_bin="" ;;
esac
if [[ -n "$platform_bin" && -d "$platform_bin" ]]; then
  sync_link_dir "$platform_bin" "$HOME/bin" "*"
fi
sync_link_dir "$DOTFILES_DIR/bin" "$HOME/bin" "*"

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
# Clean up stale optional rule links that no longer exist in dotfiles.
if [[ -L "$HOME/.claude/rules/personal-style.md" && ! -e "$HOME/.claude/rules/personal-style.md" ]]; then
  rm "$HOME/.claude/rules/personal-style.md"
  echo "removed stale link $HOME/.claude/rules/personal-style.md"
fi
link_one "$DOTFILES_DIR/claude_config/statusline.sh" "$HOME/.claude/statusline.sh"
# Agent Manager
mkdir -p "$HOME/.claude/agent-manager/bin" "$HOME/.claude/statusline.d"
sync_link_dir "$DOTFILES_DIR/claude_config/agent-manager" "$HOME/.claude/agent-manager/bin" "*.sh"
sync_link_dir "$DOTFILES_DIR/claude_config/agent-manager" "$HOME/.claude/agent-manager/bin" "*.py"
link_one "$DOTFILES_DIR/claude_config/agent-manager/statusline-ext.sh" "$HOME/.claude/statusline.d/agent-manager.sh"
# Obsidian vault config (source of truth for AGENTS.md location)
link_one "$DOTFILES_DIR/claude_config/obsidian-vault.conf" "$HOME/.claude/obsidian-vault.conf"
# Hooks
sync_link_dir "$DOTFILES_DIR/claude_config/hooks" "$HOME/.claude/hooks" "*"
# Skills (shared between Claude Code and Codex)
mkdir -p "$HOME/.claude/skills"
sync_link_subdirs "$DOTFILES_DIR/agent_config/skills" "$HOME/.claude/skills" "SKILL.md"
# Ensure settings.json has the statusline command configured (preserving other settings)
CLAUDE_SETTINGS="$HOME/.claude/settings.json"
if [[ ! -f "$CLAUDE_SETTINGS" ]]; then
  echo '{}' > "$CLAUDE_SETTINGS"
fi
tmp=$(jq '
  .permissions.defaultMode = "bypassPermissions" |
  .model = "claude-opus-4-7" |
  .env |= ((. // {}) + {
    "DISABLE_AUTOUPDATER": "1",
    "MCP_TIMEOUT": "120000",
    "ENABLE_LSP_TOOL": "1"
  }) |
  .enabledPlugins |= ((. // {}) + {
    "meta-lsp@claude-templates": true,
    "meta-lsp-hack@claude-templates": true,
    "meta-lsp-flow@claude-templates": true,
    "meta-lsp-buck2@claude-templates": true,
    "meta-lsp-thrift@claude-templates": true,
    "meta-lsp-pyrefly@claude-templates": true,
    "meta-lsp-relay@claude-templates": true,
    "meta-lsp-go@claude-templates": true,
    "meta-lsp-rust@claude-templates": true,
    "meta-lsp-typescript@claude-templates": true
  }) |
  .statusLine = {"type": "command", "command": "~/.claude/statusline.sh"} |
  .hooks.PreToolUse = [
    {
      "hooks": [
        {
          "type": "command",
          "command": "bash ~/.claude/hooks/nvim-notify.sh"
        }
      ]
    },
    {
      "matcher": "Edit|Write",
      "hooks": [
        {
          "type": "command",
          "command": "python3 ~/.claude/hooks/accept-source-controlled-edits.py"
        }
      ]
    },
    {
      "matcher": "Edit|Write",
      "hooks": [
        {
          "type": "command",
          "command": "python3 ~/.claude/hooks/snapshot-for-diff.py"
        }
      ]
    }
  ] |
  .hooks.PostToolUse = [
    {
      "matcher": "Edit|Write",
      "hooks": [
        {
          "type": "command",
          "command": "python3 ~/.claude/hooks/show-edit-diff.py"
        }
      ]
    }
  ] |
  .hooks.Stop = [
    {
      "hooks": [
        {
          "type": "command",
          "command": "bash ~/.claude/hooks/nvim-notify.sh"
        }
      ]
    },
    {
      "hooks": [
        {
          "type": "command",
          "command": "[ -f ~/.claude/agent-manager/bin/agent-tracker.sh ] && cat | bash ~/.claude/agent-manager/bin/agent-tracker.sh done || cat > /dev/null",
          "timeout": 10
        }
      ]
    }
  ] |
  .hooks.UserPromptSubmit = [
    {
      "hooks": [
        {
          "type": "command",
          "command": "bash ~/.claude/hooks/nvim-notify.sh"
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
    },
    {
      "hooks": [
        {
          "type": "command",
          "command": "bash ~/.claude/hooks/new-turn-diff.sh"
        }
      ]
    }
  ] |
  .hooks.SessionStart = [
    {
      "hooks": [
        {
          "type": "command",
          "command": "bash ~/.claude/hooks/nvim-notify.sh"
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
    },
    {
      "hooks": [
        {
          "type": "command",
          "command": "bash ~/.claude/hooks/nvim-session-id.sh",
          "timeout": 5
        }
      ]
    }
  ] |
  .hooks.Notification = [
    {
      "matcher": "permission_prompt|elicitation_dialog",
      "hooks": [
        {
          "type": "command",
          "command": "bash ~/.claude/hooks/nvim-notify.sh"
        },
        {
          "type": "command",
          "command": "[ -f ~/.claude/agent-manager/bin/agent-tracker.sh ] && cat | bash ~/.claude/agent-manager/bin/agent-tracker.sh waiting || cat > /dev/null",
          "timeout": 5
        }
      ]
    }
  ] |
  .hooks.SessionEnd = [
    {
      "hooks": [
        {
          "type": "command",
          "command": "bash ~/.claude/hooks/nvim-notify.sh"
        }
      ]
    },
    {
      "hooks": [
        {
          "type": "command",
          "command": "bash ~/.claude/hooks/cleanup-diff.sh"
        }
      ]
    }
  ]
' "$CLAUDE_SETTINGS") \
  && echo "$tmp" > "$CLAUDE_SETTINGS" \
  && echo "configured statusLine and hooks in $CLAUDE_SETTINGS"

# Codex
mkdir -p "$HOME/.codex/rules" "$HOME/.codex/skills"

# Portable Codex template + machine-local overrides (config.local.toml)
codex_config="$HOME/.codex/config.toml"
codex_existed=$([[ -f "$codex_config" ]] && echo true || echo false)
sed "s|__HOME__|$HOME|g" "$DOTFILES_DIR/codex_config/config.template.toml" > "$codex_config"
if [[ -f "$HOME/.codex/config.local.toml" ]]; then
  echo "" >> "$codex_config"
  cat "$HOME/.codex/config.local.toml" >> "$codex_config"
fi
# Ensure dotfiles repo is trusted by default unless explicitly set in local overrides.
if ! grep -Fqx "[projects.\"$HOME/dotfiles\"]" "$codex_config"; then
  echo "" >> "$codex_config"
  echo "[projects.\"$HOME/dotfiles\"]" >> "$codex_config"
  echo "trust_level = \"trusted\"" >> "$codex_config"
fi
if $codex_existed; then
  echo "updated $codex_config"
else
  echo "generated $codex_config"
fi

# Shared development rules
link_one "$DOTFILES_DIR/agent_config/global-development-preferences.md" "$HOME/.codex/rules/global-development-preferences.md"
# Shared skills
sync_link_subdirs "$DOTFILES_DIR/agent_config/skills" "$HOME/.codex/skills" "SKILL.md"

# default.rules is machine-specific — managed by Codex itself

# Ghostty
mkdir -p "$HOME/.config/ghostty"
link_one "$DOTFILES_DIR/ghostty_config" "$HOME/.config/ghostty/config"

# macOS-only: Hammerspoon, AeroSpace, SketchyBar
if [[ "$(uname -s)" == "Darwin" ]]; then
  # Hammerspoon
  mkdir -p "$HOME/.hammerspoon"
  link_one "$DOTFILES_DIR/hammerspoon.lua" "$HOME/.hammerspoon/init.lua"

  # AeroSpace
  link_one "$DOTFILES_DIR/aerospace.toml" "$HOME/.aerospace.toml"

  # SketchyBar
  mkdir -p "$HOME/.config/sketchybar/plugins"
  link_one "$DOTFILES_DIR/sketchybar/sketchybarrc" "$HOME/.config/sketchybar/sketchybarrc"
  sync_link_dir "$DOTFILES_DIR/sketchybar/plugins" "$HOME/.config/sketchybar/plugins" "*"

  # Orchest plugin manifest
  mkdir -p "$HOME/Library/Application Support/@orchest/desktop"
  link_one "$DOTFILES_DIR/orchest_plugins.json" "$HOME/Library/Application Support/@orchest/desktop/plugins.json"

  # Launchd jobs. Plists are copied (not symlinked) — launchd's behavior
  # across system upgrades is more predictable when the file is
  # materialized. See acp-broker docs/RUNBOOK.md §3.2.
  mkdir -p "$HOME/Library/LaunchAgents" \
           "$HOME/.local/state/acp-broker" \
           "$HOME/.local/state/persistence-server"
  sync_launchd_plist "$DOTFILES_DIR/launchd/com.mkarrmann.persistence-server.plist"
  sync_launchd_plist "$DOTFILES_DIR/launchd/com.mkarrmann.acp-broker.plist"
fi

# Linux-only: systemd --user units. Linger is expected to be enabled
# (`loginctl enable-linger`) so these survive logout and start at boot.
if [[ "$(uname -s)" == "Linux" ]] && command -v systemctl &>/dev/null; then
  sync_link_dir "$DOTFILES_DIR/systemd" "$HOME/.config/systemd/user" "*.service"
  systemctl --user daemon-reload 2>/dev/null || true
  shopt -s nullglob
  for unit_src in "$DOTFILES_DIR"/systemd/*.service; do
    unit_name="$(basename "$unit_src")"
    # Template units (foo@.service) can't be enabled without an instance —
    # their instances are managed declaratively below from a per-host config.
    [[ "$unit_name" == *@.service ]] && continue
    if systemctl --user enable --now "$unit_name" &>/dev/null; then
      echo "enabled $unit_name"
    else
      echo "WARNING: failed to enable $unit_name (try: systemctl --user status $unit_name)" >&2
    fi
  done
  shopt -u nullglob

  # Per-session nvim daemons (nvs@SESSION.service instances).
  # ~/.config/nvs/sessions is machine-local (not source-controlled, same as
  # ~/.localrc). Lines: `SESSION_NAME [WORKDIR]`. Comments with #, blank ok.
  nvs_sessions="$HOME/.config/nvs/sessions"
  if [[ -f "$nvs_sessions" ]]; then
    while IFS= read -r line || [[ -n "$line" ]]; do
      line="${line%%#*}"
      # shellcheck disable=SC2086
      set -- $line
      [[ $# -eq 0 ]] && continue
      session="$1"; workdir="${2:-}"
      "$HOME/bin/nvs-setup" "$session" ${workdir:+"$workdir"} \
        || echo "WARNING: nvs-setup failed for $session" >&2
    done < "$nvs_sessions"
  fi
fi

# Nori
mkdir -p "$HOME/.nori/cli"

nori_config="$HOME/.nori/cli/config.toml"
nori_existed=$([[ -f "$nori_config" ]] && echo true || echo false)
sed "s|__HOME__|$HOME|g" "$DOTFILES_DIR/nori_config/config.toml" > "$nori_config"
if [[ -f "$HOME/.nori/cli/config.local.toml" ]]; then
  echo "" >> "$nori_config"
  cat "$HOME/.nori/cli/config.local.toml" >> "$nori_config"
fi
if $nori_existed; then
  echo "updated $nori_config"
else
  echo "generated $nori_config"
fi

# Nori CLI
if ! command -v nori &>/dev/null; then
    "$DOTFILES_DIR/bin/install-or-upgrade-nori" && echo "installed nori" \
        || echo "WARNING: nori install failed" >&2
fi

if [[ ! -d "$HOME/.oh-my-zsh" ]]; then
    KEEP_ZSHRC=yes sh -c "$(curl -fsSL https://raw.githubusercontent.com/ohmyzsh/ohmyzsh/master/tools/install.sh)" "" --unattended
    echo "installed oh-my-zsh"
fi

if [[ ! -d "$HOME/.tmux/plugins/tpm" ]]; then
    git clone https://github.com/tmux-plugins/tpm "$HOME/.tmux/plugins/tpm"
    echo "installed tpm"
fi

if [[ -x "$HOME/.tmux/plugins/tpm/bin/install_plugins" ]]; then
    if ! command -v tmux &>/dev/null; then
        echo "WARNING: tmux not found, skipping plugin install (run prefix + I in tmux later)" >&2
    elif ! tmux list-sessions &>/dev/null; then
        echo "WARNING: tmux server not running, skipping plugin install (run prefix + I in tmux later)" >&2
    else
        tmux set-environment -g TMUX_PLUGIN_MANAGER_PATH "$HOME/.tmux/plugins"
        "$HOME/.tmux/plugins/tpm/bin/install_plugins" && echo "installed tmux plugins" \
            || echo "WARNING: tmux plugin install failed" >&2
    fi
fi

if ! command -v cargo &>/dev/null; then
    if command -v curl &>/dev/null; then
        curl --proto '=https' --tlsv1.2 -fsSL https://sh.rustup.rs \
            | sh -s -- -y --profile minimal --no-modify-path \
            && echo "installed cargo (via rustup)" \
            || echo "WARNING: cargo install via rustup failed" >&2
    elif command -v brew &>/dev/null; then
        brew install rust && echo "installed cargo (via brew rust)" \
            || echo "WARNING: cargo install via brew failed" >&2
    else
        echo "WARNING: cargo not found and no installer available (need curl or brew)" >&2
    fi
fi

if [[ -f "$HOME/.cargo/env" ]]; then
    # Load cargo for the current run after a fresh rustup install.
    . "$HOME/.cargo/env"
fi

if ! command -v bob &>/dev/null; then
    if command -v cargo &>/dev/null; then
        cargo install bob-nvim && echo "installed bob" \
            || echo "WARNING: bob install via cargo failed" >&2
    elif command -v brew &>/dev/null; then
        brew install bob && echo "installed bob" \
            || echo "WARNING: bob install via brew failed" >&2
    else
        echo "WARNING: bob not found and no installer available (need cargo or brew)" >&2
    fi
fi

if [[ ${#SKIPPED_FILES[@]} -gt 0 ]]; then
  echo ""
  echo "Skipped (already exist):"
  for f in "${SKIPPED_FILES[@]}"; do
    echo "  $f"
  done
fi
