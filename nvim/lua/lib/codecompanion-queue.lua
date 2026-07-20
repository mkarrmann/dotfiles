-- Per-tab CodeCompanion input/queue UI.
--
-- Mirrors the per-tab invariant enforced for claudecode.nvim terminals
-- (see `claude-per-tab-terminal.lua`): each tabpage owns at most one chat,
-- and the chat's input box, status line, queued draft, and timing all
-- belong to that same tab.
--
-- Tab ownership of a chat buffer is stamped by the user-side autocmd in
-- `plugins/codecompanion.lua` as `vim.b[chat_bufnr].cc_tab_owner`. This
-- module reads that to dispatch CodeCompanion lifecycle events to the
-- right per-tab state.
--
-- Status-line rendering (segments, wrapping, the 1s tick) lives in
-- `lib.codecompanion-statusline` and is invoked from here on draft/state
-- changes.

local statusline = require("lib.codecompanion-statusline")

local M = {}

--- @class CCQueueState
--- @field bufnr number?         input draft buffer
--- @field winnr number?         input window
--- @field status_bufnr number?  status-line buffer
--- @field status_winnr number?  status-line window
--- @field qview_bufnr number?   read-only queued-message view buffer
--- @field qview_winnr number?   read-only queued-message view window (above input)
--- @field chat_bufnr number?    the chat buffer this queue feeds
--- @field queue string[]        FIFO of pending message texts, flushed one/turn
--- @field queued boolean        derived: #queue > 0 (drives statusline/highlight)
--- @field fullscreen boolean
--- @field request_start_at number?
--- @field in_flight_id any      request id of the in-flight prompt, or nil
--- @field last_finished_status string? "success" | "cancelled" | "error" | ...
--- @field hist_idx number?      index into shared history while browsing, or nil
--- @field hist_stash string?    in-progress draft saved when history browse began

--- @type table<number, CCQueueState>
local states = {}

-- Resolve the owning tab for a chat buffer. The owner is stamped on the
-- buffer at chat-open time; if the stamp is missing or the tab no longer
-- exists, returns nil.
local function tab_for_chat(chat_bufnr)
  if not chat_bufnr or not vim.api.nvim_buf_is_valid(chat_bufnr) then return nil end
  local ok, t = pcall(function() return vim.b[chat_bufnr].cc_tab_owner end)
  if not (ok and t) then return nil end
  if not vim.api.nvim_tabpage_is_valid(t) then return nil end
  return t
end

local function any_visible()
  for _, s in pairs(states) do
    if s.status_winnr and vim.api.nvim_win_is_valid(s.status_winnr) then
      return true
    end
  end
  return false
end

local function update_ui(s)
  statusline.apply_winhighlight(s)
  statusline.refresh(s)
end

local function get_draft_text(s)
  if not s.bufnr or not vim.api.nvim_buf_is_valid(s.bufnr) then
    return nil
  end
  local lines = vim.api.nvim_buf_get_lines(s.bufnr, 0, -1, false)
  local text = vim.trim(table.concat(lines, "\n"))
  return text ~= "" and text or nil
end

local function clear_draft_buf(s)
  if s.bufnr and vim.api.nvim_buf_is_valid(s.bufnr) then
    s.suppress_unqueue = true
    vim.api.nvim_buf_set_lines(s.bufnr, 0, -1, false, {})
    s.suppress_unqueue = false
  end
  s.hist_idx = nil
  s.hist_stash = nil
  update_ui(s)
end

-- ─── Queued-message view (read-only window above the input box) ────────────
--
-- A single split window pinned directly above the input box that lists the
-- pending queue in flush order (head at top, newest just above the box). It
-- exists only while the queue is non-empty: `sync_qview` opens it on the
-- 0→N transition and closes it on N→0, so an idle chat shows just the input
-- and status lines. This replaces the old floating queue-manager pane.

local function create_qview_buf()
  local buf = vim.api.nvim_create_buf(false, true)
  vim.bo[buf].buftype = "nofile"
  vim.bo[buf].bufhidden = "hide"
  vim.bo[buf].swapfile = false
  vim.bo[buf].filetype = "codecompanion_queue"
  vim.bo[buf].modifiable = false
  return buf
