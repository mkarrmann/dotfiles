local M = {}

local draft = {}

local function get_draft_text()
  if not draft.bufnr or not vim.api.nvim_buf_is_valid(draft.bufnr) then
    return nil
  end
  local lines = vim.api.nvim_buf_get_lines(draft.bufnr, 0, -1, false)
  local text = vim.trim(table.concat(lines, "\n"))
  return text ~= "" and text or nil
end

local function update_ui()
  if not draft.winnr or not vim.api.nvim_win_is_valid(draft.winnr) then
    return
  end
  local title = draft.queued and " Queued " or " Draft "
  vim.api.nvim_win_set_config(draft.winnr, {
    title = title,
    title_pos = "center",
    border = draft.queued and "double" or "rounded",
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
  if draft.bufnr and vim.api.nvim_buf_is_valid(draft.bufnr) then
    draft.suppress_unqueue = true
    vim.api.nvim_buf_set_lines(draft.bufnr, 0, -1, false, {})
    draft.suppress_unqueue = false
  end
  draft.queued = false
  update_ui()
end

function M.on_chat_done(chat_bufnr)
  if not draft.queued or draft.chat_bufnr ~= chat_bufnr then
    return
  end

  local text = get_draft_text()
  if not text then
    draft.queued = false
    update_ui()
    return
  end

  clear_draft_buf()
  submit_to_chat(chat_bufnr, text)
end

function M.open_draft()
  local cc = require("codecompanion")
  local chat = cc.last_chat()
  if not chat then
    vim.notify("No active CodeCompanion chat", vim.log.levels.WARN)
    return
  end

  local chat_bufnr = chat.bufnr

  if draft.winnr and vim.api.nvim_win_is_valid(draft.winnr) then
    vim.api.nvim_set_current_win(draft.winnr)
    return
  end

  local width = math.floor(vim.o.columns * 0.4)
  local height = 8
  local row = vim.o.lines - height - 4
  local col = vim.o.columns - width - 2

  local buf = vim.api.nvim_create_buf(false, true)
  vim.bo[buf].filetype = "markdown"
  vim.bo[buf].bufhidden = "hide"

  local win = vim.api.nvim_open_win(buf, true, {
    relative = "editor",
    width = width,
    height = height,
    row = row,
    col = col,
    style = "minimal",
    border = "rounded",
    title = " Draft ",
    title_pos = "center",
  })

  draft = { bufnr = buf, winnr = win, chat_bufnr = chat_bufnr, queued = false, suppress_unqueue = false }

  local function send()
    local text = get_draft_text()
    if not text then
      return
    end

    local current_chat = cc.buf_get_chat(chat_bufnr)
    if current_chat and not current_chat.current_request then
      clear_draft_buf()
      submit_to_chat(chat_bufnr, text)
    else
      draft.queued = true
      update_ui()
    end
  end

  local function close()
    if vim.api.nvim_win_is_valid(win) then
      vim.api.nvim_win_close(win, true)
    end
  end

  vim.api.nvim_create_autocmd({ "TextChanged", "TextChangedI" }, {
    buffer = buf,
    callback = function()
      if draft.suppress_unqueue then
        return
      end
      if draft.queued then
        draft.queued = false
        update_ui()
      end
    end,
  })

  vim.keymap.set({ "n", "i" }, "<C-s>", send, { buffer = buf, desc = "Send/queue prompt" })
  vim.keymap.set("n", "q", close, { buffer = buf, desc = "Close draft" })
  vim.keymap.set("n", "<Esc>", close, { buffer = buf, desc = "Close draft" })

  vim.cmd("startinsert")
end

return M
