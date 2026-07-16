-- Shared durable-session resolver for CodeCompanion chats.
--
-- The lib ecosystem (winbar pill, stats/context-%, chatinfo, statusline, doctor)
-- historically keyed on ACP internals (`chat.acp_connection` /
-- `chat.acp_session_id`). Native omnigent chats expose their durable id on
-- `chat.omnigent_session` / `chat.omnigent_session_id` instead. This module is the
-- single place that maps a chat -> its durable session id regardless of family,
-- so those modules light up for omnigent without each re-implementing the lookup.
local M = {}

---Resolve the durable session id for a chat, across adapter families.
---@param chat table|nil
---@return string|nil
function M.session_id(chat)
  if not chat then
    return nil
  end
  local t = chat.adapter and chat.adapter.type
  if t == "omnigent" then
    return chat.omnigent_session_id or (chat.omnigent_session and chat.omnigent_session.session_id)
  end
  return chat.acp_session_id or (chat.acp_connection and chat.acp_connection.session_id)
end

---The sessionful family of a chat: "omnigent" | "acp" | nil (e.g. plain http).
---@param chat table|nil
---@return string|nil
function M.kind(chat)
  if not chat or not chat.adapter then
    return nil
  end
  local t = chat.adapter.type
  if t == "omnigent" or t == "acp" then
    return t
  end
  return nil
end

return M
