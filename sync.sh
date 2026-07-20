#!/usr/bin/env bash
# Config reflection only: symlinks, generated config files, and staged
# systemd/launchd unit files. Fast, idempotent, and safe to run as often as
# you like — it NEVER restarts, reconciles, or otherwise disturbs running
# infrastructure. Staged unit changes are picked up on a service's next natural
# restart; new sessions re-read regenerated config on their own.
#
# Run this after editing dotfiles. For a full machine setup that also installs
# tools and converges live services, run init.sh (which runs this first).
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
  # This restart happens ONLY when the plist content changed — i.e. you
  # edited it and are reflecting that edit, which requires a reload.
  launchctl bootout "gui/$UID/$label" 2>/dev/null || true
  if launchctl bootstrap "gui/$UID" "$dst" 2>/dev/null; then
    echo "loaded $label"
  else
    echo "WARNING: launchctl bootstrap $label failed" >&2
  fi
}

retire_launchd_plist() {
  local label="$1"
  local dst="$HOME/Library/LaunchAgents/${label}.plist"
  launchctl bootout "gui/$UID/$label" 2>/dev/null || true
  if [[ -f "$dst" ]]; then
    rm -f "$dst"
    echo "retired $label"
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
      # A directory whose children contain the marker is a skill collection,
      # such as meta-powertools-vendored, rather than a malformed skill.
      if compgen -G "${src}*/${required_file}" >/dev/null; then
        continue
      fi
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
# Obsidian vault config (source of truth for AGENTS.md location)
link_one "$DOTFILES_DIR/claude_config/obsidian-vault.conf" "$HOME/.claude/obsidian-vault.conf"
# Hooks
sync_link_dir "$DOTFILES_DIR/claude_config/hooks" "$HOME/.claude/hooks" "*"
# Skills (shared between Claude Code and Codex)
mkdir -p "$HOME/.claude/skills"
sync_link_subdirs "$DOTFILES_DIR/agent_config/skills" "$HOME/.claude/skills" "SKILL.md"
sync_link_subdirs "$DOTFILES_DIR/agent_config/skills/meta-powertools-vendored" "$HOME/.claude/skills" "SKILL.md"
# Ensure settings.json has the statusline command configured (preserving other settings)
CLAUDE_SETTINGS="$HOME/.claude/settings.json"
if [[ ! -f "$CLAUDE_SETTINGS" ]]; then
  echo '{}' > "$CLAUDE_SETTINGS"
fi
tmp=$(jq '
  .permissions.defaultMode = "bypassPermissions" |
  .model = "claude-opus-4-8" |
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
sync_link_subdirs "$DOTFILES_DIR/agent_config/skills/meta-powertools-vendored" "$HOME/.codex/skills" "SKILL.md"

# default.rules is machine-specific — managed by Codex itself

# Cross-agent MCP wiring: copies plugins/custom-mcps/mcps/*.json into each
# agent's native config (Claude settings.json, Codex config.toml, Metacode
# opencode.json). Replaces the meta-powertools bundle's MCPs that we dropped
# to reclaim skill-description budget. See agent_config/README.md.
"$DOTFILES_DIR/agent_config/sync-mcps" all || \
  echo "WARNING: agent_config/sync-mcps failed" >&2

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
  # materialized. See acp-broker docs/RUNBOOK.md §3.2. sync_launchd_plist
  # only reloads a job when its plist content actually changed.
  mkdir -p "$HOME/Library/LaunchAgents" \
           "$HOME/.local/state/acp-broker" \
           "$HOME/.local/state/persistence-server" \
           "$HOME/.local/state/omnigent-host"
  sync_launchd_plist "$DOTFILES_DIR/launchd/com.mkarrmann.persistence-server.plist"
  sync_launchd_plist "$DOTFILES_DIR/launchd/com.mkarrmann.acp-broker.plist"
  sync_launchd_plist "$DOTFILES_DIR/launchd/com.mkarrmann.omnigent-host.plist"
  # The Omnigent server moved to the HUB devserver (systemd omnigent-server).
  # Retire the old Mac-local server job so it can't bind :6767 and collide with
  # the local failover proxy that exposes the HUB server on Mac localhost.
  retire_launchd_plist "com.mkarrmann.omnigent-server"
fi

# Linux-only: systemd --user units. Linger is expected to be enabled
# (`loginctl enable-linger`) so these survive logout and start at boot.
#
# This section only STAGES units: it links the unit files, reloads the systemd
# manager (which does NOT restart running services), pre-creates state dirs,
# writes the environment file, and enables units so they start at boot. It does
# NOT restart, reconcile, remount, or otherwise disturb anything already
# running — that live convergence belongs to init.sh and the reconcile timer.
if [[ "$(uname -s)" == "Linux" ]] && command -v systemctl &>/dev/null; then
  sync_link_dir "$DOTFILES_DIR/systemd" "$HOME/.config/systemd/user" "*.service"
  sync_link_dir "$DOTFILES_DIR/systemd" "$HOME/.config/systemd/user" "*.timer"
  # Hub ownership is dynamic. Only the reconcile timer starts at boot; it
  # starts hub units on the owner and an SSH client tunnel everywhere else.
  for unit_name in omnigent-server.service \
      omnigent-prodnet.service \
      omnigent-client-proxy.service \
      omnigent-google-chat.service \
      omnigent-diff-watcher.service \
      omnigent-snapshot.timer; do
    rm -f "$HOME/.config/systemd/user/default.target.wants/$unit_name" \
          "$HOME/.config/systemd/user/timers.target.wants/$unit_name"
  done
  # daemon-reload loads new/edited unit files into the manager. It does NOT
  # restart running services — they keep their current ExecStart until their
  # next natural restart, which is exactly the safe staging we want here.
  systemctl --user daemon-reload 2>/dev/null || true

  # Pre-create state dirs: systemd opens StandardOutput=append: BEFORE creating
  # StateDirectory=, so a unit's very first start fails 209/STDOUT if the dir is
  # absent. Creating them up front makes first start idempotent.
  mkdir -p "$HOME/.local/state/omnigent-server" \
           "$HOME/.local/state/omnigent-host" \
           "$HOME/.local/state/omnigent-prodnet" \
           "$HOME/.local/state/omnigent-client-proxy" \
           "$HOME/.local/state/omnigent-diff-watcher" \
           "$HOME/.local/state/omnigent-hub"

  # Omnigent server URL for systemd --user units (nvs@ nvim -> CodeCompanion,
  # and omnigent-host). Every client uses loopback: the owner reaches the
  # server directly and other Linux hosts use omnigent-client-proxy's SSH
  # forward. environment.d is read by the user manager at start.
  # Takes full effect after the next relogin / `systemctl --user daemon-reexec`.
  mkdir -p "$HOME/.config/environment.d"
  if [[ -x "$HOME/bin/omnigent-server-url" ]]; then
    printf 'OMNIGENT_URL=%s\n' "$("$HOME/bin/omnigent-server-url" 2>/dev/null || echo http://127.0.0.1:6767)" \
      > "$HOME/.config/environment.d/omnigent.conf"
  fi

  shopt -s nullglob
  for unit_src in "$DOTFILES_DIR"/systemd/*.service; do
    unit_name="$(basename "$unit_src")"
    # Template units (foo@.service) can't be enabled without an instance —
    # their instances are managed declaratively below from a per-host config.
    [[ "$unit_name" == *@.service ]] && continue
    # Timer activation owns this oneshot; starting it during every dotfiles
    # reconciliation would create an unnecessary extra archive.
    case "$unit_name" in
      omnigent-server.service|omnigent-prodnet.service|omnigent-client-proxy.service|omnigent-google-chat.service|omnigent-diff-watcher.service|omnigent-snapshot.service|omnigent-hub-reconcile.service)
        continue
        ;;
    esac
    # enable --now wires the unit for boot and starts it if stopped; it does
    # NOT restart a unit that is already running.
    if systemctl --user enable --now "$unit_name" &>/dev/null; then
      echo "enabled $unit_name"
    else
      echo "WARNING: failed to enable $unit_name (try: systemctl --user status $unit_name)" >&2
    fi
  done
  shopt -u nullglob

  # The retry timer runs everywhere. It enables hub-only services on the active
  # owner and maintains the loopback SSH tunnel on every other devserver. This
  # only enables the timer (idempotent); the actual reconciliation it drives
  # runs on its own schedule and is triggered eagerly by init.sh.
  systemctl --user enable --now omnigent-hub-reconcile.timer 2>/dev/null \
    || echo "WARNING: failed to enable omnigent-hub-reconcile.timer" >&2
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

if [[ ${#SKIPPED_FILES[@]} -gt 0 ]]; then
  echo ""
  echo "Skipped (already exist):"
  for f in "${SKIPPED_FILES[@]}"; do
    echo "  $f"
  done
fi
