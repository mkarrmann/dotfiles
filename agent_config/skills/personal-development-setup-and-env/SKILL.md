---
name: personal-development-setup-and-env
description: >-
  Use when you need to understand the user's development environment, editor
  setup, config architecture, or where to find/place configuration files.
  Also use when creating new config files, skills, or dotfiles and you need
  to know what should be source-controlled versus local-only. Also use for
  questions about checkout roots, paired fbsource/configerator workspaces,
  or navigating between repositories. Trigger keywords: dotfiles, config,
  setup, environment, checkout, workspace root, fbsource, configerator,
  nvim config, editor, where does X go, source control config.
---

# Development Environment

## Overview

Config is split into **source-controlled** (portable, in `~/dotfiles/`) and **local-only** (machine-specific, created directly in target locations). The `local` override pattern is used consistently: source-controlled config loads first, then silently loads a local override if present.

## Paired Meta Workspaces

Each `~/checkoutN` is the working-directory root for one editor and agent session. It contains two independent repositories:

```
~/checkoutN/
├── fbsource/
└── configerator/
```

The behavioral rules for working inside one — repository selection, never hardcoding a checkout number, scoping `sl` / `jf` / `arc` / `buck` / `meta-rg` — are **not restated here**. They live in `agent_config/meta-workspace-preferences.md`, which `sync.sh` symlinks to `<workspace>/CLAUDE.md` and `<workspace>/AGENTS.md`, so any agent working in a checkout has already loaded them.

## Config Architecture

```
~/dotfiles/                          (git repo, portable across machines)
├── sync.sh                          Reflects config: symlinks, generated files, staged units.
│                                      Idempotent; never restarts running services
├── init.sh                          Runs sync.sh, then installs, then converges live services
├── meta_init.sh                     Symlinks Meta-specific nvim local config templates
├── .shellrc, .zshrc, .tmux.conf ... Shell/terminal dotfiles → ~/
├── nvim_init.lua                    → ~/.config/nvim/init.lua
├── nvim/lua/{config,plugins,lib}/*.lua  → ~/.config/nvim/lua/... (via sync.sh)
├── nvim/local/{config,plugins}/*.lua    → ~/.config/nvim/lua/... (via meta_init.sh)
├── claude_config/
│   ├── CLAUDE.md                    → ~/.claude/CLAUDE.md
│   ├── meta-config.toml             → ~/.claude/meta/config.toml (tpai rules/skills policy)
│   ├── statusline.sh                → ~/.claude/statusline.sh
│   └── hooks/*                      → ~/.claude/hooks/
├── codex_config/config.template.toml Templated → ~/.codex/config.toml
├── codex_config/config.local.example.toml Example local overrides
├── agent_config/
│   ├── global-development-preferences.md  → 3 global sinks; see "Global Agent Rules"
│   ├── meta-workspace-preferences.md      → ~/checkoutN/{CLAUDE.md,AGENTS.md}
│   └── skills/*/                    → ~/.claude/skills/* if listed in skills-global.list,
│                                      else ~/checkoutN/.claude/skills/* (see agent_config/README.md)
├── omnigent_config/                 Omnigent topology, shared client prefs, agent specs
└── bin/*                            → ~/bin/
```

**Two entry points, different blast radius.** `sync.sh` only reflects config and is safe to run any time — use it to apply dotfile edits. `init.sh` runs `sync.sh` first, then does network-bound installs, then restarts/reconciles running services. Reach for `init.sh` on a new machine or a deliberate full converge.

## Local Override Pattern

Every layer uses the same pattern — load portable config, then silently load local overrides:

| Layer | Portable | Local override | Mechanism |
|-------|----------|----------------|-----------|
| Neovim | `config/*.lua`, `plugins/*.lua` | `config/local.lua` | `pcall(require, "config.local")` in `autocmds.lua` |
| Shell | `.shellrc` | `~/.localrc` | `source ~/.localrc` in `.shellrc` |
| Tmux | `.tmux.conf` | `~/.tmux.conf.local` | `source-file` if exists |
| Claude | `CLAUDE.md` | `CLAUDE.local.md` | `@~/.claude/CLAUDE.local.md` reference |
| Codex | `config.template.toml` | `config.local.toml` | Appended by `sync.sh` |
| Codex instructions | `~/.codex/AGENTS.md` | `~/.codex/AGENTS.override.md` | Loaded first by Codex, wins |

