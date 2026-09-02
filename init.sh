#!/usr/bin/env bash
# Full machine bootstrap. Runs in three stages:
#
#   1. sync.sh  — reflect config: symlinks, generated files, staged unit files.
#                 Safe and idempotent; never disturbs running infrastructure.
#   2. installs — tools and dependencies (network-bound, slow, non-disruptive).
#   3. converge — restarts/reconciles RUNNING services so the machine is live
#                 on the latest code. THIS is the heavy-handed part.
#
# Run init.sh on a new machine, or when you deliberately want everything
# installed and converged. For a quick "apply my dotfile edits" pass that only
# touches config and NEVER restarts infrastructure, run sync.sh instead.
set -uo pipefail

DOTFILES_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

# ---------------------------------------------------------------------------
# 1. Reflect config: symlinks, generated files, staged systemd/launchd units.
# ---------------------------------------------------------------------------
"$DOTFILES_DIR/sync.sh" || echo "WARNING: config sync (sync.sh) failed" >&2

# ---------------------------------------------------------------------------
# 2. Installs / dependency bootstrap (network-bound; non-disruptive).
# ---------------------------------------------------------------------------
"$DOTFILES_DIR/bin/codecompanion-fork-ensure" \
  || echo "WARNING: CodeCompanion fork bootstrap failed; Lazy may be unable to install it" >&2

# Cross-agent plugin install: uninstalls dropped plugins, cleans orphan caches,
# installs everything in plugins.list across all agents. No-op if agent-market
# is not on PATH (skips with a single warning).
"$DOTFILES_DIR/agent_config/bootstrap-plugins" || \
  echo "WARNING: agent_config/bootstrap-plugins failed" >&2

# The operator CLI runs locally on both hub devservers and on the Mac, which
# orchestrates authenticated handoffs through x2ssh.
"$DOTFILES_DIR/bin/omnigent-version-ensure" \
  || echo "WARNING: failed to install the pinned Omnigent version" >&2
hub_project="$DOTFILES_DIR/services/omnigent-hub"
if [[ -f "$hub_project/uv.lock" ]] && command -v uv &>/dev/null; then
  (cd "$hub_project" && uv sync --frozen --all-groups) \
    || echo "WARNING: omnigent-hub dependency sync failed" >&2
fi
diff_watcher_project="$DOTFILES_DIR/services/omnigent-diff-watcher"
if [[ -f "$diff_watcher_project/uv.lock" ]] && command -v uv &>/dev/null; then
  (cd "$diff_watcher_project" && uv sync --frozen --all-groups) \
    || echo "WARNING: omnigent-diff-watcher dependency sync failed" >&2
  if [[ -x "$diff_watcher_project/.venv/bin/omnigent-diff-watcher" ]]; then
    "$diff_watcher_project/.venv/bin/omnigent-diff-watcher" \
      --config "$diff_watcher_project/config.toml" status --json >/dev/null \
      || echo "WARNING: omnigent-diff-watcher state bootstrap failed" >&2
  fi
fi

# Omnigent: register the dvsc-core ACP agent so it shows in the CodeCompanion
# omnigent picker (<leader>aM / <leader>aA), alongside polly/debby. Idempotent
# and self-skips where omnigent isn't installed; reconciles the per-host server
# URL, merges the acp: block of ~/.omnigent/config.yaml, and registers the dvsc
# builtin on the active Linux hub without restarting a running server. macOS,
# the standby, and peer clients only reconcile config.yaml; they never open a
# local chat.db. See
# bin/omnigent-dvsc-ensure and omnigent_config/agents/dvsc/.
"$DOTFILES_DIR/bin/omnigent-dvsc-ensure" \
    || echo "WARNING: omnigent-dvsc-ensure failed (dvsc agent may not appear in the picker)" >&2

# Omnigent: propagate shared, machine-agnostic client preferences into
# ~/.omnigent/config.yaml. Runs here as well as in sync.sh so a fresh
# bootstrap (where the stage-1 sync ran before omnigent was installed and
# self-skipped) still applies them once the install above completes.
"$DOTFILES_DIR/bin/omnigent-config-ensure" \
    || echo "WARNING: omnigent-config-ensure failed (shared prefs not applied)" >&2

# Omnigent: shim the managed-Codex login probe. Runs here as well as in sync.sh
# for the same reason as config-ensure above — the stage-1 sync self-skips when
# omnigent is not yet installed, and the dismissal half needs its interpreter.
"$DOTFILES_DIR/bin/omnigent-codex-login-ensure" \
    || echo "WARNING: omnigent-codex-login-ensure failed (codex-native first turns may fail)" >&2

# Omnigent: register the direct-harness builtin agents (claude-sdk, codex) so
# they show in the CodeCompanion omnigent picker (<leader>aM / <leader>aA)
# alongside polly/debby/dvsc. The picker's model/effort steps key off each
# agent's harness family, so registering the specs is the whole job. Idempotent
# and self-skips off the active Linux hub. See bin/omnigent-agents-ensure and
# omnigent_config/agents/{claude,codex}/.
"$DOTFILES_DIR/bin/omnigent-agents-ensure" \
    || echo "WARNING: omnigent-agents-ensure failed (claude/codex agents may not appear in the picker)" >&2

