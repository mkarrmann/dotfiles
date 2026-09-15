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

Config is split into **source-controlled** (portable, in `~/dotfiles/`) and **local-only** (machine-specific, created directly in target locations). Source-controlled config loads first, then silently loads a local override if present.

Two profiles, resolved by `bin/dotfiles-profile` as `DOTFILES_PROFILE`:

- **work** — Meta devservers and the work Mac. Meta plugins, internal MCPs, checkout-scoped skills, `meta_init.sh`.
- **desktop** — the personal Linux box (Sway). Runs its own local Omnigent server; gets the AWS Agent Toolkit.

Omnigent client config applies on both; only real hub infrastructure is gated to work.

## Paired Meta Workspaces

Each `~/checkoutN` is the working-directory root for one editor and agent session and holds two independent repositories, `fbsource/` and `configerator/`. The behavioral rules for working inside one — repository selection, never hardcoding a checkout number, scoping `sl` / `jf` / `arc` / `buck` / `meta-rg` — live in `agent_config/meta-workspace-preferences.md`, which `sync.sh` symlinks to `<workspace>/CLAUDE.md` and `<workspace>/AGENTS.md`; an agent working in a checkout has already loaded them.

## Config Architecture

```
~/dotfiles/                          (git repo, portable across machines)
├── sync.sh                          Reflects config: symlinks, generated files, staged units.
│                                      Idempotent; never restarts running services
├── init.sh                          Runs sync.sh, then network-bound installs and live-service convergence
├── meta_init.sh                     Work only: Meta nvim local config, ~/.zshenv.local, dvsc-core-acp deps,
│                                      headless Obsidian, agent-market plugins
├── .shellrc, .zshrc, .tmux.conf ... → ~/
├── nvim_init.lua                    → ~/.config/nvim/init.lua
├── nvim/lua/{config,plugins,lib}/   → ~/.config/nvim/lua/... (sync.sh)
├── nvim/local/{config,plugins}/     → ~/.config/nvim/lua/... (meta_init.sh)
├── claude_config/                   CLAUDE.md, statusline.sh, hooks/, agent-manager/ → ~/.claude/...;
│                                      meta-config.toml → ~/.claude/meta/config.toml (work only);
│                                      settings.json keys rewritten in place by sync.sh
├── codex_config/                    config.template.toml (+ config.work.toml on work) + ~/.codex/config.local.toml
│                                      → rendered ~/.codex/config.toml by agent_config/codex_config.py
├── agent_config/
│   ├── global-development-preferences.md  → 3 global sinks; see "Global Agent Rules"
│   ├── meta-workspace-preferences.md      → ~/checkoutN/{CLAUDE.md,AGENTS.md}
│   ├── skills/*/                    → ~/.claude/skills/* if listed in skills-global.list,
│   │                                  else ~/checkoutN/.claude/skills/* (see agent_config/README.md)
│   ├── sync-mcps, plugins/, plugins.list, drop-plugins.list, aws-skills.list
├── omnigent_config/                 topology.env, config.shared.yaml, config.server.yaml, agents/, policy_modules/
├── systemd/ (+ systemd/desktop/)    user units → ~/.config/systemd/user; launchd/ plists on the Mac
├── sway_config, wofi_config         Linux desktop; aerospace.toml, hammerspoon.lua, sketchybar/ on the Mac
├── bin/*                            → ~/bin/
└── bin-linux/*, bin-macos/*         → ~/bin/, linked by uname first and taking priority over bin/
```

**Two entry points, different blast radius.** `sync.sh` only reflects config and is safe to run any time — use it to apply dotfile edits. `init.sh` runs `sync.sh` first, then does network-bound installs and restarts/reconciles running services. Reach for `init.sh` on a new machine or a deliberate full converge.

## Local Override Pattern

Every layer uses the same pattern — load portable config, then silently load local overrides:

