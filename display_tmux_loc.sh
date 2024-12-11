#!/bin/bash
# https://www.internalfb.com/intern/wiki/Development_Environment/Tmux/#show-current-bookmark
tmux showenv -g TMUX_LOC_$(tmux display -p "#D" | tr -d %) | sed 's/^.*=//'
