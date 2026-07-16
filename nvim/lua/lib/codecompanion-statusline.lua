-- Status-line rendering for the per-tab CodeCompanion queue UI.
--
-- Extracted from codecompanion-queue.lua so the queue module can focus
-- on submit/queue/lifecycle logic. The status line is a single nvim
-- buffer rendered into a 1-row (auto-growing to ≤6) window below the
-- input box. Content is rebuilt on demand by `refresh(state)` and on a
-- shared 1s tick managed by `start(state)` / `stop(state)`.
--
-- A `state` table is passed by the caller; the fields this module
-- reads/writes are:
--   state.status_bufnr  -- buffer that holds the rendered lines
--   state.status_winnr  -- window showing that buffer
--   state.chat_bufnr    -- chat we're describing
--   state.queued        -- bool, drives "Queued" vs "Draft"
--   state.request_start_at  -- os.time() of in-flight request, or nil
--   state.tick_active   -- internal, set by start()/stop()
--
-- The 1s tick is a single shared timer; any state that called start()
-- contributes a "wants ticking" flag and the timer stops itself when
-- no state wants it.

local M = {}

local ns = vim.api.nvim_create_namespace("codecompanion_status")
local STATUS_MAX_ROWS = 6

-- Set of states that want the periodic refresh. Keys are state tables.
local ticking = {}
local timer

-- ─── Highlights ──────────────────────────────────────────────────────────

local function setup_highlights()
  local normal_hl = vim.api.nvim_get_hl(0, { name = "Normal", link = false })
  local warn_hl = vim.api.nvim_get_hl(0, { name = "DiagnosticWarn", link = false })

  local bg = normal_hl.bg
  if bg == nil then
    bg = vim.o.background == "light" and 0xFFFFFF or 0x1E1E2E
  end
  local warn_fg = warn_hl.fg or 0xE0A500

  local function blend(c1, c2, alpha)
    local r1, g1, b1 = math.floor(c1 / 65536) % 256, math.floor(c1 / 256) % 256, c1 % 256
    local r2, g2, b2 = math.floor(c2 / 65536) % 256, math.floor(c2 / 256) % 256, c2 % 256
    return math.floor(r1 * alpha + r2 * (1 - alpha)) * 65536
      + math.floor(g1 * alpha + g2 * (1 - alpha)) * 256
      + math.floor(b1 * alpha + b2 * (1 - alpha))
  end

  vim.api.nvim_set_hl(0, "CCQueuedNormal", { bg = blend(warn_fg, bg, 0.1) })
  vim.api.nvim_set_hl(0, "CCQueuedBorder", { fg = warn_fg })
end

setup_highlights()
vim.api.nvim_create_autocmd("ColorScheme", {
  group = vim.api.nvim_create_augroup("codecompanion_statusline_highlights", { clear = true }),
  callback = setup_highlights,
})

-- ─── Segment building ────────────────────────────────────────────────────

local function fmt_tokens(n)
  if n >= 1000 then
    return string.format("%.1fk", n / 1000)
  end
  return tostring(n)
end