| Layer              | Portable                        | Local override                | Mechanism                                                            |
| ------------------ | ------------------------------- | ----------------------------- | -------------------------------------------------------------------- |
| Neovim             | `config/*.lua`, `plugins/*.lua` | `config/local.lua`            | `pcall(require, "config.local")` in `autocmds.lua`                   |
| Shell              | `.shellrc`                      | `~/.localrc`                  | `source ~/.localrc` in `.shellrc`                                    |
| Tmux               | `.tmux.conf`                    | `~/.tmux.conf.local`          | `source-file` if exists                                              |
| Claude             | `CLAUDE.md`                     | `CLAUDE.local.md`             | `@~/.claude/CLAUDE.local.md` reference                               |
| Codex              | `config.template.toml`          | `~/.codex/config.local.toml`  | Deep-merged (local wins) by `codex_config.py` when `sync.sh` renders |
| Codex instructions | `~/.codex/AGENTS.md`            | `~/.codex/AGENTS.override.md` | Read by Codex; `sync.sh` leaves it alone                             |

**Rule of thumb:** `local.lua` / `localrc` / etc. are the machine-specific escape hatches — not in dotfiles. Shared config (even Meta-specific) lives in dotfiles under a descriptive name.

## Meta Config Opt-In Pattern

Meta-specific Neovim config lives in `nvim/local/` (source-controlled, but only symlinked by `meta_init.sh`). On a Meta machine, run `bash meta_init.sh` after `init.sh`; it symlinks `nvim/local/config/*.lua` and `nvim/local/plugins/*.lua` into the nvim runtime and creates `~/.config/nvim/lua/config/local.lua` containing `require("config.meta")` if it does not exist.

- **`plugins/meta.lua`** — auto-loaded by lazy.nvim. meta.nvim is `cond`-guarded on its install path; none-ls in the same file is not.
- **`config/meta.lua`** — loaded only via the `config/local.lua` opt-in.

On non-Meta machines neither file is symlinked, so Meta config is completely absent.

## Global Agent Rules

`agent_config/global-development-preferences.md` is the single canonical file. `sync.sh` symlinks it into each agent's own global-instruction path; no agent reads another's, and each sees it exactly once.

| Sink                                                | Serves                                                                               |
| --------------------------------------------------- | ------------------------------------------------------------------------------------ |
| `~/.claude/rules/global-development-preferences.md` | Claude Code TUI, Claude Agent SDK, Omnigent `claude-sdk` agents (claude/polly/debby) |
| `~/.codex/AGENTS.md`                                | Codex TUI, `codex exec`, codex app-server, Omnigent `codex` agents                   |
| `opencode.json` → `instructions`                    | Metacode. **Not** `~/.config/opencode/AGENTS.md` — see below                         |

`~/.claude/CLAUDE.md` pulls the first in via `@~/.claude/rules/...`.

**Rules are split by scope.** The global file holds only machine-agnostic preferences. Anything specific to the Meta checkout layout lives in `meta-workspace-preferences.md`, symlinked into each detected workspace root as both `CLAUDE.md` and `AGENTS.md`. Claude Code reads `CLAUDE.md` and walks up from subdirectories; Codex reads `AGENTS.md` in cwd only and does not walk up. So a session started at the workspace root gets the rules in either agent, a Codex session started inside `fbsource/` gets that repo's own `AGENTS.md` instead, and machines with no checkout get neither — which is why this content must not sit in the global file.

Why the two non-obvious paths work (verified against `~/repos/omnigent`, 2026-08):

- **Claude Agent SDK under Omnigent.** A spec's `prompt:` becomes the system prompt but does not suppress CLAUDE.md: `skills_filter` defaults to `"all"` → `setting_sources=None` → the SDK emits `--setting-sources=user,project`, and Omnigent deliberately omits `--bare`, which would skip CLAUDE.md discovery.
- **Codex under Omnigent.** The executor redirects `CODEX_HOME` to a per-session private home but symlinks `AGENTS.md` / `AGENTS.override.md` in from the real `~/.codex`. Suppressed only by `HARNESS_CODEX_MINIMAL_CONFIG`, which Omnigent sets just for its background title-generation sessions.