end

-- Paint the queue into the view buffer. Each message is prefixed with "» " on
-- its first line; continuation lines of a multi-line message are indented to
-- align under it. A blank line separates messages.
local function render_qview(s)
  local buf = s.qview_bufnr
  if not (buf and vim.api.nvim_buf_is_valid(buf)) then return end
  local lines = {}
  for i, msg in ipairs(s.queue) do
    local msg_lines = vim.split(msg, "\n", { plain = true })
    for j, ml in ipairs(msg_lines) do
      lines[#lines + 1] = (j == 1 and "» " or "  ") .. ml
    end
    if i < #s.queue then lines[#lines + 1] = "" end
  end
  vim.bo[buf].modifiable = true
  vim.api.nvim_buf_set_lines(buf, 0, -1, false, lines)
  vim.bo[buf].modifiable = false
end

-- Open/close/resize the view to match the queue. No-op if the input window is
-- gone (chat hidden): the view is recreated by the next sync once the input
-- box is back.
local function sync_qview(s)
  local has = s.queue and #s.queue > 0

  if not has then
    if s.qview_winnr and vim.api.nvim_win_is_valid(s.qview_winnr) then
      pcall(vim.api.nvim_win_close, s.qview_winnr, true)
    end
    s.qview_winnr = nil
    return
  end

  if not (s.qview_bufnr and vim.api.nvim_buf_is_valid(s.qview_bufnr)) then
    s.qview_bufnr = create_qview_buf()
  end

  if not (s.qview_winnr and vim.api.nvim_win_is_valid(s.qview_winnr)) then
    if not (s.winnr and vim.api.nvim_win_is_valid(s.winnr)) then return end
    local cur = vim.api.nvim_get_current_win()
    -- HACK: guard the WinNew redirect autocmd against grabbing this split
    -- (it fires while `prev` still points at the input window).
    s.creating_qview = true
    vim.api.nvim_set_current_win(s.winnr)
    vim.cmd("aboveleft split")
    s.creating_qview = false
    local w = vim.api.nvim_get_current_win()
    vim.api.nvim_win_set_buf(w, s.qview_bufnr)
    vim.wo[w].number = false
    vim.wo[w].relativenumber = false
    vim.wo[w].signcolumn = "no"
    vim.wo[w].winfixbuf = true
    vim.wo[w].statusline = " "
    vim.wo[w].wrap = true
    vim.wo[w].linebreak = true
    vim.wo[w].cursorline = false
    s.qview_winnr = w
    if vim.api.nvim_win_is_valid(cur) then
      vim.api.nvim_set_current_win(cur)
    end
  end

  render_qview(s)

  local total = math.max(1, vim.api.nvim_buf_line_count(s.qview_bufnr))
  local cap = math.max(1, math.floor(vim.o.lines / 3))
  pcall(vim.api.nvim_win_set_height, s.qview_winnr, math.min(total, cap))
  -- Splitting shrank the input box; restore its height.
  if s.winnr and vim.api.nvim_win_is_valid(s.winnr) then
    pcall(vim.api.nvim_win_set_height, s.winnr, s.fullscreen and vim.o.lines or 8)
  end
end

local function close_qview(s)
  if s.qview_winnr and vim.api.nvim_win_is_valid(s.qview_winnr) then
    pcall(vim.api.nvim_win_close, s.qview_winnr, true)
  end
  if s.qview_bufnr and vim.api.nvim_buf_is_valid(s.qview_bufnr) then
    pcall(vim.api.nvim_buf_delete, s.qview_bufnr, { force = true })
  end
  s.qview_winnr = nil
  s.qview_bufnr = nil
end

-- Recompute the derived `queued` flag from the queue and repaint every
-- surface that reflects queue contents: the input/status highlight, the
-- status line, and the queued-message view.
local function sync_queue_ui(s)
  s.queued = s.queue ~= nil and #s.queue > 0
  update_ui(s)
  sync_qview(s)
end

-- Session-scoped prompt history, shared across every tab's input box (like
-- shell history). Oldest first; capped at MAX_HISTORY. In-memory only — not
-- persisted across nvim restarts.
local MAX_HISTORY = 200
local history = {}

