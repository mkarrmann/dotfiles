---
name: personal-development-setup-and-env
description: >-
  Use when you need to understand the user's development environment, editor
  setup, config architecture, or where to find/place configuration files.
  Also use when creating new config files, skills, or dotfiles and you need
  to know what should be source-controlled versus local-only. Trigger
  keywords: dotfiles, config, setup, environment, nvim config, editor,
  where does X go, source control config.
---

# Development Environment

## Overview

Config is split into **source-controlled** (portable, in `~/dotfiles/`) and **local-only** (machine-specific, created directly in target locations). The `local` override pattern is used consistently: source-controlled config loads first, then silently loads a local override if present.

## Config Architecture

```
~/dotfiles/                          (git repo, portable across machines)
├── init.sh                          Symlinks portable config; skip-on-conflict
├── meta_init.sh                     Symlinks Meta-specific local config templates
├── .shellrc, .zshrc, .tmux.conf ... Shell/terminal dotfiles → ~/
├── nvim_init.lua                    → ~/.config/nvim/init.lua
├── nvim/lua/{config,plugins,lib}/*.lua  → ~/.config/nvim/lua/... (via init.sh)
├── nvim/local/{config,plugins}/*.lua    → ~/.config/nvim/lua/... (via meta_init.sh)
├── claude_config/
│   ├── CLAUDE.md                    → ~/.claude/CLAUDE.md
│   ├── statusline.sh                → ~/.claude/statusline.sh
│   └── hooks/*                      → ~/.claude/hooks/
├── codex_config/config.template.toml Templated → ~/.codex/config.toml
├── codex_config/config.local.example.toml Example local overrides
├── agent_config/
│   ├── global-development-preferences.md  → ~/.claude/rules/ AND ~/.codex/rules/
│   └── skills/*/                    → ~/.claude/skills/* (directory symlinks)
└── bin/*                            → ~/bin/
```

## Local Override Pattern

Every layer uses the same pattern — load portable config, then silently load local overrides:

| Layer | Portable | Local override | Mechanism |
|-------|----------|----------------|-----------|
| Neovim | `config/*.lua`, `plugins/*.lua` | `config/local.lua` | `pcall(require, "config.local")` in `autocmds.lua` |
| Shell | `.shellrc` | `~/.localrc` | `source ~/.localrc` in `.shellrc` |
| Tmux | `.tmux.conf` | `~/.tmux.conf.local` | `source-file` if exists |
| Claude | `CLAUDE.md` | `CLAUDE.local.md` | `@~/.claude/CLAUDE.local.md` reference |
| Codex | `config.template.toml` | `config.local.toml` | Appended by `init.sh` |

**Rule of thumb:** `local.lua` / `localrc` / etc. are the machine-specific escape hatches — not in dotfiles. Shared config (even Meta-specific) lives in dotfiles under a descriptive name.

## Meta Config Opt-In Pattern

Meta-specific Neovim local config templates live in `nvim/local/` (source-controlled, but only symlinked by `meta_init.sh`). On a Meta machine, run `bash meta_init.sh` after `init.sh` to:

1. Symlink `nvim/local/config/*.lua` and `nvim/local/plugins/*.lua` into the nvim runtime
2. Create `~/.config/nvim/lua/config/local.lua` with `require("config.meta")` if it doesn't exist

- **`plugins/meta.lua`** — symlinked by `meta_init.sh`; auto-loaded by lazy.nvim; `cond` guards make it a no-op if meta.nvim isn't installed.
- **`config/meta.lua`** — symlinked by `meta_init.sh`; loaded via `config/local.lua` opt-in (also created by `meta_init.sh`).

On non-Meta machines (where only `init.sh` runs), neither file is symlinked — Meta config is completely absent.

## Where Things Go

| What | Location | Source-controlled? |
|------|----------|-------------------|
| New portable skill | `~/dotfiles/agent_config/skills/<name>/SKILL.md` | Yes — auto-symlinked by `init.sh` |
| Meta-specific skill | `~/.claude/skills/<name>/SKILL.md` | No — created directly |
| Claude rules (shared w/ Codex) | `~/dotfiles/agent_config/global-development-preferences.md` | Yes |
| Meta nvim plugins | `~/dotfiles/nvim/local/plugins/meta.lua` | Yes — symlinked by `meta_init.sh`, cond-guarded |
| Meta nvim config (LSPs, etc.) | `~/dotfiles/nvim/local/config/meta.lua` | Yes — symlinked by `meta_init.sh`, opt-in via local.lua |
| Machine-specific nvim config | `~/.config/nvim/lua/config/local.lua` | No — created by `meta_init.sh` or manually |
| Project-specific Claude context | `~/.claude/projects/<project>.md` | No |
| Machine-specific shell config | `~/.localrc` | No |

