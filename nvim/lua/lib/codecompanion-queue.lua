local M = {}

local status_ns = vim.api.nvim_create_namespace("codecompanion_status")

local state = {
  bufnr = nil,
  winnr = nil,
  status_bufnr = nil,
  status_winnr = nil,
  status_timer = nil,
  chat_bufnr = nil,
  queued = false,
  suppress_unqueue = false,
  fullscreen = false,
  request_start_at = nil,
}

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

vim.api.nvim_create_autocmd("WinClosed", {
  group = vim.api.nvim_create_augroup("codecompanion_queue_close", { clear = true }),
  callback = function(args)
    local closed = tonumber(args.match)
    if closed ~= state.winnr and closed ~= state.status_winnr then
      return
    end
    local chat_win = state.chat_bufnr and vim.fn.bufwinid(state.chat_bufnr)
    if chat_win and chat_win ~= -1 then
      pcall(vim.api.nvim_win_close, chat_win, true)
    end
  end,
})

vim.api.nvim_create_autocmd("WinNew", {
  group = vim.api.nvim_create_augroup("codecompanion_queue_redirect", { clear = true }),
  callback = function()
    if not state.winnr or not vim.api.nvim_win_is_valid(state.winnr) then
      return
    end
    local prev = vim.fn.win_getid(vim.fn.winnr("#"))
    if prev ~= state.winnr and prev ~= state.status_winnr then
      return
    end

    local new_win = vim.api.nvim_get_current_win()
    vim.schedule(function()
      if not vim.api.nvim_win_is_valid(new_win) then
        return
      end
      local chat_win = state.chat_bufnr and vim.fn.bufwinid(state.chat_bufnr)
      if not chat_win or chat_win == -1 then
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

local function build_status_segments()
  local left = {}
  local right = {}

  if not state.chat_bufnr then
    return left, right
  end

  local meta = (_G.codecompanion_chat_metadata or {})[state.chat_bufnr] or {}

  local chat = require("codecompanion").buf_get_chat(state.chat_bufnr)
  local adapter_type = chat and chat.adapter and chat.adapter.type
  local dvsc_sel
  if chat and chat.adapter and chat.adapter.name == "dvsc_core_broker" then
    local sel_for_buf = _G.codecompanion_dvsc_selection_for_buf
    if type(sel_for_buf) == "function" then
      dvsc_sel = sel_for_buf(state.chat_bufnr)
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

  if state.queued then
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

  if state.request_start_at then
    local elapsed = os.time() - state.request_start_at
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

local function refresh_status()
  if not state.status_bufnr or not vim.api.nvim_buf_is_valid(state.status_bufnr) then
    return
  end

  local left, right = build_status_segments()
  local merged, full_text = build_wrapped_status(left, right)

  vim.bo[state.status_bufnr].modifiable = true
  vim.api.nvim_buf_set_lines(state.status_bufnr, 0, -1, false, { full_text })
  vim.bo[state.status_bufnr].modifiable = false

  if state.status_winnr and vim.api.nvim_win_is_valid(state.status_winnr) then
    local width = vim.api.nvim_win_get_width(state.status_winnr)
    local target_height = math.min(4, needed_status_height(full_text, width))
    vim.api.nvim_win_set_height(state.status_winnr, target_height)
  end

  vim.api.nvim_buf_clear_namespace(state.status_bufnr, status_ns, 0, -1)
  local col = 0
  for _, seg in ipairs(merged) do
    if seg[2] then
      vim.api.nvim_buf_add_highlight(state.status_bufnr, status_ns, seg[2], 0, col, col + #seg[1])
    end
    col = col + #seg[1]
  end
end

local function stop_status_timer()
  if state.status_timer then
    state.status_timer:stop()
    state.status_timer:close()
    state.status_timer = nil
  end
end

local function start_status_timer()
  if state.status_timer then return end
  state.status_timer = vim.uv.new_timer()
  state.status_timer:start(0, 1000, vim.schedule_wrap(function()
    if not state.status_winnr or not vim.api.nvim_win_is_valid(state.status_winnr) then
      stop_status_timer()
      return
    end
    refresh_status()
  end))
end

local function get_draft_text()
  if not state.bufnr or not vim.api.nvim_buf_is_valid(state.bufnr) then
    return nil
  end
  local lines = vim.api.nvim_buf_get_lines(state.bufnr, 0, -1, false)
  local text = vim.trim(table.concat(lines, "\n"))
  return text ~= "" and text or nil
end

local function update_ui()
  local whl_input = ""
  local whl_status = ""

  if state.queued then
    whl_input = "Normal:CCQueuedNormal,EndOfBuffer:CCQueuedNormal,WinSeparator:CCQueuedBorder"
    whl_status = "Normal:CCQueuedNormal,WinSeparator:CCQueuedBorder"
  end

  if state.winnr and vim.api.nvim_win_is_valid(state.winnr) then
    vim.wo[state.winnr].winhighlight = whl_input
  end
  if state.status_winnr and vim.api.nvim_win_is_valid(state.status_winnr) then
    vim.wo[state.status_winnr].winhighlight = whl_status
  end

  refresh_status()
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

local function clear_draft_buf()
  if state.bufnr and vim.api.nvim_buf_is_valid(state.bufnr) then
    state.suppress_unqueue = true
    vim.api.nvim_buf_set_lines(state.bufnr, 0, -1, false, {})
    state.suppress_unqueue = false
  end
  state.queued = false
  update_ui()
end

local function send()
  local text = get_draft_text()
  if not text then
    return
  end

  local cc = require("codecompanion")
  local chat = cc.buf_get_chat(state.chat_bufnr)
  if chat and not chat.current_request then
    clear_draft_buf()
    submit_to_chat(state.chat_bufnr, text)
  else
    state.queued = true
    state.suppress_unqueue = true
    update_ui()
    vim.schedule(function()
      state.suppress_unqueue = false
    end)
  end
end

local function toggle_fullscreen()
  if not state.winnr or not vim.api.nvim_win_is_valid(state.winnr) then
    return
  end

  if state.fullscreen then
    vim.api.nvim_win_set_height(state.winnr, 8)
  else
    vim.api.nvim_win_set_height(state.winnr, vim.o.lines)
  end
  state.fullscreen = not state.fullscreen
end

local function create_buf()
  local buf = vim.api.nvim_create_buf(false, true)
  vim.bo[buf].filetype = "codecompanion_input"
  vim.bo[buf].bufhidden = "hide"

  vim.keymap.set({ "n", "i" }, "<C-s>", send, { buffer = buf, desc = "Send/queue prompt" })
  vim.keymap.set({ "n", "i" }, "<C-g>", toggle_fullscreen, { buffer = buf, desc = "Toggle fullscreen" })

  vim.api.nvim_create_autocmd({ "TextChanged", "TextChangedI" }, {
    buffer = buf,
    callback = function()
      if state.suppress_unqueue then
        return
      end
      if state.queued then
        state.queued = false
        update_ui()
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

function M.on_chat_opened(chat_bufnr)
  if state.winnr and vim.api.nvim_win_is_valid(state.winnr) then
    return
  end

  if not state.bufnr or not vim.api.nvim_buf_is_valid(state.bufnr) then
    state.bufnr = create_buf()
  end
  if not state.status_bufnr or not vim.api.nvim_buf_is_valid(state.status_bufnr) then
    state.status_bufnr = create_status_buf()
  end

  state.chat_bufnr = chat_bufnr

  local chat_winnr = vim.fn.bufwinid(chat_bufnr)
  if chat_winnr == -1 then
    return
  end

  local prev_win = vim.api.nvim_get_current_win()
  vim.api.nvim_set_current_win(chat_winnr)

  vim.cmd("belowright split")
  local input_win = vim.api.nvim_get_current_win()
  vim.api.nvim_win_set_buf(input_win, state.bufnr)

  vim.cmd("belowright split")
  local status_win = vim.api.nvim_get_current_win()
  vim.api.nvim_win_set_buf(status_win, state.status_bufnr)

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

  state.winnr = input_win
  state.status_winnr = status_win
  update_ui()
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
  if state.chat_bufnr ~= chat_bufnr then
    return
  end
  state.fullscreen = false
  stop_status_timer()
  if state.status_winnr and vim.api.nvim_win_is_valid(state.status_winnr) then
    vim.api.nvim_win_close(state.status_winnr, true)
  end
  state.status_winnr = nil
  if state.winnr and vim.api.nvim_win_is_valid(state.winnr) then
    vim.api.nvim_win_close(state.winnr, true)
  end
  state.winnr = nil
end

function M.on_chat_done(chat_bufnr)
  if not state.queued or state.chat_bufnr ~= chat_bufnr then
    return
  end

  local text = get_draft_text()
  if not text then
    state.queued = false
    update_ui()
    return
  end

  clear_draft_buf()
  submit_to_chat(chat_bufnr, text)
end

function M.bufnr()
  return state.bufnr
end

function M.chat_bufnr()
  return state.chat_bufnr
end

function M.on_request_started(bufnr)
  if state.chat_bufnr ~= bufnr then
    return
  end
  state.request_start_at = os.time()
  refresh_status()
end

function M.on_request_finished(bufnr)
  if state.chat_bufnr ~= bufnr then
    return
  end
  state.request_start_at = nil
  refresh_status()
end

function M.focus()
  if state.winnr and vim.api.nvim_win_is_valid(state.winnr) then
    vim.api.nvim_set_current_win(state.winnr)
    vim.cmd("startinsert")
  else
    vim.notify("No CodeCompanion input box open", vim.log.levels.WARN)
  end
end

return M
