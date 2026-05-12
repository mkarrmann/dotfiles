local M = {}

local state = {
  buf = nil,
  win = nil,
}

local function fmt_tok(n)
  if n >= 1000 then
    return string.format("%d.%dk", math.floor(n / 1000), math.floor((n % 1000) / 100))
  end
  return tostring(n)
end

local function close_panel()
  if state.win and vim.api.nvim_win_is_valid(state.win) then
    pcall(vim.api.nvim_win_close, state.win, true)
  end
  state.win = nil
end

local function current_snapshot()
  local ok_cc, cc = pcall(require, "codecompanion")
  local ok_q, queue = pcall(require, "lib.codecompanion-queue")
  local ok_s, stats = pcall(require, "lib.codecompanion-stats")
  if not (ok_cc and ok_q and ok_s) then
    return nil
  end

  local bufnr = queue.chat_bufnr()
  if not bufnr then
    return nil
  end

  local chat = cc.buf_get_chat(bufnr)
  if not chat then
    return nil
  end

  local sid = chat.acp_session_id or (chat.acp_connection and chat.acp_connection.session_id)
  if not sid then
    return nil
  end

  local usage = stats.get(sid)
  if not usage or not usage.size or usage.size == 0 then
    return {
      pct = nil,
      lines = {
        "CodeCompanion Context",
        "session: " .. sid,
        "usage: waiting for usage_update",
      },
    }
  end

  local pct = math.floor(100 * usage.used / usage.size)
  local cost = "n/a"
  if usage.cost and usage.cost.amount then
    cost = string.format("$%.4f", usage.cost.amount)
  end

  return {
    pct = pct,
    lines = {
      "CodeCompanion Context",
      "session: " .. sid,
      string.format("usage: %d%% (%s/%s)", pct, fmt_tok(usage.used), fmt_tok(usage.size)),
      "cost: " .. cost,
    },
  }
end

local function ensure_panel()
  if state.win and vim.api.nvim_win_is_valid(state.win) then
    if vim.api.nvim_win_get_tabpage(state.win) == vim.api.nvim_get_current_tabpage() then
      return state.win
    end
    pcall(vim.api.nvim_win_close, state.win, true)
    state.win = nil
  end

  if not (state.buf and vim.api.nvim_buf_is_valid(state.buf)) then
    state.buf = vim.api.nvim_create_buf(false, true)
    vim.bo[state.buf].buftype = "nofile"
    vim.bo[state.buf].bufhidden = "hide"
    vim.bo[state.buf].swapfile = false
    vim.bo[state.buf].modifiable = false
    vim.bo[state.buf].filetype = "codecompanion_status_panel"
  end

  vim.cmd("botright split")
  state.win = vim.api.nvim_get_current_win()
  vim.api.nvim_win_set_buf(state.win, state.buf)

  vim.wo[state.win].wrap = true
  vim.wo[state.win].linebreak = false
  vim.wo[state.win].number = false
  vim.wo[state.win].relativenumber = false
  vim.wo[state.win].signcolumn = "no"
  vim.wo[state.win].statusline = " "
  vim.wo[state.win].winfixheight = true
  vim.wo[state.win].spell = false

  return state.win
end

local function needed_height(lines, width)
  local total = 0
  local usable = math.max(1, width)
  for _, line in ipairs(lines) do
    local w = vim.fn.strdisplaywidth(line)
    total = total + math.max(1, math.ceil(w / usable))
  end
  return math.max(1, total)
end

function M.refresh()
  local snap = current_snapshot()
  if not snap then
    close_panel()
    return
  end

  local cur = vim.api.nvim_get_current_win()
  local win = ensure_panel()

  vim.bo[state.buf].modifiable = true
  vim.api.nvim_buf_set_lines(state.buf, 0, -1, false, snap.lines)
  vim.bo[state.buf].modifiable = false

  local h = needed_height(snap.lines, vim.api.nvim_win_get_width(win))
  vim.api.nvim_win_set_height(win, h)

  if vim.api.nvim_win_is_valid(cur) and cur ~= win then
    vim.api.nvim_set_current_win(cur)
  end
end

function M.setup()
  local group = vim.api.nvim_create_augroup("codecompanion_status_panel", { clear = true })

  vim.api.nvim_create_autocmd("User", {
    group = group,
    pattern = {
      "CodeCompanionACPSessionUpdate",
      "CodeCompanionChatOpened",
      "CodeCompanionChatHidden",
      "CodeCompanionChatDone",
    },
    callback = function()
      vim.schedule(M.refresh)
    end,
  })

  vim.api.nvim_create_autocmd({ "VimResized", "WinResized", "TabEnter" }, {
    group = group,
    callback = function()
      vim.schedule(M.refresh)
    end,
  })

  vim.api.nvim_create_autocmd("WinClosed", {
    group = group,
    callback = function(args)
      if state.win and tonumber(args.match) == state.win then
        state.win = nil
      end
    end,
  })
end

return M
