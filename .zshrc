# If not running interactively, don't do anything
[[ ! -o interactive ]] && return

# Oh My Zsh
export ZSH="$HOME/.oh-my-zsh"
ZSH_THEME=""
plugins=(git vi-mode)
if [[ -f $ZSH/oh-my-zsh.sh ]]; then
  source $ZSH/oh-my-zsh.sh
fi
setopt PUSHD_SILENT

# Shared config (env, PATH, aliases, functions, tool init)
if [[ -f ~/.shellrc ]]; then
  source ~/.shellrc
fi

# --- optional git prompt (fallback) ---
if [[ -f ~/.git-prompt.sh ]]; then
  source ~/.git-prompt.sh
fi

# ---- SCM provider wrapper (localrc wins, git fallback, else empty) ----
__prompt_scm() {
  if (( $+functions[_scm_prompt] )); then
    _scm_prompt
  elif (( $+functions[__git_ps1] )); then
    __git_ps1 " (%s)"
  else
    printf ""
  fi
}

# ---- run before each prompt (zsh equivalent of PROMPT_COMMAND) ----
precmd() {
  __PROMPT_STATUS=$?
  __PROMPT_SCM="$(__prompt_scm)"

  if [[ -n "$TMUX" && -n "$TMUX_PANE" ]]; then
    tmux setenv -g "TMUX_LOC_${TMUX_PANE#%}" "$__PROMPT_SCM"
  fi
}

# ---- base prompt text (can be overridden by localrc) ----
PROMPT_BASE=${PROMPT_BASE:-'[%~'}

# ---- build PROMPT (reads $__PROMPT_* dynamically each prompt) ----
setopt PROMPT_SUBST

PROMPT='%{%F{yellow}%}'
PROMPT+="${PROMPT_BASE}"
PROMPT+='${__PROMPT_SCM}'
PROMPT+=']%{%f%} '
PROMPT+='%{%B%F{red}%}${__PROMPT_STATUS}%{%f%b%} '
PROMPT+='%{%F{green}%}%D{%H:%M:%S}%{%f%} '
PROMPT+='%# '

export PROMPT

# History
HISTSIZE=1000000
SAVEHIST=1000000
HISTFILE=~/.zsh_history_actual
setopt APPEND_HISTORY
setopt INC_APPEND_HISTORY
setopt EXTENDED_HISTORY
setopt HIST_IGNORE_DUPS
# oh-my-zsh enables SHARE_HISTORY, which re-reads the history file before each
# prompt. This causes externally-synced commands from other machines to appear
# at the top of the scrollback mid-session. Disable it so up-arrow always
# starts with this shell's most recent command.
unsetopt SHARE_HISTORY

# Local zsh-only config (not source-controlled)
if [[ -f ~/.zshrc.local ]]; then
  source ~/.zshrc.local
fi

# Sync Neovim's tab-local cwd when cd-ing inside a Neovim terminal.
chpwd() {
  if [[ -n "$NVIM" ]]; then
    command nvim --server "$NVIM" --remote-expr "execute('silent tcd '.fnameescape('$(pwd)'))" &!
  fi
}

