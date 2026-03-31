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

# Hot path: workspace switch — updates all workspaces (not just FOCUSED/PREV)
# to avoid stale highlights from cross-monitor switches or rapid switching races.
# $FOCUSED is already set as an env var by the exec-on-workspace-change trigger.
if [ "$SENDER" = "aerospace_workspace_change" ]; then
    VISIBLE=" $(aerospace list-workspaces --monitor all --visible | tr '\n' ' ') "

    ARGS=()
    for sid in $(aerospace list-workspaces --all); do
        if [ "$sid" = "$FOCUSED" ]; then
            ARGS+=(--set "space.$sid" background.drawing=on background.color=0xff89b4fa label.color=0xffffffff)
        elif [[ "$VISIBLE" == *" $sid "* ]]; then
            ARGS+=(--set "space.$sid" background.drawing=on background.color=0xff585b70 label.color=0xffffffff)
        else
            ARGS+=(--set "space.$sid" background.drawing=off label.color=0xff888888)
        fi
    done

    if [ ${#ARGS[@]} -gt 0 ]; then
        sketchybar "${ARGS[@]}" 2>/dev/null
    fi
    exit 0
fi

# Cold path: full reconciliation (display_change, system_woke, initial startup)
FOCUSED=$(aerospace list-workspaces --focused)
VISIBLE=" $(aerospace list-workspaces --monitor all --visible | tr '\n' ' ') "

# Build nsscreen-screens-id → sketchybar arrangement-id mapping.
# AeroSpace uses NSScreen array indices; sketchybar uses its own arrangement IDs.
# These can differ, so we bridge through CGDirectDisplayID which both systems use
# internally but expose under different names.
NSSCREEN_CGID=$(swift -e '
import AppKit
for (i, s) in NSScreen.screens.enumerated() {
    let id = (s.deviceDescription[NSDeviceDescriptionKey("NSScreenNumber")] as! NSNumber).uint32Value
    print("\(i + 1) \(id)")
}' 2>/dev/null)

SB_DISPLAYS=$(sketchybar --query displays 2>/dev/null)

DISPLAY_MAP=$(python3 -c "
import sys, json
ns_raw = sys.argv[1].strip() if len(sys.argv) > 1 else ''
sb_raw = sys.argv[2].strip() if len(sys.argv) > 2 else ''
if not ns_raw or not sb_raw:
    exit(0)
cg_to_arr = {d['DirectDisplayID']: d['arrangement-id']
             for d in json.loads(sb_raw)}
for line in ns_raw.split('\n'):
    ns_id, cg_id = line.split()
    print(ns_id, cg_to_arr.get(int(cg_id), 1))
" "$NSSCREEN_CGID" "$SB_DISPLAYS" 2>/dev/null)

# Pre-parse display map into bash variables (DISP_1, DISP_2, etc.)
# so the workspace loop doesn't fork subshells for lookups.
while read -r ns_id arr_id; do
    declare "DISP_${ns_id}=${arr_id}"
done <<< "$DISPLAY_MAP"

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
while IFS=$'\t' read -r sid nsscreen_id; do
    if ! echo "$EXISTING" | grep -qx "space.$sid"; then
        NEW_ITEMS+=("$sid")
    fi

    # Look up arrangement-id from pre-parsed map; default to 1 (main display)
    var="DISP_${nsscreen_id}"
    display="${!var:-1}"

    if [ "$sid" = "$FOCUSED" ]; then
        ARGS+=(--set "space.$sid"
            associated_display="$display"
            background.drawing=on
            background.color=0xff89b4fa
            label.color=0xffffffff)
    elif [[ "$VISIBLE" == *" $sid "* ]]; then
        ARGS+=(--set "space.$sid"
            associated_display="$display"
            background.drawing=on
            background.color=0xff585b70
            label.color=0xffffffff)
    else
        ARGS+=(--set "space.$sid"
            associated_display="$display"
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
