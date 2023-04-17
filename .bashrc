if [ -f /etc/bash_completion ]; then
  . /etc/bash_completion
fi

source ~/.git-prompt.sh
export PS1="\[\e[32m\]\${PWD}\[\e[95m\] \$(__git_ps1) \[\033[00m\] \$ "

# Alias definitions.
# You may want to put all your additions into a separate file like
# ~/.bash_aliases, instead of adding them here directly.
# See /usr/share/doc/bash-doc/examples in the bash-doc package.

if [ -f ~/.bash_aliases ]; then
    . ~/.bash_aliases
fi

if [ -f ~/.local_rc ]; then
    . ~/.local_rc
fi

# Check if pyenv is installed
command -v pyenv >> /dev/null
if [ $? -eq 0 ]; then
	export PYENV_ROOT="$HOME/.pyenv"
	# Add pyenv shims to PATH only once.
	if [[ ! "$PATH" =~ "$PYENV_ROOT/bin" ]]; then
	    export PATH="$PYENV_ROOT/bin:$PYENV_ROOT/shims:$PATH"
	fi
	eval "$(pyenv init -)"
fi
