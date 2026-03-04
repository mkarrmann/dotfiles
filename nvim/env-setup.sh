#!/bin/bash
# Neovim environment setup
# Source this file to enable environment-specific Neovim workarounds
#
# Add to your .localrc/.bashrc/.zshrc:
#   source ~/dotfiles/nvim/env-setup.sh

# Auto-detect if we need the wrapper
if [[ "$http_proxy" == *"fwdproxy"* ]] || [[ -n "$THRIFT_TLS_CL_CERT_PATH" ]]; then
    # At Meta or similar environment that needs the wrapper
    if [[ -x "$HOME/dotfiles/bin/nvim-env-wrapper" ]]; then
        alias nvim="$HOME/dotfiles/bin/nvim-env-wrapper"
    elif [[ -x "$HOME/bin/nvim-env-wrapper" ]]; then
        # Fallback if symlinked to ~/bin
        alias nvim="$HOME/bin/nvim-env-wrapper"
    fi
fi

# Optional: Utility function for manual Lazy sync at Meta
nvim-lazy-sync() {
    echo "Attempting to sync Lazy plugins (may fail due to proxy)..."
    (
        unset THRIFT_TLS_CL_CERT_PATH THRIFT_TLS_CL_KEY_PATH
        NO_PROXY="${NO_PROXY},github.com,*.github.com" nvim --headless "+Lazy! sync" +qa
    )
}