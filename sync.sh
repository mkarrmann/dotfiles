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

CONFLICTS=()
SKILL_ISSUES=()

link_one() {
  local src="$1"
  local dst="$2"
  local want="${src%/}"

  if [[ ! -e "$src" ]]; then
    echo "ERROR: source missing: $src" >&2
    exit 1
  fi

  if [[ -L "$dst" ]]; then
    local have have_resolved want_resolved
    have="$(readlink "$dst")"
    if [[ "$have" == "$want" || "$have" == "$want/" ]]; then
      return 0
    fi
    if [[ -e "$dst" ]]; then
      have_resolved="$(readlink -f "$dst")"
      want_resolved="$(readlink -f "$want")"
      if [[ "$have_resolved" == "$want_resolved" ]]; then
        return 0
      fi
    fi
    CONFLICTS+=("$dst -> $have (expected $want)")
    return 0
  fi

  # A real file/dir at the destination shadows the dotfiles copy: the dotfiles
  # version is not what gets loaded, so edits to it silently have no effect.
  # Report whether the two agree, since that decides whether the shadow can just
  # be deleted or has local content that must be merged first.
  if [[ -e "$dst" ]]; then
    local kind="file"
    [[ -d "$dst" ]] && kind="directory"
    if diff -rq "$want" "$dst" >/dev/null 2>&1; then
      CONFLICTS+=("$dst — real $kind shadowing $want [identical; safe to replace]")
    else
      CONFLICTS+=("$dst — real $kind shadowing $want [CONTENT DIFFERS; merge first]")
    fi
    return 0
  fi

  ln -s "$src" "$dst"
  echo "linked $dst -> $src"
}

