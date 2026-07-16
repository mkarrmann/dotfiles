-- Reap the ACP connection (broker client connection -> per-session agent +
-- its MCP fleet) whenever a CodeCompanion chat is closed by ANY means, not
-- just the built-in `<C-c>` close action or quitting nvim.
--
-- Background. CodeCompanion only calls `acp_connection:disconnect()` (which
-- SIGKILLs the `acp-broker-attach-tag` bridge, EOFing the broker connection
-- and triggering the broker's reap-on-session-end) from two places:
-- `Chat:close()` (the `<C-c>` action) and a `VimLeavePre` autocmd. The chat
-- buffer sets no `bufhidden`, so closing its window/tab (`:tabclose`,
-- `:close`, `<C-w>c`) leaves the buffer loaded-but-hidden and never fires
-- `BufUnload` -- so the agent + ~5GB MCP fleet leak until nvim exits.
--
-- This module closes that gap:
--   * BufUnload/BufWipeout (`:bd`/`:bw`, and nvim exit) -> disconnect that chat.
--   * WinClosed (`:tabclose`/window close) -> after the window is gone,
--     disconnect any ACP chat whose buffer is no longer displayed in ANY
--     window (across all tabs). A chat still shown in another tab survives.
--
-- Note: this treats "not visible in any window" as "closed", so hiding or
-- toggling a chat away also reaps it. That matches the intent of always
-- reaping on close; if you want hide-to-keep-alive, drop the WinClosed hook.

local M = {}

---Disconnect/tear down the session backing `chat`, if any. Idempotent: CC's
---`disconnect()` is `assert(handle):kill(9)`, so guard with pcall against
---double-calls (e.g. WinClosed racing VimLeavePre). Omnigent has no local agent
---process -- only the SSE stream job -- so reaping it just stops that stream (the
---durable server session lives on).
---@param chat table|nil
local function disconnect(chat)
  if not (chat and chat.adapter) then
    return
  end
  if chat.adapter.type == "acp" and chat.acp_connection then
    pcall(function()
      chat.acp_connection:disconnect()
    end)
  elseif chat.adapter.type == "omnigent" and chat.omnigent_session then
    pcall(function()
      chat.omnigent_session:stop_stream()
    end)
  end
end

---Reap every hidden chat whose buffer is no longer displayed in any window.
---Omnigent chats are intentionally EXEMPT here: with background_updates on, a
---hidden omnigent chat keeps its stream so wakeups still render (and toast) while
---you're looking elsewhere -- reaping on hide would defeat that. Omnigent streams
---are reaped only on real buffer teardown (BufUnload/BufWipeout) and Chat:close.
local function reap_hidden_chats()
  local ok, cc = pcall(require, "codecompanion")
  if not ok then
    return
  end
  for _, entry in ipairs(cc.buf_get_chat() or {}) do
    local chat = entry.chat
    if chat and chat.bufnr and vim.api.nvim_buf_is_valid(chat.bufnr) then
      if chat.adapter and chat.adapter.type ~= "omnigent" and #vim.fn.win_findbuf(chat.bufnr) == 0 then
        disconnect(chat)
      end
    end
  end
end

function M.setup()
  local group = vim.api.nvim_create_augroup("cc_acp_reap_on_close", { clear = true })

  -- Explicit buffer teardown (`:bd`/`:bw`) and nvim exit unload the buffer.
  vim.api.nvim_create_autocmd({ "BufUnload", "BufWipeout" }, {
    group = group,
    callback = function(args)
      local ok, cc = pcall(require, "codecompanion")
      if ok then
        disconnect(cc.buf_get_chat(args.buf))
      end
    end,
  })

  -- Window/tab close leaves the chat buffer hidden (no BufUnload). Sweep
  -- after the window is actually gone so win_findbuf reflects the new state.
  vim.api.nvim_create_autocmd("WinClosed", {
    group = group,
    callback = function()
      vim.schedule(reap_hidden_chats)
    end,
  })
end

return M