Traps:

- **`~/.codex/rules/` is not an instructions directory.** It is the exec-policy store and keeps only `*.rules` entries; a `.md` there is silently ignored. `sync.sh` deletes the stale link.
- **Metacode ignores `~/.config/opencode/AGENTS.md`.** It loads global rules only from the `instructions` array in `opencode.json`, which `sync-mcps` writes; `sync.sh` removes the old no-op symlink. Its startup banner prints `N rules loaded`.
- **YAML frontmatter is not portable across sinks.** Claude Code strips it; Codex injects it verbatim as instruction text. Rules files are loaded by path, so a description buys nothing and costs tokens in Codex. Keep frontmatter for skills only, where it is mandatory.
- **`~/.claude/rules/` is a Claude-only convention.** Codex and Metacode never read it.
- **`skills: none` on an Omnigent spec also kills the rules.** It forces `setting_sources=[]`, suppressing CLAUDE.md along with the skill listing. No spec sets it today.

Not covered: **dvsc / devmate.** Its spec declares no `prompt:`, and the generic ACP harness injects no instructions — dvsc-core owns its prompt end to end. If a future harness reads none of these sinks, Omnigent's `instructions:` / `prompt:` spec field is the harness-agnostic fallback (same field; the parser prefers `instructions:`). It resolves a sibling filename inside the agent dir, reaches every executor — the ACP harness folds it into the first user turn — and agent bundles are content-addressed, so a sibling file re-registers when it changes.

## Where Things Go

| What                                                 | Location                                                         | Source-controlled?                                                                                                  |
| ---------------------------------------------------- | ---------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------- |
| New skill (portable or Meta-specific)                | `~/dotfiles/agent_config/skills/<name>/SKILL.md`                 | Yes — symlinked by `sync.sh`; global only if in `skills-global.list`, else checkout-scoped                          |
| Truly local-only skill                               | `~/.claude/skills/<name>/SKILL.md`                               | No — created directly                                                                                               |
| Cross-agent rules (machine-agnostic)                 | `~/dotfiles/agent_config/global-development-preferences.md`      | Yes — 3 sinks via `sync.sh`                                                                                         |
| Meta checkout-layout rules                           | `~/dotfiles/agent_config/meta-workspace-preferences.md`          | Yes — → `~/checkoutN/{CLAUDE.md,AGENTS.md}`                                                                         |
| tpai rules/skills policy                             | `~/dotfiles/claude_config/meta-config.toml`                      | Yes — → `~/.claude/meta/config.toml` (work only)                                                                    |
| Meta nvim plugins / config                           | `~/dotfiles/nvim/local/{plugins,config}/meta.lua`                | Yes — symlinked by `meta_init.sh`; config opt-in via `local.lua`                                                    |
| Machine-specific nvim config                         | `~/.config/nvim/lua/config/local.lua`                            | No — created by `meta_init.sh` or manually                                                                          |
| Claude Code auto-memory                              | `~/.claude/projects/<cwd-slug>/memory/MEMORY.md` + topic files   | No — written by Claude Code                                                                                         |
| Machine-specific shell config                        | `~/.localrc`                                                     | No                                                                                                                  |
| CLI tools (gh, marksman, nori, aws, stylua)          | `~/.local/bin`                                                   | No — `bin/gh-ensure`, `bin/marksman-ensure`, `bin/install-or-upgrade-nori`, `bin/stylua-ensure`, run by `init.sh`   |
| AWS Agent Toolkit (`aws-mcp` server, `aws-*` skills) | `~/.claude.json`, `~/.claude/skills/aws-*`, `~/.agents/skills/*` | No — `bin/aws-agent-toolkit-ensure` (desktop `init.sh`, after `aws login`); gated by `agent_config/aws-skills.list` |

