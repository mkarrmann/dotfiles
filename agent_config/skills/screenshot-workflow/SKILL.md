---
name: screenshot-workflow
description: >-
  Use when the user wants to share a screenshot, image, or visual context with
  the agent. Also use when the user says "look at my screenshot", "latest
  screenshot", or references an image they want you to see. Covers where
  screenshots land on each machine and how to read them by path.
---

# Screenshot Workflow

## Reading a screenshot

Screenshots are read by file path with the `Read` tool. `~/bin/latest-screenshot`
(source-controlled in `~/dotfiles/bin/`) prints the absolute path of the most
recently modified file in `~/screenshots/`, so "look at my latest screenshot"
is:

```bash
~/bin/latest-screenshot
```

then `Read` the returned path. The directory is not created by any dotfiles
script; if it is missing, ask Matt where the image was saved instead of
guessing.

## How images get there

**Devserver reached through VS Code Remote (work).** Claude Code runs inside
Neovim inside tmux over SSH, and none of those layers pass image drag-and-drop
through. The one component outside the terminal pipeline is VS Code's Explorer
sidebar: dragging a file from the Mac onto a folder there uploads it to the
remote filesystem. A `screenshots` symlink inside the checkout
(`~/checkoutN/fbsource/screenshots` → `~/screenshots`) makes the landing
folder visible in that tree; create it by hand, it is not managed.

**Linux desktop (Sway).** `Print` and `Shift+Print` (`sway_config`) capture a
region or the whole screen with `grim` and copy the PNG to the clipboard via
`wl-copy`. Nothing is written to disk, so a screenshot Matt wants read by path
has to be saved into `~/screenshots/` first.
