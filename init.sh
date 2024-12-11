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

