#!/usr/bin/env bash

# On display changes, give AeroSpace time to reassign workspaces
if [ "$SENDER" = "display_change" ] || [ "$SENDER" = "system_woke" ]; then
    sleep 2
fi

add_space_item() {
    local sid="$1"
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
}

# Hot path: workspace switch — only update the 2 items that changed
if [ "$SENDER" = "aerospace_workspace_change" ] && [ -n "$FOCUSED" ] && [ -n "$PREV" ]; then
    VISIBLE=" $(aerospace list-workspaces --monitor all --visible | tr '\n' ' ') "

    if [[ "$VISIBLE" == *" $PREV "* ]]; then
        sketchybar \
            --set "space.$FOCUSED" background.drawing=on background.color=0xff89b4fa label.color=0xffffffff \
            --set "space.$PREV" background.drawing=on background.color=0xff585b70 label.color=0xffffffff \
            2>/dev/null
    else
        sketchybar \
            --set "space.$FOCUSED" background.drawing=on background.color=0xff89b4fa label.color=0xffffffff \
            --set "space.$PREV" background.drawing=off label.color=0xff888888 \
            2>/dev/null
    fi

    # If --set failed, the item doesn't exist yet — create and retry
    if [ $? -ne 0 ]; then
        add_space_item "$FOCUSED" 2>/dev/null
        add_space_item "$PREV" 2>/dev/null
        if [[ "$VISIBLE" == *" $PREV "* ]]; then
            sketchybar \
                --set "space.$FOCUSED" background.drawing=on background.color=0xff89b4fa label.color=0xffffffff \
                --set "space.$PREV" background.drawing=on background.color=0xff585b70 label.color=0xffffffff
        else
            sketchybar \
                --set "space.$FOCUSED" background.drawing=on background.color=0xff89b4fa label.color=0xffffffff \
                --set "space.$PREV" background.drawing=off label.color=0xff888888
        fi
    fi
    exit 0
fi

# Cold path: full reconciliation (display_change, system_woke, initial startup)
FOCUSED=$(aerospace list-workspaces --focused)
VISIBLE=" $(aerospace list-workspaces --monitor all --visible | tr '\n' ' ') "

# Reconcile stale items
EXISTING=$(sketchybar --query bar | grep -o '"space\.[^"]*"' | tr -d '"')
CURRENT_WORKSPACES=$(aerospace list-workspaces --all)
for item in $EXISTING; do
    sid="${item#space.}"
    if ! echo "$CURRENT_WORKSPACES" | grep -qx "$sid"; then
        sketchybar --remove "$item"
    fi
done

ARGS=()
NEW_ITEMS=()
while IFS=$'\t' read -r sid display; do
    if ! echo "$EXISTING" | grep -qx "space.$sid"; then
        NEW_ITEMS+=("$sid")
    fi

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

for sid in "${NEW_ITEMS[@]}"; do
    add_space_item "$sid"
done

if [ ${#ARGS[@]} -gt 0 ]; then
    sketchybar "${ARGS[@]}"
fi
