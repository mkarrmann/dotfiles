#!/usr/bin/env bash

# On display changes, give AeroSpace time to reassign workspaces
if [ "$SENDER" = "display_change" ] || [ "$SENDER" = "system_woke" ]; then
    sleep 2
fi

FOCUSED=$(aerospace list-workspaces --focused)
VISIBLE=$(aerospace list-workspaces --monitor all --visible)
CURRENT_WORKSPACES=$(aerospace list-workspaces --all)

# Remove sketchybar items for workspaces that no longer exist
EXISTING_ITEMS=$(sketchybar --query bar | grep -o '"space\.[^"]*"' | tr -d '"')
for item in $EXISTING_ITEMS; do
    sid="${item#space.}"
    if ! echo "$CURRENT_WORKSPACES" | grep -qx "$sid"; then
        sketchybar --remove "$item"
    fi
done

# Add items for new workspaces, update all items
for sid in $CURRENT_WORKSPACES; do
    if ! echo "$EXISTING_ITEMS" | grep -qx "space.$sid"; then
        sketchybar --add item "space.$sid" left \
            --set "space.$sid" \
            background.color=0xff89b4fa \
            background.corner_radius=5 \
            background.height=20 \
            background.drawing=off \
            label="$sid" \
            label.color=0xff888888 \
            label.font="SF Pro:Bold:13.0" \
            label.padding_left=8 \
            label.padding_right=8 \
            click_script="aerospace workspace $sid"
    fi

    display=$(aerospace list-workspaces --all \
        --format '%{workspace}%{tab}%{monitor-appkit-nsscreen-screens-id}' |
        awk -F'\t' -v ws="$sid" '$1 == ws { print $2 }')

    if [ "$sid" = "$FOCUSED" ]; then
        sketchybar --set "space.$sid" \
            associated_display="${display:-1}" \
            background.drawing=on \
            background.color=0xff89b4fa \
            label.color=0xffffffff
    elif echo "$VISIBLE" | grep -qx "$sid"; then
        sketchybar --set "space.$sid" \
            associated_display="${display:-1}" \
            background.drawing=on \
            background.color=0xff585b70 \
            label.color=0xffffffff
    else
        sketchybar --set "space.$sid" \
            associated_display="${display:-1}" \
            background.drawing=off \
            label.color=0xff888888
    fi
done