## Editor Stack

**Framework:** LazyVim (Neovim distribution on lazy.nvim), nvim-cmp for completion, `midnight.nvim` theme.

**Portable plugins** (in dotfiles): telescope, treesitter, flash, lualine, undotree, tmux-navigator, codecompanion.nvim (personal fork), nvim-dap + nvim-jdtls (Java, with the Maven/Presto build integration in `lib/presto-maven.lua`), obsidian.nvim, scope.nvim, remote-nvim.

**Meta plugins** (`nvim/local/plugins/meta.lua`, symlinked by `meta_init.sh`): meta.nvim (detected at `/usr/share/fb-editor-support/nvim` on Linux or `/usr/local/share/fb-editor-support/nvim` on Mac), none-ls.

**Meta config** (`nvim/local/config/meta.lua`, opt-in via `local.lua`): Meta LSPs (`buck2@meta`, `cppls@meta`, `hhvm`, `ids@meta`, `linttool@meta`, `pyrefly@meta`, `rust-analyzer@meta`, `thriftlsp@meta`), MetaMate AI, Buck keybindings, `Hg*` command keymaps, Telescope myles/biggrep extensions.

For meta.nvim capabilities, see the `neovim-meta` skill.

## Remote Neovim Sessions (nvs)

On devvms Neovim runs as a **headless server** (`bin/nvs --launch SESSION`, under `systemd/nvs@.service`) with a thin **TUI client** on the Mac (`bin-macos/nvs`, `nvim --server localhost:PORT --remote-ui`) connected through ET tunnels. Sessions survive disconnects; you just reconnect the UI.

| File                               | Where  | Purpose                                                                                                                                                            |
| ---------------------------------- | ------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `bin/nvs`, `systemd/nvs@.service`  | Remote | Headless server per session; loads clipboard-relay                                                                                                                 |
| `bin-macos/nvs`                    | Mac    | TUI client — waits for the tunnel, connects `--remote-ui`                                                                                                          |
| `bin-macos/nvs-tunnels`            | Mac    | Per-devvm ET tunnels: forward session ports, reverse 8765 (clipboard), 7847 (ACP), 13100→3100 (Orchest); starts the clipboard listener. Does **not** start servers |
| `bin-macos/nvs-clip-listen`        | Mac    | Listens on 8765, pipes to `pbcopy`                                                                                                                                 |
| `nvim/lua/lib/clipboard-relay.lua` | Remote | `g:clipboard` provider + `TextYankPost` autocmd — sends yanks via `nc -w 1` to the Mac                                                                             |
| `bin-macos/startup-windows`        | Mac    | Launches tunnel + session windows via AeroSpace                                                                                                                    |

**Clipboard.** The headless server has no terminal, so OSC 52 has nowhere to go; yanks go over the reverse tunnel to `nvs-clip-listen` instead. Copy (remote → Mac) is automatic on every yank. Paste (Mac → remote) is `Cmd+V` in Ghostty (bracketed paste); `"+p` pastes the last _remote_ yank, not the current Mac clipboard.

**Sessions.** Named `<DEVSERVER>-checkoutN` (e.g. `FTW-checkout1`); port `cksum(name) % 1000 + 7000`. Checkout sessions start in `~/checkoutN`. `~/.config/nvs` is dotsync2-managed (the blanket `".config"` include sweeps it up), so every devserver sees the same directory; per-session `.env` files carry the host prefix, and the session list is host-scoped as `sessions.<short hostname>`, resolved by `bin/nvs-sessions-file` with unsuffixed `sessions` as the fallback. Declaring another host's sessions spawns servers nothing connects to. `WORKDIR` is read only at unit start; `nvs-restart SESSION` applies an edit, and `nvs-setup` warns when a live server has drifted.

## Window management

