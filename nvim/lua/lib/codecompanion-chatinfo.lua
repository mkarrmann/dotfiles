-- Stable, pinned ACP session id for a CodeCompanion chat, rendered in the
-- chat window's winbar.
--
-- The live `chat.acp_connection.session_id` is volatile: it is nilled when
-- the agent process exits and silently re-minted on the next turn (see the
-- ensure_session HACK in plugins/codecompanion.lua), and resume/fork mint
-- fresh ids. The status bar reads that live value, so the displayed id
-- changes out from under you on every disconnect.
--
-- This module pins the FIRST session id established for a chat buffer and
-- never overwrites it for that buffer's life, so the id you see is a stable
-- handle (e.g. for `session/load`-by-bsid lookups). It is shown in the chat
-- window's winbar — always visible, in the chat, and not scrolled away.
--
-- The pin is intentionally reset only when the transcript itself is torn
-- down or replaced: chat close, or an in-place adapter swap with clear
-- (tab_chat_set_adapter). A deliberate resume/fork opens a NEW chat buffer,
-- which naturally pins to its loaded session on first establish.

local M = { _pinned = {} }

-- Pin `sid` for `bufnr` if nothing is pinned yet. No-op on empty/nil sid
-- or when already pinned (first-write-wins).
function M.pin(bufnr, sid)
  if not bufnr or type(sid) ~= "string" or sid == "" then return end
  if M._pinned[bufnr] == nil then
    M._pinned[bufnr] = sid
    -- A late first-establish happens after the window (and its winbar
    -- expression) already rendered "pending"; nudge a redraw.
    pcall(vim.cmd.redrawstatus)
  end
end

function M.get(bufnr)
  return bufnr and M._pinned[bufnr] or nil
end

function M.reset(bufnr)
  if bufnr then M._pinned[bufnr] = nil end
end

-- Winbar expression target. Evaluated per-window per-redraw via
-- `winbar = "%{%v:lua.require('lib.codecompanion-chatinfo').winbar()%}"`.
-- Returns a statusline string (highlight items allowed because of the
-- %{%...%} wrapper). Empty for non-chat windows.
function M.winbar()
  local bufnr = vim.api.nvim_get_current_buf()
  if vim.bo[bufnr].filetype ~= "codecompanion" then return "" end

  local sid = M._pinned[bufnr]
  if not sid then
    return "%#Comment# session: (pending) %*"
  end

  local label = "%#Constant# session: " .. sid .. " %*"

  -- Surface involuntary reminting: if the live session has drifted from the
  -- pinned handle, the agent no longer holds the original context.
  local ok, cc = pcall(require, "codecompanion")
  if ok then
    local chat = cc.buf_get_chat(bufnr)
    local live = chat and chat.acp_connection and chat.acp_connection.session_id
    if type(live) == "string" and live ~= "" and live ~= sid then
      label = label .. "%#DiagnosticWarn# ≠ live: " .. live .. " %*"
    end
  end

  return label
end

function M.setup()
  local grp = vim.api.nvim_create_augroup("codecompanion_chatinfo", { clear = true })

  -- Attach the winbar to any window showing a chat buffer. BufWinEnter +
  -- FileType together cover first open, toggle re-show, and tab moves.
  vim.api.nvim_create_autocmd({ "BufWinEnter", "FileType" }, {
    group = grp,
    callback = function(args)
      local buf = args.buf
      if not (buf and vim.api.nvim_buf_is_valid(buf)) then return end
      if vim.bo[buf].filetype ~= "codecompanion" then return end
      local win = vim.fn.bufwinid(buf)
      if win == -1 then return end
      vim.wo[win].winbar = "%{%v:lua.require('lib.codecompanion-chatinfo').winbar()%}"
    end,
  })

  vim.api.nvim_create_autocmd("User", {
    group = grp,
    pattern = "CodeCompanionChatClosed",
    callback = function(args)
      local bufnr = args.data and args.data.bufnr
      if bufnr then M.reset(bufnr) end
    end,
  })
end

return M
