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
--- @field chat_bufnr number?    the chat buffer this queue feeds
--- @field queued boolean
--- @field suppress_unqueue boolean
--- @field fullscreen boolean
--- @field request_start_at number?
--- @field in_flight_id any      request id of the in-flight prompt, or nil
--- @field last_finished_status string? "success" | "cancelled" | "error" | ...

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
  s.queued = false
  update_ui(s)
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

local function send(t)
  local s = states[t]
  if not s then return end
  local text = get_draft_text(s)
  if not text then return end

  local cc = require("codecompanion")
  local chat = cc.buf_get_chat(s.chat_bufnr)
  if not chat then
    -- Chat is gone but the input window is still open. Don't queue
    -- (nothing will ever flush it). Leave the draft so the user can
    -- copy it.
    vim.notify("CodeCompanion chat is closed; draft kept in input box.",
      vim.log.levels.WARN)
    s.queued = false
    update_ui(s)
    return
  end

  if not s.in_flight_id then
    -- Submit immediately. Only clear the draft after the buffer mutation
    -- succeeds, so a failed submit doesn't silently swallow the text.
    if submit_to_chat(s.chat_bufnr, text) then
      clear_draft_buf(s)
    else
      vim.notify("CodeCompanion submit failed; draft kept.", vim.log.levels.WARN)
    end
    return
  end

  -- Queue for after the in-flight request finishes.
  s.queued = true
  s.suppress_unqueue = true
  update_ui(s)
  vim.schedule(function() s.suppress_unqueue = false end)
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

  vim.api.nvim_create_autocmd({ "TextChanged", "TextChangedI" }, {
    buffer = buf,
    callback = function()
      local s = states[t]
      if not s then return end
      if s.suppress_unqueue then return end
      if s.queued then
        s.queued = false
        update_ui(s)
      end
    end,
  })

  return buf
end

-- Closes the chat window when its sibling input/status window closes —
-- but only when the closing window and the chat window live in the
-- same tab. The previous implementation looked up the chat with
-- `bufwinid(chat_bufnr)`, which finds any window showing the chat
-- buffer; in a `:tab split` scenario that would close the wrong tab's
-- chat.
vim.api.nvim_create_autocmd("WinClosed", {
  group = vim.api.nvim_create_augroup("codecompanion_queue_close", { clear = true }),
  callback = function(args)
    local closed = tonumber(args.match)
    if not closed then return end
    for t, s in pairs(states) do
      if closed == s.winnr or closed == s.status_winnr then
        if not s.chat_bufnr then return end
        -- Find a chat window in the same tab as `t`.
        for _, win in ipairs(vim.api.nvim_tabpage_list_wins(t)) do
          if vim.api.nvim_win_get_buf(win) == s.chat_bufnr then
            pcall(vim.api.nvim_win_close, win, true)
            return
          end
        end
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
    local prev = vim.fn.win_getid(vim.fn.winnr("#"))
    if prev ~= s.winnr and prev ~= s.status_winnr then
      return
    end

    local new_win = vim.api.nvim_get_current_win()
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
    for t, s in pairs(states) do
      if not valid[t] then
        statusline.stop(s)
        if s.bufnr and vim.api.nvim_buf_is_valid(s.bufnr) then
          pcall(vim.api.nvim_buf_delete, s.bufnr, { force = true })
        end
        if s.status_bufnr and vim.api.nvim_buf_is_valid(s.status_bufnr) then
          pcall(vim.api.nvim_buf_delete, s.status_bufnr, { force = true })
        end
        states[t] = nil
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
  s.queued = s.queued or false
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
  if s.status_winnr and vim.api.nvim_win_is_valid(s.status_winnr) then
    vim.api.nvim_win_close(s.status_winnr, true)
  end
  s.status_winnr = nil
  if s.winnr and vim.api.nvim_win_is_valid(s.winnr) then
    vim.api.nvim_win_close(s.winnr, true)
  end
  s.winnr = nil
  statusline.stop(s)
end

-- Auto-flush a queued draft when a request completes successfully.
-- On cancel/error, drop the queued flag but keep the text in the input
-- box so the user can decide whether to resend.
function M.on_chat_done(chat_bufnr)
  local t = tab_for_chat(chat_bufnr)
  if not t then return end
  local s = states[t]
  if not s or not s.queued or s.chat_bufnr ~= chat_bufnr then return end

  if s.last_finished_status and s.last_finished_status ~= "success" then
    s.queued = false
    update_ui(s)
    return
  end

  local text = get_draft_text(s)
  if not text then
    s.queued = false
    update_ui(s)
    return
  end

  if submit_to_chat(chat_bufnr, text) then
    clear_draft_buf(s)
  else
    s.queued = false
    update_ui(s)
  end
end

function M.on_chat_closed(chat_bufnr)
  local t = tab_for_chat(chat_bufnr)
  if not t then return end
  local s = states[t]
  if not s or s.chat_bufnr ~= chat_bufnr then return end
  statusline.stop(s)
  if s.status_winnr and vim.api.nvim_win_is_valid(s.status_winnr) then
    pcall(vim.api.nvim_win_close, s.status_winnr, true)
  end
  if s.winnr and vim.api.nvim_win_is_valid(s.winnr) then
    pcall(vim.api.nvim_win_close, s.winnr, true)
  end
  if s.bufnr and vim.api.nvim_buf_is_valid(s.bufnr) then
    pcall(vim.api.nvim_buf_delete, s.bufnr, { force = true })
  end
  if s.status_bufnr and vim.api.nvim_buf_is_valid(s.status_bufnr) then
    pcall(vim.api.nvim_buf_delete, s.status_bufnr, { force = true })
  end
  states[t] = nil
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
