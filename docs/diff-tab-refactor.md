# Design doc: extract `diff-tab.lua` and add CodeCompanion diff support

**Status:** Implemented 2026-05-16
**Author:** mkarrmann (with Claude)
**Date:** 2026-05-16
**Audience:** Future me, or any agent picking this up cold.

---

## TL;DR

`lib/claude-diff.lua` (~590 lines) gives Claude Code sessions a dedicated tab with a side-by-side diff of every file the agent has touched, with `]f`/`[f` file navigation and a turn/session mode toggle. I want the same UX for CodeCompanion. Naive port = duplicate ~70% of the file, which will drift the moment either side sees a bug fix.

**Proposal**: extract the generic per-session, per-file diff-tab machinery into a new `lib/diff-tab.lua` engine. Then `claude-diff.lua` and a new `codecompanion-diff.lua` become thin wrappers (~150 lines each) that handle their own snapshot capture and lifecycle plumbing, but share all the UI/state/keymap code.

The two providers differ only at the **edges**: where snapshots come from (Claude wrapper hands us paths to disk snapshot files it produced; CodeCompanion wrapper monkey-patches the actual write paths — ACP `Connection:handle_fs_write_file_request` and HTTP `diff.review` — and reads disk before each write) and how lifecycle events are surfaced (Claude wrapper calls APIs explicitly; CodeCompanion fires `User` autocmds we listen to). Everything in between — tab management, scratch buffers, mode toggle, file navigation, winbar, keymaps — is identical and lifts out cleanly.

---

## 1. Background

### 1.1 What `lib/claude-diff.lua` does today

Claude Code (the terminal-embedded agent, separate from CodeCompanion) edits files in the background. Without `claude-diff`, the only way to see what changed is to look at git status afterwards — by which point all the agent's intermediate edits have been collapsed.

`claude-diff` solves this by maintaining a parallel view:

- **Per-session state**, keyed by Claude Code session ID (string passed in from the wrapper).
- **Per-file state** (one entry per file the agent has touched in this session) containing three scratch buffers:
  - `after_buf` — current disk content (the "after" we're diffing toward).
  - `turn_buf` — snapshot of the file's content at the start of the current turn (user message → assistant response cycle).
  - `session_buf` — snapshot at session start (before the first edit Claude made in this session).
- **Two display modes**:
  - `turn` (default) — left pane = `after_buf`, right pane = `turn_buf`. Shows just this turn's net changes.
  - `session` — left pane = `after_buf`, right pane = `session_buf`. Shows everything Claude has done in this session.
- **Dedicated tab**, opened on demand via `M.toggle()`. Vertical split, two windows held in `state.left_win` / `state.right_win`. Both windows have `diffthis` applied and `scrollbind = true`.
- **In-tab keymaps**, set buffer-locally on every buffer the diff tab might display:
  - `]f`/`[f` — next/previous file (cycles)
  - `]F`/`[F` — last/first file
  - `gf` — `vim.ui.select` picker over the file list
  - `gq` — close the diff tab (returns to `work_tab`)
  - `gm` — toggle between turn and session mode
- **Lifecycle**:
  - `M.file_edited(path, turn_snap_path, session_snap_path, session_id)` — wrapper calls this every time Claude writes a file. claude-diff reads the snapshot files into scratch bufs, refreshes the diff tab if it's open and on this file.
  - `M.new_turn(session_id)` — wrapper calls this at each new user prompt. claude-diff drops turn snapshots so the next edit re-snaps fresh turn-before content.
  - `M.cleanup(session_id)` — wrapper calls this when the Claude session ends. claude-diff disposes all bufs/state/augroup, closes the diff tab if open.
  - `M.debug()` — dumps session state to a notify.

### 1.2 How Claude Code feeds it

The Claude Code wrapper (separate codebase) hooks Claude's file-write events. For each write:

1. Capture the **pre-write content** to a temp file (`turn_snap_path`). If this is the first time this file has been touched in this session, also write it to `session_snap_path`.
2. Let Claude perform the write.
3. Call `claude-diff.file_edited(path, turn_snap_path, session_snap_path, session_id)`.

The wrapper owns snapshot file management; `claude-diff` just reads them on demand. The result: `claude-diff.lua` has no concept of "when does a snapshot get taken" — it just receives paths.

### 1.3 Why I want this for CodeCompanion too

CodeCompanion's stock diff UI is a single-file floating window with a merged view (additions and deletions interleaved with extmarks). For small targeted edits it's fine. For longer agent runs that touch many files, it has two structural shortcomings:

1. **No file-level navigation.** Each `show_diff` is independent — accept this one, move on, the diff disappears. To revisit you'd have to scroll the chat for the tool output.
2. **No turn / session retrospective.** Once you've accepted, you can no longer see "what did this turn change" or "what has the agent done all session?" without leaving the editor and running `git diff`.

The `claude-diff` model solves both. The interesting realization is that CodeCompanion gives us **better hook points** than the Claude wrapper does — we don't need an external snapshot-file producer.

### 1.4 CodeCompanion signals available

**False start (kept here as a warning).** My first instinct was that `helpers.show_diff(args)` was a single chokepoint that every agent edit funnels through — capture `args.from_lines` + `args.chat_bufnr` and you'd have all writes. **This is wrong.** `show_diff` is the *render* function for the floating accept/reject UI, not the *write* function. It's called conditionally:

| Path | When `show_diff` is **skipped** |
|------|------|
| Small diff under `display.diff.threshold_for_chat` | Always — diff text is inlined into the chat buffer (`approval_prompt.present_diff` line 132) |
| Chat buffer not focused at request time | Only fires if the user later picks "View" (line 137) |
| HTTP `insert_edit_into_file` with `opts.approved == true` (auto-approve list) | Always — `opts.apply()` runs directly (`insert_edit_into_file/diff.lua:134`) |
| `display.diff.enabled == false` or `require_confirmation_after == false` | Always |
| Inline edits with buffer in always-approved list | Always (`inline/init.lua:794-799`) |

A `show_diff`-based wrapper would systematically miss the most common workflows (small edits, unfocused chat, auto-approved tools) — exactly the cases where a session-level diff view is most useful. Also `args.bufnr` is not the edited file's buffer (it's the diff display buffer, often a freshly created scratch), and `args.keymaps.on_accept` is not set on the ACP permission path at all (`request_permission.lua` only sets `on_reject`), so any "refresh after accept" hook is dead code for the most important provider.

