# If not running interactively, don't do anything
[[ $- != *i* ]] && return

# Shared config (env, PATH, aliases, functions, tool init)
if [[ -f ~/.shellrc ]]; then
  source ~/.shellrc
fi

# START: Setup PS1

# --- optional git prompt (fallback) ---
if [[ -f ~/.git-prompt.sh ]]; then
  source ~/.git-prompt.sh
fi

# ---- SCM provider wrapper (localrc wins, git fallback, else empty) ----
__prompt_scm() {
  if declare -F _scm_prompt >/dev/null 2>&1; then
    _scm_prompt
  elif declare -F __git_ps1 >/dev/null 2>&1; then
    __git_ps1 " (%s)"
  else
    printf ""
  fi
}

# ---- run before each prompt ----
__prompt_command() {
  # NOTE: __PROMPT_STATUS is captured at the start of PROMPT_COMMAND, not here
  __PROMPT_SCM="$(__prompt_scm)"

  if [[ -n "$TMUX" && -n "$TMUX_PANE" ]]; then
    tmux setenv -g "TMUX_LOC_${TMUX_PANE#%}" "$__PROMPT_SCM"
  fi
}

PROMPT_COMMAND="__prompt_command${PROMPT_COMMAND:+;$PROMPT_COMMAND}"

# ---- base prompt text (can be overridden by localrc) ----
#: "${PROMPT_BASE:='[\u@\h \w'}"
PROMPT_BASE=${PROMPT_BASE:-'[\w'}

# ---- build PS1 once (PS1 reads $__PROMPT_* dynamically each prompt) ----
PS1='\[\033[0;33m\]'                 # yellow
PS1+="${PROMPT_BASE}"                # e.g. [user@host cwd
PS1+='${__PROMPT_SCM}'               # SCM string computed by PROMPT_COMMAND
PS1+=']\[\033[0m\] '                 # close bracket + reset

PS1+='\[\033[1;31m\]${__PROMPT_STATUS}\[\033[0m\] '  # red exit code
PS1+='\[\033[0;32m\]\D{%H:%M:%S}\[\033[0m\] '         # green time
PS1+='\$ '

export PS1

# END: Setup PS1

if [ -f /etc/bash_completion ]; then
  . /etc/bash_completion
fi

# TODO merge with Meta's prompt
#source ~/.git-prompt.sh
#export PS1="\[\e[32m\]\${PWD}\[\e[95m\] \$(__git_ps1) \[\033[00m\] \$ "

# Keep oodles of command history (see https://fburl.com/bashhistory).
# This tells Bash not to limit the size of the history file.
export HISTFILESIZE=-1
# This sets the limit of the in-memory history list to 1 million, which is more than you'll ever need.
export HISTSIZE=1000000
# This tells Bash to avoid saving a command to the history repeatedly (if you run it several times in a row).
export HISTCONTROL=ignoredups
# This tells Bash to use a non-standard name for the history file. Without this, it's surprisingly easy to accidentally wipe out your history (e.g., if you run bash --norc, Bash loads with its default history settings and will truncate the history file based on the default limits). This protects you by guaranteeing that Bash has loaded your history settings before it touches your history file (otherwise it would touch the default history file instead).
# WARNING: If you change HISTFILE, make sure you are not exporting HISTFILE — that negates the benefits of changing HISTFILE in the first place.
HISTFILE=~/.bash_history_actual
# This makes Bash append new commands to the history file every time it displays a prompt (i.e., after every command finishes). Without this, appending won't happen until Bash exits. Use this if you want a newly-opened terminal to see the history from other still-open terminals.
export PROMPT_COMMAND="__PROMPT_STATUS=\$?; history -a; $PROMPT_COMMAND"

# for tmux resurrect
#HISTS_DIR=$HOME/.bash_history.d
#mkdir -p "${HISTS_DIR}"
#if [ -n "${TMUX_PANE}" ]; then
#  export HISTFILE="${HISTS_DIR}/tmux_${TMUX_PANE}"
# fi
# save history at each command, instead of at exit
# https://web.archive.org/web/20150908175333/http://briancarper.net/blog/248/
#export PROMPT_COMMAND="history -a; history -n; $PROMPT_COMMAND"
shopt -s histappend
# Local bash-only config (not source-controlled)
if [[ -f ~/.bashrc.local ]]; then
  source ~/.bashrc.local
fi
