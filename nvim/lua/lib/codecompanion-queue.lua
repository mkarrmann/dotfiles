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

local M = {}

local status_ns = vim.api.nvim_create_namespace("codecompanion_status")

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

--- @type table<number, CCQueueState>
local states = {}

local status_timer = nil

local function setup_highlights()
  local normal_hl = vim.api.nvim_get_hl(0, { name = "Normal", link = false })
  local warn_hl = vim.api.nvim_get_hl(0, { name = "DiagnosticWarn", link = false })

  local bg = normal_hl.bg
  if bg == nil then
    bg = vim.o.background == "light" and 0xFFFFFF or 0x1E1E2E
  end
  local warn_fg = warn_hl.fg or 0xE0A500

  local function blend(c1, c2, alpha)
    local r1, g1, b1 = math.floor(c1 / 65536) % 256, math.floor(c1 / 256) % 256, c1 % 256
    local r2, g2, b2 = math.floor(c2 / 65536) % 256, math.floor(c2 / 256) % 256, c2 % 256
    return math.floor(r1 * alpha + r2 * (1 - alpha)) * 65536
      + math.floor(g1 * alpha + g2 * (1 - alpha)) * 256
      + math.floor(b1 * alpha + b2 * (1 - alpha))
  end

  vim.api.nvim_set_hl(0, "CCQueuedNormal", { bg = blend(warn_fg, bg, 0.1) })
  vim.api.nvim_set_hl(0, "CCQueuedBorder", { fg = warn_fg })
end

setup_highlights()
vim.api.nvim_create_autocmd("ColorScheme", {
  group = vim.api.nvim_create_augroup("codecompanion_queue_highlights", { clear = true }),
  callback = setup_highlights,
})

local function fmt_tokens(n)
  if n >= 1000 then
    return string.format("%.1fk", n / 1000)
  end
  return tostring(n)
end

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

