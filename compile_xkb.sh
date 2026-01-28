#!/usr/bin/env bash
set -euo pipefail

# See regarding general setup, and how this sets up en dashes and em dashes: https://chatgpt.com/share/697a67b4-9a08-800f-9818-aff97e92c343

# Will need to run `sudo apt install libxkbcommon-tools` first

mkdir -p $HOME/.config/xdb

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"

xkbcli compile-keymap \
  --include "$SCRIPT_DIR/xkb" --include-defaults \
  --layout my_us_xkb --variant basic \
  --options lv3:ralt_switch \
  > "$HOME/.config/xkb/my_us_xkb.xkb"
