# zprofile is sourced only in login shells.

# Environment and PATH (available to all login shells, even non-interactive ones
# like AeroSpace exec-and-forget).
if [[ -f ~/.shell_env ]]; then
  source ~/.shell_env
fi

# Interactive login shells source ~/.zshrc automatically after ~/.zprofile.
