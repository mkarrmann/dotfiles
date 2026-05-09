# Shared env (source-controlled) — PATH, env vars needed by all shell types
if [[ -f ~/.shell_env ]]; then
  source ~/.shell_env
fi

# Local zshenv (not source-controlled) — runs for ALL shell types
if [[ -f ~/.zshenv.local ]]; then
  source ~/.zshenv.local
fi
. "$HOME/.cargo/env"
