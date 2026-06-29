#!/bin/bash
# Ensures the `meta` CLI is installed and available.
# devfeature may report "already enabled" even when the RPM is missing.
# The remove+install cycle forces a real installation in that case.

set -euo pipefail

hash -r
if ! command -v meta &>/dev/null; then
  devfeature remove meta 2>/dev/null || true
  devfeature install meta 2>&1
  hash -r
fi

if ! command -v meta &>/dev/null; then
  echo "ERROR: meta CLI installation failed" >&2
  exit 1
fi

devfeature persist meta 2>/dev/null || true
echo "meta CLI is available at $(command -v meta)"