**Rule of thumb:** `local.lua` / `localrc` / etc. are the machine-specific escape hatches — not in dotfiles. Shared config (even Meta-specific) lives in dotfiles under a descriptive name.

## Meta Config Opt-In Pattern

Meta-specific Neovim local config templates live in `nvim/local/` (source-controlled, but only symlinked by `meta_init.sh`). On a Meta machine, run `bash meta_init.sh` after `init.sh` to:

1. Symlink `nvim/local/config/*.lua` and `nvim/local/plugins/*.lua` into the nvim runtime
2. Create `~/.config/nvim/lua/config/local.lua` with `require("config.meta")` if it doesn't exist

- **`plugins/meta.lua`** — symlinked by `meta_init.sh`; auto-loaded by lazy.nvim; `cond` guards make it a no-op if meta.nvim isn't installed.
- **`config/meta.lua`** — symlinked by `meta_init.sh`; loaded via `config/local.lua` opt-in (also created by `meta_init.sh`).

On non-Meta machines (where only `init.sh` runs), neither file is symlinked — Meta config is completely absent.

## Global Agent Rules

`agent_config/global-development-preferences.md` is the single canonical file. `sync.sh` symlinks it into each agent's own global-instruction path; no agent reads another's, and each sees it exactly once.

| Sink | Serves |
|------|--------|
| `~/.claude/rules/global-development-preferences.md` | Claude Code TUI, Claude Agent SDK, Omnigent `claude-sdk` agents (claude/polly/debby) |
| `~/.codex/AGENTS.md` | Codex TUI, `codex exec`, codex app-server, Omnigent `codex` agents |
| `opencode.json` → `instructions` | Metacode. **Not** `~/.config/opencode/AGENTS.md` — see below |

`~/.claude/CLAUDE.md` pulls the first in via `@~/.claude/rules/...`.

**Rules are split by scope.** `global-development-preferences.md` holds only machine-agnostic preferences and goes everywhere. Anything specific to the Meta checkout layout lives in `meta-workspace-preferences.md`, which `sync.sh` symlinks into each detected workspace root as both `CLAUDE.md` and `AGENTS.md`. Those two names are not redundant, and each agent still sees the content once:

| At `~/checkoutN/` | `CLAUDE.md` | `AGENTS.md` |
|---|---|---|
| Claude Code | read; walks up from subdirectories | ignored |
| Codex | ignored | read in cwd only; does **not** walk up |

So a session started at the workspace root — the normal case — gets the rules in either agent, while a Codex session started inside `fbsource/` gets that repo's own `AGENTS.md` instead. Machines with no checkout get neither file, which is why this content must not sit in the global file.

Why the two non-obvious paths work (verified empirically 2026-08):

- **Claude Agent SDK under Omnigent.** A spec's `prompt:` becomes a full-replacement `--system-prompt`, which does *not* suppress CLAUDE.md. `skills_filter` defaults to `"all"` → `setting_sources=None` → the SDK emits `--setting-sources=user,project`. Omnigent deliberately omits `--bare`, which would skip CLAUDE.md discovery.
- **Codex under Omnigent.** The executor redirects `CODEX_HOME` to a per-session private home, but symlinks `AGENTS.md` / `AGENTS.override.md` in from the real `~/.codex` so instructions survive the redirect. Suppressed only by `HARNESS_CODEX_MINIMAL_CONFIG`, which nothing sets.

Traps:

- **`~/.codex/rules/` is not an instructions directory.** It is the exec-policy store, and the loader keeps only `*.rules` entries — a `.md` there is silently ignored. `sync.sh` deletes the stale link.
- **Metacode ignores `~/.config/opencode/AGENTS.md`.** Despite that being the documented opencode convention, it loads global rules only from the `instructions` array in `opencode.json`, which `sync-mcps` writes. The old symlink was a no-op on every machine; `sync.sh` now removes it. Metacode's startup banner is the quickest check — it prints `N rules loaded`.
- **YAML frontmatter is not portable across sinks.** Claude Code strips it (a `name:`/`description:` block never reaches the model); Codex injects it verbatim as instruction text. Rules files are loaded by path, not selected from a listing, so a description buys nothing and costs tokens in Codex. Keep frontmatter for skills only, where it is mandatory.
- **`~/.claude/rules/` is a Claude-only convention.** Codex and Metacode never read it. Extra `.md` files there reach Claude Code alone; anything cross-agent must go in the canonical file.
- **`skills: none` on an Omnigent spec also kills the rules.** It forces `setting_sources=[]`, suppressing CLAUDE.md along with the skill listing. No spec sets it today.