local function build_status_segments(s)
  local left = {}
  local right = {}

  if not s.chat_bufnr then
    return left, right
  end

  local meta = (_G.codecompanion_chat_metadata or {})[s.chat_bufnr] or {}

  local chat = require("codecompanion").buf_get_chat(s.chat_bufnr)
  local adapter_type = chat and chat.adapter and chat.adapter.type
  local dvsc_sel
  if chat and chat.adapter and chat.adapter.name == "dvsc_core_broker" then
    local sel_for_buf = _G.codecompanion_dvsc_selection_for_buf
    if type(sel_for_buf) == "function" then
      dvsc_sel = sel_for_buf(s.chat_bufnr)
    end
  end
  local acp_session_id = (
    adapter_type == "acp"
    and chat
    and chat.acp_connection
    and chat.acp_connection.session_id
  ) or nil
  local acp_usage
  if acp_session_id then
    local ok_stats, stats = pcall(require, "lib.codecompanion-stats")
    if ok_stats then
      acp_usage = stats.get(acp_session_id)
    end
  end

  if s.queued then
    left[#left + 1] = { " Queued ", "DiagnosticWarn" }
  else
    left[#left + 1] = { " Draft ", "Comment" }
  end
  left[#left + 1] = { " · ", "Comment" }
  local adapter_name = (meta.adapter and meta.adapter.name)
    or (chat and chat.adapter and (chat.adapter.name or chat.adapter.formatted_name))
    or "unknown"
  left[#left + 1] = { adapter_name, "Function" }
  if meta.adapter and meta.adapter.model then
    left[#left + 1] = { " · ", "Comment" }
    left[#left + 1] = { meta.adapter.model, "String" }
  end
  if meta.cycles and meta.cycles > 0 then
    left[#left + 1] = { " · ", "Comment" }
    left[#left + 1] = { "turn " .. meta.cycles, "Number" }
  end

  if acp_session_id then
    right[#right + 1] = { acp_session_id, "Constant" }
  end
  if meta.mode and meta.mode.name then
    right[#right + 1] = { meta.mode.name, "String" }
  elseif dvsc_sel and dvsc_sel.mode then
    right[#right + 1] = { dvsc_sel.mode, "String" }
  end
  if dvsc_sel and dvsc_sel.model then
    right[#right + 1] = { dvsc_sel.model, "String" }
  end
  if dvsc_sel and dvsc_sel.effort then
    right[#right + 1] = { "effort:" .. tostring(dvsc_sel.effort), "DiagnosticInfo" }
  end
  if meta.tools and meta.tools > 0 then
    right[#right + 1] = { meta.tools .. " tools", "DiagnosticInfo" }
  end
  if meta.context_items and meta.context_items > 0 then
    right[#right + 1] = { meta.context_items .. " ctx", "DiagnosticInfo" }
  end
  if meta.tokens and meta.tokens > 0 then
    right[#right + 1] = { fmt_tokens(meta.tokens) .. " tokens", "DiagnosticInfo" }
  elseif acp_usage and acp_usage.used and acp_usage.used > 0 then
    right[#right + 1] = { fmt_tokens(acp_usage.used) .. " tokens", "DiagnosticInfo" }
  end
  if acp_usage and acp_usage.used and acp_usage.size and acp_usage.size > 0 then
    local pct = math.floor(100 * acp_usage.used / acp_usage.size)
    right[#right + 1] = {
      string.format("%d%% %s/%s", pct, fmt_tokens(acp_usage.used), fmt_tokens(acp_usage.size)),
      "DiagnosticInfo",
    }
  end

  if s.request_start_at then
    local elapsed = os.time() - s.request_start_at
    local hl = "DiagnosticInfo"
    if elapsed >= 90 then
      hl = "DiagnosticError"
    elseif elapsed >= 30 then
      hl = "DiagnosticWarn"
    end
    right[#right + 1] = { string.format("%ds", elapsed), hl }
  end

  return left, right
end

local function build_wrapped_status(left, right)
  local merged = {}
  for _, seg in ipairs(left) do
    merged[#merged + 1] = seg
  end
  for i, seg in ipairs(right) do
    if i == 1 and #merged > 0 then
      merged[#merged + 1] = { " · ", "Comment" }
    elseif i > 1 then
      merged[#merged + 1] = { " · ", "Comment" }
    end
    merged[#merged + 1] = seg
  end

  local text = ""
  for _, seg in ipairs(merged) do
    text = text .. seg[1]
  end

  return merged, text
end

local function needed_status_height(text, width)
  if text == "" then
    return 1
  end
  local usable = math.max(1, width)
  local display_width = vim.fn.strdisplaywidth(text)
  return math.max(1, math.ceil(display_width / usable))
end

local function refresh_status(s)
  if not s.status_bufnr or not vim.api.nvim_buf_is_valid(s.status_bufnr) then
    return
  end

  local left, right = build_status_segments(s)
  local merged, full_text = build_wrapped_status(left, right)

  vim.bo[s.status_bufnr].modifiable = true
  vim.api.nvim_buf_set_lines(s.status_bufnr, 0, -1, false, { full_text })
  vim.bo[s.status_bufnr].modifiable = false

  if s.status_winnr and vim.api.nvim_win_is_valid(s.status_winnr) then
    local width = vim.api.nvim_win_get_width(s.status_winnr)
    local target_height = math.min(4, needed_status_height(full_text, width))
    vim.api.nvim_win_set_height(s.status_winnr, target_height)
  end

  vim.api.nvim_buf_clear_namespace(s.status_bufnr, status_ns, 0, -1)
  local col = 0
  for _, seg in ipairs(merged) do
    if seg[2] then
      vim.api.nvim_buf_add_highlight(s.status_bufnr, status_ns, seg[2], 0, col, col + #seg[1])
    end
    col = col + #seg[1]
  end
end

local function refresh_all()
  for _, s in pairs(states) do
    if s.status_bufnr and vim.api.nvim_buf_is_valid(s.status_bufnr) then
      refresh_status(s)
    end
  end
end

local function stop_status_timer()
  if status_timer then
    status_timer:stop()
    status_timer:close()
    status_timer = nil
  end
end

local function start_status_timer()
  if status_timer then return end
  status_timer = vim.uv.new_timer()
  status_timer:start(0, 1000, vim.schedule_wrap(function()
    if not any_visible() then
      stop_status_timer()
      return
    end
    refresh_all()
  end))
end

local function get_draft_text(s)
  if not s.bufnr or not vim.api.nvim_buf_is_valid(s.bufnr) then
    return nil
  end
  local lines = vim.api.nvim_buf_get_lines(s.bufnr, 0, -1, false)
  local text = vim.trim(table.concat(lines, "\n"))
  return text ~= "" and text or nil
end

local function update_ui(s)
  local whl_input = ""
  local whl_status = ""

  if s.queued then
    whl_input = "Normal:CCQueuedNormal,EndOfBuffer:CCQueuedNormal,WinSeparator:CCQueuedBorder"
    whl_status = "Normal:CCQueuedNormal,WinSeparator:CCQueuedBorder"
  end

  if s.winnr and vim.api.nvim_win_is_valid(s.winnr) then
    vim.wo[s.winnr].winhighlight = whl_input
  end
  if s.status_winnr and vim.api.nvim_win_is_valid(s.status_winnr) then
    vim.wo[s.status_winnr].winhighlight = whl_status
  end

  refresh_status(s)
end

local function submit_to_chat(chat_bufnr, text)
  local chat = require("codecompanion").buf_get_chat(chat_bufnr)
  if not chat then
    return
  end

  local lines = vim.split(text, "\n")
  local line_count = vim.api.nvim_buf_line_count(chat_bufnr)
  vim.api.nvim_buf_set_lines(chat_bufnr, line_count, line_count, false, lines)

  chat:submit()
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

local function send(t)
  local s = states[t]
  if not s then return end
  local text = get_draft_text(s)
  if not text then
    return
  end

  local cc = require("codecompanion")
  local chat = cc.buf_get_chat(s.chat_bufnr)
  if chat and not chat.current_request then
    clear_draft_buf(s)
    submit_to_chat(s.chat_bufnr, text)
  else
    s.queued = true
    s.suppress_unqueue = true
    update_ui(s)
    vim.schedule(function()
      s.suppress_unqueue = false
    end)
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

local function create_status_buf()
  local buf = vim.api.nvim_create_buf(false, true)
  vim.bo[buf].buftype = "nofile"
  vim.bo[buf].bufhidden = "hide"
  vim.bo[buf].swapfile = false
  vim.bo[buf].modifiable = false
  return buf
end

-- Closes the chat window when its sibling input/status window closes,
-- restricted to the tab that owns those windows.
vim.api.nvim_create_autocmd("WinClosed", {
  group = vim.api.nvim_create_augroup("codecompanion_queue_close", { clear = true }),
  callback = function(args)
    local closed = tonumber(args.match)
    if not closed then return end
    for _, s in pairs(states) do
      if closed == s.winnr or closed == s.status_winnr then
        local chat_win = s.chat_bufnr and vim.fn.bufwinid(s.chat_bufnr)
        if chat_win and chat_win ~= -1 then
          pcall(vim.api.nvim_win_close, chat_win, true)
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
      if not vim.api.nvim_win_is_valid(new_win) then
        return
      end
      local chat_win = s.chat_bufnr and vim.fn.bufwinid(s.chat_bufnr)
      if not chat_win or chat_win == -1 then
        return
      end
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
    for _, t in ipairs(vim.api.nvim_list_tabpages()) do
      valid[t] = true
    end
    for t, s in pairs(states) do
      if not valid[t] then
        if s.bufnr and vim.api.nvim_buf_is_valid(s.bufnr) then
          pcall(vim.api.nvim_buf_delete, s.bufnr, { force = true })
        end
        if s.status_bufnr and vim.api.nvim_buf_is_valid(s.status_bufnr) then
          pcall(vim.api.nvim_buf_delete, s.status_bufnr, { force = true })
        end
        states[t] = nil
      end
    end
    if not any_visible() then
      stop_status_timer()
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
    s.status_bufnr = create_status_buf()
  end

  s.chat_bufnr = chat_bufnr
  s.queued = s.queued or false
  s.suppress_unqueue = false
  s.fullscreen = false

  local chat_winnr = vim.fn.bufwinid(chat_bufnr)
  if chat_winnr == -1 then
    return
  end

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

  vim.wo[status_win].number = false
  vim.wo[status_win].relativenumber = false
  vim.wo[status_win].signcolumn = "no"
  vim.wo[status_win].winfixheight = true
  vim.wo[status_win].winfixbuf = true
  vim.wo[status_win].statusline = " "
  vim.wo[status_win].cursorline = false
  vim.wo[status_win].wrap = true
  vim.wo[status_win].linebreak = true

  vim.wo[input_win].number = false
  vim.wo[input_win].relativenumber = false
  vim.wo[input_win].signcolumn = "no"
  vim.wo[input_win].winfixheight = true
  vim.wo[input_win].winfixbuf = true
  vim.wo[input_win].statusline = " "
  vim.wo[input_win].wrap = true
  vim.wo[input_win].linebreak = true

  s.winnr = input_win
  s.status_winnr = status_win
  update_ui(s)
  start_status_timer()

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
  if not s or s.chat_bufnr ~= chat_bufnr then
    return
  end
  s.fullscreen = false
  if s.status_winnr and vim.api.nvim_win_is_valid(s.status_winnr) then
    vim.api.nvim_win_close(s.status_winnr, true)
  end
  s.status_winnr = nil
  if s.winnr and vim.api.nvim_win_is_valid(s.winnr) then
    vim.api.nvim_win_close(s.winnr, true)
  end
  s.winnr = nil
  if not any_visible() then
    stop_status_timer()
  end
end

function M.on_chat_done(chat_bufnr)
  local t = tab_for_chat(chat_bufnr)
  if not t then return end
  local s = states[t]
  if not s or not s.queued or s.chat_bufnr ~= chat_bufnr then
    return
  end

  local text = get_draft_text(s)
  if not text then
    s.queued = false
    update_ui(s)
    return
  end

  clear_draft_buf(s)
  submit_to_chat(chat_bufnr, text)
end

function M.on_chat_closed(chat_bufnr)
  local t = tab_for_chat(chat_bufnr)
  if not t then return end
  local s = states[t]
  if not s or s.chat_bufnr ~= chat_bufnr then
    return
  end
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
  if not any_visible() then
    stop_status_timer()
  end
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

function M.on_request_started(bufnr)
  local t = tab_for_chat(bufnr)
  if not t then return end
  local s = states[t]
  if not s or s.chat_bufnr ~= bufnr then
    return
  end
  s.request_start_at = os.time()
  refresh_status(s)
end

function M.on_request_finished(bufnr)
  local t = tab_for_chat(bufnr)
  if not t then return end
  local s = states[t]
  if not s or s.chat_bufnr ~= bufnr then
    return
  end
  s.request_start_at = nil
  refresh_status(s)
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