**Actual chokepoints:**

- **ACP writes — `Connection:handle_fs_write_file_request` (`acp/init.lua:815`).** Every `fs/write_text_file` RPC from claude-agent-acp / codex-acp / dvsc-core-acp flows through this one handler, regardless of approval mode. It has `params.sessionId`, `params.path`, `params.content`, and we can `read_file_lines(path)` *before* calling the original to capture pre-write content. This catches 100% of ACP writes including `bypassPermissions` mode.
- **HTTP `insert_edit_into_file` — `diff.review(opts)` (`insert_edit_into_file/diff.lua:131`).** Called unconditionally before the three-branch `present_diff` dispatch. It has `opts.from_lines`, `opts.to_lines`, `opts.chat_bufnr`, and `opts.title` (display name; we resolve via the chat's tracked filepath). Patching here captures all HTTP edits regardless of which approval branch fires.
- **Inline edits — `inline/init.lua:806`.** Has its own `show_diff` call site but no chat session, so out of scope for v1.

**Lifecycle / lookup signals (these the doc had right):**

- **`User CodeCompanionRequestStarted`** — fires at every user prompt → turn boundary. `args.data.bufnr` is the chat bufnr.
- **`User CodeCompanionChatClosed`** — fires on chat close → session end.
- **`User CodeCompanionChatCleared`** — fires on `chat:clear()`, including from `<leader>aZ` restart. Treat like session end + new session.
- **`vim.t.codecompanion_chat_bufnr`** — already stamped per-tab by my existing autocmd in `plugins/codecompanion.lua`. Equivalent to claude-diff's `vim.t.claude_session_id` lookup.
- **Connection → chat lookup**: each `Chat` holds its `acp_connection`. To map a `Connection` instance back to `chat_bufnr`, walk `_G.codecompanion.chats` (or the equivalent registry) and match. Cache the inverse for the connection's lifetime.

---

## 2. Problem

### 2.1 Why duplicate is bad here

`claude-diff.lua` and a hypothetical `codecompanion-diff.lua` would share, by my count:

- All tab management (`setup_diff_tab`, `close_diff_tab`, the `TabClosed` autocmd to clean up state).
- All pair rendering (`show_pair`, `update_winbar`, the `diffthis` + `scrollbind` setup).
- All file navigation keymaps (`]f`/`[f`/`]F`/`[F`/`gf`/`gq`/`gm`).
- Scratch buffer creation and cleanup (`make_scratch_buf`, `update_scratch_buf`, `delete_buf`).
- State shape: `{ mode, diff_tab, work_tab, left_win, right_win, index, files, turn_files, file_data }`.
- Mode toggle behavior.

That's the entire middle of the file — only the public API (`file_edited` / `file_edit`, `new_turn`, `cleanup`) and the read-from-disk vs read-from-memory snapshot capture differ.

Active development risk: both providers are likely to see fixes (`scrollbind` interactions, winbar formatting, keymap additions, focus restoration edge cases). Each fix would need to be applied twice. After three to four updates, the two files will be subtly out of sync and the bug-versus-feature inventory will diverge.

### 2.2 What's genuinely shared vs. wrapper-specific

| Concern | Shared (move to `diff-tab.lua`) | Wrapper-specific (stays per-provider) |
|---|---|---|
| Tab lifecycle (open/close/autocmds) | ✓ | |
| Pair rendering & winbar | ✓ | |
| Per-file scratch buffers | ✓ | |
| File-list state & navigation keymaps | ✓ | |
| Mode toggle (turn ↔ session) | ✓ | |
| **Where snapshots come from** | | ✓ (Claude: disk paths; CC: in-memory) |
| **What is a "session"** | | ✓ (Claude: wrapper session_id; CC: chat bufnr) |
| **Lifecycle event source** | | ✓ (Claude: explicit API calls; CC: User autocmds) |
| **Tab → session lookup** | | ✓ (`vim.t.claude_session_id` vs `vim.t.codecompanion_chat_bufnr`) |

Roughly 70% of `claude-diff.lua` lifts cleanly.

---

## 3. Goals & Non-Goals

### Goals

- **Shared engine**: one `lib/diff-tab.lua` exposing per-session diff-tab management, instantiable per provider with its own state space.
- **Thin wrappers**: `claude-diff.lua` and `codecompanion-diff.lua` reduce to snapshot capture + lifecycle plumbing + manager instantiation. Target ~150 lines each.
- **No behavior change for Claude Code users**: pure refactor on the Claude side. Same public API, same keymaps, same UX.
- **Drop-in CodeCompanion support**: works for ACP permission flows (claude_code, dvsc, codex, devmate adapters) and HTTP `insert_edit_into_file` tool out of the box.
- **No new external dependencies.**
- **Side-by-side coexistence**: both providers can be active simultaneously without state collision.

### Non-Goals

- **Not changing the diff renderer.** We still use Vim's built-in `diffthis`. No third-party diff library, no rendering inside floating windows.
- **Not replacing CodeCompanion's stock floating diff UI.** That stays as the in-flight permission UI; our split tab is an *additional* view you can open on demand.
- **Not capturing inline edits (`:CodeCompanion` outside chat).** No chat session = no session key. The wrapper falls through cleanly; just no diff-tab entry created.
- **Not persisting state across `nvim` restarts.** Sessions are in-memory only, just like today.
- **Not supporting cross-tab diff viewing.** Diff is per-session per-tab; if the chat moves tabs we don't follow.
- **Not changing `vim.t` key names** for existing claude-diff users — keep `vim.t.claude_session_id` and `vim.t.claude_diff_session`.

---

## 4. Current state inventory

`lib/claude-diff.lua` (589 lines), annotated by what moves where:

| Symbol | Lines | Move target | Notes |
|---|---|---|---|
| `_counter` | 3 | `diff-tab.lua` | Used for buffer name uniqueness. |
| `_sessions` | 4 | `diff-tab.lua` (per-manager) | Becomes `self._sessions` on the manager instance. |
| `KEYMAPS` constant | 6 | `diff-tab.lua` | List of keys to unregister on close. |
| `read_file_lines` | 10–21 | **`claude-diff.lua`** (wrapper-specific) | Only Claude needs disk reads; CC has in-memory lines. |
| `make_scratch_buf` | 23–36 | `diff-tab.lua` | Take a `label` arg so wrappers can prefix names (e.g. `"after"`, `"turn-before"`, `"session-before"`). |
| `update_scratch_buf` | 38–42 | `diff-tab.lua` | |
| `delete_buf` | 44–48 | `diff-tab.lua` | |
| `get_state` | 50–68 | `diff-tab.lua` (manager method) | |
| `get_file_list` | 70–72 | `diff-tab.lua` | |
| `get_before_buf` | 74–80 | `diff-tab.lua` | |
| `update_winbar` | 84–129 | `diff-tab.lua` | Winbar text format hardcoded today; expose as config option (see §5.2). |
| `show_pair` | 133–204 | `diff-tab.lua` | |
| `set_keymaps` (closure over `session_id`) | 210–295 | `diff-tab.lua` | Refactored to take `(buf, manager, session_id)`. |
| `remove_keymaps_from_buf` | 297–304 | `diff-tab.lua` | |
| `close_diff_tab` | 308–347 | `diff-tab.lua` | |
| `setup_diff_tab` | 349–407 | `diff-tab.lua` | Tab-local var key (`vim.t.claude_diff_session`) becomes `vim.t[manager.tab_var]`. |
| `M.toggle` | 411–433 | `diff-tab.lua` (manager method) | Reads `vim.t[manager.tab_var]` instead of hardcoded keys. |
| `M.file_edited` | 435–505 | **`claude-diff.lua`** (wrapper-specific) | Reads disk snapshots, calls `manager:add_file(...)`. |
| `M.new_turn` | 507–544 | `diff-tab.lua` (manager method) | Generic enough — drops turn snapshots, refreshes view. |
| `M.cleanup` | 546–571 | `diff-tab.lua` (manager method) | |
| `M.debug` | 573–586 | `diff-tab.lua` (manager method) | Wrappers can extend with provider-specific info. |

Wrapper-specific bits that stay in `claude-diff.lua`:
- `read_file_lines` (Claude wrapper writes snapshot files to disk; CC doesn't)
- `M.file_edited` glue that reads disk and calls `manager:add_file`
- Tab var key name (`claude_session_id` / `claude_diff_session`)
- Augroup naming prefix (`claude_diff_` + session_id)

New wrapper bits unique to `codecompanion-diff.lua`:
- Monkey-patch of `Connection:handle_fs_write_file_request` (`acp/init.lua`) for ACP write capture
- Monkey-patch of `diff.review` (`insert_edit_into_file/diff.lua`) for HTTP write capture
- `Connection → chat_bufnr` lookup (walk `_G.codecompanion.chats` and match `acp_connection`)
- `User` autocmd registrations for `CodeCompanionRequestStarted` / `ChatClosed` / `ChatCleared`
- Tab var key (`codecompanion_chat_bufnr`)

---

## 5. Design

### 5.1 Module structure

```
nvim/lua/lib/
├── diff-tab.lua              [NEW]    generic diff-tab engine
├── claude-diff.lua           [REFACTORED]  Claude Code wrapper (was: full impl)
└── codecompanion-diff.lua    [NEW]    CodeCompanion wrapper
```

`diff-opts.lua` (the existing tiny helper for `scrollbind`/`foldenable`/winbar) is unrelated and stays put.

### 5.2 `diff-tab.lua` API

The engine exposes a `new(opts)` constructor returning a manager. Each manager has its own state space, augroup namespace, and tab-var key, so multiple providers can coexist without interference.

```lua
---@class DiffTab.ManagerOpts
---@field name string              -- used in augroup names (e.g. "claude_diff", "codecompanion_diff")
---@field tab_var string           -- vim.t.<this> identifies session in current tab
---@field diff_tab_var string?     -- optional separate var stamped inside the diff tab itself
                                   --   (defaults to "<name>_session")
---@field winbar_format? table     -- optional override of winbar text segments

---@class DiffTab.AddFileOpts
---@field after_lines string[]               -- current disk content
---@field turn_before_lines? string[]        -- snapshot for turn mode (optional on subsequent
                                             --   edits to same file in same turn)
---@field session_before_lines? string[]     -- snapshot for session mode (optional on subsequent
                                             --   edits to same file in same session)

local diff_tab = require("lib.diff-tab")

-- Construct a manager
local mgr = diff_tab.new({
  name = "codecompanion_diff",
  tab_var = "codecompanion_chat_bufnr",
})

-- Public API on the manager
mgr:add_file(session_id, file_path, opts)        -- DiffTab.AddFileOpts
mgr:refresh_after(session_id, file_path, lines)  -- update after_buf only (file changed on disk)
mgr:new_turn(session_id)                         -- drop turn snapshots
mgr:cleanup(session_id)                          -- dispose session
mgr:toggle()                                     -- open/close diff tab for current tab's session
mgr:debug()                                      -- inspect state
```

Internally each manager carries:
- `self._sessions` — `{ [session_id] = state }` table (state shape identical to today's).
- `self._counter` — buffer-name uniqueness counter (per-manager so claude and CC bufnames don't collide).
- `self.opts` — `name`, `tab_var`, `diff_tab_var`.

### 5.3 `claude-diff.lua` after refactor

```lua
local mgr = require("lib.diff-tab").new({
  name = "claude_diff",
  tab_var = "claude_session_id",
  diff_tab_var = "claude_diff_session",
})

local M = {}

-- Wrapper-specific: read snapshot files Claude wrapper has produced on disk
local function read_file_lines(path)
  if not path or path == "" then return nil end
  local f = io.open(path, "r")
  if not f then return nil end
  local content = f:read("*a")
  f:close()
  return vim.split(content, "\n", { plain = true })
end

function M.file_edited(file_path, turn_snap_path, session_snap_path, session_id)
  if not session_id or session_id == "" then return true end
  file_path = vim.fn.fnamemodify(file_path, ":p")
  vim.schedule(function()
    pcall(function()
      mgr:add_file(session_id, file_path, {
        after_lines = read_file_lines(file_path) or {},
        turn_before_lines = read_file_lines(turn_snap_path),
        session_before_lines = read_file_lines(session_snap_path),
      })
    end)
  end)
  return true
end

function M.new_turn(session_id) mgr:new_turn(session_id); return true end
function M.cleanup(session_id) mgr:cleanup(session_id); return true end
function M.toggle() mgr:toggle() end
function M.debug() mgr:debug() end

return M
```

Roughly ~50 lines. Public API and behavior identical to today.

### 5.4 `codecompanion-diff.lua` (new)

The wrapper hooks the **write paths**, not the render path. Two monkey-patches: one on ACP `Connection:handle_fs_write_file_request` (catches all ACP writes regardless of approval mode), one on HTTP `diff.review` (catches all `insert_edit_into_file` invocations regardless of which approval branch fires). The wrapper reads pre-write content from disk and passes both `before`/`after` line arrays to the engine. The engine's own first-seen tracking decides whether incoming `before_lines` becomes the turn-/session-anchor or is dropped.

```lua
local mgr = require("lib.diff-tab").new({
  name = "codecompanion_diff",
  tab_var = "codecompanion_chat_bufnr",
})

local M = {}

local function read_file_lines(path)
  local f = io.open(path, "r")
  if not f then return nil end
  local content = f:read("*a")
  f:close()
  return vim.split(content, "\n", { plain = true })
end

-- Find the chat_bufnr that owns a given Connection instance.
-- Cached per-connection for the connection's lifetime.
local _conn_to_chat = setmetatable({}, { __mode = "k" })  -- weak keys

local function chat_bufnr_for_connection(conn)
  local cached = _conn_to_chat[conn]
  if cached and vim.api.nvim_buf_is_valid(cached) then return cached end
  local ok, codecompanion = pcall(require, "codecompanion")
  if not ok or not codecompanion.chats then return nil end
  for _, chat in pairs(codecompanion.chats or {}) do
    if chat.acp_connection == conn and chat.bufnr then
      _conn_to_chat[conn] = chat.bufnr
      return chat.bufnr
    end
  end
  return nil
end

-- Common entry: a write happened (or is about to happen) for `path`,
-- attributed to chat `chat_bufnr`. `before_lines` is pre-write content,
-- `after_lines` is post-write content.
function M.record_write(chat_bufnr, path, before_lines, after_lines)
  if not chat_bufnr or not path then return end
  path = vim.fn.fnamemodify(path, ":p")
  mgr:add_file(chat_bufnr, path, {
    after_lines = after_lines or before_lines or {},
    -- Engine deduplicates: first-seen-this-turn becomes turn anchor,
    -- first-seen-this-session becomes session anchor; later writes
    -- only refresh after_lines.
    turn_before_lines = before_lines,
    session_before_lines = before_lines,
  })
end

function M.cleanup(chat_bufnr)
  if not chat_bufnr then return end
  mgr:cleanup(chat_bufnr)
end

function M.new_turn(chat_bufnr)
  if not chat_bufnr then return end
  mgr:new_turn(chat_bufnr)
end

function M.toggle() mgr:toggle() end
function M.debug() mgr:debug() end

function M.setup()
  ---- ACP write capture --------------------------------------------------
  -- Patch the RPC handler. Pre-read disk for `before`, let the original
  -- write happen, then record with `after = params.content`. Covers
  -- bypassPermissions and any other path that skips the in-flight diff UI.
  local Connection = require("codecompanion.acp.init")
  local orig_fs_write = Connection.handle_fs_write_file_request
  function Connection:handle_fs_write_file_request(id, params)
    local chat_bufnr, before_lines
    if type(params) == "table" and type(params.path) == "string" then
      chat_bufnr = chat_bufnr_for_connection(self)
      if chat_bufnr then
        before_lines = read_file_lines(params.path) or {}
      end
    end
    local result = orig_fs_write(self, id, params)
    if chat_bufnr and type(params.content) == "string" then
      local after_lines = vim.split(params.content, "\n", { plain = true })
      vim.schedule(function()
        pcall(M.record_write, chat_bufnr, params.path, before_lines, after_lines)
      end)
    end
    return result
  end

  ---- HTTP insert_edit_into_file capture ---------------------------------
  -- Patch diff.review (called unconditionally before any approval branching).
  -- opts has from_lines, to_lines, chat_bufnr, title (display name).
  -- We need the absolute filepath; the diff module doesn't carry it directly,
  -- but the tool's args.filepath is available via the chat's most-recent
  -- tool call. Best path: have the tool init.lua pass `filepath` explicitly
  -- through opts (one-line upstream tweak), or extract via title resolution.
  local diff_review = require("codecompanion.interactions.chat.tools.builtin.insert_edit_into_file.diff")
  local orig_review = diff_review.review
  function diff_review.review(opts)
    local chat_bufnr = opts.chat_bufnr
    local path = opts.filepath  -- ideally upstreamed; fall back to title resolution
      or (opts.title and vim.fn.fnamemodify(opts.title, ":p"))
    if chat_bufnr and path and opts.from_lines then
      vim.schedule(function()
        pcall(M.record_write, chat_bufnr, path, opts.from_lines, opts.to_lines)
      end)
    end
    return orig_review(opts)
  end

  ---- Lifecycle ---------------------------------------------------------
  local group = vim.api.nvim_create_augroup("codecompanion_diff_lifecycle", { clear = true })

  vim.api.nvim_create_autocmd("User", {
    pattern = "CodeCompanionRequestStarted",
    group = group,
    callback = function(args)
      local bufnr = args.data and args.data.bufnr
      if bufnr then M.new_turn(bufnr) end
    end,
  })

  vim.api.nvim_create_autocmd("User", {
    pattern = { "CodeCompanionChatClosed", "CodeCompanionChatCleared" },
    group = group,
    callback = function(args)
      local bufnr = args.data and args.data.bufnr
      if bufnr then M.cleanup(bufnr) end
    end,
  })
end

return M
```

Roughly ~120 lines. Public API parallel to claude-diff (`toggle`, `cleanup`, `debug`, `new_turn`) plus the single internal `record_write` entry that both monkey-patches funnel through.

**Why this shape:**

- **`record_write` is the single internal entry point.** Both write paths (ACP and HTTP) reduce to "we have a chat_bufnr, a path, before_lines, after_lines — record it." All snapshot-first-seen logic lives inside `diff-tab.lua`'s `add_file`, which already deduplicates `turn_before_lines` / `session_before_lines` to "only set if not already present in this session/turn." No `_seen` tracking in the wrapper.
- **Connection→chat lookup is cached weakly** so a closed chat's stale entry GCs naturally.
- **The HTTP path needs `opts.filepath` to be available.** The cleanest fix is a one-line upstream change to `insert_edit_into_file/init.lua:164` to pass `filepath = source.process_opts.path or source.display_name` in the `review()` call. Failing that, fall back to resolving `opts.title` (the display name, usually a relative path) against cwd; brittle but workable.
- **`ChatCleared` lifecycle** is added so `<leader>aZ` (chat restart) drops the diff session cleanly. The new chat that replaces it starts fresh.
- **No `accepted()` hook needed.** For ACP, `record_write` fires post-write inside the RPC handler — `after_lines` is already correct. For HTTP, `to_lines` from `opts` is the post-write content (we record it before `apply()` runs, but `apply()` is what writes it — so the recorded `after_lines` matches what will be on disk).

### 5.5 Snapshot capture asymmetry — handled at the wrapper boundary

The key insight: `diff-tab.lua` doesn't care **how** snapshots are obtained. `add_file(session_id, path, { after_lines, turn_before_lines?, session_before_lines? })` just takes line arrays. Internal dedup logic decides whether `turn_before_lines` / `session_before_lines` on a subsequent call for the same path is honored (first-seen-this-turn / first-seen-this-session) or ignored.

- **Claude wrapper**: reads snapshot files written by the external wrapper script and passes line arrays.
- **CodeCompanion wrapper**: reads disk *before* the write (ACP path via `Connection:handle_fs_write_file_request`; HTTP path via `diff.review`'s `opts.from_lines`) and passes line arrays.

Why disk reads on the CC side, when `from_lines` is available in-memory for HTTP and `params.content` for ACP? Three reasons:

1. **Authority**: For the ACP `bypassPermissions` case (and any auto-approved write) there is no `from_lines` because no diff was computed. We have to read disk regardless. Making disk reads the rule, not the exception, simplifies the wrapper.
2. **Truthfulness**: `tool_call.content[1].oldText` in the ACP permission flow is the *agent's claim* of what was there. The agent could be wrong (stale file read, race with user edit, hallucination). Disk read is ground truth.
3. **Symmetry with Claude wrapper**: both wrappers now have the same model — snapshot is "what's on disk right before the write." This makes any future bug fix to snapshot semantics apply identically.

Either provider can also call `refresh_after(session_id, path, new_after_lines)` independently when the file changes via a non-agent path (e.g., user manual edit). Not wired in v1 — see §8.

### 5.6 Coexistence and conflict surfaces

Both managers can be active simultaneously (the user could be using both Claude Code and CodeCompanion in adjacent tabs). Safety properties:

- **Separate state spaces** — each manager has its own `_sessions` table; no cross-talk.
- **Separate augroups** — `claude_diff_<session_id>` vs `codecompanion_diff_<session_id>`.
- **Separate tab-var keys** — `vim.t.claude_session_id` vs `vim.t.codecompanion_chat_bufnr`. `toggle()` reads the right one.
- **Separate scratch buffer names** — `after://path#N`, `turn-before://path#N`, `session-before://path#N` — but namespaced with a per-manager counter, so even if both providers diff the same file, their bufnames don't collide.
- **Separate diff tabs** — each manager creates its own tabpage. Toggling Claude's diff doesn't affect CC's.

Each provider's in-tab keymaps (`]f` etc.) are set buffer-locally on the manager's own buffers, so they don't leak.

### 5.7 What this gives the CodeCompanion user

After this lands, opening a chat in tab N:

1. As the agent edits files (via `insert_edit_into_file` or ACP `fs/write_text_file`), CodeCompanion's normal floating diff appears for accept/reject.
2. Accepting an edit doesn't lose the diff — it's captured into `codecompanion-diff`'s session state.
3. At any time, `<leader>aw` (proposed) opens a new tab with the side-by-side diff of the current chat's most-recently-touched file.
4. `]f`/`[f` to walk through every file the agent has touched.
5. `gm` to toggle between "what changed this turn" and "what has the agent done all session".
6. `gq` to close the diff tab; `<leader>aw` again to reopen with state intact.
7. Closing the chat tears everything down.

---

## 6. Migration plan

### Phase 1: extract engine (zero behavior change)

1. Create `lib/diff-tab.lua` with the `new()` constructor + manager methods.
2. Move generic functions from `claude-diff.lua` verbatim, parameterize on `manager.opts.name` / `manager.opts.tab_var` / `manager.opts.diff_tab_var`.
3. Refactor `claude-diff.lua` to instantiate a manager and delegate. Strip the moved code.
4. Smoke test:
   - Start a Claude Code session.
   - Edit a few files across multiple turns.
   - Open diff tab via existing keymap.
   - Navigate with `]f`/`[f`/`]F`/`[F`/`gf`.
   - Toggle `gm`.
   - Close `gq`.
   - Reopen.
   - Close the Claude session, verify cleanup.

### Phase 2: add CodeCompanion wrapper

5. Create `lib/codecompanion-diff.lua` per §5.4. Both monkey-patches (ACP `Connection`, HTTP `diff.review`) in `setup()`.
6. Wire it in `plugins/codecompanion.lua`:
   - Add `require("lib.codecompanion-diff").setup()` to the `config` function.
   - Add `<leader>aw` keymap → `require("lib.codecompanion-diff").toggle()`.
7. Smoke test the high-coverage paths (the whole point of the redesign):
   - **ACP, small diff (`show_diff` skipped)**: submit a one-line edit; verify capture without ever opening the floating diff UI.
   - **ACP, large diff with chat focused**: submit a multi-file refactor; accept; verify all files in diff tab.
   - **ACP, `bypassPermissions`**: enable bypass; submit an edit; verify still captured (this was the v1 gap that motivated the redesign).
   - **HTTP `insert_edit_into_file`**: switch to a non-ACP adapter (e.g., copilot); submit an edit; verify capture.
   - **HTTP with always-approved**: pre-approve via `<leader>g1`, submit an edit, verify still captured.
   - Then the UI checks: `<leader>aw` → diff tab opens, `]f`/`[f` walks files, `gm` toggles turn/session, `gq` closes, `<leader>aw` reopens with state intact.
   - Close chat (`<leader>aQ`), verify diff tab tears down.
   - Restart chat (`<leader>aZ`), verify old diff session is cleaned up via `ChatCleared`.

### Phase 3: handle edge cases

8. Two chats in different tabs editing the same file — verify state isolation (each diff tab shows its own session's changes).
9. Multi-write tool calls — verify each write is captured separately (engine dedup handles `before_lines` correctly).
10. Test inline edits (`:CodeCompanion` not in chat) — confirmed out of scope; verify they don't crash the wrapper.

### Phase 4: documentation

11. Update `nvim/README.md` (or this doc's status to "Implemented") with the keymap and behavior.

---

## 7. Alternatives considered

### 7.1 Duplicate `claude-diff.lua` as `codecompanion-diff.lua`, no extraction

- **Pro**: fastest path to a working CC implementation; ~30 minutes of search-replace.
- **Con**: every future bug fix or UX tweak applies in two places. After three to four updates, the files diverge subtly and we can't tell whether a difference is a deliberate provider-specific choice or accidental drift.
- **Rejected**: both providers will see active development. The duplicate-and-defer-the-refactor path optimizes for the wrong horizon.

### 7.2 Make CodeCompanion call into `claude-diff.lua` directly with a fake session ID

- **Pro**: zero new files, glue-only.
- **Con**: semantic mismatch. `claude-diff.lua` reads `vim.t.claude_session_id` to find the current tab's session — overloading that for CC sessions either requires writing CC session IDs to a tab var named "claude", which is misleading, or muddling `toggle()` to read either key, which deepens the coupling.
- **Rejected**: provider names should not leak into each other's state.

### 7.3 Implement as a CodeCompanion `display.diff` provider

- **Pro**: native plugin integration, no monkey-patch.
- **Con**: CodeCompanion's `display.diff` config is just window opts + `threshold_for_chat` + `word_highlights`. There's no `provider = "..."` hook to swap the diff renderer wholesale. The plugin's diff is hardcoded to its merged-view renderer.
- **Rejected as standalone path**. Could complement: we monkey-patch for snapshot capture (which is what we need anyway) and let CC's stock floating diff continue to serve as the in-flight accept/reject UI.

### 7.4 Skip the extraction and only support CodeCompanion

- **Pro**: reduces scope by ~30%.
- **Con**: leaves a permanent ~600-line `claude-diff.lua` that's structurally identical to the CC implementation but technically unrelated. Worst of both worlds.
- **Rejected**.

---

## 8. Risks & open questions

### 8.1 ACP `Connection` internals drift

Patching `Connection:handle_fs_write_file_request` reaches into plugin internals (`acp/init.lua:815`). Upstream could rename, restructure, or split the handler. Mitigation:
- Pin behavior with a comment in the monkey-patch noting the expected method signature.
- Gate the wrap defensively — if `params.path` or `params.content` is missing, fall through to the original without capturing.
- Periodically re-check on plugin updates. If upstream lands the `prompt_builder:on_write_text_file` integration (the hook is already exposed at `acp/prompt_builder.lua:57` but unused by `ACPHandler`), pivot to that — it's the designed extension point and bypasses the monkey-patch entirely.

### 8.2 `Connection → chat_bufnr` lookup is heuristic

`chat_bufnr_for_connection` walks `_G.codecompanion.chats` (or equivalent registry) and matches `chat.acp_connection == self`. Risks:
- If the registry key/name changes upstream, lookup returns nil and we silently drop captures. Mitigation: assert at `setup()` time that the registry exists; log a one-time warning otherwise.
- If multiple chats ever share a connection (broker multiplexing?), we'd attribute writes to whichever chat we find first. Worth verifying — for the broker case, each `Chat` should still own a distinct `acp_connection` (broker is *inside* the connection, not above it).

### 8.3 HTTP path needs `opts.filepath`

`diff.review` is called from `insert_edit_into_file/init.lua:164` without an explicit `filepath`. The wrapper falls back to resolving `opts.title` (display name like `"path/to/file.lua"`) against cwd, which works for normal file edits but fails for buffer-source edits (where `display_name` is `"buffer 12"`). Two options:

- **(a) Upstream a one-line tweak**: add `filepath = source.process_opts.path or (source.process_opts.buffer and vim.api.nvim_buf_get_name(source.process_opts.buffer))` to the `diff.review` call. Trivial PR, but adds an upstream dependency.
- **(b) Cache-then-resolve**: add an autocmd on `User CodeCompanionToolStarted` that stashes `args.args.filepath` keyed by `args.bufnr` (chat bufnr). The patched `diff.review` reads from that cache. Self-contained, no upstream change. Slightly racier under concurrent tools (but tools serialize per-chat anyway).

Recommend (b) for v1.

### 8.4 `oldText` / `from_lines` vs disk drift

For ACP, we read disk pre-write inside the RPC handler — authoritative.

For HTTP via `diff.review`, `opts.from_lines` comes from `source.content`, which `make_file_source` (`init.lua:79`) reads from disk at the start of `execute_edit`. There's a small window between that read and `diff.review` being called where the user could modify the file. Acceptable — same race as Claude Code wrapper.

For ACP's `tool_call.content[1].oldText` (used only by `show_diff` for in-flight rendering, not by us): the agent's *claim* of pre-write content. We deliberately do not trust it — we read disk instead.

### 8.5 Multiple chats in flight (different tabs) — state collision?

Each chat is its own session in `_sessions`, keyed by `chat_bufnr`. ACP captures resolve `chat_bufnr` via the connection lookup. HTTP captures get `chat_bufnr` directly from `opts.chat_bufnr`. Both should isolate cleanly. Worth a manual test with two chats open in different tabs editing different files.

### 8.6 Tab var key for CC

Two options:
- (a) Reuse `vim.t.codecompanion_chat_bufnr` (already stamped by my existing autocmd). Same lifecycle, no double-bookkeeping.
- (b) Add a separate `vim.t.codecompanion_diff_session` stamped only inside the diff tab itself.

**Decision: do both** — same as `claude-diff` does today. Use `codecompanion_chat_bufnr` for "the chat session in this tab", and inside the diff tab itself stamp `codecompanion_diff_session` pointing back to the source chat bufnr. `toggle()` reads `vim.t.codecompanion_diff_session or vim.t.codecompanion_chat_bufnr` so it works both in the source tab and in the diff tab.

### 8.7 Keymap binding

`<leader>aw` is currently free in my config (checked via grep, no conflicts with `lua/config/keymaps.lua` or any plugin). Mnemonic: "workdiff" or "agent workspace". Open to bikeshedding.

### 8.8 `claude-diff.lua` rename

Consider renaming to `claude-code-diff.lua` for parallelism with `codecompanion-diff.lua`? Probably not — `claude-diff` is already used by external scripts (the wrapper calls into it), renaming would require coordinating that change too. Stick with the current name.

### 8.9 Refactor regression

Pure mechanical extraction has a low surprise budget. The risk is missing a subtle dependency — e.g. a function that closes over `_sessions` but doesn't take a session as param. Mitigation: write the engine first standalone, manually walk through each moved function looking for bare closures, then do the wrapper rewrite.

---

## 9. Out-of-scope follow-ups

Worth noting for later, not part of this design:

- **bsid-based persistence**: CodeCompanion's broker captures sessions to a sqlite DB with `broker_session_id`. We could snapshot the session-start state at that level and survive nvim restarts. Big project, separate doc.
- **Diff renderer choice**: today we use `:diffthis` which has known limitations (no inline word-level diff, slow on large files). Could swap for `mini.diff` or roll a custom renderer. Independent of this refactor.
- **Per-edit history within a turn**: claude-diff collapses all edits to a file within a turn into a single before/after pair. CodeCompanion has natural per-tool boundaries; we could surface those as sub-states. Probably not worth the UX complexity.
- **Cross-session diff comparison**: "show me what dvsc did vs what claude did to the same file". Out of scope; a different mental model.
