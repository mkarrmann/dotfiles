---
name: neovim-meta
description: >-
  Use when answering questions about the user's Neovim editor capabilities,
  available commands, source control features, Phabricator integration, or
  what meta.nvim provides. Also use when asked about editor keybindings,
  repository selection from a checkout root, diff viewing, blame, smartlog,
  CI signals, code browsing, or any "can my editor do X" question. Trigger
  keywords: neovim, nvim, editor, checkout, workspace root, Myles, Biggrep,
  Buck, HgSsl, smartlog, phabricator, diff, blame, CI, signals, browse,
  meta.nvim.
---

# Neovim meta.nvim Reference

## Overview

The user's Neovim runs **LazyVim** with Meta's **meta.nvim** system plugin (`/usr/share/fb-editor-support/nvim`). Source control is entirely Mercurial/Sapling-centric — no fugitive, lazygit, or git-focused SCM plugins.

**Key architectural fact:** meta.nvim delegates heavily to `hg`/`sl` CLI commands. Many features (phabstatus, CI signals, blame) come from the Mercurial command output, not from meta.nvim querying APIs independently. When investigating what's available, check what the underlying `hg` command outputs, not just what meta.nvim's Lua code queries.

## Checkout-Root Repository Selection

Checkout Neovim sessions start in `~/checkoutN`, above the sibling `fbsource/` and `configerator/` repositories. Repository-aware commands do not require Neovim's global cwd to be a repository. They resolve context in this order:

1. The current real file's nearest `.hg` repository.
2. The most recently used repository in the current tab.
3. Another real file visible in the tab, or the cwd if it is itself in a repository.
4. A prompt among immediate child directories containing `.hg` (normally `fbsource` and `configerator`).

Use `:MetaRepo` to force a repository selection for the current tab. If no repository can be resolved or discovered, the command reports an error. The resolver passes an explicit cwd to each subprocess; it does not change Neovim's global cwd.

The resolver applies to:

| Tool | Behavior |
|------|----------|
| Myles (`<leader>p`) | Uses the current file's directory, or the selected repository root for a virtual buffer. |
| Biggrep (`<leader>sg`, `<leader>sr`, `:Bgs`, `:Bgf`, `:Bgr`) | Still uses the Biggrep extension; the wrapper only supplies the resolved directory/repository as `cwd`. |
| Sapling (`<leader>hs`, `:HgSsl`, `:HgSslSplit`, diff helpers, `:SlPull`) | Runs against the resolved Sapling repository. |
| Buck (`<leader>B{t,T,f,l,b,r,g}` and ownership queries) | Uses the nearest `.buckconfig` root for the active file or selected repository. |

Shell commands remain separate from this editor behavior: agents should explicitly run `sl`, `jf`, `arc`, `buck`, and repository-scoped searches from the intended sibling repository.

## Source Control — HgSsl (Interactive Smartlog)

`:HgSsl` opens an interactive smartlog tab. **Two-phase async rendering:**

1. **Phase 1 (instant):** Runs `hg sl` — plain smartlog, no network calls, no signals
2. **Phase 2 (delayed):** Runs `hg ssl` — super smartlog with Phabricator data. Replaces buffer. Shows "ssl updated" notification

**Phase 2 includes BOTH review status AND CI signal indicators:**

| Symbol | Meaning | Highlight |
|--------|---------|-----------|
| `✓` | CI signals passed | Green (`String`) |
| `✗` | CI signals failed | Red (`DiagnosticError`) |
| `‼` | CI signals warning | Yellow (`DiagnosticWarn`) |

These are rendered by `hg ssl` itself (via the `{phabstatus}` Mercurial extension with `signalstatus=True`) and highlighted by `/usr/share/fb-editor-support/nvim/syntax/hgssl.vim`.

**Review statuses shown:** Accepted, Needs Review, Needs Final Review, Changes Planned, Landing, Committed, Reverted, Abandoned, Waiting For Author, Unpublished

**SSL keybindings:**

