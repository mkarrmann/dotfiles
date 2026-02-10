# If not running interactively, don't do anything
[[ ! -o interactive ]] && return

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

# Keybindings: vi mode history-search on Up/Down (equivalent of .inputrc)
bindkey '\e[A' history-search-backward
bindkey '\e[B' history-search-forward

# Completion
autoload -Uz compinit && compinit
