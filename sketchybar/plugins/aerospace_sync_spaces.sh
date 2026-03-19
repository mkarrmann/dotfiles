#!/usr/bin/env bash

FOCUSED=$(aerospace list-workspaces --focused)

aerospace list-workspaces --all \
    --format '%{workspace}%{tab}%{monitor-appkit-nsscreen-screens-id}' |
    while IFS=$'\t' read -r sid display; do
        if [ "$sid" = "$FOCUSED" ]; then
            sketchybar --set "space.$sid" \
                associated_display="$display" \
                background.drawing=on \
                label.color=0xffffffff
        else
            sketchybar --set "space.$sid" \
                associated_display="$display" \
                background.drawing=off \
                label.color=0xff888888
        fi
    done