Not covered:

- **dvsc / devmate.** Its spec declares no `prompt:`, and the generic ACP harness injects no instructions — dvsc-core owns its prompt end to end. Devmate's own personal-rules channel is `~/.llms/rules/*.md`, which dotfiles does not populate; note that llm-rules would then *also* inject it into Claude Code, double-exposing it.

If a future harness reads none of these, Omnigent's `instructions:` / `prompt:` spec field is the harness-agnostic fallback. They are the same field (the parser prefers `instructions:`, falls back to `prompt:`), it resolves a sibling filename inside the agent dir, and it reaches every executor — the ACP harness folds it into the first user turn. Agent dirs register by path and are content-addressed, so a sibling `AGENTS.md` travels with the bundle and re-registers when it changes.

## Where Things Go

| What | Location | Source-controlled? |
|------|----------|-------------------|
| New portable skill | `~/dotfiles/agent_config/skills/<name>/SKILL.md` | Yes — auto-symlinked by `sync.sh` |
| Cross-agent rules (machine-agnostic) | `~/dotfiles/agent_config/global-development-preferences.md` | Yes — 3 sinks via `sync.sh` |
| Meta checkout-layout rules | `~/dotfiles/agent_config/meta-workspace-preferences.md` | Yes — → `~/checkoutN/{CLAUDE.md,AGENTS.md}` |
| tpai rules/skills policy | `~/dotfiles/claude_config/meta-config.toml` | Yes — → `~/.claude/meta/config.toml` |
| Meta-specific skill | `~/.claude/skills/<name>/SKILL.md` | No — created directly |
| Meta nvim plugins | `~/dotfiles/nvim/local/plugins/meta.lua` | Yes — symlinked by `meta_init.sh`, cond-guarded |
| Meta nvim config (LSPs, etc.) | `~/dotfiles/nvim/local/config/meta.lua` | Yes — symlinked by `meta_init.sh`, opt-in via local.lua |
| Machine-specific nvim config | `~/.config/nvim/lua/config/local.lua` | No — created by `meta_init.sh` or manually |
| Project-specific Claude context | `~/.claude/projects/<project>.md` | No |
| Machine-specific shell config | `~/.localrc` | No |

## Editor Stack

**Framework:** LazyVim (Neovim distribution on lazy.nvim)

**Portable plugins** (in dotfiles): telescope, nvim-cmp, treesitter, flash, lualine, undotree, tmux-navigator, codecompanion.nvim, midnight/catppuccin themes

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

Sessions are named `<DEVSERVER>-checkoutN`, e.g. `FTW-checkout1`, `CCO-checkout1`. Ports are deterministic: `cksum(name) % 1000 + 7000`.

Checkout sessions start in `~/checkoutN`, giving tools access to both sibling repositories. Repository-aware Neovim commands resolve their own execution root; see the `neovim-meta` skill for the selection rules.

**`~/.config/nvs` is dotsync2-managed, not machine-local.** The blanket `".config"` include in `dotsync2 paths list` sweeps it up, so every devserver sees the same directory — deleting a file here deletes it everywhere. The per-session `.env` files tolerate that because their names carry the host prefix. The session list cannot, so it is host-scoped: `sessions.<short hostname>` (e.g. `sessions.devvm20365`), resolved by `bin/nvs-sessions-file` with unsuffixed `sessions` as the fallback. Declaring another host's sessions spawns headless servers nothing connects to.

`WORKDIR` is read only at unit start, so editing it while a server is live does nothing until `nvs-restart SESSION`; `nvs-setup` warns when a live server has drifted from its config.

### Workspace layout

CCO and FTW checkouts are interleaved (CCO on even slots, FTW on odd). FTW
has checkouts 1–3; CCO has checkouts 1–4. Each numbered workspace also holds
a Chrome window. Code editing runs through Ghostty-backed `nvs` sessions.

