#!/bin/bash
# Google Drive auto-mount script for Meta ODs
# Sourced from .bashrc on every shell open.
# Sources config.sh for user settings, then mounts gdrive if not already mounted.
#
# This script is designed to be fast and silent when already mounted.
# The health check and mount are done in the background to avoid blocking
# shell startup on a slow FUSE mount.

GDRIVE_SCRIPTS_DIR="$HOME/.claude/gdrive-mount-scripts"

# Source user config
if [ ! -f "$GDRIVE_SCRIPTS_DIR/config.sh" ]; then
    return 0 2>/dev/null || exit 0
fi
source "$GDRIVE_SCRIPTS_DIR/config.sh"

# Export proxy globally
export http_proxy="http://fwdproxy:8080"
export https_proxy="http://fwdproxy:8080"
export no_proxy=".facebook.net,.facebook.com,.tfbnw.net,.fb.com,.thefacebook.com,localhost"

# Only run mount logic once per OD (flag file in /tmp survives shell restarts but not OD restarts)
MOUNT_FLAG="/tmp/${USER}/gdrive-mount-done"

if [ -f "$MOUNT_FLAG" ]; then
    # Already ran on this OD — quick non-blocking check via mountpoint only.
    # Avoid ls/stat on the FUSE mount since it can block for 30+ seconds.
    if mountpoint -q "$GDRIVE_MOUNT_POINT" 2>/dev/null; then
        return 0 2>/dev/null || exit 0
    fi
    # Mount is missing — fall through to remount
fi

# Run the rest in a background subshell so shell startup is never blocked.
(
    mkdir -p "/tmp/${USER}"

    # Install mclone if missing
    if ! command -v mclone &>/dev/null; then
        feature install mclone 2>/dev/null && hash -r
        if ! command -v mclone &>/dev/null; then
            echo "[gdrive-mount] Failed to install mclone" >&2
            exit 1
        fi
    fi

    # Check for rclone config
    if [ ! -f "$HOME/.config/rclone/rclone.conf" ]; then
        echo "[gdrive-mount] No mclone config found. Run /gdrive-setup in Claude to configure." >&2
        exit 1
    fi

    # Refresh token unconditionally (cheap if already fresh)
    mclone refresh-token -a -e 2>/dev/null

    # Start token refresh daemon if not running
    if ! pgrep -f "mclone refresh-tokens-periodically" >/dev/null 2>&1; then
        mclone refresh-tokens-periodically --daemon 2>/dev/null
    fi

    # Mount if not already mounted
    if ! mountpoint -q "$GDRIVE_MOUNT_POINT" 2>/dev/null; then
        fusermount -uz "$GDRIVE_MOUNT_POINT" 2>/dev/null || true
        mkdir -p "$GDRIVE_MOUNT_POINT"

        mclone mount "${GDRIVE_REMOTE_NAME}:${GDRIVE_REMOTE_FOLDER}" "$GDRIVE_MOUNT_POINT" \
            --vfs-cache-mode writes \
            --dir-cache-time 1s \
            --poll-interval 10s \
            --daemon 2>/dev/null

        sleep 2

        if mountpoint -q "$GDRIVE_MOUNT_POINT" 2>/dev/null; then
            echo "[gdrive-mount] Google Drive mounted at $GDRIVE_MOUNT_POINT"
        else
            echo "[gdrive-mount] Mount failed. Run /gdrive-setup in Claude to troubleshoot." >&2
            exit 1
        fi
    fi

    # Mark as done for this OD
    touch "$MOUNT_FLAG"
) &>/dev/null &
disown