**Mac (AeroSpace).** `bin-macos/startup-windows` owns the layout: workspace 1 local (Ghostty, Chrome, Omnigent), T tunnel windows (Ghostty, one per devvm), 2/4/6/8 CCO checkout1–4, 3/5/7 FTW checkout1–3, 11 dashboard (`arrange-ws11`), Z sweep/overflow. Each numbered workspace holds an `nvs` Ghostty window, a Chrome window and an Omnigent window. `arrange-workspaces [--force N]` is the layout dispatcher (sidebar | accordion; 11 delegates; Z untouched; serialized via a lock). `auto-accordion` is an optional `on-window-detected` callback, currently disabled in `aerospace.toml`. Automatic startup logs to `~/.local/state/startup-windows-logs/latest.log`.

**Linux desktop (Sway).** `bin-linux/startup-windows` is a rewrite for sway, not a port: workspaces 1–6 map to local directories (no devservers), 9 is the dashboard, Z is the sweep. `bin-linux/arrange-workspaces` lays out Orchest sidebar | stack, `sway-auto-stack` is the event-driven counterpart of auto-accordion, and `sway-windows-lib.sh` identifies windows by `app_id`. `sway_config` → `~/.config/sway/config`, which includes `~/.config/sway/config.d/*` for machine-specific pieces such as monitor layout.

**AeroSpace behaviours the Mac scripts work around** (each is embodied in `arrange-workspaces` / `startup-windows`; check there before changing them):

- `move left/right` at a container boundary nests a perpendicular sub-container; into an adjacent container it _enters_ it. Use `move` only for interior swaps.
- `layout accordion` on a root-level child changes the root layout. Apply it only inside a nested container created by `join-with`, which is a no-op on floating windows.
- `move-node-to-workspace` always inserts at root level, rightmost — the only reliable way to extract a window from a nested container.
- Normalization is off (`enable-normalization-flatten-containers = false`), so single-child containers persist; `arrange-workspaces` builds single-window accordions by borrowing a scaffold window from Z.
- `aerospace layout <a> <b> ...` _cycles_ through its args. Use explicit `h_tiles` / `v_tiles` / `h_accordion` / `v_accordion`. `flatten-workspace-tree` resets root to `default-root-container-layout` (accordion), so follow it with `layout h_tiles`.
- Spatial order after flatten is unpredictable, and `focus left/right` wraps even with `--boundaries-action stop` when a phantom window is in the tree. `discover_spatial_order` uses visited-ID cycle detection, and `focus_verified` re-queries the focused window after every `focus --window-id`, sweeping ordinary windows that fail to Z. Orchest windows are never swept (a move changes their persisted `desktopWorkspaceId`).
- Windows can appear late (`wait_for_new_window` gives up after 10 s; a reconciliation pass catches stragglers), CLI clients can wedge during the login burst (calls are bounded and read-only queries retried), and Chrome restores every previous window onto the active workspace (startup waits for the burst, then distributes them).
- macOS bash is 3.2: no `declare -A`. Use `grep -qx` against newline-separated ID lists.

## Adding a New Skill

1. **Portable or Meta-specific:** Create `~/dotfiles/agent_config/skills/<name>/SKILL.md`, then run `sync.sh`. It lands in `~/.claude/skills/` only if the name is in `agent_config/skills-global.list`; otherwise it is scoped to `~/checkoutN/.claude/skills/` and advertised only inside a Meta checkout. Keep the global list short — each entry costs context in every unrelated session, and Codex's catalog budget is small (see `agent_config/README.md`).
2. **Local-only:** Create `~/.claude/skills/<name>/SKILL.md` directly.

Frontmatter `name:` and `description:` are both mandatory. Claude Code infers a missing `name` from the directory, but Omnigent skips the skill with only a stderr warning — so it silently loads in one harness and not the other. `sync.sh` validates this and reports `SKILL PROBLEMS`.