## Editor Stack

**Framework:** LazyVim (Neovim distribution on lazy.nvim)

**Portable plugins** (in dotfiles): telescope, nvim-cmp, treesitter, flash, lualine, undotree, tmux-navigator, claudecode.nvim, midnight/catppuccin themes

**Meta plugins** (in dotfiles `nvim/local/plugins/meta.lua`, symlinked by `meta_init.sh`, cond-guarded): meta.nvim (detected at `/usr/share/fb-editor-support/nvim` on Linux or `/usr/local/share/fb-editor-support/nvim` on Mac), none-ls

**Meta config** (in dotfiles `nvim/local/config/meta.lua`, opt-in via local.lua): Meta LSPs (cppls, fb-pyright, pyre, buck2, linttool), MetaMate AI, Buck keybindings, Telescope extensions (myles, biggrep, hg), custom Maven/Presto build integration

**For meta.nvim capabilities reference**, see the `neovim-meta` skill if available on this machine.

## Remote Neovim Sessions (nvs)

Neovim runs as a **headless server** on devvms (`nvim --headless --listen PORT`) with a thin **TUI client** on the Mac (`nvim --server localhost:PORT --remote-ui`) connected through ET tunnels. This gives persistent sessions that survive disconnects — the headless server keeps running, and you just reconnect the UI.

### Architecture

```
Mac (Ghostty)                    ET tunnel                    Devvm
┌──────────────┐                                        ┌──────────────────┐
│ bin-macos/nvs │── forward tunnel (-t) ──────────────► │ bin/nvs           │
│ (TUI client)  │   localhost:PORT → localhost:PORT      │ (headless server) │
│               │                                        │                   │
│ nvs-clip-listen◄── reverse tunnel (-r) ◄──── nc ◄────│ clipboard-relay   │
│ (port 8765)   │   devvm:8765 → Mac:8765               │ (vim.g.clipboard) │
└──────────────┘                                        └──────────────────┘
         ▲
         │ nvs-tunnels sets up both tunnels + starts listener + remote servers
```

### Key files

| File | Where | Purpose |
|------|-------|---------|
| `bin/nvs` | Remote (cross-platform) | Starts headless nvim server, loads clipboard-relay |
| `bin-macos/nvs` | Mac only | TUI client — waits for tunnel, connects `--remote-ui` |
| `bin-macos/nvs-tunnels` | Mac only | Sets up ET tunnels (forward + reverse) per devvm |
| `bin-macos/nvs-clip-listen` | Mac only | Listens on port 8765, pipes to `pbcopy` |
| `nvim/lua/lib/clipboard-relay.lua` | Remote | Custom `g:clipboard` — sends yanks via nc to Mac |
| `bin-macos/startup-windows` | Mac only | Launches tunnel + session windows via AeroSpace |

### Clipboard

The headless server has no terminal, so OSC 52 (the normal clipboard mechanism) has nowhere to go. Instead, a **reverse ET tunnel** (`-r 8765:8765`) connects the devvm back to the Mac. On yank, `clipboard-relay.lua` spawns `nc -w 1 localhost 8765` asynchronously and sends the text. On the Mac, `nvs-clip-listen` receives it and pipes to `pbcopy`.

- **Copy (remote → Mac):** Automatic on every yank. `clipboard-relay.lua` handles `"+y` via `vim.g.clipboard` and regular `y` via a `TextYankPost` autocmd.
- **Paste (Mac → remote):** Use `Cmd+V` in Ghostty (sends clipboard as bracketed paste). `"+p` pastes the last *remote* yank, not the current Mac clipboard.

### Session naming

Sessions are named like `FTW-main1`, `FTW-fbsource1`, `CCO-main1`. Ports are deterministic: `cksum(name) % 1000 + 7000`.

### Workspace layout

