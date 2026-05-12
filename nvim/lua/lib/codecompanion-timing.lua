-- Per-prompt round-trip timing for CodeCompanion chat turns.
--
-- Subscribes to CodeCompanionRequestStarted / Streaming / Finished:
--  * Started   -> stamp start_ns keyed by bufnr+id
--  * Streaming -> snapshot the assistant header line (it's only written on
--                 the first chunk, via Builder:_should_add_header)
--  * Finished  -> compute elapsed and pin an eol virt-text "(1.23s)" on
--                 the header line via our own namespace
--
-- Header line source: chat.builder.state.current_section_start (0-based),
-- updated in builder.lua:302-306 each time a new role-transition header
-- is rendered. Falls back to scanning for the last "^## <llm_role>" line.
--
-- Uses a private namespace -- NS_VIRTUAL_TEXT is unsafe because CC clears
-- it on the first InsertEnter (see ui/init.lua:92).

local M = {}

local NS = vim.api.nvim_create_namespace("acp_broker_cc_timing")

-- _pending[bufnr] = { id = <request id>, start_ns = <hrtime>, header_line = <0-based|nil> }
-- Only one in-flight prompt per chat buffer, so bufnr is a sufficient key.
M._pending = {}

local function fmt_elapsed(ns)
  local seconds = ns / 1e9
  if seconds < 60 then
    return string.format("%.2fs", seconds)
  end
  local mins = math.floor(seconds / 60)
  local secs = seconds - mins * 60
  return string.format("%dm%05.2fs", mins, secs)
end

local function get_chat(bufnr)
  local ok, cc = pcall(require, "codecompanion")
  if not ok then return nil end
  local ok2, chat = pcall(cc.buf_get_chat, bufnr)
  if not ok2 then return nil end
  return chat
end

local function llm_header_line(bufnr, chat)
  if chat and chat.builder and chat.builder.state then
    local line = chat.builder.state.current_section_start
    if type(line) == "number" and line >= 0 then
      return line
    end
  end
  if not vim.api.nvim_buf_is_valid(bufnr) then return nil end
  local total = vim.api.nvim_buf_line_count(bufnr)
  for i = total - 1, 0, -1 do
    local lines = vim.api.nvim_buf_get_lines(bufnr, i, i + 1, false)
    if lines[1] and lines[1]:match("^## ") then
      return i
    end
  end
  return nil
end

function M.on_started(data)
  if not data or data.interaction ~= "chat" then return end
  local bufnr = data.bufnr
  if not bufnr or not vim.api.nvim_buf_is_valid(bufnr) then return end
  M._pending[bufnr] = { id = data.id, start_ns = vim.uv.hrtime() }
end

function M.on_streaming(data)
  if not data or data.interaction ~= "chat" then return end
  local bufnr = data.bufnr
  local entry = bufnr and M._pending[bufnr]
  if not entry or entry.id ~= data.id then return end
  local chat = get_chat(bufnr)
  entry.header_line = llm_header_line(bufnr, chat)
end

function M.on_finished(data)
  if not data or data.interaction ~= "chat" then return end
  local bufnr = data.bufnr
  if not bufnr then return end
  local entry = M._pending[bufnr]
  M._pending[bufnr] = nil
  if not entry or entry.id ~= data.id then return end
  if not vim.api.nvim_buf_is_valid(bufnr) then return end

  local elapsed = vim.uv.hrtime() - entry.start_ns
  local line = entry.header_line or llm_header_line(bufnr, get_chat(bufnr))
  if not line then return end

  local label = " (" .. fmt_elapsed(elapsed)
  if data.status and data.status ~= "success" then
    label = label .. ", " .. tostring(data.status)
  end
  label = label .. ")"

  local hl = (data.status == "error" and "DiagnosticError")
    or (data.status == "cancelled" and "DiagnosticWarn")
    or "Comment"

  -- right_align + priority>100 to draw above CC's render_headers separator
  -- extmark (ui/init.lua:443, virt_text fills vim.go.columns with `─` at
  -- priority=100). With virt_text_pos="eol" the label rendered behind the
  -- separator and was invisible.
  pcall(vim.api.nvim_buf_set_extmark, bufnr, NS, line, 0, {
    virt_text = { { label, hl } },
    virt_text_pos = "right_align",
    priority = 200,
    hl_mode = "combine",
  })
end

function M.setup()
  vim.api.nvim_create_autocmd("User", {
    pattern = "CodeCompanionRequestStarted",
    callback = function(args) M.on_started(args.data or {}) end,
  })
  vim.api.nvim_create_autocmd("User", {
    pattern = "CodeCompanionRequestStreaming",
    callback = function(args) M.on_streaming(args.data or {}) end,
  })
  vim.api.nvim_create_autocmd("User", {
    pattern = "CodeCompanionRequestFinished",
    callback = function(args) M.on_finished(args.data or {}) end,
  })
end

return M
