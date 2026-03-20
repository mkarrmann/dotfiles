#!/usr/bin/env bash
# Wrapper script for fb-pyright-ls that resolves Meta-managed paths at
# invocation time. lspmux server calls this script once; subsequent nvim
# instances reuse the running process.
#
# This mirrors the path resolution logic in:
#   /usr/share/fb-editor-support/nvim/lua/meta/lsp/extensions.lua
#   /usr/share/fb-editor-support/nvim/lsp/fb-pyright-ls@meta.lua
set -euo pipefail

# --- Resolve Node binary (highest-numbered vscode-server dir) ---
node_bin=""
for base in /usr/local/fbpkg/nuclide/vscode-server /usr/local/fbpkg/vscodefb/vscode-server; do
  if [[ -d "$base" ]]; then
    # Sort numerically descending, pick the highest.
    candidate=$(find "$base" -maxdepth 1 -regex '.*/[0-9]+$' -type d \
      | sort -t/ -k"$(echo "$base/" | tr -cd '/' | wc -c)" -n -r \
      | head -1)
    if [[ -n "$candidate" && -x "$candidate/node" ]]; then
      node_bin="$candidate/node"
    fi
  fi
done

if [[ -z "$node_bin" ]]; then
  echo "ERROR: could not find a vscode-server node binary" >&2
  exit 1
fi

# --- Resolve latest nuclide.pyls extension ---
ext_dir="${XDG_DATA_HOME:-$HOME/.local/share}/nvim/meta-vscode-exts"
ext_folder=""
latest_ver=""

for d in "$ext_dir"/nuclide.pyls-*/; do
  [[ -d "$d" ]] || continue
  base=$(basename "$d")
  ver="${base#nuclide.pyls-}"
  if [[ -z "$latest_ver" ]] || [[ "$(printf '%s\n%s' "$ver" "$latest_ver" | sort -t. -k1,1n -k2,2n -k3,3n | tail -1)" == "$ver" ]]; then
    latest_ver="$ver"
    ext_folder="${d%/}"
  fi
done

if [[ -z "$ext_folder" ]]; then
  echo "ERROR: could not find nuclide.pyls extension in $ext_dir" >&2
  exit 1
fi

binary="$ext_folder/src/fb-pyright-ls-prebuild.js"
typeshed="$ext_folder/src/typeshed-fallback"
glean_proxy="$ext_folder/src/glean-server-proxy"

# --- Determine cwd (matches version-check logic in fb-pyright-ls@meta.lua) ---
lib_path="$ext_folder/VendorLib"
cwd_path="$lib_path"
if [[ "$latest_ver" =~ ([0-9]+)\.([0-9]+)\.([0-9]+)$ ]]; then
  major="${BASH_REMATCH[1]}"
  minor="${BASH_REMATCH[2]}"
  build="${BASH_REMATCH[3]}"
  # Breaking version: 1.0.1691 — use ext_folder for newer, lib_path for older.
  if (( major > 1 || (major == 1 && minor > 0) || (major == 1 && minor == 0 && build > 1691) )); then
    cwd_path="$ext_folder"
  fi
fi

export NODE_OPTIONS="--max-old-space-size=10240"
export PYTHON_PATH=""

cd "$cwd_path"
exec "$node_bin" "$binary" \
  --stdio \
  --typeshedpath "$typeshed" \
  --heuristics \
  --python-version 3.10 \
  --buck-async \
  --enable-glean-support \
  --glean-server-proxy-path "$glean_proxy"