sync_link_dir() {
  local src_dir="$1"
  local dst_dir="$2"
  local pattern="$3"
  local priority_dir="${4:-}"
  local target

  mkdir -p "$dst_dir"

  shopt -s nullglob
  for src in "$src_dir"/$pattern; do
    if [[ -n "$priority_dir" && -e "$priority_dir/$(basename "$src")" ]]; then
      continue
    fi
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

# --- Skill scoping ----------------------------------------------------------
#
# Meta-specific skills are linked into each ~/checkoutN/.claude/skills instead
# of ~/.claude/skills, so they are only advertised while cwd is inside a Meta
# checkout. Both Claude Code and Omnigent walk the ancestor .claude/skills chain
# upward from cwd (verified 2026-08-04), so a skill linked at ~/checkoutN is
# visible from ~/checkoutN/fbsource and ~/checkoutN/configerator alike.
#
# A single global directory advertised ~70 Meta skills in every tree, including
# unrelated ones such as ~/repos/*. Beyond some list length the harness renders
# skills as bare names without their descriptions, which is what makes the
# relevant skill impossible to spot. Skills listed in skills-global.list stay
# global; everything else, including all of meta-powertools-vendored, is scoped.

SKILLS_SRC="$DOTFILES_DIR/agent_config/skills"
SKILLS_VENDORED="$SKILLS_SRC/meta-powertools-vendored"
SKILLS_GLOBAL_LIST="$DOTFILES_DIR/agent_config/skills-global.list"

skill_scope() {
  if [[ -f "$SKILLS_GLOBAL_LIST" ]] && grep -qxF -- "$1" "$SKILLS_GLOBAL_LIST"; then
    echo global
  else
    echo meta
  fi
}

skill_dirs() {
  local d
  shopt -s nullglob
  for d in "$SKILLS_SRC"/*/ "$SKILLS_VENDORED"/*/; do
    [[ "$(basename "$d")" == "meta-powertools-vendored" ]] && continue
    [[ -e "${d}SKILL.md" ]] || continue
    printf '%s\n' "$d"
  done
  shopt -u nullglob
}

link_skills_scoped() {
  local dst_parent="${1%/}"
  local scope="$2"
  local src name dst target
  mkdir -p "$dst_parent"

  # Drop links this script previously created that no longer belong here:
  # source deleted, or the skill moved between global and meta scope.
  shopt -s nullglob
  for dst in "$dst_parent"/*; do
    [[ -L "$dst" ]] || continue
    target="$(readlink "$dst")"
    [[ "$target" == "$SKILLS_SRC/"* ]] || continue
    name="$(basename "$dst")"
    if [[ ! -e "${target%/}/SKILL.md" ]] || [[ "$(skill_scope "$name")" != "$scope" ]]; then
      rm "$dst"
      echo "removed skill link $dst"
    fi
  done
  shopt -u nullglob

  while IFS= read -r src; do
    name="$(basename "$src")"
    [[ "$(skill_scope "$name")" == "$scope" ]] || continue
    link_one "$src" "$dst_parent/$name"
  done < <(skill_dirs)
}

# Claude Code derives a missing `name` from the directory name, but Omnigent
# requires it and drops the skill with only a stderr warning, so treat both
# fields as mandatory rather than letting a skill vanish from one harness.
validate_skill_frontmatter() {
  local d name fm
  shopt -s nullglob
  for d in "$SKILLS_SRC"/*/ "$SKILLS_VENDORED"/*/; do
    name="$(basename "$d")"
    [[ "$name" == "meta-powertools-vendored" ]] && continue
    if [[ ! -e "${d}SKILL.md" ]]; then
      SKILL_ISSUES+=("$name: no SKILL.md (directory is not a loadable skill)")
      continue
    fi
    if [[ "$(head -n1 "${d}SKILL.md")" != "---" ]]; then
      SKILL_ISSUES+=("$name: SKILL.md has no YAML frontmatter (loads in neither harness)")
      continue
    fi
    fm="$(awk 'NR>1 && $0=="---"{exit} NR>1' "${d}SKILL.md")"
    grep -qE '^name:' <<<"$fm" ||
      SKILL_ISSUES+=("$name: frontmatter missing 'name:' (Omnigent silently drops it)")
    grep -qE '^description:' <<<"$fm" ||
      SKILL_ISSUES+=("$name: frontmatter missing 'description:' (no trigger text for the model)")
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
sync_link_dir "$DOTFILES_DIR/bin" "$HOME/bin" "*" "$platform_bin"

# wofi
mkdir -p "$HOME/.config/wofi"
link_one "$DOTFILES_DIR/wofi_config" "$HOME/.config/wofi/config"

# sway
mkdir -p "$HOME/.config/sway"
link_one "$DOTFILES_DIR/sway_config" "$HOME/.config/sway/config"

# Claude Code
mkdir -p "$HOME/.claude/projects" "$HOME/.claude/rules" "$HOME/.claude/hooks" "$HOME/.claude/meta"
link_one "$DOTFILES_DIR/claude_config/CLAUDE.md" "$HOME/.claude/CLAUDE.md"
link_one "$DOTFILES_DIR/agent_config/global-development-preferences.md" "$HOME/.claude/rules/global-development-preferences.md"
# Clean up stale optional rule links that no longer exist in dotfiles.
if [[ -L "$HOME/.claude/rules/personal-style.md" && ! -e "$HOME/.claude/rules/personal-style.md" ]]; then
  rm "$HOME/.claude/rules/personal-style.md"
  echo "removed stale link $HOME/.claude/rules/personal-style.md"
fi
# Meta tpai rules/skills integration overrides. Kept here rather than left as a
# machine-local file so every host suppresses the duplicate skill injection; see
# claude_config/meta-config.toml for the measurement behind it.
link_one "$DOTFILES_DIR/claude_config/meta-config.toml" "$HOME/.claude/meta/config.toml"
link_one "$DOTFILES_DIR/claude_config/statusline.sh" "$HOME/.claude/statusline.sh"
# Agent Manager
mkdir -p "$HOME/.claude/agent-manager/bin" "$HOME/.claude/statusline.d"
sync_link_dir "$DOTFILES_DIR/claude_config/agent-manager" "$HOME/.claude/agent-manager/bin" "*.sh"
sync_link_dir "$DOTFILES_DIR/claude_config/agent-manager" "$HOME/.claude/agent-manager/bin" "*.py"
# Obsidian vault config (source of truth for AGENTS.md location)
link_one "$DOTFILES_DIR/claude_config/obsidian-vault.conf" "$HOME/.claude/obsidian-vault.conf"
# Hooks
sync_link_dir "$DOTFILES_DIR/claude_config/hooks" "$HOME/.claude/hooks" "*"
# Skills
validate_skill_frontmatter
link_skills_scoped "$HOME/.claude/skills" global
# Omnigent currently creates each private Codex home under
# <session-workspace>/.codex-tmp. Redirect that directory outside Eden so
# SQLite/WAL and transcript writes do not become repository file-change events.
# The launch preflight in codecompanion.lua retries active directories after
# their current session closes.
HGIGNORE_LOCAL="$HOME/.hgignore-local"
touch "$HGIGNORE_LOCAL"
if ! grep -Fqx '.codex-tmp/**' "$HGIGNORE_LOCAL"; then
  printf '\nsyntax: glob\n.codex-tmp\n.codex-tmp/**\nsyntax: regexp\n' >> "$HGIGNORE_LOCAL"
  echo "ignored .codex-tmp in $HGIGNORE_LOCAL"
fi
touch "$HOME/.hgrc"
if ! grep -Fqx "ignore.omnigent-codex-tmp = $HGIGNORE_LOCAL" "$HOME/.hgrc"; then
  printf '\n[ui]\nignore.omnigent-codex-tmp = %s\n' "$HGIGNORE_LOCAL" >> "$HOME/.hgrc"
  echo "configured Sapling to read $HGIGNORE_LOCAL"
fi
# A workspace root is any real directory directly under $HOME that holds an
# fbsource or configerator checkout: ~/checkoutN, plus older layouts such as
# ~/local/configerator. Detecting them beats globbing checkout* so a checkout
# outside the naming convention still gets its skills.
#
# Symlinked candidates count: ~/local is a symlink to the devserver data volume
# and holds real checkouts. Roots are deduplicated by resolved path so a
# workspace reachable under two names is only linked once, and the links are
# created under the resolved path because cwd resolution is physical.
#
# $HOME itself is never a candidate (the glob starts one level down), so the
# ~/fbsource and ~/configerator convenience symlinks cannot promote every
# directory on the machine into a Meta workspace.
seen_ws=""
shopt -s nullglob
for ws in "$HOME"/*/; do
  [[ -d "${ws}fbsource" || -d "${ws}configerator" ]] || continue
  # A repo checkout is never a workspace root, even though the configerator
  # checkout does contain a directory called configerator/. Linking into one
  # would drop 56 untracked symlinks inside a source repo.
  [[ -e "${ws}.hg" || -e "${ws}.sl" || -e "${ws}.git" ]] && continue
  ws_real="$(readlink -f "${ws%/}")"
  if printf '%s\n' "$seen_ws" | grep -Fqx -- "$ws_real"; then
    continue
  fi
  seen_ws="${seen_ws}${seen_ws:+$'\n'}${ws_real}"
  link_skills_scoped "$ws_real/.claude/skills" meta
  # Workspace-layout rules belong to the checkout, not to every machine, so they
  # live here rather than in global-development-preferences.md. Both names are
  # needed and neither duplicates the other: Claude Code reads only CLAUDE.md
  # (and walks up to it from a subdirectory), Codex reads only AGENTS.md (and
  # only in its own cwd, so it picks this up for sessions started at the
  # workspace root -- the normal case -- while sessions started inside a repo
  # get that repo's own AGENTS.md instead).
  link_one "$DOTFILES_DIR/agent_config/meta-workspace-preferences.md" "$ws_real/CLAUDE.md"
  link_one "$DOTFILES_DIR/agent_config/meta-workspace-preferences.md" "$ws_real/AGENTS.md"
  for repo_name in fbsource configerator; do
    repo_path="$ws_real/$repo_name"
    [[ -d "$repo_path" ]] || continue
    "$DOTFILES_DIR/bin/omnigent-codex-tmp-ensure" "$repo_path" ||
      echo "WARNING: Codex temp redirect deferred for $repo_path" >&2
  done
done
shopt -u nullglob

# Skills hand-linked into ~/.claude/skills from inside a repo checkout pin every
# workspace to whichever checkout the link happens to name (often indirectly, via
# the ~/fbsource convenience symlink) and duplicate the harness's own
# ancestor-scoped discovery, which already finds them when cwd is in the owning
# subtree. Match on the resolved path so the indirection does not hide them.
shopt -s nullglob
for dst in "$HOME"/.claude/skills/*; do
  [[ -L "$dst" ]] || continue
  skill_target="$(readlink -f "$dst")"
  if [[ "$skill_target" == */fbsource/* || "$skill_target" == */configerator/* ]]; then
    rm "$dst"
    echo "removed checkout-pinned skill link $dst -> $skill_target"
  fi
done
shopt -u nullglob
# Ensure settings.json has the statusline command configured (preserving other settings)
CLAUDE_SETTINGS="$HOME/.claude/settings.json"
if [[ ! -f "$CLAUDE_SETTINGS" ]]; then
  echo '{}' > "$CLAUDE_SETTINGS"
fi
tmp=$(jq '
  .permissions.defaultMode = "bypassPermissions" |
  .model = "claude-opus-5[1m]" |
  # Claude Code budgets the skill listing at
  #   skillListingBudgetFraction * context_tokens * chars_per_token(3)
  # and when the listing exceeds it, drops EVERY evictable description
  # rather than truncating -- leaving a bare name list with no trigger
  # text. The default 0.01 yields only 6,000 chars against a 200k
  # window, which the bundled skills alone overrun.
  #
  # Note the window is 1e6 only when the model id literally contains
  # "[1m]"; anything that strips that suffix (Omnigent passes
  # --model claude-opus-5) falls back to 200k. So size this for the
  # 200k case: measured listing is ~39,500 chars, needing >= 0.066.
  # 0.10 gives 60,000 chars, ~50% headroom for new skills.
  #
  # This is a cap, not a reservation: the real per-turn cost is the
  # listing itself (~39.5KB, ~13k tokens), unchanged by raising it.
  # Leave skillListingMaxDescChars at its 1536 default -- the longest
  # description today is 1135 (dataviz), so nothing is truncated.
  .skillListingBudgetFraction = 0.10 |
  del(.skillListingMaxDescChars) |
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
# Codex resolves its home as $CODEX_HOME, falling back to ~/.codex
# (codex-rs/utils/home-dir/src/lib.rs). Mirror that precedence so a machine
# that relocates CODEX_HOME is still configured, and so a set-but-missing
# CODEX_HOME gets created here rather than failing Codex's own startup check.
codex_home="${CODEX_HOME:-$HOME/.codex}"
mkdir -p "$codex_home/rules" "$codex_home/skills"

# Portable Codex template + machine-local overrides (config.local.toml)
codex_config="$codex_home/config.toml"
codex_existed=$([[ -f "$codex_config" ]] && echo true || echo false)
sed "s|__HOME__|$HOME|g" "$DOTFILES_DIR/codex_config/config.template.toml" > "$codex_config"
if [[ -f "$codex_home/config.local.toml" ]]; then
  echo "" >> "$codex_config"
  cat "$codex_home/config.local.toml" >> "$codex_config"
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

# Shared development rules. Codex loads global instructions from
# $codex_home/AGENTS.override.md then $codex_home/AGENTS.md, upstream and at
# Meta alike (codex-rs/codex-home/src/instructions/mod.rs). AGENTS.override.md
# is left free as a machine-local escape hatch that wins over this link.
link_one "$DOTFILES_DIR/agent_config/global-development-preferences.md" "$codex_home/AGENTS.md"
# $codex_home/rules is the exec-policy store: the loader keeps only entries
# whose extension is `rules` (codex-rs/core/src/exec_policy.rs), so the .md we
# used to link there was silently ignored rather than read as instructions.
codex_retired_rule="$codex_home/rules/global-development-preferences.md"
if [[ -L "$codex_retired_rule" ]]; then
  rm "$codex_retired_rule"
  echo "removed stale link $codex_retired_rule"
fi
# Shared skills
sync_link_subdirs "$DOTFILES_DIR/agent_config/skills" "$codex_home/skills" "SKILL.md"
sync_link_subdirs "$DOTFILES_DIR/agent_config/skills/meta-powertools-vendored" "$codex_home/skills" "SKILL.md"

# default.rules is machine-specific — managed by Codex itself

# Metacode
# Metacode's global config dir is OPENCODE_CONFIG_DIR, else <xdg-config>/opencode
# (packages/core/src/global.ts, via the xdg-basedir package — plain XDG on every
# platform, macOS included). Mirror that precedence so a relocated config dir is
# still configured.
opencode_config="${OPENCODE_CONFIG_DIR:-${XDG_CONFIG_HOME:-$HOME/.config}/opencode}"
mkdir -p "$opencode_config"

# Shared development rules. Metacode reads AGENTS.md from that dir as its global
# instructions, so the same canonical file backs all three agents.
link_one "$DOTFILES_DIR/agent_config/global-development-preferences.md" "$opencode_config/AGENTS.md"

# Skills are wired by sync-mcps below (Metacode loads them from
# opencode.json skills.paths, not from symlinks).

# Cross-agent MCP wiring: copies plugins/custom-mcps/mcps/*.json into each
# agent's native config (Claude settings.json, Codex config.toml, Metacode
# opencode.json). Replaces the meta-powertools bundle's MCPs that we dropped
# to reclaim skill-description budget. See agent_config/README.md.
"$DOTFILES_DIR/agent_config/sync-mcps" all || \
  echo "WARNING: agent_config/sync-mcps failed" >&2

# Omnigent: propagate shared, machine-agnostic client preferences
# (omnigent_config/config.shared.yaml) into this machine's live
# ~/.omnigent/config.yaml. Deep-merges only the declared keys, preserving
# machine-specific host:/server:/acp:. Self-skips before omnigent is
# installed (fresh bootstrap re-runs it from init.sh stage 2).
"$DOTFILES_DIR/bin/omnigent-config-ensure" || \
  echo "WARNING: omnigent-config-ensure failed (shared prefs not applied)" >&2

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
  # materialized. sync_launchd_plist only reloads a job when its plist
  # content actually changed.
  mkdir -p "$HOME/Library/LaunchAgents" \
           "$HOME/.local/state/omnigent-host"
  sync_launchd_plist "$DOTFILES_DIR/launchd/com.mkarrmann.omnigent-host.plist"
  # The Omnigent server moved to the HUB devserver (systemd omnigent-server).
  # Retire the old Mac-local server job so it can't bind :6767 and collide with
  # the local failover proxy that exposes the HUB server on Mac localhost.
  retire_launchd_plist "com.mkarrmann.omnigent-server"
  # ACP-broker and its persistence-server are deprecated (superseded by
  # omnigent). Retire the old Mac-local jobs on sync.
  retire_launchd_plist "com.mkarrmann.acp-broker"
  retire_launchd_plist "com.mkarrmann.persistence-server"
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

  # Omnigent env for systemd --user units (nvs@ nvim -> CodeCompanion, and
  # omnigent-host). Every client uses loopback: the owner reaches the
  # server directly and other Linux hosts use omnigent-client-proxy's SSH
  # forward. environment.d is read by the user manager at start.
  # Takes full effect after the next relogin / `systemctl --user daemon-reexec`.
  #
  # TIKTOKEN_CACHE_DIR points at the vendored BPE blob — tiktoken's download
  # host does not resolve here, so Omnigent's count_tokens() fails without it
  # (see omnigent_config/tiktoken-cache/README.md).
  mkdir -p "$HOME/.config/environment.d"
  {
    printf 'TIKTOKEN_CACHE_DIR=%s\n' "$DOTFILES_DIR/omnigent_config/tiktoken-cache"
    # Pin litellm to its bundled offline cost map; the github refresh it tries on
    # import cannot resolve here (see bin/omnigent-version-ensure).
    printf 'LITELLM_LOCAL_MODEL_COST_MAP=True\n'
    if [[ -x "$HOME/bin/omnigent-server-url" ]]; then
      printf 'OMNIGENT_URL=%s\n' "$("$HOME/bin/omnigent-server-url" 2>/dev/null || echo http://127.0.0.1:6767)"
    fi
  } > "$HOME/.config/environment.d/omnigent.conf"

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

if [[ ${#SKILL_ISSUES[@]} -gt 0 ]]; then
  {
    echo ""
    echo "SKILL PROBLEMS (${#SKILL_ISSUES[@]}) — these skills will not load correctly:"
    for f in "${SKILL_ISSUES[@]}"; do
      echo "  $f"
    done
  } >&2
fi

# Shadows are silent by nature: the dotfiles copy still exists and still looks
# authoritative, so without this report an edit there can go nowhere for months.
if [[ ${#CONFLICTS[@]} -gt 0 ]]; then
  {
    echo ""
    echo "SHADOWED (${#CONFLICTS[@]}) — destination is not the dotfiles symlink, so the"
    echo "dotfiles copy is NOT what gets loaded and edits to it have no effect:"
    for f in "${CONFLICTS[@]}"; do
      echo "  $f"
    done
    echo ""
    echo "To resolve an [identical] entry:  rm <dest> && ./sync.sh"
    echo "For [CONTENT DIFFERS], copy anything worth keeping into the dotfiles source first."
  } >&2
fi
