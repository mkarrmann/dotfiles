# Design doc: centralize workspace layout in personal config

**Status:** Draft
**Author:** mkarrmann (with Claude)
**Date:** 2026-05-07
**Audience:** Future me, or any agent picking this up cold.

---

## TL;DR

After migrating my personal Meta dev setup from `~/fbsource{,2,3}` + `~/configerator{,2,3}` to a paired-workspace layout (`~/checkout1/{fbsource,configerator}` and `~/checkout2/{fbsource,configerator}`), I had to update **12 files** that hardcoded the old paths in personal config. The new layout still has hardcoded paths in the same files — just spelled `~/checkout1` instead. This doc proposes a single source of truth (`~/dotfiles/workspaces.sh` + `~/dotfiles/nvim/lua/lib/workspaces.lua`) so future workspace renames or additions become a one-line change.

---

## 1. Background

### 1.1 The Meta dev environment

Meta engineers work in two large monorepos cloned via [EdenFS](https://www.internalfb.com/wiki/Source-Control-Users/Get_Started/NewToEden/), a virtual filesystem optimized for huge repos:

- **`fbsource`** — the main source monorepo (`fbcode/`, `arvr/`, `fbandroid/`, `xplat/`, etc.).
- **`configerator`** — the runtime config monorepo (Thrift configs, JustKnobs, deployment configs).

Engineers typically `fbclone fbsource` into `~/fbsource` and `fbclone configerator` into `~/configerator`. EdenFS pins each clone to its absolute mount path in `~/local/.eden/config.json` — there is no `eden mv` / `eden rename`, so relocating a checkout means `eden rm` + re-clone.

### 1.2 Multi-checkout workflow

I keep two parallel checkouts so I can do "checkout2" work (experiments, oncall, hack-day code) without clobbering my "checkout1" branch state. Pre-migration this looked like:

```
~/fbsource          → /home/mkarrmann/local/fbsource  (symlink)
~/fbsource2         → real Eden mount
~/fbsource3         → real Eden mount (rarely used)
~/configerator      → /home/mkarrmann/local/configerator  (symlink)
~/configerator2     → real Eden mount
~/configerator3     → real Eden mount
```

Post-migration this is:

```
~/checkout1/fbsource         → real Eden mount  (primary)
~/checkout1/configerator     → real Eden mount  (primary)
~/checkout2/fbsource      → real Eden mount  (parallel work)
~/checkout2/configerator  → real Eden mount  (parallel work)
```

The motivation for the rename: opening a single VS Code multi-root workspace per top-level dir (`~/checkout1` vs `~/checkout2`) gives me both `fbsource` and `configerator` side-by-side, and `~/checkout1`/`~/checkout2` are more meaningful directory names than `~/fbsource2`.

### 1.3 Maven build isolation

Java Presto code (which I work on) is built via Maven, not Buck. Concurrent builds in different workspaces would clobber each other through:

- The default `~/.m2/repository` (installed JARs).
- The `out-of-tree-build-root` (compiled classes).

So my `~/.localrc` defines a `_checkout_suffix` function that returns `""` for the primary workspace and `-<workspace>` (e.g. `-checkout2`) for any other. Maven invocations apply:

- `-Dmaven.repo.local=${BUILD_ROOT}/m2-repo${suffix}`
- `-Dout-of-tree-build-root=${BUILD_ROOT}/<project>${suffix}`

`BUILD_ROOT` is `/data/users/mkarrmann/builds`. After this refactor, the secondary workspace `~/checkout2` produces `${BUILD_ROOT}/m2-repo-checkout2` and `${BUILD_ROOT}/presto-trunk-checkout2` etc.

### 1.4 Multi-devserver orchestration

I work across **two devservers** simultaneously, both reachable from my Mac via [ET](https://www.internalfb.com/wiki/Engineering_Terminal_(ET)/) tunnels:

- **FTW**: `devvm36111.ftw0.facebook.com`
- **CCO**: `devvm20365.cco0.facebook.com`

Each devserver has both workspaces (`~/checkout1` and `~/checkout2`). Window orchestration on the Mac is via `~/dotfiles/bin-macos/startup-windows`, which uses [AeroSpace](https://github.com/nikitabobko/AeroSpace) to lay out 9 numeric workspaces:

| WS | Contents |
|----|----------|
| 1  | local Mac terminal + Chrome |
| T  | per-devserver SSH tunnel windows (Ghostty) |
| 2  | FTW general shell |
| 3  | FTW main workspace (Ghostty + VS Code) |
| 4  | FTW scratch workspace (Ghostty + VS Code) |
| 5  | CCO general shell |
| 6  | CCO main workspace |
| 7  | CCO scratch workspace |
| 8/9| Chrome scratch |
| Z  | sweep / stragglers |

VS Code remote opens are via `vscode-remote://fb-remote+<devvm>/home/mkarrmann/<name>.code-workspace` — multi-root files that bundle `fbsource` and `configerator` from the same workspace dir.

### 1.5 Neovim sessions

Headless Neovim servers run on each devserver, persistent across SSH sessions. The wrapper `~/dotfiles/bin/nvs` takes a session name and optional working dir; it hashes the name to a port in `[7000, 8000)`, starts a `nvim --headless --listen localhost:<port>` if not already running, and `cd`s to the workdir on first start.

The Mac side runs `nvs-tunnels` per devserver, which:
1. Sets up SSH port forwarding for each session's port.
2. Invokes `nvs --server-only <session> <workdir>` on the remote — this starts the headless server with the right `cd` and a watchdog that auto-restarts it within ~5s if it dies.

Post-migration sessions are named `FTW-checkout1`, `FTW-checkout2`, `CCO-checkout1`, `CCO-checkout2`.

### 1.6 Dotfiles distribution

`~/dotfiles/` is synced across all my machines via `dotsync2` (Meta-internal). Files inside `~/dotfiles/` are typically symlinked into `~/`, e.g. `~/.bashrc -> ~/dotfiles/.bashrc` and `~/bin/<tool> -> ~/dotfiles/bin/<tool>`. The Mac-only dir `~/dotfiles/bin-macos/` is excluded from devservers.

---

## 2. Problem

The recent migration from `~/fbsource{,2,3}` to `~/checkout1/fbsource` + `~/checkout2/fbsource` required edits to **12 files** in personal config:

1. `~/.localrc` — `_fbsource_root`, `_checkout_suffix`, `gfb`, `con`, `gf`, `gp`
2. `~/dotfiles/bin-macos/startup-windows` — workspace table, regexes, nvs args, vscode-remote URIs
3. `~/dotfiles/bin-macos/nvs-tunnels` — example comment
4. `~/dotfiles/bin/devmate_mux` — `cd "$HOME/fbsource"`
5. `~/dotfiles/bin/migrate-checkouts` — script accepts `--primary`/`--secondary` flags (already parameterized but defaults baked in)
6. `~/dotfiles/nvim/lua/lib/presto-maven.lua` — `DEV_HOME = ~/fbsource/fbcode/github`
7. `~/dotfiles/nvim/lua/lib/fdb-dap.lua` — DEBUGPY_DOTSLASH path + buck cwd
8. `~/dotfiles/nvim/lua/plugins/dap.lua` — DAP source-map paths × 3
9. `~/dotfiles/agent_config/skills/presto-build/presto-build` — fallback path + suffix table
10. `~/dotfiles/agent_config/skills/presto-build/SKILL.md` — doc text
11. `~/dotfiles/agent_config/skills/presto-deploy/SKILL.md` — example paths
12. `~/dotfiles/agent_config/skills/presto-gateway-deploy/SKILL.md` + `presto-gateway-deploy-finish` — TW_CONFIG path + doc
13. `~/dotfiles/agent_config/skills/screenshot-workflow/SKILL.md` — symlink description
14. `~/.local_init.sh` — Sphinx venv + Rust toolchain paths × 4
15. `~/.claude/projects/presto.md` — checkout isolation table

(Plus `~/checkout1.code-workspace` and `~/checkout2.code-workspace` — these are inherently per-workspace and not interesting here.)

This is a code smell. Renaming a workspace, adding a third, or changing the primary should not be a 12-file diff.

### 2.1 Why Meta tooling doesn't have this problem

Meta's own tools (`arc`, `sl`, `eden`, `buck`) walk up from `cwd` to find their config (`.arcconfig`, `.sl/`, `.eden/root`, `.buckconfig`). They don't care which checkout you're in — they discover it. **The hardcoding lives entirely in the personal-config layer.**

### 2.2 Why each hardcode exists (root-cause taxonomy)

Each of the 12 files falls into one of four categories:

#### A. **Inherent — must reference a specific checkout**

The DAP source maps in `dap.lua` and `fdb-dap.lua` map binary debug-info paths back to source. The binary was compiled from a specific checkout, so the IDE has to know which one. This category is irreducible — but the *number* of hardcoded checkouts should match the number of workspaces (one map per workspace).

#### B. **"Pick a default when no PWD context"**

`.local_init.sh` (system setup, runs from `$HOME`), the `presto-build` script's fallback (`echo "$HOME/fbsource/fbcode"`), and the `devmate_mux` wrapper (`cd "$HOME/fbsource"` because devmate_mux's auto-detection is broken on devvms) all need *some* default when they can't infer a checkout from cwd. Defensible — but the literal path should be a variable, not baked in.

#### C. **"Convenience shortcut"**

`presto-gateway-deploy-finish`'s `TW_CONFIG`, `presto-maven.lua`'s `DEV_HOME`, the `gfb` and `con` aliases — these reach for a specific checkout when they could just walk up from cwd to find one (the way `presto-build`'s `_detect_fbcode_home` does). **Not defensible.** This is the actual smell.

#### D. **Documentation strings**

The four `SKILL.md` files mention old paths in prose. Not load-bearing; just stale descriptions that mislead readers (including LLM agents).

### 2.3 Cost analysis

Today: every workspace rename or addition is an N-file diff. With N+1 workspaces I have to either accept that the secondary workspaces are second-class (no DAP mapping, no shortcut aliases) or add another N edits.

After this refactor: one-line edit to `workspaces.sh`, plus per-workspace files (`.code-workspace`, peacock theme) where each workspace inherently differs.

---

## 3. Goals & Non-Goals

### Goals

1. Single source of truth for workspace names and the primary workspace.
2. Convenience shortcuts (`gfb`, `con`, `cd "$HOME/fbsource"`) walk up from cwd to find the active checkout, falling back to the primary workspace.
3. Default-fallback paths (setup scripts, presto-build) reference the primary via a variable, not a literal.
4. DAP source maps enumerate all workspaces dynamically.
5. Adding/renaming a workspace requires ≤ 2 edits (the central source + the new `.code-workspace` file).
6. Existing tools (`arc`, `sl`, `buck`, `eden`) keep working — no changes to their behavior.

### Non-Goals

- Refactoring `migrate-checkouts` itself. It already takes `--primary`/`--secondary` flags; auto-reading from `workspaces.sh` is a nice-to-have but not blocking.
- Auto-creating workspace `.code-workspace` files when adding a workspace. They have unique colors and folder lists that benefit from manual review.
- Changing the workspace layout itself (e.g. flat vs nested). The `~/<workspace>/{fbsource,configerator}` structure is fine.
- Touching Meta-side tooling. Out of scope.

---

## 4. Current state inventory

Detailed list of every place workspace assumptions live, with file path, line range, what it does, and which category it falls into. A fresh agent should be able to use this as a checklist for the refactor.

### Shell layer

| File | Lines | What | Category | Notes |
|------|-------|------|----------|-------|
| `~/.localrc` | 50–95 | `_workspace_root`, `_fbsource_root`, `_configerator_root`, `_checkout_suffix`, `gfb`, `con`, `gf`, `gp`, `_mf_flags`, `_mp_flags` | C (shortcuts) + B (suffix) | `_workspace_root` already case-matches `~/checkout1` and `~/checkout2` literally — needs to become a loop over `WORKSPACES` |
| `~/.local_init.sh` | 62, 63, 94, 232 | Sphinx venv + Rust toolchain paths, all under `~/checkout1/fbsource/...` | B (default) | Setup runs from `$HOME` so cwd-walking won't help — needs `$PRIMARY_FBSOURCE` |
| `~/dotfiles/bin/devmate_mux` | 4 | `cd "$HOME/main/fbsource"` before invoking real `devmate_mux` | B/C | Wrapper exists because real devmate_mux's auto-detect is broken on devvms |
| `~/dotfiles/bin/migrate-checkouts` | top | `PRIMARY_NAME=main`, `SECONDARY_NAME=scratch` defaults | already parameterized | Could read defaults from `workspaces.sh` |

### Neovim layer

| File | Lines | What | Category | Notes |
|------|-------|------|----------|-------|
| `~/dotfiles/nvim/lua/lib/presto-maven.lua` | 4 | `DEV_HOME = ~/checkout1/fbsource/fbcode/github` | C | Could walk up from `vim.fn.getcwd()` |
| `~/dotfiles/nvim/lua/lib/fdb-dap.lua` | 10, 175 | `DEBUGPY_DOTSLASH` + buck cwd | C | Same — could walk up |
| `~/dotfiles/nvim/lua/plugins/dap.lua` | 45–47 | LLDB source maps | A | Must enumerate all workspaces (currently only maps `~/checkout1`) |

### Mac orchestration layer

| File | Lines | What | Category |
|------|-------|------|----------|
| `~/dotfiles/bin-macos/startup-windows` | 30–53 | Workspace table: 4 entries per devserver hardcoded | A (per-workspace) + B (table-of-workspaces) |
| `~/dotfiles/bin-macos/nvs-tunnels` | 17 | Example comment in usage docstring | D |

### Tooling-script layer

| File | Lines | What | Category |
|------|-------|------|----------|
| `~/dotfiles/agent_config/skills/presto-build/presto-build` | 18, 28, 36–40 | Auto-detects checkout from cwd; falls back to `~/checkout1/fbsource/fbcode`; suffix derivation already updated | B (fallback) + cleanly-done |
| `~/dotfiles/agent_config/skills/presto-gateway-deploy/presto-gateway-deploy-finish` | 14 | `TW_CONFIG="$HOME/main/fbsource/...gateway-test.tw"` | C |

### Documentation layer

| File | What |
|------|------|
| `~/dotfiles/agent_config/skills/presto-build/SKILL.md` | Mentions workspace layout |
| `~/dotfiles/agent_config/skills/presto-deploy/SKILL.md` | Example paths |
| `~/dotfiles/agent_config/skills/presto-gateway-deploy/SKILL.md` | Suffix doc |
| `~/dotfiles/agent_config/skills/screenshot-workflow/SKILL.md` | Symlink description |
| `~/.claude/projects/presto.md` | Checkout isolation table |
| `~/dotfiles/docs/workspace-layout-refactor.md` | This doc |

### Per-workspace files (out of scope; inherent)

| File | What |
|------|------|
| `~/dotfiles/checkout1.code-workspace` | Multi-root workspace, blue peacock theme |
| `~/dotfiles/checkout2.code-workspace` | Multi-root workspace, teal peacock theme |
| `~/checkout1.code-workspace`, `~/checkout2.code-workspace` | Symlinks → dotfiles |

---

## 5. Design

### 5.1 Single source of truth: `~/dotfiles/workspaces.sh`

```bash
# ~/dotfiles/workspaces.sh
# Workspace layout for personal dev setup. Sourced from .localrc, .local_init.sh,
# and any tooling script that needs to know about workspaces.
#
# To add a workspace:
#   1. Add it to WORKSPACES (and decide if it should be PRIMARY).
#   2. Create ~/dotfiles/<name>.code-workspace (peacock-themed).
#   3. Re-run ~/bin/migrate-checkouts to clone into ~/<name>/{fbsource,configerator}.

export WORKSPACES=(main scratch)
export PRIMARY_WORKSPACE="${PRIMARY_WORKSPACE:-main}"

# Convenience derived values.
export PRIMARY_WORKSPACE_DIR="$HOME/$PRIMARY_WORKSPACE"
export PRIMARY_FBSOURCE="$PRIMARY_WORKSPACE_DIR/fbsource"
export PRIMARY_CONFIGERATOR="$PRIMARY_WORKSPACE_DIR/configerator"

# Detect which workspace PWD is in. Echoes the workspace name (e.g. "checkout1",
# "checkout2") or PRIMARY_WORKSPACE if PWD is not inside any workspace.
_current_workspace() {
  local ws
  for ws in "${WORKSPACES[@]}"; do
    case "$PWD" in
      "$HOME/$ws"|"$HOME/$ws"/*) echo "$ws"; return ;;
    esac
  done
  echo "$PRIMARY_WORKSPACE"
}

_current_workspace_dir()    { echo "$HOME/$(_current_workspace)"; }
_current_fbsource()         { echo "$HOME/$(_current_workspace)/fbsource"; }
_current_configerator()     { echo "$HOME/$(_current_workspace)/configerator"; }

# Maven suffix: empty for primary; '-<workspace>' otherwise.
_workspace_suffix() {
  local ws=$(_current_workspace)
  [[ "$ws" == "$PRIMARY_WORKSPACE" ]] && echo "" || echo "-$ws"
}
```

### 5.2 Lua mirror: `~/dotfiles/nvim/lua/lib/workspaces.lua`

```lua
-- Mirror of workspaces.sh for the Neovim side. Single source of truth for
-- which workspaces exist and where the primary one lives.
local M = {}

-- Read from env if set (so .localrc and Lua agree); fall back to literal.
local function from_env_or(name, default)
  local v = vim.env[name]
  if v == nil or v == "" then return default end
  return v
end

M.workspaces = {"checkout1", "checkout2"}
M.primary    = from_env_or("PRIMARY_WORKSPACE", "checkout1")

local function expand(p) return vim.fn.expand(p) end

M.primary_dir         = function() return expand("~/" .. M.primary) end
M.primary_fbsource    = function() return expand("~/" .. M.primary .. "/fbsource") end
M.primary_configerator = function() return expand("~/" .. M.primary .. "/configerator") end

-- Like _current_workspace in shell: which workspace contains PWD?
function M.current()
  local cwd = vim.fn.getcwd()
  local home = expand("~")
  for _, ws in ipairs(M.workspaces) do
    local prefix = home .. "/" .. ws
    if cwd == prefix or cwd:sub(1, #prefix + 1) == prefix .. "/" then
      return ws
    end
  end
  return M.primary
end

function M.current_fbsource()  return expand("~/" .. M.current() .. "/fbsource") end
function M.suffix()
  local ws = M.current()
  if ws == M.primary then return "" end
  return "-" .. ws
end

-- Yield {name, dir, fbsource, configerator} for each workspace. Used by
-- DAP source maps to enumerate every workspace's source roots.
function M.all()
  local out = {}
  for _, ws in ipairs(M.workspaces) do
    table.insert(out, {
      name         = ws,
      dir          = expand("~/" .. ws),
      fbsource     = expand("~/" .. ws .. "/fbsource"),
      configerator = expand("~/" .. ws .. "/configerator"),
    })
  end
  return out
end

return M
```

### 5.3 Refactor each file by category

#### Category A — Inherent (DAP source maps)

`~/dotfiles/nvim/lua/plugins/dap.lua`:

```lua
local W = require("lib.workspaces")
local sourceMap = {}
for _, w in ipairs(W.all()) do
  table.insert(sourceMap, { ".", w.fbsource .. "/fbcode" })
  table.insert(sourceMap, { ".", w.fbsource })
  table.insert(sourceMap, { "/home/engshare", w.fbsource .. "/fbcode" })
end
-- Use `sourceMap` in lldb-dap config.
```

`~/dotfiles/nvim/lua/lib/fdb-dap.lua`:

```lua
local W = require("lib.workspaces")
-- Try current workspace's fbsource first; fall back to primary.
local function resolve_fbsource()
  local current = W.current_fbsource()
  if vim.fn.isdirectory(current) == 1 then return current end
  return W.primary_fbsource()
end

local DEBUGPY_DOTSLASH = resolve_fbsource() .. "/fbcode/sand/python_debugging/adapter/dotslash/debugpy_adapter"
-- ... and the buck cwd similarly
```

#### Category B — Default-fallback paths

`~/.local_init.sh`:

```bash
. "$HOME/dotfiles/workspaces.sh"

install_presto_docs_venv() {
    local venv_dir="$PRIMARY_FBSOURCE/fbcode/github/presto-trunk/presto-docs/presto-docs-venv"
    local req_file="$PRIMARY_FBSOURCE/fbcode/github/presto-trunk/presto-docs/requirements.txt"
    # ...
}

setup_rust_toolchain() {
    local toolchain_bin="$PRIMARY_FBSOURCE/xplat/rust/toolchain/current/basic/bin"
    # ...
}
```

`~/dotfiles/agent_config/skills/presto-build/presto-build`:

```bash
. "$HOME/dotfiles/workspaces.sh"

_detect_fbcode_home() {
    # walk up from PWD, falling back to primary
    local d="$PWD"
    while [[ "$d" != "/" ]]; do
        if [[ -d "$d/github/presto-trunk" && "$(basename "$d")" == "fbcode" ]]; then
            echo "$d"; return
        fi
        d="$(dirname "$d")"
    done
    echo "$PRIMARY_FBSOURCE/fbcode"
}

# Use _workspace_suffix from workspaces.sh — already does the right thing.
_CHECKOUT_SUFFIX="$(_workspace_suffix)"
```

#### Category C — Convenience shortcuts

`~/.localrc` (replace existing functions):

```bash
. "$HOME/dotfiles/workspaces.sh"

# Drop _workspace_root, _fbsource_root, _checkout_suffix — replaced by
# workspaces.sh equivalents.
unalias gfb 2>/dev/null; function gfb() { cd "$(_current_fbsource)/fbcode"; }
unalias con 2>/dev/null; function con() { cd "$(_current_configerator)"; }
unalias gf  2>/dev/null; function gf()  { cd "$(_current_fbsource)/fbcode/github/presto-facebook-trunk"; }
unalias gp  2>/dev/null; function gp()  { cd "$(_current_fbsource)/fbcode/github/presto-trunk"; }

_checkout_suffix() { _workspace_suffix; }   # keep name for back-compat with _mf_flags / _mp_flags
```

`~/dotfiles/bin/devmate_mux`:

```bash
. "$HOME/dotfiles/workspaces.sh"
cd "$PRIMARY_FBSOURCE" 2>/dev/null
exec /usr/local/bin/devmate_mux "$@"
```

`~/dotfiles/agent_config/skills/presto-gateway-deploy/presto-gateway-deploy-finish`:

```bash
. "$HOME/dotfiles/workspaces.sh"
TW_CONFIG="$PRIMARY_FBSOURCE/fbcode/tupperware/config/presto/gateway/gateway-test.tw"
```

`~/dotfiles/nvim/lua/lib/presto-maven.lua`:

```lua
local W = require("lib.workspaces")
local DEV_HOME = vim.uv.fs_realpath(W.current_fbsource() .. "/fbcode/github")
                 or (W.current_fbsource() .. "/fbcode/github")
```

#### Category D — Documentation

Mostly mechanical text edits to reference `~/<workspace>/fbsource` (or `<workspace>` as a placeholder) instead of literal paths. Mention `workspaces.sh` as the source of truth where relevant. Already done as part of the migration; will need a second pass to update the wording from "main/scratch" to "configurable workspace".

### 5.4 startup-windows table generation

`startup-windows` currently hardcodes 4 entries per devserver (Ghostty + VS Code for each of main + scratch). Refactor to generate the table from a workspace list:

```bash
# Top of script
WORKSPACES=(main scratch)   # or read from a Mac-side config file

DEVSERVERS=(
  "FTW:devvm36111.ftw0.facebook.com:3"   # ws_start = 3 (main on 3, scratch on 4)
  "CCO:devvm20365.cco0.facebook.com:6"
)

for entry in "${DEVSERVERS[@]}"; do
  IFS=':' read -r prefix host ws_start <<< "$entry"
  for i in "${!WORKSPACES[@]}"; do
    name="${WORKSPACES[$i]}"
    ws=$((ws_start + i))
    WORKSPACES_TABLE+=(
      "$ws|ghostty|$prefix: $name|$prefix: $name\$|~|nvs $prefix-$name"
      "$ws|vscode|$prefix: vscode-$name|$name.*$host|vscode-remote://fb-remote+$host/home/mkarrmann/$name.code-workspace"
      "$ws|chrome|Chrome: ws$ws"
    )
  done
done
```

Then the existing window-creation loop iterates over `WORKSPACES_TABLE` as before. Adding a 3rd workspace requires only adding to `WORKSPACES`.

### 5.5 Discovery and bootstrapping order

Source order matters:

```
~/.bashrc / ~/.zshrc
  → ~/.localrc
    → ~/dotfiles/workspaces.sh        # defines envs and helper functions
    → existing localrc body (which now uses _current_fbsource etc.)
```

`~/.local_init.sh` and any standalone tooling script must source `workspaces.sh` themselves at the top — they're not invoked from an interactive shell.

For Lua: `lib.workspaces` reads `vim.env.PRIMARY_WORKSPACE` set by the parent shell. If nvim is launched outside a shell that sourced `workspaces.sh` (rare — nvs's `setsid` shouldn't break this since env is inherited), it falls back to the literal default `"checkout1"`.

---

## 6. Migration plan

Estimated effort: ~2 hours including testing.

1. **Create `~/dotfiles/workspaces.sh`** with the contents from §5.1. Source from `~/.localrc` first, then test that the new `_current_*` and `_workspace_suffix` functions work in interactive shell. (15 min)
2. **Create `~/dotfiles/nvim/lua/lib/workspaces.lua`** with the contents from §5.2. Test from `:lua print(require("lib.workspaces").current_fbsource())`. (15 min)
3. **Refactor each Category C file** to use the new helpers (§5.3). After each, smoke-test: `gfb`, `con`, `cd ~/checkout1/fbsource && presto-build --help` (verifies suffix derivation). (30 min)
4. **Refactor Category B files** (`.local_init.sh`, `presto-build`'s fallback). Don't run `.local_init.sh` blindly — it has side effects; just verify the paths it would use. (15 min)
5. **Refactor Category A** (DAP files). Test by setting a breakpoint in a buck-built C++ binary and confirming the source resolves in nvim. (20 min)
6. **Refactor `startup-windows`** to generate from `WORKSPACES` (§5.4). Test by running it on the Mac and confirming no functional change. (20 min)
7. **Update `migrate-checkouts`** to read defaults from `workspaces.sh` instead of hardcoding `checkout1`/`checkout2`. Optional. (15 min)
8. **Doc pass** — update SKILL.md files, `~/.claude/projects/presto.md`, and the workspace-layout-refactor doc itself to reference `workspaces.sh`. (15 min)
9. **Cross-devserver smoke test** — `dotsync2` to FTW, source `.localrc`, run `gfb && pwd`, `presto-build --help`. (10 min)
10. **Add a memory entry** at `~/.claude/projects/-data-users-mkarrmann-fbsource/memory/` noting the new central workspace config so future Claude sessions don't re-introduce hardcoded paths. (5 min)

---

## 7. Alternatives considered

### 7.1 Status quo (do nothing)

Accept that workspace renames are an N-file diff. Rejected because the migration we just did makes future renames likely (e.g. adding `oncall`, `experiment-X`, retiring `checkout2` for something more specific).

### 7.2 Symlink farm

Make every reference go through symlinks like `~/.config/dev-checkout/primary/fbsource -> ~/checkout1/fbsource`. Rejected because:
- We just removed exactly this pattern (`~/fbsource -> ~/local/fbsource`) because it's a footgun with Eden — tools that `realpath` see the canonical path; tools that don't, see the symlink. Round trips through symlinks confuse Buck, watchman, sl.

### 7.3 Per-tool config files

Each tool (`presto-build`, `nvs`, etc.) gets its own config file with workspace info. Rejected: same N-file problem, just under different filenames.

### 7.4 Config in JSON/YAML, generators

`~/dotfiles/workspaces.json` + a generator that produces shell + Lua. Rejected as over-engineered for two consumers (shell + Lua). Two parallel files (`workspaces.sh` + `workspaces.lua`) with the same data is fine and avoids a build step.

### 7.5 Auto-detect workspaces from filesystem

Walk `~/*` looking for dirs containing both `fbsource` and `configerator`. Rejected: too magic, slow on cold cache, hard to disambiguate intent (is `~/old-fbsource-experiment` a workspace?).

---

## 8. Open questions

1. **Is `PRIMARY_WORKSPACE` per-machine or global?** Probably global (same answer everywhere via dotsync). But conceivable that one devserver's "primary" is different. Defer until needed.

2. **Should `nvs` itself read `workspaces.sh`?** Currently `nvs <name> <workdir>` is fully generic — the workdir is passed in by `nvs-tunnels`. Refactor isn't necessary; `nvs-tunnels` already drives the choice via the Mac-side `WORKSPACES` table.

3. **Do per-workspace `.code-workspace` files belong in `workspaces.sh`?** Probably not — colors and folder lists are workspace-specific data, not metadata about which workspaces exist. Keep them as separate files.

4. **What about the `gfb` / `con` "current vs primary" semantics?** Today `gfb` cd's to the *current* fbsource (the one PWD is inside). Sometimes I want "go to the primary" instead. Could add `gfb-main` / `gfb-scratch` variants. Defer.

5. **Should `migrate-checkouts` enforce that the workspaces in `workspaces.sh` exist after migration?** I.e. for each workspace in the list, ensure there's a clone. Currently the script defaults to two and accepts overrides. Could iterate over `${WORKSPACES[@]}` instead. Worth doing if I add a third workspace.

---

## 9. References

- **EdenFS docs:** `/data/users/mkarrmann/checkout1/fbsource/fbcode/eden/fs/cli/main.py` (`remove_checkout_impl`)
- **fbclone:** `/data/users/mkarrmann/checkout1/fbsource/fbcode/eden/scm/fb/fbclone/src/main.rs`
- **Migration script:** `~/dotfiles/bin/migrate-checkouts`
- **Pre-migration audit:** the conversation that produced this doc (Claude session 2026-05-07)
- **AeroSpace:** https://github.com/nikitabobko/AeroSpace
- **Internal: New to EdenFS:** https://www.internalfb.com/wiki/Source-Control-Users/Get_Started/NewToEden/

## 10. Appendix: Why the migration was done in the first place

Pre-migration, the layout was:
```
~/fbsource{,2,3}      ~/configerator{,2,3}
```

This meant:
- VS Code workspaces were single-folder: `~/fbsource2.code-workspace` opened only fbsource. Configerator had to be opened separately or not at all in that VS Code window.
- Workspace identity was implicit in the directory suffix — easy to mix up which `fbsource2` belonged with which `configerator2`.
- The `~/fbsource -> ~/local/fbsource` symlink chain was a known Eden footgun (different tools see different paths after `realpath`).

Post-migration:
```
~/checkout1/{fbsource,configerator}      ~/checkout2/{fbsource,configerator}
```

A single VS Code multi-root workspace per top-level dir bundles fbsource + configerator. The naming makes the intent ("checkout1" vs "checkout2") explicit. The symlink layer is gone.

The cost of the migration was: tearing down 7 Eden mounts and re-cloning 4, ~30 minutes per devserver, plus updating the 12 personal-config files inventoried in §4. **This refactor is the second-order fix to make the next migration cost essentially zero.**
