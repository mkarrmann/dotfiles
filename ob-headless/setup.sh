#!/bin/bash
# Setup Obsidian headless sync on a Meta devserver.
#
# Prerequisites:
#   - Run `ob login` and `ob sync-setup --vault Personal --path ~/obsidian/Personal`
#     interactively once per machine (credentials are stored in ~/.config/obsidian-headless/).
#
# What this script does:
#   1. Downloads Node 22 (if not already present)
#   2. Installs obsidian-headless + ws npm packages
#   3. Patches the obsidian-headless lock verify for btrfs compatibility
#   4. Copies ob wrapper and proxy-preload.js from dotfiles
#   5. Installs and enables the systemd user service
set -euo pipefail

NODE_VERSION="22.22.0"
OB_DIR="$HOME/ob-headless"
DOTFILES_OB="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

mkdir -p "$OB_DIR" "$HOME/.local/log"

# 1. Download Node if missing
NODE_DIR="$OB_DIR/node-v${NODE_VERSION}-linux-x64"
if [[ ! -x "$NODE_DIR/bin/node" ]]; then
  echo "Downloading Node v${NODE_VERSION}..."
  curl -fsSL "https://nodejs.org/dist/v${NODE_VERSION}/node-v${NODE_VERSION}-linux-x64.tar.xz" \
    -o "$OB_DIR/node-v${NODE_VERSION}-linux-x64.tar.xz"
  tar -xf "$OB_DIR/node-v${NODE_VERSION}-linux-x64.tar.xz" -C "$OB_DIR"
  echo "Node v${NODE_VERSION} installed"
else
  echo "Node v${NODE_VERSION} already present"
fi

export PATH="$NODE_DIR/bin:$PATH"

# 2. Install npm packages
if [[ ! -d "$OB_DIR/node_modules/obsidian-headless" ]]; then
  echo "Installing obsidian-headless and ws..."
  cd "$OB_DIR"
  [[ -f package.json ]] || npm init -y --silent
  npm install obsidian-headless ws --silent
  echo "npm packages installed"
else
  echo "npm packages already present"
fi

# 3. Patch obsidian-headless lock verify for btrfs mtime precision
# HACK: btrfs loses sub-millisecond precision in utimes round-trip, causing
# the strict equality check to fail. Use a 2ms tolerance instead.
CLI_JS="$OB_DIR/node_modules/obsidian-headless/cli.js"
if grep -q 'this\.lockTime===this\.get()' "$CLI_JS" 2>/dev/null; then
  sed -i 's/this\.lockTime===this\.get()/Math.abs(this.lockTime-this.get())<2/g' "$CLI_JS"
  echo "patched cli.js lock verify for btrfs"
else
  echo "cli.js lock verify already patched (or not found)"
fi

# 4. Copy ob wrapper and proxy-preload from dotfiles
cp "$DOTFILES_OB/ob" "$OB_DIR/ob"
chmod +x "$OB_DIR/ob"
cp "$DOTFILES_OB/proxy-preload.js" "$OB_DIR/proxy-preload.js"
echo "copied ob wrapper and proxy-preload.js"

# 5. Install and enable systemd user service
mkdir -p "$HOME/.config/systemd/user"
cp "$DOTFILES_OB/obsidian-sync.service" "$HOME/.config/systemd/user/obsidian-sync.service"
systemctl --user daemon-reload
systemctl --user enable obsidian-sync.service
echo "systemd service installed and enabled"

# Start the service if sync is configured
if [[ -d "$HOME/.config/obsidian-headless/sync" ]] && ls "$HOME/.config/obsidian-headless/sync"/*/config.json &>/dev/null; then
  rm -rf "$HOME/obsidian/Personal/.obsidian/.sync.lock" 2>/dev/null
  systemctl --user restart obsidian-sync.service
  echo "obsidian-sync service started"
else
  echo ""
  echo "NOTE: Sync not yet configured. Run these commands interactively:"
  echo "  $OB_DIR/ob login"
  echo "  $OB_DIR/ob sync-setup --vault Personal --path ~/obsidian/Personal"
  echo "Then: systemctl --user start obsidian-sync.service"
fi
