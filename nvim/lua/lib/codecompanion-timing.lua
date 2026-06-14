-- Per-message timestamps + per-prompt round-trip timing for CodeCompanion
-- chat turns.
--
-- Each chat turn produces two header "sections" in the buffer: a user
-- header ("## Me") and an LLM header ("## CodeCompanion (<adapter>)").
-- We stamp a right-aligned virtual-text label on each header:
--   * user header -> the wall-clock time the prompt was sent (HH:MM:SS),
--     captured at RequestStarted.
--   * LLM header  -> the wall-clock time the response began (HH:MM:SS),
--     captured at the first RequestStreaming chunk, plus the round-trip
--     duration appended at RequestFinished ("14:32:05 · 1.23s").
--
-- Persistence across re-renders. CodeCompanion rebuilds the whole chat
-- buffer (`set_lines(0,-1)`) on compaction, session restore, etc., which
-- deletes every extmark and then re-runs `UI:render_headers`. So instead
-- of pinning to a line number once, we keep an ordered list of section
-- timestamps per buffer (`M._sections[bufnr]`) and RE-DERIVE the header
-- lines on demand in `M.reapply`, which the `UI:render_headers` monkey-
-- patch (plugins/codecompanion.lua) calls after every render. Sections are
-- matched to headers BOTTOM-UP: the last `#sections` headers in the buffer
-- map to our sections in order. Bottom alignment means restored history
-- (which we have no timestamps for) sits untimed at the top while freshly
-- timed turns line up against the most recent headers — no index drift.
--
-- Header text is matched against the exact configured role strings (not a
-- bare "^## ") so markdown "## ..." headings inside an assistant response
-- are never mistaken for message headers.
--
-- Uses a private namespace -- NS_VIRTUAL_TEXT is unsafe because CC clears
-- it on the first InsertEnter (see ui/init.lua:92).

local M = {}

local NS = vim.api.nvim_create_namespace("acp_broker_cc_timing")

-- _pending[bufnr] = { id, start_ns, streamed = bool, llm_index = number? }
-- Tracks the in-flight request so RequestFinished can attach a duration to
-- the right LLM section. Only one in-flight prompt per chat buffer.
M._pending = {}

-- _sections[bufnr] = { { kind = "user"|"llm", time = <os.time()>,
--                        duration_ns = number?, status = string? }, ... }
-- Chronological; one entry per rendered header. See bottom-up mapping above.
M._sections = {}

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

-- Resolve the exact "## Me" / "## CodeCompanion (...)" header strings for
-- this chat from its configured roles, mirroring ui/init.lua:set_llm_role.
local function role_headers(chat)
  local ui = chat and chat.ui
  if not ui or not ui.roles then return nil, nil end
  local user = ui.roles.user
  local llm = ui.roles.llm
  if type(llm) == "function" then
    local ok, resolved = pcall(llm, ui.adapter)
    llm = ok and resolved or nil
  end
  return user, llm
end

local function build_label(sec)
  local chunks = { { os.date("%H:%M:%S", sec.time), "Comment" } }
  if sec.kind == "llm" and sec.duration_ns then
    local hl = (sec.status == "error" and "DiagnosticError")
      or (sec.status == "cancelled" and "DiagnosticWarn")
      or "Comment"
    local extra = " · " .. fmt_elapsed(sec.duration_ns)
    if sec.status and sec.status ~= "success" then
      extra = extra .. ", " .. tostring(sec.status)
    end
    chunks[#chunks + 1] = { extra, hl }
  end
  return chunks
end

-- Re-derive header lines from the buffer and pin each section's label.
-- Safe to call any time; clears and rebuilds our namespace each pass.
function M.reapply(bufnr)
  if not bufnr or not vim.api.nvim_buf_is_valid(bufnr) then return end
  pcall(vim.api.nvim_buf_clear_namespace, bufnr, NS, 0, -1)

  local sections = M._sections[bufnr]
  if not sections or #sections == 0 then return end

  local chat = get_chat(bufnr)
  local user_role, llm_role = role_headers(chat)
  if not user_role and not llm_role then return end
  local pat_user = user_role and ("^## " .. vim.pesc(user_role))
  local pat_llm = llm_role and ("^## " .. vim.pesc(llm_role))

  local lines = vim.api.nvim_buf_get_lines(bufnr, 0, -1, false)
  local headers = {}
  for i, content in ipairs(lines) do
    if (pat_user and content:match(pat_user)) or (pat_llm and content:match(pat_llm)) then
      headers[#headers + 1] = i - 1 -- 0-based
    end
  end

  -- Bottom-up: the last #sections headers correspond to our sections.
  local offset = #headers - #sections
  for si, sec in ipairs(sections) do
    local hi = offset + si
    if hi >= 1 and hi <= #headers and sec.time then
      pcall(vim.api.nvim_buf_set_extmark, bufnr, NS, headers[hi], 0, {
        virt_text = build_label(sec),
        virt_text_pos = "right_align",
        priority = 200,
        hl_mode = "combine",
      })
    end
  end
end

local function push_section(bufnr, kind)
  local secs = M._sections[bufnr]
  if not secs then
    secs = {}
    M._sections[bufnr] = secs
  end
  secs[#secs + 1] = { kind = kind, time = os.time() }
  return #secs
end

function M.on_started(data)
  if not data or data.interaction ~= "chat" then return end
  local bufnr = data.bufnr
  if not bufnr or not vim.api.nvim_buf_is_valid(bufnr) then return end
  M._pending[bufnr] = { id = data.id, start_ns = vim.uv.hrtime(), streamed = false }
  push_section(bufnr, "user")
  M.reapply(bufnr)
end

function M.on_streaming(data)
  if not data or data.interaction ~= "chat" then return end
  local bufnr = data.bufnr
  local entry = bufnr and M._pending[bufnr]
  if not entry or entry.id ~= data.id then return end
  if entry.streamed then return end
  entry.streamed = true
  entry.llm_index = push_section(bufnr, "llm")
  M.reapply(bufnr)
end

function M.on_finished(data)
  if not data or data.interaction ~= "chat" then return end
  local bufnr = data.bufnr
  if not bufnr then return end
  local entry = M._pending[bufnr]
  M._pending[bufnr] = nil
  if not entry or entry.id ~= data.id then return end

  local secs = M._sections[bufnr]
  if secs and entry.llm_index and secs[entry.llm_index] then
    secs[entry.llm_index].duration_ns = vim.uv.hrtime() - entry.start_ns
    secs[entry.llm_index].status = data.status
  end
  M.reapply(bufnr)
end

-- Drop all timing state for a buffer. Call on chat close and whenever the
-- transcript is wiped (adapter swap with clear) so stale section times
-- don't bottom-align onto a fresh, empty chat.
function M.reset(bufnr)
  if not bufnr then return end
  M._pending[bufnr] = nil
  M._sections[bufnr] = nil
  if vim.api.nvim_buf_is_valid(bufnr) then
    pcall(vim.api.nvim_buf_clear_namespace, bufnr, NS, 0, -1)
  end
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
  vim.api.nvim_create_autocmd("User", {
    pattern = "CodeCompanionChatClosed",
    callback = function(args)
      local bufnr = args.data and args.data.bufnr
      if bufnr then M.reset(bufnr) end
    end,
  })
end

return M