local function push_history(text)
  if not text or text == "" then return end
  if history[#history] == text then return end
  history[#history + 1] = text
  if #history > MAX_HISTORY then
    table.remove(history, 1)
  end
end

-- Overwrite the input draft with `text` without tripping the TextChanged
-- unqueue/hist-reset handler, then park the cursor at the end.
local function set_draft_text(s, text)
  if not s.bufnr or not vim.api.nvim_buf_is_valid(s.bufnr) then return end
  local lines = vim.split(text or "", "\n")
  s.suppress_unqueue = true
  vim.api.nvim_buf_set_lines(s.bufnr, 0, -1, false, lines)
  s.suppress_unqueue = false
  if s.winnr and vim.api.nvim_win_is_valid(s.winnr) then
    local last = #lines
    vim.api.nvim_win_set_cursor(s.winnr, { last, #lines[last] })
  end
end

-- Step to an older prompt. On first step we stash the in-progress draft so
-- stepping back down past the newest entry restores it. `hist_idx` points at
-- the entry currently shown; `#history + 1` is the sentinel "working draft".
local function history_prev(t)
  local s = states[t]
  if not s or #history == 0 then return end
  if not s.hist_idx then
    s.hist_stash = get_draft_text(s) or ""
    s.hist_idx = #history + 1
  end
  if s.hist_idx > 1 then
    s.hist_idx = s.hist_idx - 1
  end
  set_draft_text(s, history[s.hist_idx])
end

-- Step to a newer prompt; stepping past the newest restores the stashed draft.
local function history_next(t)
  local s = states[t]
  if not s or not s.hist_idx then return end
  s.hist_idx = s.hist_idx + 1
  if s.hist_idx > #history then
    set_draft_text(s, s.hist_stash or "")
    s.hist_idx = nil
    s.hist_stash = nil
  else
    set_draft_text(s, history[s.hist_idx])
  end
end

-- Append `text` to the chat buffer as a user message and call `chat:submit()`.
--
-- parser.messages walks captures from `chat.header_line - 1` (0-indexed),
-- so `header_line` must be 1-indexed and point AT the `## Me` heading or
-- tree-sitter never sees the role node and silently captures nothing.
--
-- Steady-state path: after every turn CodeCompanion's `ready_for_input`
-- writes a fresh trailing `## Me`. We reuse that heading and put our text
-- under it — this matches CodeCompanion's own submit path, keeps any
-- Context block (turn 1) attached to the user message, and avoids the
-- visible duplicate `## Me` the previous implementation produced.
--
-- Cold-start fallback: if no `## Me` exists at all (chat was just opened
-- with no Context block, or some odd state), append a fresh section.
--
-- Returns true on success, false if the chat went away mid-flight.
local function submit_to_chat(chat_bufnr, text)
  local chat = require("codecompanion").buf_get_chat(chat_bufnr)
  if not chat then return false end

  -- The chat buffer is kept non-modifiable at rest (read-only enforcement in
  -- plugins/codecompanion.lua) so it can't be hand-edited. Unlock it for this
  -- programmatic write; chat:submit() re-locks it when the request starts and
  -- Chat:reset re-locks it when the turn ends.
  vim.bo[chat_bufnr].modifiable = true

  local lines = vim.api.nvim_buf_get_lines(chat_bufnr, 0, -1, false)

  local last_me_idx
  for i = #lines, 1, -1 do
    if lines[i] == "## Me" then
      last_me_idx = i
      break
    end
  end

  local text_lines = vim.split(text, "\n")

  if last_me_idx then
    -- Reuse the heading. Replace anything (blank or otherwise) that
    -- follows it with: a single blank separator, the existing content
    -- (if any), and then our text. In the common case where ready_for_input
    -- already wrote an empty trailing `## Me`, the "existing content"
    -- list is just blank lines, which collapse cleanly.
    local existing = {}
    for i = last_me_idx + 1, #lines do
      existing[#existing + 1] = lines[i]
    end

    -- Trim trailing blanks from existing so we get a single separator
    -- before our text.
    while #existing > 0 and existing[#existing] == "" do
      existing[#existing] = nil
    end

    local new_tail = { "" }
    for _, l in ipairs(existing) do new_tail[#new_tail + 1] = l end
    if #existing > 0 then new_tail[#new_tail + 1] = "" end
    for _, l in ipairs(text_lines) do new_tail[#new_tail + 1] = l end

    vim.api.nvim_buf_set_lines(chat_bufnr, last_me_idx, -1, false, new_tail)
    chat.header_line = last_me_idx
  else
    local appended = {}
    if #lines > 0 and lines[#lines] ~= "" then
      appended[#appended + 1] = ""
    end
    appended[#appended + 1] = "## Me"
    appended[#appended + 1] = ""
    local header_line = #lines + #appended - 1
    for _, l in ipairs(text_lines) do appended[#appended + 1] = l end
    vim.api.nvim_buf_set_lines(chat_bufnr, #lines, #lines, false, appended)
    chat.header_line = header_line
  end

  chat:submit()
  return true
end

-- Submit `text` to the chat now, recording history and clearing the box on
-- success. Returns true on success. Caller guarantees the chat exists.
local function submit_now(s, text)
  if submit_to_chat(s.chat_bufnr, text) then
    push_history(text)
    clear_draft_buf(s)
    return true
  end
  vim.notify("CodeCompanion submit failed; draft kept.", vim.log.levels.WARN)
  return false
end

-- <C-s> from the input box.
--
--  * Idle + empty queue           → submit the draft immediately.
--  * Busy or queue non-empty      → append the draft to the FIFO and free the
--                                   box for the next message.
--  * Empty box + queue paused     → flush the head now (resume; see
--                                   `on_chat_done`, which pauses on cancel/error).
local function send(t)
  local s = states[t]
  if not s then return end

  local chat = require("codecompanion").buf_get_chat(s.chat_bufnr)
  if not chat then
    -- Chat is gone but the input window is still open. Leave the draft so
    -- the user can copy it; queuing would never flush.
    vim.notify("CodeCompanion chat is closed; draft kept in input box.",
      vim.log.levels.WARN)
    return
  end

  local text = get_draft_text(s)
  if not text then
    -- Nothing to send. Resume a paused queue if one is waiting.
    if not s.in_flight_id and #s.queue > 0 then
      local head = table.remove(s.queue, 1)
      if not submit_to_chat(s.chat_bufnr, head) then
        table.insert(s.queue, 1, head)
      end
      sync_queue_ui(s)
    end
    return
  end

  if not s.in_flight_id and #s.queue == 0 then
    submit_now(s, text)
    return
  end

  s.queue[#s.queue + 1] = text
  push_history(text)
  clear_draft_buf(s)
  sync_queue_ui(s)
end

-- <C-q> from the input box: pull the most recently queued message back into
-- the input box for editing or discarding, emptying that slot. Re-sending
-- (<C-s>) appends it to the tail again; clearing the box discards it. Refuses
-- when the box already holds a draft so nothing is clobbered.
local function pull_back(t)
  local s = states[t]
  if not s then return end
  if #s.queue == 0 then
    vim.notify("No queued message to edit", vim.log.levels.INFO)
    return
  end
  if get_draft_text(s) then
    vim.notify("Input box isn't empty; send or clear it first", vim.log.levels.WARN)
    return
  end
  set_draft_text(s, table.remove(s.queue))
  sync_queue_ui(s)
  if s.winnr and vim.api.nvim_win_is_valid(s.winnr) then
    vim.api.nvim_set_current_win(s.winnr)
    vim.cmd("startinsert")
  end
end

local function toggle_fullscreen(t)
  local s = states[t]
  if not s or not s.winnr or not vim.api.nvim_win_is_valid(s.winnr) then
    return
  end

  if s.fullscreen then
    vim.api.nvim_win_set_height(s.winnr, 8)
  else
    vim.api.nvim_win_set_height(s.winnr, vim.o.lines)
  end
  s.fullscreen = not s.fullscreen
end

local function create_input_buf(t)
  local buf = vim.api.nvim_create_buf(false, true)
  vim.bo[buf].filetype = "codecompanion_input"
  vim.bo[buf].bufhidden = "hide"
  vim.b[buf].cc_input_tab = t

  vim.keymap.set({ "n", "i" }, "<C-s>", function() send(t) end,
    { buffer = buf, desc = "Send/queue prompt" })
  vim.keymap.set({ "n", "i" }, "<C-g>", function() toggle_fullscreen(t) end,
    { buffer = buf, desc = "Toggle fullscreen" })
  vim.keymap.set({ "n", "i" }, "<C-q>", function() pull_back(t) end,
    { buffer = buf, desc = "Pull last queued message back to edit" })

  -- Edge-triggered history navigation: <Up>/<Down> browse prompt history only
  -- at the first/last line, and otherwise fall through to ordinary cursor
  -- movement within a multi-line draft.
  vim.keymap.set({ "n", "i" }, "<Up>", function()
    if vim.api.nvim_win_get_cursor(0)[1] > 1 then
      vim.api.nvim_feedkeys(
        vim.api.nvim_replace_termcodes("<Up>", true, false, true), "n", false)
    else
      history_prev(t)
    end
  end, { buffer = buf, desc = "Previous prompt / move up" })
  vim.keymap.set({ "n", "i" }, "<Down>", function()
    if vim.api.nvim_win_get_cursor(0)[1] < vim.api.nvim_buf_line_count(buf) then
      vim.api.nvim_feedkeys(
        vim.api.nvim_replace_termcodes("<Down>", true, false, true), "n", false)
    else
      history_next(t)
    end
  end, { buffer = buf, desc = "Next prompt / move down" })

  vim.api.nvim_create_autocmd({ "TextChanged", "TextChangedI" }, {
    buffer = buf,
    callback = function()
      local s = states[t]
      if not s then return end
      if s.suppress_unqueue then return end
      -- A manual edit ends any history browse and forks a fresh draft. The
      -- queue itself is independent of the box now, so editing the draft no
      -- longer unqueues anything.
      s.hist_idx = nil
      s.hist_stash = nil
    end,
  })

  return buf
end

-- Re-entrancy guard for teardown. `M.teardown` closes windows and the
-- chat buffer, each of which fires events (WinClosed, ChatClosed) that
-- route back here; the guard makes those nested calls no-ops so the
-- single in-progress teardown owns the whole sequence.
local tearing_down = {}

-- Bring down a tab's entire CodeCompanion UI together and synchronously:
-- the chat window+buffer, the input window+buffer, the status
-- window+buffer, the status-line timer, the per-tab state, and the tab
-- var. This is the single teardown path — every close entry point
-- (chat closed, input/status window closed, tab closed) funnels here so
-- the three panes always live and die as a unit.
--
-- Idempotent: safe to call repeatedly and re-entrantly. Closing the
-- chat buffer here fires `CodeCompanionChatClosed` -> `on_chat_closed`
-- -> `teardown` again; the guard (and the `states[t]` nil check)
-- absorb the re-entry. Also handles the "chat buffer already deleted"
-- case, since CodeCompanion's `Chat:close` fires `ChatClosed` and only
-- then deletes the buffer — so by the time our scheduled handler runs
-- the buffer (and its tab stamp) may be gone, which is why callers pass
-- the resolved tab in explicitly.
function M.teardown(t)
  if not t then return end
  local s = states[t]
  if not s then return end
  if tearing_down[t] then return end
  tearing_down[t] = true

  statusline.stop(s)
  close_qview(s)

  -- Close the chat window(s) in the owning tab. Window close doesn't
  -- delete the chat buffer; that happens below via chat:close().
  if s.chat_bufnr and vim.api.nvim_tabpage_is_valid(t) then
    for _, win in ipairs(vim.api.nvim_tabpage_list_wins(t)) do
      if vim.api.nvim_win_get_buf(win) == s.chat_bufnr then
        pcall(vim.api.nvim_win_close, win, true)
      end
    end
  end
  if s.status_winnr and vim.api.nvim_win_is_valid(s.status_winnr) then
    pcall(vim.api.nvim_win_close, s.status_winnr, true)
  end
  if s.winnr and vim.api.nvim_win_is_valid(s.winnr) then
    pcall(vim.api.nvim_win_close, s.winnr, true)
  end

  -- Close the chat itself. Prefer chat:close() when the chat still
  -- exists so its ACP connection is disconnected; fall back to a raw
  -- buffer delete. When we got here *from* a ChatClosed event the chat
  -- is already gone (buf_get_chat -> nil, buffer invalid) and both
  -- branches are skipped.
  if s.chat_bufnr and vim.api.nvim_buf_is_valid(s.chat_bufnr) then
    local chat = require("codecompanion").buf_get_chat(s.chat_bufnr)
    if chat then
      pcall(function() chat:close() end)
    else
      pcall(vim.api.nvim_buf_delete, s.chat_bufnr, { force = true })
    end
  end
  if s.bufnr and vim.api.nvim_buf_is_valid(s.bufnr) then
    pcall(vim.api.nvim_buf_delete, s.bufnr, { force = true })
  end
  if s.status_bufnr and vim.api.nvim_buf_is_valid(s.status_bufnr) then
    pcall(vim.api.nvim_buf_delete, s.status_bufnr, { force = true })
  end

  if vim.api.nvim_tabpage_is_valid(t) then
    pcall(vim.api.nvim_tabpage_del_var, t, "codecompanion_chat_bufnr")
  end

  states[t] = nil
  tearing_down[t] = nil
end

-- When a sibling input/status window closes, tear the whole UI down —
-- the three panes are a unit (see `M.teardown`). Matching is per-tab
-- via the stamped `winnr`/`status_winnr`, so a `:tab split` can't close
-- the wrong tab's chat.
--
-- The hide path (`on_chat_hidden`) nulls these fields *before* closing
-- its windows precisely so this handler doesn't mistake a toggle-off
-- for a teardown.
vim.api.nvim_create_autocmd("WinClosed", {
  group = vim.api.nvim_create_augroup("codecompanion_queue_close", { clear = true }),
  callback = function(args)
    local closed = tonumber(args.match)
    if not closed then return end
    for t, s in pairs(states) do
      -- The queued-message view comes and goes with the queue; its close is
      -- never a teardown. Just drop the stale handle.
      if closed == s.qview_winnr then
        s.qview_winnr = nil
        return
      end
      if closed == s.winnr or closed == s.status_winnr then
        M.teardown(t)
        return
      end
    end
  end,
})

-- When a new window is opened from the input or status pane, redirect it
-- into a split alongside the chat — but only when the chat lives in the
-- same tab as the new window.
vim.api.nvim_create_autocmd("WinNew", {
  group = vim.api.nvim_create_augroup("codecompanion_queue_redirect", { clear = true }),
  callback = function()
    local t = vim.api.nvim_get_current_tabpage()
    local s = states[t]
    if not s or not s.winnr or not vim.api.nvim_win_is_valid(s.winnr) then
      return
    end
    -- The queued-message view is deliberately split off the input window; don't
    -- redirect it into a chat split.
    if s.creating_qview then return end
    local prev = vim.fn.win_getid(vim.fn.winnr("#"))
    if prev ~= s.winnr and prev ~= s.status_winnr then
      return
    end

    local new_win = vim.api.nvim_get_current_win()
    -- Never redirect a floating window (opened from the input box so `prev`
    -- matches s.winnr) into a split.
    if vim.api.nvim_win_get_config(new_win).relative ~= "" then
      return
    end
    vim.schedule(function()
      if not vim.api.nvim_win_is_valid(new_win) then return end
      local chat_win = s.chat_bufnr and vim.fn.bufwinid(s.chat_bufnr)
      if not chat_win or chat_win == -1 then return end
      if vim.api.nvim_win_get_tabpage(chat_win) ~= vim.api.nvim_win_get_tabpage(new_win) then
        return
      end

      local buf = vim.api.nvim_win_get_buf(new_win)
      -- HACK: pin bufhidden across the close so a buf with bufhidden=wipe
      -- isn't destroyed when its only window closes.
      local prev_bufhidden = vim.bo[buf].bufhidden
      vim.bo[buf].bufhidden = "hide"
      vim.api.nvim_win_close(new_win, false)
      vim.api.nvim_set_current_win(chat_win)
      vim.cmd("vertical rightbelow split")
      vim.api.nvim_win_set_buf(vim.api.nvim_get_current_win(), buf)
      if vim.api.nvim_buf_is_valid(buf) then
        vim.bo[buf].bufhidden = prev_bufhidden
      end
    end)
  end,
})

vim.api.nvim_create_autocmd("TabClosed", {
  group = vim.api.nvim_create_augroup("codecompanion_queue_tabclosed", { clear = true }),
  callback = function()
    local valid = {}
    for _, t in ipairs(vim.api.nvim_list_tabpages()) do valid[t] = true end
    for t in pairs(states) do
      if not valid[t] then
        M.teardown(t)
      end
    end
  end,
})

function M.on_chat_opened(chat_bufnr)
  local t = tab_for_chat(chat_bufnr)
  if not t then return end

  local s = states[t]
  if s and s.winnr and vim.api.nvim_win_is_valid(s.winnr) then
    s.chat_bufnr = chat_bufnr
    return
  end

  s = s or {}
  states[t] = s

  if not s.bufnr or not vim.api.nvim_buf_is_valid(s.bufnr) then
    s.bufnr = create_input_buf(t)
  end
  if not s.status_bufnr or not vim.api.nvim_buf_is_valid(s.status_bufnr) then
    s.status_bufnr = statusline.create_buf()
  end

  s.chat_bufnr = chat_bufnr
  s.queue = s.queue or {}
  s.queued = #s.queue > 0
  s.suppress_unqueue = false
  s.fullscreen = false

  local chat_winnr = vim.fn.bufwinid(chat_bufnr)
  if chat_winnr == -1 then return end

  local prev_win = vim.api.nvim_get_current_win()
  vim.api.nvim_set_current_win(chat_winnr)

  vim.cmd("belowright split")
  local input_win = vim.api.nvim_get_current_win()
  vim.api.nvim_win_set_buf(input_win, s.bufnr)

  vim.cmd("belowright split")
  local status_win = vim.api.nvim_get_current_win()
  vim.api.nvim_win_set_buf(status_win, s.status_bufnr)

  vim.wo[status_win].winfixheight = true
  vim.api.nvim_win_set_height(status_win, 1)
  vim.api.nvim_win_set_height(input_win, 8)

  for _, w in ipairs({ status_win, input_win }) do
    vim.wo[w].number = false
    vim.wo[w].relativenumber = false
    vim.wo[w].signcolumn = "no"
    vim.wo[w].winfixheight = true
    vim.wo[w].winfixbuf = true
    vim.wo[w].statusline = " "
    vim.wo[w].wrap = true
    vim.wo[w].linebreak = true
  end
  vim.wo[status_win].cursorline = false

  s.winnr = input_win
  s.status_winnr = status_win
  update_ui(s)
  statusline.start(s)
  -- Restore the queued-message view if this tab reopened with a pending queue.
  sync_qview(s)

  -- HACK: On first open, focus the input box. On subsequent opens (toggle
  -- cycles), restore focus to wherever the user was — CodeCompanion's toggle
  -- already handles focus for the chat pane.
  if prev_win == chat_winnr then
    vim.api.nvim_set_current_win(input_win)
    vim.cmd("startinsert")
  else
    vim.api.nvim_set_current_win(prev_win)
  end
end

function M.on_chat_hidden(chat_bufnr)
  local t = tab_for_chat(chat_bufnr)
  if not t then return end
  local s = states[t]
  if not s or s.chat_bufnr ~= chat_bufnr then return end
  s.fullscreen = false
  close_qview(s)
  -- Null the window handles *before* closing so the WinClosed handler
  -- sees no matching winnr/status_winnr and treats this as a toggle-off,
  -- not a full teardown. The input/status buffers are kept for re-open.
  local status_winnr, winnr = s.status_winnr, s.winnr
  s.status_winnr = nil
  s.winnr = nil
  if status_winnr and vim.api.nvim_win_is_valid(status_winnr) then
    pcall(vim.api.nvim_win_close, status_winnr, true)
  end
  if winnr and vim.api.nvim_win_is_valid(winnr) then
    pcall(vim.api.nvim_win_close, winnr, true)
  end
  statusline.stop(s)
end

-- Auto-flush the queue one message per turn. On success, pop the head and
-- submit it; the next turn's completion flushes the following one, and so on.
-- On cancel/error the queue is left intact (pause): nothing is lost, and the
-- user resumes with an empty <C-s> in the input box (see `send`) once things
-- look right.
function M.on_chat_done(chat_bufnr)
  local t = tab_for_chat(chat_bufnr)
  if not t then return end
  local s = states[t]
  if not s or not s.queue or #s.queue == 0 or s.chat_bufnr ~= chat_bufnr then return end

  if s.last_finished_status and s.last_finished_status ~= "success" then
    sync_queue_ui(s)
    return
  end

  local text = table.remove(s.queue, 1)
  if not submit_to_chat(chat_bufnr, text) then
    table.insert(s.queue, 1, text)
  end
  sync_queue_ui(s)
end

-- Chat closed by CodeCompanion (or by us). Route to the unified
-- teardown. The tab is passed in explicitly by the ChatClosed handler
-- because CodeCompanion deletes the chat buffer synchronously inside
-- Chat:close (right after firing ChatClosed), so by the time this runs
-- the buffer — and its `cc_tab_owner` stamp — may already be gone,
-- making `tab_for_chat` return nil. Fall back to it only when no tab
-- was provided (e.g. legacy callers).
function M.on_chat_closed(chat_bufnr, tab)
  local t = tab or tab_for_chat(chat_bufnr)
  M.teardown(t)
end

-- Returns the input bufnr for the current tab. Used by the cmp slash source.
function M.bufnr()
  local t = vim.api.nvim_get_current_tabpage()
  local s = states[t]
  return s and s.bufnr or nil
end

-- Returns the chat bufnr that the cmp slash source should target. Resolves
-- via the input buffer the user is typing in (which knows its owning tab),
-- falling back to the current tab's chat.
function M.chat_bufnr()
  local cur_buf = vim.api.nvim_get_current_buf()
  local t
  local ok, input_tab = pcall(function() return vim.b[cur_buf].cc_input_tab end)
  if ok and input_tab and vim.api.nvim_tabpage_is_valid(input_tab) then
    t = input_tab
  else
    t = vim.api.nvim_get_current_tabpage()
  end
  local s = states[t]
  return s and s.chat_bufnr or nil
end

-- Lifecycle hooks driven by `User CodeCompanionRequestStarted/Finished`
-- in plugins/codecompanion.lua. The id-based matching is the canonical
-- "in flight" signal — `chat.current_request` is unreliable across
-- cancellation (Chat:stop() clears it synchronously while the finish
-- handler runs later).
function M.on_request_started(bufnr, id)
  local t = tab_for_chat(bufnr)
  if not t then return end
  local s = states[t]
  if not s or s.chat_bufnr ~= bufnr then return end
  s.in_flight_id = id or true
  s.last_finished_status = nil
  s.request_start_at = os.time()
  statusline.refresh(s)
end

function M.on_request_finished(bufnr, id, status)
  local t = tab_for_chat(bufnr)
  if not t then return end
  local s = states[t]
  if not s or s.chat_bufnr ~= bufnr then return end
  -- Only clear if this is the request we were tracking. Out-of-order
  -- Finished events (e.g. from a previous cancelled request arriving
  -- after a newer one started) must not blow away in-flight state.
  if id == nil or s.in_flight_id == nil or s.in_flight_id == id or s.in_flight_id == true then
    s.in_flight_id = nil
    s.request_start_at = nil
    s.last_finished_status = status
  end
  statusline.refresh(s)
end

function M.focus()
  local t = vim.api.nvim_get_current_tabpage()
  local s = states[t]
  if s and s.winnr and vim.api.nvim_win_is_valid(s.winnr) then
    vim.api.nvim_set_current_win(s.winnr)
    vim.cmd("startinsert")
  else
    vim.notify("No CodeCompanion input box open in this tab", vim.log.levels.WARN)
  end
end

return M
