#!/usr/bin/env bash

# On display changes, give AeroSpace time to reassign workspaces
if [ "$SENDER" = "display_change" ] || [ "$SENDER" = "system_woke" ]; then
    sleep 2

    # Reconcile stale items
    CURRENT=$(aerospace list-workspaces --all)
    EXISTING=$(sketchybar --query bar | grep -o '"space\.[^"]*"' | tr -d '"')
    for item in $EXISTING; do
        sid="${item#space.}"
        if ! echo "$CURRENT" | grep -qx "$sid"; then
            sketchybar --remove "$item"
        fi
    done
fi

FOCUSED=$(aerospace list-workspaces --focused)
VISIBLE=" $(aerospace list-workspaces --monitor all --visible | tr '\n' ' ') "

ARGS=()
while IFS=$'\t' read -r sid display; do
    if [ "$sid" = "$FOCUSED" ]; then
        ARGS+=(--set "space.$sid"
            associated_display="${display:-1}"
            background.drawing=on
            background.color=0xff89b4fa
            label.color=0xffffffff)
    elif [[ "$VISIBLE" == *" $sid "* ]]; then
        ARGS+=(--set "space.$sid"
            associated_display="${display:-1}"
            background.drawing=on
            background.color=0xff585b70
            label.color=0xffffffff)
    else
        ARGS+=(--set "space.$sid"
            associated_display="${display:-1}"
            background.drawing=off
            label.color=0xff888888)
    fi
done < <(aerospace list-workspaces --all \
    --format '%{workspace}%{tab}%{monitor-appkit-nsscreen-screens-id}')

if [ ${#ARGS[@]} -gt 0 ]; then
    sketchybar "${ARGS[@]}"
fi
