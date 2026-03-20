#!/usr/bin/env bash

FOCUSED=$(aerospace list-workspaces --focused)
VISIBLE=$(aerospace list-workspaces --monitor all --visible)

aerospace list-workspaces --all \
    --format '%{workspace}%{tab}%{monitor-appkit-nsscreen-screens-id}' |
    while IFS=$'\t' read -r sid display; do
        if [ "$sid" = "$FOCUSED" ]; then
            sketchybar --set "space.$sid" \
                associated_display="$display" \
                background.drawing=on \
                background.color=0xff89b4fa \
                label.color=0xffffffff
        elif echo "$VISIBLE" | grep -qx "$sid"; then
            sketchybar --set "space.$sid" \
                associated_display="$display" \
                background.drawing=on \
                background.color=0xff585b70 \
                label.color=0xffffffff
        else
            sketchybar --set "space.$sid" \
                associated_display="$display" \
                background.drawing=off \
                label.color=0xff888888
        fi
    done