# ---------------------------------------------------------------------------
# 3. Live convergence (Linux). Restarts/reconciles RUNNING services so the
#    host is live on the latest code and routing. This is the heavy-handed
#    part that can momentarily disturb in-flight sessions; sync.sh omits all of
#    it, and the omnigent-hub-reconcile.timer performs the same reconciliation
#    continuously on its own schedule.
# ---------------------------------------------------------------------------
if [[ "$(uname -s)" == "Linux" ]] && command -v systemctl &>/dev/null; then
  # A running service keeps the old ExecStart process across daemon-reload.
  # Refresh an existing client tunnel in place, but do not stop it first: a
  # candidate may temporarily lack a delegated Persistent Storage credential,
  # and its last validated route must remain usable while reconciliation waits.
  systemctl --user try-restart omnigent-client-proxy.service 2>/dev/null || true
  # A code pull updates the watcher's venv and sources in place. Restart only
  # when it is already active so the owner loads the new code; try-restart is a
  # no-op on the standby, whose gate and reconciler keep it stopped.
  systemctl --user try-restart omnigent-diff-watcher.service 2>/dev/null || true

  # Materialize routing before any client or service resolves its server URL.
  # Candidates read Persistent Storage directly; ordinary devservers discover
  # through the candidates and never need the shared mount themselves.
  if "$DOTFILES_DIR/bin/omnigent-server-url" --is-candidate \
      && [[ -x "$hub_project/.venv/bin/omnigent-hub" ]]; then
    "$DOTFILES_DIR/bin/omnigent-hub" cache-routing --force-remount --json >/dev/null \
      || echo "WARNING: failed to mount Persistent Storage and refresh the Omnigent active-hub cache; run 'omnigent-hub status' interactively" >&2
    "$DOTFILES_DIR/bin/omnigent-retire-legacy-standby" \
      || echo "WARNING: failed to retire legacy Omnigent standby launchers" >&2
  elif [[ -x "$hub_project/.venv/bin/omnigent-hub" ]]; then
    "$DOTFILES_DIR/bin/omnigent-hub" discover --json >/dev/null \
      || echo "WARNING: failed to discover the active Omnigent hub through the configured candidates" >&2
  fi

  # The private Google Chat bridge runs only beside the central server. Its
  # stable personal policy is tracked; the hub check materializes a private
  # runtime env containing the resolved loopback server URL. Other devservers
  # install the same unit, whose ExecCondition keeps it inactive.
  gchat_project="$DOTFILES_DIR/services/omnigent-google-chat"
  "$DOTFILES_DIR/bin/omnigent-google-chat-ensure" \
    || echo "WARNING: omnigent-google-chat runtime config generation failed" >&2
  if "$DOTFILES_DIR/bin/omnigent-server-url" --is-candidate \
      && [[ -x /usr/local/bin/meta ]] \
      && [[ -f "$gchat_project/uv.lock" ]] \
      && command -v uv &>/dev/null; then
    (cd "$gchat_project" && uv sync --frozen --all-groups) \
      || echo "WARNING: omnigent-google-chat dependency sync failed" >&2
  fi

  # The reconcile timer (enabled by sync.sh) enables hub-only services on the
  # active owner and maintains the loopback SSH tunnel elsewhere. Trigger one
  # pass eagerly so a fresh bootstrap converges without waiting for the timer.
  "$DOTFILES_DIR/bin/omnigent-hub" reconcile-services --json >/dev/null \
    || echo "WARNING: initial Omnigent service reconciliation failed" >&2

  omnigent_onboarded=false
  for _ in $(seq 1 15); do
    if "$DOTFILES_DIR/bin/omnigent-onboard-check"; then
      omnigent_onboarded=true
      break
    fi
    sleep 2
  done
  if [[ "$omnigent_onboarded" != true ]]; then
    echo "WARNING: Omnigent onboarding did not converge; run 'omnigent-hub status' for the specific failing invariant" >&2
  fi

  # Per-session nvim daemons (nvs@SESSION.service instances).
  # The list is host-scoped, because ~/.config/nvs is dotsync2-managed and so
  # is identical on every devserver; see bin/nvs-sessions-file for which file
  # wins. Lines: `SESSION_NAME [WORKDIR]`. Comments with #, blank ok.
  if nvs_sessions="$("$DOTFILES_DIR/bin/nvs-sessions-file")"; then
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

# ---------------------------------------------------------------------------
# 4. Shell & toolchain installs (oh-my-zsh, tpm, cargo, bob, nori).
# ---------------------------------------------------------------------------
# Nori CLI
if ! command -v nori &>/dev/null; then
    "$DOTFILES_DIR/bin/install-or-upgrade-nori" && echo "installed nori" \
        || echo "WARNING: nori install failed" >&2
fi

# Omnigent TLS front door (macOS only; self-skips elsewhere). Installs caddy
# and reports whether its local CA is trusted -- it deliberately does not TRUST
# it here, since that needs sudo and this script runs unattended at login.
# See bin-macos/omnigent-tls-ensure.
"$DOTFILES_DIR/bin-macos/omnigent-tls-ensure" \
    || echo "WARNING: omnigent-tls-ensure failed (desktop app may not connect)" >&2

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

# StyLua, pinned to the version the CodeCompanion fork's format CI uses. Unlike
# bob above this is version-checked rather than presence-checked, because a
# formatter that drifts between machines rewrites files depending on where you
# edited. See bin/stylua-ensure.
"$DOTFILES_DIR/bin/stylua-ensure" \
    || echo "WARNING: stylua install failed (make format will be unavailable)" >&2

# marksman, the Markdown language server. Non-fatal: GitHub release assets are
# not reachable from every host here, and nvim leaves marksman disabled when the
# binary is absent rather than erroring. See bin/marksman-ensure.
"$DOTFILES_DIR/bin/marksman-ensure" \
    || echo "WARNING: marksman install failed (markdown LSP will stay disabled)" >&2