| Workspace | Content |
|-----------|---------|
| T | Tunnel windows (one per devvm) |
| 2 | FTW: main1 |
| 3 | FTW: fbsource1 + vscode |
| 4 | FTW: fbsource2 + vscode |
| 5-7 | CCO equivalents |

### Workspace management scripts

| Script | Purpose |
|--------|---------|
| `startup-windows` | Creates/places all windows on correct AeroSpace workspaces, runs orchest, reconciles late-appearing windows, sweeps strays to Z |
| `arrange-workspaces` | Sets sidebar\|accordion layout per workspace (Orchest 20% left, rest in accordion right). Use `--force` to bypass fingerprint caching. |
| `auto-accordion` | AeroSpace `on-window-detected` callback — moves new windows into the accordion container. Suppressed by `/tmp/startup-windows.lock`. |

### AeroSpace gotchas

These behaviors differ from what you'd expect and have caused bugs:

- **`move left/right` at a container boundary creates nesting.** Instead of stopping or wrapping, it creates a perpendicular sub-container (e.g. `v_tiles` inside `h_tiles`). Only use `move` for interior swaps where there's a neighbor on both sides.
- **`move left/right` into an adjacent container ENTERS it.** Moving a window toward a neighboring container at the same level moves the window inside that container, not swapping positions. This means you cannot reorder a window and a container at the same level using `move`.
- **`layout accordion` on a root-level child changes the ROOT layout.** It sets the parent container's layout, and if the parent is root, all windows become accordion. Only use `layout accordion` on windows inside a nested container (created by `join-with`).
- **`layout floating` → `layout tiling` re-inserts into the SAME container.** Floating a window and re-tiling it does not extract it to root level — it goes back into its original container. Cannot be used to extract windows from nested containers.
- **`move-node-to-workspace` always inserts at root level, rightmost.** The window lands as the last child of the workspace root container, never inside a nested container. This is the only reliable way to extract a window from a nested container (round-trip to another workspace and back).
- **`join-with` is a no-op on floating windows.** Both windows must be tiling for `join-with` to create a container. Filter for tiling windows when selecting join targets.
- **AeroSpace auto-collapses single-child containers (when normalization is enabled).** With `enable-normalization-flatten-containers = false`, single-child containers persist. `arrange-workspaces` creates single-window accordion containers using a scaffold: borrow a tiling window from workspace Z, join it with the target, set accordion layout, return the scaffold. The sidebar is never moved, so it stays on the left.
- **`aerospace layout <a> <b> ...` cycles through args, it does not set them.** If the current layout matches one of the args, it advances to the next; otherwise it picks the first. So `layout tiles horizontal` from `v_accordion` becomes `v_tiles` (picks "tiles", orientation preserved) — *not* `h_tiles`. Use the explicit composite layout names (`h_tiles`, `v_tiles`, `h_accordion`, `v_accordion`) when you need a deterministic result.
- **`flatten-workspace-tree` resets root to `default-root-container-layout` (accordion).** Always follow flatten with `layout h_tiles` (not `layout tiles horizontal` — that cycles, see above) to override.
- **Spatial order after flatten is unpredictable.** Windows added last (e.g. Orchest from `orchest-open-workspaces`) end up rightmost. Discover order by walking `focus left`/`focus right`; don't assume positions.
- **`wait_for_new_window` uses a 10s timeout.** Remote VS Code connections (`vscode-remote://`) often take longer. The reconciliation pass in `startup-windows` catches these late-appearing windows.
- **macOS bash is 3.2.** No associative arrays (`declare -A`). Use `grep -qx` against newline-separated ID lists instead.

### Debugging workspace layouts

```bash
# Check layout structure for a workspace
aerospace list-windows --workspace N --format \
  '%{window-id} %{app-name} | parent=%{window-parent-container-layout} | root=%{workspace-root-container-layout}'

# Re-arrange a single workspace
arrange-workspaces --force N

# Re-run full startup (idempotent — moves existing windows, creates missing ones)
startup-windows
```

## Adding a New Skill

1. **Portable:** Create `~/dotfiles/agent_config/skills/<name>/SKILL.md`, then re-run `init.sh` (or manually symlink to `~/.claude/skills/<name>`)
2. **Local-only:** Create `~/.claude/skills/<name>/SKILL.md` directly
