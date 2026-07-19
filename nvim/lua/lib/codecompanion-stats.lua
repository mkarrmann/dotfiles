-- Per-session ACP usage stats consumed by the lualine context-% component.
--
-- Subscribes to the User CodeCompanionACPSessionUpdate autocmd fired from
-- the PromptBuilder monkey-patch in plugins/codecompanion.lua, filters to
-- usage_update payloads, and caches the latest {used, size, cost} keyed
-- by ACP session_id. Lualine reads via M.get(session_id).
--
-- The usage_update wire shape comes from
-- @agentclientprotocol/sdk: { used: number, size: number, cost?: { amount, currency } }.
-- See dvsc-core-acp/packages/acp-wrapper/src/agent.ts #emitUsageUpdate
-- for the producing side.

local M = { _by_session = {} }

vim.api.nvim_create_autocmd("User", {
  pattern = "CodeCompanionACPSessionUpdate",
  callback = function(args)
    local data = args.data or {}
    local update = data.update
    local sid = data.session_id
    if not sid or not update or update.sessionUpdate ~= "usage_update" then
      return
    end
    M._by_session[sid] = {
      used = update.used,
      size = update.size,
      cost = update.cost,
    }
  end,
})

-- Native omnigent usage. The omnigent handler/observer fire this with a
-- {context_tokens, context_window, total_cost_usd} usage table (context_window
-- back-filled from the session snapshot when the SSE usage event nulls it). Map
-- context_tokens -> used (updates each turn) and context_window -> size (sticky:
-- a later null must not clear a size we already learned).
vim.api.nvim_create_autocmd("User", {
  pattern = "CodeCompanionOmnigentUsage",
  callback = function(args)
    local d = args.data or {}
    local sid = d.session_id
    local usage = d.usage
    if not sid or type(usage) ~= "table" then
      return
    end
    local cur = M._by_session[sid] or {}
    if usage.context_tokens then
      cur.used = usage.context_tokens
    end
    if usage.context_window then
      cur.size = usage.context_window
    end
    if usage.total_cost_usd then
      cur.cost = { amount = usage.total_cost_usd, currency = "USD" }
    end
    M._by_session[sid] = cur
  end,
})

function M.get(session_id)
  if not session_id then return nil end
  return M._by_session[session_id]
end

-- Evict the cache entry for a closed chat. The CodeCompanionChatClosed
-- event fires before chat.acp_connection is torn down, so we can still
-- resolve session_id from the chat object.
vim.api.nvim_create_autocmd("User", {
  pattern = "CodeCompanionChatClosed",
  callback = function(args)
    local bufnr = args.data and args.data.bufnr
    if not bufnr then return end
    local ok, chat = pcall(function() return require("codecompanion").buf_get_chat(bufnr) end)
    if not ok or not chat then return end
    local sid = require("lib.codecompanion-session").session_id(chat)
    if sid then M._by_session[sid] = nil end
  end,
})

function M.context_pct(session_id)
  local s = M.get(session_id)
  if not s or not s.size or s.size == 0 then return nil end
  return math.floor(100 * s.used / s.size)
end

return M