| Key | Action |
|-----|--------|
| `<CR>` | Context-sensitive command menu (checkout, show, rebase, submit, hide, split, metaedit, open in phabricator, etc.) |
| `j`/`k` | Navigate to next/prev commit |
| `r` | Refresh |
| `c`/`C` | Jump to current commit |
| `s` | Show commit (`hg show`) |
| `gx` | Open diff in Phabricator |

**Submit actions from SSL:** `jf submit`, `jf submit --stack`, `jf submit --draft`, `jf submit --draft --stack`

**Optional status annotation:** If `CONFIG.ssl.status == true`, annotates current commit with `hg status` output (modified/added/removed files) via extmarks.

## Source Control — Other Commands

| Command | Description |
|---------|-------------|
| `:HgBlame` | Scrollbound blame split; `<CR>` on diff ID opens Phabricator; `gq` to close |
| `:HgDiff` | Telescope picker of diff hunks (respects base revision) |
| `:HgDiffIgnoreAllSpace` | Same but ignoring whitespace |
| `:HgDiffCurrentCommit` | Diff hunks for current commit only |
| `:HgStatus` | Interactive status window with staging keybinds (`a`/`s`/`X`/`u`/`d`) |
| `:HgHistory [N]` | Last N diffs for current file (default 50) |
| `:HgCommit` | Open commit message editor |
| `:HgAmend` | Amend changes into current commit |
| `:HgCommitInteractive` | `hg commit --interactive` |
| `:HgAbsorb` | `hg absorb` (auto-distribute changes across stack) |
| `:HgHunkRevert` | Revert hunk under cursor |
| `:HgRead` | Restore buffer to committed version |
| `:HgPrev`/`:HgNext` | Navigate commit stack |
| `:HgChangeBase [rev]` | Change base revision for diff/gutter (empty resets) |
| `:HgChangeBaseStack` | Set base to last public ancestor |
| `:HgShowBase` | Show current base revision |
| `:HgLineBlameToggle` | Toggle inline blame virtual text |
| `:SlPull` | Async `sl pull` (user-defined in `config/local.lua`) |

**Hunk navigation:** `]h`/`[h` next/prev hunk, `]H`/`[H` last/first hunk

**Gutter signs:** Automatic `+` (DiffAdd) and `_` (DiffDelete) signs, comparing buffer against `hg cat` of base revision, updated on save and debounced on text change.

## Phabricator Integration

| Command/Action | Description |
|----------------|-------------|
| `:HgBrowse` | Open current file/selection in Phabricator code browser |
| `:HgBrowseYank` | Yank the Phabricator URL |
| `:HgBrowseRev` / `:HgBrowseRevYank` | Same but pinned to current commit |
| `:MetaDiffComments` | Fetch and display Phabricator comments for current diff (via `jf graphql`) |
| `:MetaDiffCheckout` | Telescope picker of your diffs with checkout |
| `:MetaDiffOpenFiles` | Telescope picker of your diffs, opens files |
| `gx` (normal mode) | Smart opener — if word matches `[DTPSNCX]\d+`, opens via BunnyLOL (Phabricator diff, task, paste, etc.) |

## Other Meta Features

**Telescope extensions:** `myles` (file finder, `<leader>p`), `biggrep` (code search: `<leader>sg` string, `<leader>sr` regex; `:Bgs`/`:Bgf`/`:Bgr` commands), `hg` (diff picker)

**Meta LSPs:** `cppls@meta`, `fb-pyright-ls@meta`, `pyre@meta`, `buck2@meta`, `linttool@meta`

**MetaMate:** AI code completion, configured for Java, Python, C++, Rust, and other filetypes

**Buck2:** Test/build/run with `<leader>B{t,T,f,l,b,r,g}` keybindings

**Other modules:** `meta.dap` (debugger), `meta.jf` (JustFab/GraphQL), `meta.slog` (server log tailing), `meta.dmt` (dev message terminal), `meta.cmp` (completion for tasks/tags/revsub/title), `meta.cmds` (codehub links)
