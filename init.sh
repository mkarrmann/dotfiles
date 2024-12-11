#!/bin/bash

ln .bash_aliases ~/.bash_aliases
ln .bashrc ~/.bashrc
ln .screenrc ~/.screenrc
ln .inputrc ~/.inputrc
ln .bash_profile ~/.bash_profile
ln .tmux.conf ~/.tmux.conf
ln .git-prompt.sh ~/.git-prompt.sh
# TODO look into using hammerspoon again
#ln hammerspoon.lua ~/.hammerspoon/init.lua
mkdir -p ~/.config/nvim
ln nvim_init.lua ~/.config/nvim/init.lua
