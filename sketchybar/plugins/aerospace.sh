#!/usr/bin/env bash

if [ "$1" = "$FOCUSED_WORKSPACE" ]; then
    sketchybar --set "$NAME" background.drawing=on label.color=0xffffffff
else
    sketchybar --set "$NAME" background.drawing=off label.color=0xff888888
fi
