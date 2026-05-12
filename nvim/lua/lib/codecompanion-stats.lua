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

function M.get(session_id)
  if not session_id then return nil end
  return M._by_session[session_id]
end

function M.context_pct(session_id)
  local s = M.get(session_id)
  if not s or not s.size or s.size == 0 then return nil end
  return math.floor(100 * s.used / s.size)
end

return M
