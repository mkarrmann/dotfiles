local M = {}

local LOG_PATH = vim.fn.expand("~/.local/state/nvim/codecompanion.log")
local PROC_PATTERNS = {
  "claude-agent-acp/dist/index.js",
  "claude_code/.*stream-json",
}

local function shell_lines(cmd)
  local out = vim.fn.systemlist(cmd)
  if vim.v.shell_error ~= 0 then
    return {}
  end
  return out
end

local function find_processes()
  local pids = {}
  local seen = {}
  for _, pattern in ipairs(PROC_PATTERNS) do
    for _, raw in ipairs(shell_lines({ "pgrep", "-f", pattern })) do
      local pid = tonumber(raw)
      if pid and not seen[pid] then
        seen[pid] = true
        pids[#pids + 1] = pid
      end
    end
  end
  return pids
end

local function describe_pid(pid)
  local lines = {}
  local ps = shell_lines({ "ps", "-o", "pid,ppid,etime,command", "-p", tostring(pid) })
  for _, l in ipairs(ps) do
    lines[#lines + 1] = l
  end
  local listen = shell_lines({ "lsof", "-p", tostring(pid), "-iTCP", "-sTCP:LISTEN", "-P", "-n" })
  if #listen > 1 then
    lines[#lines + 1] = "  LISTEN:"
    for i = 2, #listen do
      lines[#lines + 1] = "    " .. listen[i]
    end
  end
  return lines
end

local function tail_log(n)
  if vim.fn.filereadable(LOG_PATH) == 0 then
    return { "(no log at " .. LOG_PATH .. ")" }
  end
  local pattern = "/^%[ERROR%]|^%[WARN%]|Internal error|Process exited|401|timeout|hang/"
  local cmd = string.format("awk '%s' %s | tail -n %d", pattern:gsub("'", "'\\''"), vim.fn.shellescape(LOG_PATH), n)
  local out = vim.fn.systemlist(cmd)
  if #out == 0 then
    return { "(no matching entries in log)" }
  end
  return out
end

local function chat_state()
  local queue = require("lib.codecompanion-queue")
  local chat_bufnr = queue.chat_bufnr()
  local lines = {}
  if not chat_bufnr then
    lines[#lines + 1] = "(no active chat buffer)"
    return lines
  end
  local chat = require("codecompanion").buf_get_chat(chat_bufnr)
  if not chat then
    lines[#lines + 1] = "chat_bufnr=" .. chat_bufnr .. " (buf_get_chat returned nil)"
    return lines
  end
  lines[#lines + 1] = "chat_bufnr=" .. chat_bufnr
  lines[#lines + 1] = "adapter=" .. (chat.adapter and chat.adapter.name or "?")
  lines[#lines + 1] = "type=" .. (chat.adapter and chat.adapter.type or "?")
  lines[#lines + 1] = "current_request=" .. tostring(chat.current_request ~= nil)
  if chat.acp_connection and chat.acp_connection.session_id then
    lines[#lines + 1] = "acp.session_id=" .. chat.acp_connection.session_id
  end
  return lines
end

function M.run()
  local out = {}

  local function section(title, body)
    out[#out + 1] = "## " .. title
    out[#out + 1] = ""
    if type(body) == "string" then
      out[#out + 1] = body
    else
      for _, line in ipairs(body) do
        out[#out + 1] = line
      end
    end
    out[#out + 1] = ""
  end

  out[#out + 1] = "# CodeCompanion Doctor"
  out[#out + 1] = "Generated " .. os.date("%Y-%m-%d %H:%M:%S")
  out[#out + 1] = ""

  section("Chat state", chat_state())

  local pids = find_processes()
  if #pids == 0 then
    section("Processes", "(no claude-agent-acp or claude_code processes running)")
  else
    local body = {}
    for _, pid in ipairs(pids) do
      vim.list_extend(body, describe_pid(pid))
      body[#body + 1] = ""
    end
    section("Processes (" .. #pids .. ")", body)
  end

  section("Recent log entries", tail_log(50))

  out[#out + 1] = "## Cleanup commands"
  out[#out + 1] = ""
  out[#out + 1] = "    pkill -f claude-agent-acp/dist/index.js"
  out[#out + 1] = "    pkill -f 'claude_code/.*stream-json'"

  local buf = vim.api.nvim_create_buf(false, true)
  vim.bo[buf].buftype = "nofile"
  vim.bo[buf].bufhidden = "wipe"
  vim.bo[buf].filetype = "markdown"
  vim.api.nvim_buf_set_lines(buf, 0, -1, false, out)
  pcall(vim.api.nvim_buf_set_name, buf, "[CodeCompanion Doctor]")
  vim.cmd("vsplit")
  vim.api.nvim_win_set_buf(0, buf)
end

local function descendants(parent)
  local result = {}
  for _, raw in ipairs(shell_lines({ "pgrep", "-P", tostring(parent) })) do
    local pid = tonumber(raw)
    if pid then
      result[#result + 1] = pid
      vim.list_extend(result, descendants(pid))
    end
  end
  return result
end

function M.cleanup_orphans()
  for _, pid in ipairs(descendants(vim.fn.getpid())) do
    local cmd = (shell_lines({ "ps", "-o", "command=", "-p", tostring(pid) })[1]) or ""
    if cmd:find("claude%-agent%-acp") or cmd:find("claude_code") then
      vim.fn.system({ "kill", tostring(pid) })
    end
  end
end

return M