local function build_segments(state)
  local left, right = {}, {}
  if not state.chat_bufnr then return left, right end

  local meta = (_G.codecompanion_chat_metadata or {})[state.chat_bufnr] or {}

  local chat = require("codecompanion").buf_get_chat(state.chat_bufnr)
  local adapter_type = chat and chat.adapter and chat.adapter.type
  local dvsc_sel
  if chat and chat.adapter and chat.adapter.name == "dvsc_core_broker" then
    local sel_for_buf = _G.codecompanion_dvsc_selection_for_buf
    if type(sel_for_buf) == "function" then
      dvsc_sel = sel_for_buf(state.chat_bufnr)
    end
  end
  -- Durable session id across families (acp OR omnigent).
  local acp_session_id
  do
    local ok_sess, sesslib = pcall(require, "lib.codecompanion-session")
    if ok_sess then
      acp_session_id = sesslib.session_id(chat)
    end
  end
  local acp_usage
  if acp_session_id then
    local ok_stats, stats = pcall(require, "lib.codecompanion-stats")
    if ok_stats then
      acp_usage = stats.get(acp_session_id)
    end
  end
  -- Prefer the pinned session id for display so the status bar matches the
  -- stable winbar handle and doesn't flicker on disconnect/remint. Usage is
  -- still keyed by the live id above.
  local display_session_id = acp_session_id
  do
    local ok_ci, ci = pcall(require, "lib.codecompanion-chatinfo")
    if ok_ci then
      display_session_id = ci.get(state.chat_bufnr) or acp_session_id
    end
  end

  if state.queued then
    left[#left + 1] = { " Queued ", "DiagnosticWarn" }
  else
    left[#left + 1] = { " Draft ", "Comment" }
  end
  left[#left + 1] = { " · ", "Comment" }
  local adapter_name = (meta.adapter and meta.adapter.name)
    or (chat and chat.adapter and (chat.adapter.name or chat.adapter.formatted_name))
    or "unknown"
  left[#left + 1] = { adapter_name, "Function" }
  if meta.adapter and meta.adapter.model then
    left[#left + 1] = { " · ", "Comment" }
    left[#left + 1] = { meta.adapter.model, "String" }
  end
  if meta.cycles and meta.cycles > 0 then
    left[#left + 1] = { " · ", "Comment" }
    left[#left + 1] = { "turn " .. meta.cycles, "Number" }
  end

  if display_session_id then
    right[#right + 1] = { display_session_id, "Constant" }
  end
  if meta.mode and meta.mode.name then
    right[#right + 1] = { meta.mode.name, "String" }
  elseif dvsc_sel and dvsc_sel.mode then
    right[#right + 1] = { dvsc_sel.mode, "String" }
  end
  if dvsc_sel and dvsc_sel.model then
    right[#right + 1] = { dvsc_sel.model, "String" }
  end
  if dvsc_sel and dvsc_sel.effort then
    right[#right + 1] = { "effort:" .. tostring(dvsc_sel.effort), "DiagnosticInfo" }
  end
  if meta.tools and meta.tools > 0 then
    right[#right + 1] = { meta.tools .. " tools", "DiagnosticInfo" }
  end
  if meta.context_items and meta.context_items > 0 then
    right[#right + 1] = { meta.context_items .. " ctx", "DiagnosticInfo" }
  end
  if meta.tokens and meta.tokens > 0 then
    right[#right + 1] = { fmt_tokens(meta.tokens) .. " tokens", "DiagnosticInfo" }
  elseif acp_usage and acp_usage.used and acp_usage.used > 0 then
    right[#right + 1] = { fmt_tokens(acp_usage.used) .. " tokens", "DiagnosticInfo" }
  end
  if acp_usage and acp_usage.used and acp_usage.size and acp_usage.size > 0 then
    local pct = math.floor(100 * acp_usage.used / acp_usage.size)
    right[#right + 1] = {
      string.format("%d%% %s/%s", pct, fmt_tokens(acp_usage.used), fmt_tokens(acp_usage.size)),
      "DiagnosticInfo",
    }
  end

  if state.request_start_at then
    local elapsed = os.time() - state.request_start_at
    local hl = "DiagnosticInfo"
    if elapsed >= 90 then
      hl = "DiagnosticError"
    elseif elapsed >= 30 then
      hl = "DiagnosticWarn"
    end
    right[#right + 1] = { string.format("%ds", elapsed), hl }
  end

  return left, right
end

local function merge_segments(left, right)
  local merged = {}
  for _, seg in ipairs(left) do merged[#merged + 1] = seg end
  for i, seg in ipairs(right) do
    if i == 1 and #merged > 0 then
      merged[#merged + 1] = { " · ", "Comment" }
    elseif i > 1 then
      merged[#merged + 1] = { " · ", "Comment" }
    end
    merged[#merged + 1] = seg
  end
  return merged
end

-- Soft-wrap merged segments into rows of at most `width` display cells.
-- Vim's 'wrap'+'linebreak' won't break inside long unbroken tokens (the
-- bsid is one of them), so we do it by hand. Each row carries
-- per-row highlight spans in byte offsets.
local function pack_rows(merged, width)
  width = math.max(1, width)
  local rows = {}
  local cur_text, cur_hls, cur_width = "", {}, 0

  local function flush()
    rows[#rows + 1] = { text = cur_text, hls = cur_hls }
    cur_text, cur_hls, cur_width = "", {}, 0
  end

  for _, seg in ipairs(merged) do
    local text, hl = seg[1], seg[2]
    for ch in text:gmatch("[%z\1-\127\194-\244][\128-\191]*") do
      local cw = vim.fn.strdisplaywidth(ch)
      if cur_width + cw > width and cur_width > 0 then
        flush()
      end
      local start_col = #cur_text
      cur_text = cur_text .. ch
      cur_width = cur_width + cw
      if hl then
        local last = cur_hls[#cur_hls]
        if last and last[1] == hl and last[3] == start_col then
          last[3] = start_col + #ch
        else
          cur_hls[#cur_hls + 1] = { hl, start_col, start_col + #ch }
        end
      end
    end
  end

  if cur_text ~= "" or #rows == 0 then flush() end
  return rows
end

-- ─── Public API ──────────────────────────────────────────────────────────

function M.refresh(state)
  if not state.status_bufnr or not vim.api.nvim_buf_is_valid(state.status_bufnr) then
    return
  end

  local left, right = build_segments(state)
  local merged = merge_segments(left, right)

  local width = 80
  if state.status_winnr and vim.api.nvim_win_is_valid(state.status_winnr) then
    width = math.max(1, vim.api.nvim_win_get_width(state.status_winnr))
  end

  local rows = pack_rows(merged, width)
  local lines = {}
  for i, row in ipairs(rows) do lines[i] = row.text end

  vim.bo[state.status_bufnr].modifiable = true
  vim.api.nvim_buf_set_lines(state.status_bufnr, 0, -1, false, lines)
  vim.bo[state.status_bufnr].modifiable = false

  if state.status_winnr and vim.api.nvim_win_is_valid(state.status_winnr) then
    local target_height = math.min(STATUS_MAX_ROWS, math.max(1, #lines))
    vim.api.nvim_win_set_height(state.status_winnr, target_height)
  end

  vim.api.nvim_buf_clear_namespace(state.status_bufnr, ns, 0, -1)
  for i, row in ipairs(rows) do
    for _, h in ipairs(row.hls) do
      vim.api.nvim_buf_add_highlight(state.status_bufnr, ns, h[1], i - 1, h[2], h[3])
    end
  end
end

-- Apply queued-vs-draft window highlighting to both the input and
-- status windows owned by `state`.
function M.apply_winhighlight(state)
  local whl_input, whl_status = "", ""
  if state.queued then
    whl_input = "Normal:CCQueuedNormal,EndOfBuffer:CCQueuedNormal,WinSeparator:CCQueuedBorder"
    whl_status = "Normal:CCQueuedNormal,WinSeparator:CCQueuedBorder"
  end
  if state.winnr and vim.api.nvim_win_is_valid(state.winnr) then
    vim.wo[state.winnr].winhighlight = whl_input
  end
  if state.status_winnr and vim.api.nvim_win_is_valid(state.status_winnr) then
    vim.wo[state.status_winnr].winhighlight = whl_status
  end
end

local function stop_timer()
  if timer then
    timer:stop()
    timer:close()
    timer = nil
  end
end

local function any_ticking()
  for _ in pairs(ticking) do return true end
  return false
end

function M.start(state)
  ticking[state] = true
  if timer then return end
  timer = vim.uv.new_timer()
  timer:start(0, 1000, vim.schedule_wrap(function()
    if not any_ticking() then
      stop_timer()
      return
    end
    for s in pairs(ticking) do
      M.refresh(s)
    end
  end))
end

function M.stop(state)
  ticking[state] = nil
  if not any_ticking() then stop_timer() end
end

-- Create a scratch status buffer.
function M.create_buf()
  local buf = vim.api.nvim_create_buf(false, true)
  vim.bo[buf].buftype = "nofile"
  vim.bo[buf].bufhidden = "hide"
  vim.bo[buf].swapfile = false
  vim.bo[buf].modifiable = false
  return buf
end

return M
