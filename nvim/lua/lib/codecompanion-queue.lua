local M = {}

local ns = vim.api.nvim_create_namespace("codecompanion_input_status")

local sep = "%#Comment# · %*"

local state = {
  bufnr = nil,
  winnr = nil,
  chat_bufnr = nil,
  queued = false,
  suppress_unqueue = false,
  fullscreen = false,
}

local function fmt_tokens(n)
  if n >= 1000 then
    return string.format("%.1fk", n / 1000)
  end
  return tostring(n)
end

vim.api.nvim_create_autocmd("WinNew", {
  group = vim.api.nvim_create_augroup("codecompanion_queue_redirect", { clear = true }),
  callback = function()
    if not state.winnr or not vim.api.nvim_win_is_valid(state.winnr) then
      return
    end
    if vim.fn.win_getid(vim.fn.winnr("#")) ~= state.winnr then
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
      vim.api.nvim_win_close(new_win, false)
      vim.api.nvim_set_current_win(chat_win)
      vim.cmd("vertical rightbelow split")
      vim.api.nvim_win_set_buf(vim.api.nvim_get_current_win(), buf)
    end)
  end,
})

function _G._codecompanion_input_statusline()
  if not state.chat_bufnr then
    return " "
  end

  local meta = (_G.codecompanion_chat_metadata or {})[state.chat_bufnr]
  if not meta then
    return " "
  end

  local chat = require("codecompanion").buf_get_chat(state.chat_bufnr)
  local adapter_type = chat and chat.adapter and chat.adapter.type

  -- Left side: adapter · model · turn N
  local left = " %#Function#" .. (meta.adapter.name or "unknown") .. "%*"
  if meta.adapter.model then
    left = left .. sep .. "%#String#" .. meta.adapter.model .. "%*"
  end
  if meta.cycles and meta.cycles > 0 then
    left = left .. sep .. "%#Number#turn " .. meta.cycles .. "%*"
  end

  -- Right side: session · mode · tools · ctx items · tokens
  local right_parts = {}
  if adapter_type == "acp" and chat.acp_connection and chat.acp_connection.session_id then
    right_parts[#right_parts + 1] = "%#Constant#" .. chat.acp_connection.session_id .. "%*"
  end
  if meta.mode and meta.mode.name then
    right_parts[#right_parts + 1] = "%#String#" .. meta.mode.name .. "%*"
  end
  if meta.tools and meta.tools > 0 then
    right_parts[#right_parts + 1] = "%#DiagnosticInfo#" .. meta.tools .. " tools%*"
  end
  if meta.context_items and meta.context_items > 0 then
    right_parts[#right_parts + 1] = "%#DiagnosticInfo#" .. meta.context_items .. " ctx%*"
  end
  if adapter_type == "http" and meta.tokens and meta.tokens > 0 then
    right_parts[#right_parts + 1] = "%#DiagnosticInfo#" .. fmt_tokens(meta.tokens) .. " tokens%*"
  end

  if #right_parts > 0 then
    return left .. "%=" .. table.concat(right_parts, sep) .. " "
  end
  return left
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
  if not state.bufnr or not vim.api.nvim_buf_is_valid(state.bufnr) then
    return
  end
  vim.api.nvim_buf_clear_namespace(state.bufnr, ns, 0, -1)
  local label = state.queued and { " Queued ", "DiagnosticWarn" } or { " Draft ", "Comment" }
  vim.api.nvim_buf_set_extmark(state.bufnr, ns, 0, 0, {
    virt_text = { label },
    virt_text_pos = "right_align",
  })
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

function M.on_chat_opened(chat_bufnr)
  if state.winnr and vim.api.nvim_win_is_valid(state.winnr) then
    return
  end

  if not state.bufnr or not vim.api.nvim_buf_is_valid(state.bufnr) then
    state.bufnr = create_buf()
  end

  state.chat_bufnr = chat_bufnr

  local chat_winnr = vim.fn.bufwinid(chat_bufnr)
  if chat_winnr == -1 then
    return
  end

  local prev_win = vim.api.nvim_get_current_win()
  vim.api.nvim_set_current_win(chat_winnr)
  vim.cmd("belowright split")
  vim.cmd("resize 8")

  local win = vim.api.nvim_get_current_win()
  vim.api.nvim_win_set_buf(win, state.bufnr)

  vim.wo[win].number = false
  vim.wo[win].relativenumber = false
  vim.wo[win].signcolumn = "no"
  vim.wo[win].winfixheight = true
  vim.wo[win].statusline = " "
  vim.wo[win].winbar = "%!v:lua._codecompanion_input_statusline()"
  vim.wo[win].wrap = true
  vim.wo[win].linebreak = true

  state.winnr = win
  update_ui()

  -- HACK: On first open, focus the input box. On subsequent opens (toggle
  -- cycles), restore focus to wherever the user was — CodeCompanion's toggle
  -- already handles focus for the chat pane.
  if prev_win == chat_winnr then
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

function M.focus()
  if state.winnr and vim.api.nvim_win_is_valid(state.winnr) then
    vim.api.nvim_set_current_win(state.winnr)
    vim.cmd("startinsert")
  else
    vim.notify("No CodeCompanion input box open", vim.log.levels.WARN)
  end
end

return M