| Workspace | Content |
|-----------|---------|
| 1 | Local macOS |
| T | Tunnel windows (one per devvm) |
| 2 | CCO: checkout1 |
| 3 | FTW: checkout1 |
| 4 | CCO: checkout2 |
| 5 | FTW: checkout2 |
| 6 | CCO: checkout3 |
| 7 | FTW: checkout3 |
| 8 | CCO: checkout4 |
| Z | Sweep/overflow (stray windows) |

### Workspace management scripts

| Script | Purpose |
|--------|---------|
| `startup-windows` | Creates/places all windows on correct AeroSpace workspaces, runs orchest, reconciles late-appearing windows, distributes Chrome session-restored windows, sweeps strays to Z |
| `arrange-workspaces` | Public layout dispatcher. Standard workspaces use sidebar\|accordion, workspace 11 delegates to its dashboard arranger, and Z is intentionally unchanged. Preserves focus and serializes arrangement runs. |
| `arrange-ws11` | Workspace-11 dashboard implementation. Arranges existing windows by default; startup uses `--ensure-windows` to provision missing dashboard windows. |
| `auto-accordion` | Optional AeroSpace `on-window-detected` callback. Currently disabled in `aerospace.toml`; if re-enabled, it is suppressed while the `/tmp/startup-windows.lock` directory exists. |

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
- **`aerospace focus left/right` defaults to `--boundaries-action wrap-around-the-workspace`, AND `stop` doesn't always engage.** Bare `focus left` from the leftmost window wraps to the rightmost — focus *always* changes. Passing `--boundaries-action stop` helps in the well-behaved case but **still wraps** when a phantom/unfocusable window sits in the tree (observed with `cmux` occasionally reporting `window-id 0` in workspace 1 — `focus right` from the rightmost *visible* window wraps past it to the leftmost). Conclusion: never trust "walk until focus stops moving" as a sole terminator. `discover_spatial_order` in `arrange-workspaces` uses cycle detection (track visited window IDs, break on repeat) as a backstop. Confirmed on AeroSpace 0.20.3-Beta. The `alt-h/j/k/l` keybindings setting wrap-around explicitly are redundant (matches the default), not the cause.
- **`aerospace focus --window-id N` can silently land focus on a *different* window for phantom/sentinel IDs.** Observed with cmux reporting `window-id 0`: focusing 0 from one neighbor lands on 0, but from another lands on whichever window AeroSpace's resolver picks (typically 163). The result is starting-state-dependent, so any logic that assumes "focus --window-id X means focus is now X" can silently misbehave. Generic detection: after each `focus --window-id`, re-query `list-windows --focused --format '%{window-id}'` and compare. `arrange-workspaces` does this in `focus_verified` and sweeps ordinary windows that fail verification to workspace Z before computing the layout. Orchest windows are never swept: moving one to Z changes its persisted `desktopWorkspaceId`, so the layout pass instead fails and retries while preserving the binding.
- **`wait_for_new_window` uses a 10s timeout.** Some app windows can take longer to appear or settle their titles. The reconciliation pass in `startup-windows` catches these late-appearing windows.
- **Individual AeroSpace CLI clients can wedge during a login-time window burst.** `startup-windows` and `arrange-workspaces` bound each CLI call and retry read-only queries. Automatic startup logs to `~/.local/state/startup-windows-logs/latest.log` for post-login diagnosis.
- **Chrome restores all previous windows onto the active workspace.** `startup-windows` waits for that restore burst to settle, assigns one restored window to each managed workspace, and moves surplus restored windows to Z.
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

1. **Portable:** Create `~/dotfiles/agent_config/skills/<name>/SKILL.md`, then run `sync.sh`. It lands in `~/.claude/skills/` only if the name is in `agent_config/skills-global.list`; otherwise it is scoped to `~/checkoutN/.claude/skills/` and advertised only inside a Meta checkout. Keep the global list short — each entry costs context in every unrelated session.
2. **Local-only:** Create `~/.claude/skills/<name>/SKILL.md` directly

Frontmatter `name:` and `description:` are both mandatory. Claude Code infers a missing `name` from the directory, but Omnigent drops the skill with only a stderr warning — so it silently loads in one harness and not the other. `sync.sh` validates this and reports `SKILL PROBLEMS`.
