export DATACHAT_ROOT=$HOME/work/datachat
export GOPATH=${DATACHAT_ROOT}/web/web_server
export PATH=$PATH:${GOPATH}/bin
export DATACHAT_FILE_SYS=$HOME/work/datachat/app_data
export no_proxy="*"

set -o vi

# Alias definitions.
# You may want to put all your additions into a separate file like
# ~/.bash_aliases, instead of adding them here directly.
# See /usr/share/doc/bash-doc/examples in the bash-doc package.

if [ -f ~/.bash_aliases ]; then
    . ~/.bash_aliases
fi

export PYENV_ROOT="$HOME/.pyenv"
export PATH="$PYENV_ROOT/bin:$PATH"
eval "$(pyenv init -)"
