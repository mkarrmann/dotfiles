# If not running interactively, don't do anything
[[ ! -o interactive ]] && return

# Oh My Zsh enables SHARE_HISTORY while it loads. Point it at /dev/null so it
# cannot eagerly ingest the unbounded, dotsync2-merged cross-devserver archive;
# the bounded history setup below imports only the useful tail explicitly.
HISTFILE=/dev/null

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

# History uses two files with deliberately different contracts:
#
# - history: local, bounded, and used for normal zsh persistence
# - .zsh_history_actual: append-only cross-devserver archive merged by dotsync2
#
# dotsync2's history merge does not propagate truncation, so using its archive
# directly as HISTFILE makes every fresh shell load and periodically rewrite an
# ever-growing file. Importing only its tail keeps cross-devserver recall while
# bounding startup work and per-shell memory.
ZSH_HISTORY_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/zsh"
ZSH_HISTORY_ARCHIVE="$HOME/.zsh_history_actual"
ZSH_HISTORY_IMPORT_LINES=100000
command mkdir -p -m 700 -- "$ZSH_HISTORY_DIR"
HISTFILE="$ZSH_HISTORY_DIR/history"
if [[ ! -e "$HISTFILE" ]]; then
  (umask 077; : >| "$HISTFILE")
fi
if [[ ! -e "$ZSH_HISTORY_ARCHIVE" ]]; then
  (umask 077; : >| "$ZSH_HISTORY_ARCHIVE")
fi

HISTSIZE=100000
SAVEHIST=100000
setopt APPEND_HISTORY
setopt INC_APPEND_HISTORY
setopt EXTENDED_HISTORY
setopt HIST_IGNORE_DUPS
setopt HIST_IGNORE_ALL_DUPS
setopt HIST_IGNORE_SPACE
# oh-my-zsh enables SHARE_HISTORY, which re-reads the history file before each
# prompt. This causes externally-synced commands from other machines to appear
# at the top of the scrollback mid-session. Disable it so up-arrow always
# starts with this shell's most recent command.
unsetopt SHARE_HISTORY

if [[ -s "$ZSH_HISTORY_ARCHIVE" ]]; then
  # `tail` seeks from the end of a regular file, so this remains bounded even
  # as the cross-devserver archive grows. Drop a leading multiline-command
  # fragment if the line boundary happens to land inside one history entry.
  fc -R =(
    command tail -n "$ZSH_HISTORY_IMPORT_LINES" -- "$ZSH_HISTORY_ARCHIVE" |
      command awk 'found || /^: [0-9]+:[0-9]+;/ { found = 1; print }'
  )
else
  # The local file is a fallback if the archive is unavailable or has not been
  # populated yet; normally every command is synchronously written to both.
  fc -R "$HISTFILE"
fi

autoload -Uz add-zsh-hook
zmodload zsh/datetime
_archive_zsh_history() {
  emulate -L zsh
  local entry="${1%$'\n'}" continuation=$'\\\n'
  [[ -n "$entry" && "$entry" != ' '* ]] || return 0
  entry="${entry//$'\n'/$continuation}"
  print -r -- ": ${EPOCHSECONDS}:0;${entry}" >> "$ZSH_HISTORY_ARCHIVE" || true
}
add-zsh-hook -d zshaddhistory _archive_zsh_history
add-zsh-hook zshaddhistory _archive_zsh_history

# Local zsh-only config (not source-controlled)
if [[ -f ~/.zshrc.local ]]; then
  source ~/.zshrc.local
fi

# Fix for dotsync2 certificate issue
export THRIFT_TLS_CL_CERT_PATH=/var/facebook/credentials/mkarrmann/x509/mkarrmann.pem
export THRIFT_TLS_CL_KEY_PATH=/var/facebook/credentials/mkarrmann/x509/mkarrmann.pem
