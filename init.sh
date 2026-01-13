#!/bin/bash

ln -s .bash_aliases ~/.bash_aliases
ln -s .bashrc ~/.bashrc
ln -s .screenrc ~/.screenrc
ln -s .inputrc ~/.inputrc
ln -s .bash_profile ~/.bash_profile
ln -s .tmux.conf ~/.tmux.conf
ln -s .git-prompt.sh ~/.git-prompt.sh
# TODO look into using hammerspoon again
#ln hammerspoon.lua ~/.hammerspoon/init.lua
mkdir -p ~/.config/nvim
ln -s nvim_init.lua ~/.config/nvim/init.lua

ln -s ./bin/* ~/bin/

# Use hammerspoon on Mac. See Powertoys config in README for Windows
if [ "$(uname)" == "Darwin" ]; then
    mkdir ~/.hammerspoon
    ln init.lua ~/.hammerspoon/init.lua
elif [ "$(expr substr $(uname -s) 1 5)" == "Linux" ]; then
    # Installs vim plug (need to install manually on other platforms)
    # Still need to call :PlugInstall inside of nvim to get all dependencies
    sh -c 'curl -fLo "${XDG_DATA_HOME:-$HOME/.local/share}"/nvim/site/autoload/plug.vim --create-dirs \
           https://raw.githubusercontent.com/junegunn/vim-plug/master/plug.vim'
fi

mkdir -p ~/.config/wofi
ln -s wofi_config ~/.config/wofi/config

