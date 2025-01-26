if [ -f /etc/bash_completion ]; then
  . /etc/bash_completion
fi

# Alias definitions.
# You may want to put all your additions into a separate file like
# ~/.bash_aliases, instead of adding them here directly.
# See /usr/share/doc/bash-doc/examples in the bash-doc package.

if [ -f ~/.bash_aliases ]; then
    . ~/.bash_aliases
fi

if [ -f ~/.bash_functions ]; then
    . ~/.bash_functions
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
export PROMPT_COMMAND="history -a; $PROMPT_COMMAND"

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

export EDITOR=nvim
export PATH=$PATH:$HOME/bin

if [ -f ~/.localrc ]; then
  source ~/.localrc
fi

#THIS MUST BE AT THE END OF THE FILE FOR SDKMAN TO WORK!!!
export SDKMAN_DIR="$HOME/.sdkman"
[[ -s "$HOME/.sdkman/bin/sdkman-init.sh" ]] && source "$HOME/.sdkman/bin/sdkman-init.sh"
