---
name: screenshot-workflow
description: >-
  Use when the user wants to share a screenshot, image, or visual context with
  Claude Code. Also use when the user says "look at my screenshot", "latest
  screenshot", or references an image they want you to see. Covers the
  drag-and-drop upload workflow and how to read uploaded images.
---

# Screenshot Workflow

## Overview

The user runs Claude Code inside Neovim inside tmux over SSH via VS Code. None of the terminal layers (SSH, tmux, Neovim) support image drag-and-drop passthrough. Screenshots must be uploaded via VS Code's Explorer sidebar and read by file path.

## Workflow

1. User drags a screenshot from their local machine onto the `screenshots` folder in the VS Code **Explorer sidebar** (file tree panel). VS Code uploads it to the remote filesystem automatically.
2. User tells Claude Code to look at the screenshot.
3. Claude reads the image using the `Read` tool with the file path.

## Finding Screenshots

A helper script is available at `~/bin/latest-screenshot`. It prints the absolute path of the most recently modified file in `~/screenshots/`.

When the user says "look at my latest screenshot" or similar:

```bash
latest-screenshot
```

Then use the `Read` tool on the returned path.

## Setup

Managed by `~/dotfiles/init.sh`:

- **`~/screenshots/`** — landing directory for uploaded images
- **`~/fbsource2/screenshots`** — symlink so the folder is visible in the VS Code Explorer (workspace root is `~/fbsource2`)
- **`~/bin/latest-screenshot`** — helper script (source-controlled in `~/dotfiles/bin/`)

## Why This Exists

VS Code's Explorer sidebar is the only component in the user's stack (VS Code SSH → tmux → Neovim → Claude Code) that operates outside the terminal pipeline and can receive OS-level drag-and-drop events with file upload over SSH. Neovim file explorers (neo-tree, oil.nvim, etc.) are TUI applications that cannot receive binary image data.
