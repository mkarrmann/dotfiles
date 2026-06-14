-- Collapsible ACP tool-call output rendered as virtual lines.
--
-- CodeCompanion renders each ACP tool call as a single real buffer line and
-- updates it in place via `update_buf_line` (interactions/chat/ui/builder.lua),
-- which replaces exactly one line. Streaming status changes and concurrent
-- in-flight tool calls both rely on that one-line-per-call invariant — adding
-- real output lines would shift the cached line numbers of sibling calls.
--
-- So the full tool output is attached as `virt_lines` on the header line's
-- extmark instead of as real lines. Extmarks track their anchor across edits,
-- so the header line moving (later messages, compaction) never desyncs the
-- output, and no sibling line bookkeeping is required.
--
-- Collapsed (default): a compact ` ⊞ N` hint at end-of-line on the header.
-- Expanded: the soft-wrapped output as virtual lines beneath the header.
-- Toggle the call under the cursor with `za` (see codecompanion.lua keymap).
local M = {}

local api = vim.api
local NS = api.nvim_create_namespace("cc_tool_output")

-- state[bufnr][mark_id] = { lines = string[], expanded = boolean }
local state = {}

local CONFIG = {
  max_lines = 500, -- cap rendered output (post-wrap) to keep the buffer sane
  hl_body = "Comment",
  hl_hint = "Comment",
  icon_collapsed = "⊞",
  icon_expanded = "⊟",
}

---Width available for a wrapped output line in a window showing `bufnr`.
---@param bufnr number
---@return number
local function body_width(bufnr)
  local width = 80
  for _, win in ipairs(api.nvim_list_wins()) do
    if api.nvim_win_is_valid(win) and api.nvim_win_get_buf(win) == bufnr then
      width = api.nvim_win_get_width(win)
      break
    end
  end
  -- Leave room for the "  │ " gutter prefix.
  return math.max(40, width - 6)
end

---Split raw output text into display lines, expanding tabs and soft-wrapping
---long lines to `width`. Caps the total at CONFIG.max_lines.
---@param text string
---@param width number
---@return string[] lines
---@return boolean truncated
local function to_display_lines(text, width)
  local out = {}
  local truncated = false
  for _, raw in ipairs(vim.split(text, "\n", { plain = true })) do
    local line = raw:gsub("\t", "    ")
    if line == "" then
      out[#out + 1] = ""
    else
      -- Soft-wrap by display width (handles multibyte via strcharpart).
      while vim.fn.strdisplaywidth(line) > width do
        -- Find the largest char-prefix that fits in `width` columns.
        local lo, hi, cut = 1, vim.fn.strchars(line), 1
        while lo <= hi do
          local mid = math.floor((lo + hi) / 2)
          if vim.fn.strdisplaywidth(vim.fn.strcharpart(line, 0, mid)) <= width then
            cut = mid
            lo = mid + 1
          else
            hi = mid - 1
          end
        end
        out[#out + 1] = vim.fn.strcharpart(line, 0, cut)
        line = vim.fn.strcharpart(line, cut)
      end
      out[#out + 1] = line
    end
    if #out >= CONFIG.max_lines then
      truncated = true
      break
    end
  end
  return out, truncated
end

---Build the virt_lines payload for an expanded mark.
---@param entry { lines: string[], truncated?: boolean }
---@return table virt_lines
local function build_virt_lines(entry)
  local vl = {}
  for _, line in ipairs(entry.lines) do
    vl[#vl + 1] = { { "  │ ", CONFIG.hl_hint }, { line, CONFIG.hl_body } }
  end
  if entry.truncated then
    vl[#vl + 1] = { { "  │ ", CONFIG.hl_hint }, { "… output truncated", CONFIG.hl_hint } }
  end
  return vl
end

---(Re)place the extmark for `mark_id` according to its expanded state.
---@param bufnr number
---@param row0 number 0-based header row
---@param mark_id number
local function render(bufnr, row0, mark_id)
  local entry = state[bufnr] and state[bufnr][mark_id]
  if not entry then
    return
  end
  local n = #entry.lines
  local opts = { id = mark_id, priority = 130 }
  if entry.expanded then
    opts.virt_text = { { " " .. CONFIG.icon_expanded, CONFIG.hl_hint } }
    opts.virt_text_pos = "eol"
    opts.virt_lines = build_virt_lines(entry)
    opts.virt_lines_above = false
  else
    opts.virt_text = { { (" %s %d"):format(CONFIG.icon_collapsed, n), CONFIG.hl_hint } }
    opts.virt_text_pos = "eol"
  end
  pcall(api.nvim_buf_set_extmark, bufnr, NS, row0, 0, opts)
end

---Attach (collapsed) output to the tool-call header line.
---@param bufnr number
---@param header_line number 1-based line of the tool-call header
---@param text string raw output text
function M.set(bufnr, header_line, text)
  if not (bufnr and api.nvim_buf_is_valid(bufnr)) then
    return
  end
  if type(text) ~= "string" or text == "" then
    return
  end
  local row0 = header_line - 1
  if row0 < 0 or row0 >= api.nvim_buf_line_count(bufnr) then
    return
  end

  local lines, truncated = to_display_lines(text, body_width(bufnr))
  if #lines == 0 then
    return
  end

  state[bufnr] = state[bufnr] or {}

  -- Reuse an existing mark on this row if the same tool call streamed an
  -- earlier output chunk, so we update rather than stack duplicates.
  local existing = api.nvim_buf_get_extmarks(bufnr, NS, { row0, 0 }, { row0, -1 }, {})
  local mark_id = existing[1] and existing[1][1]
  if not mark_id then
    mark_id = api.nvim_buf_set_extmark(bufnr, NS, row0, 0, { priority = 130 })
  end

  local prev = state[bufnr][mark_id]
  state[bufnr][mark_id] = {
    lines = lines,
    truncated = truncated,
    expanded = prev and prev.expanded or false,
  }
  render(bufnr, row0, mark_id)
end

---Toggle expand/collapse for the tool call whose header is the cursor line.
---@param bufnr number
---@param cursor_line number 1-based
---@return boolean handled true if a tool output mark was toggled
function M.toggle(bufnr, cursor_line)
  if not (state[bufnr] and api.nvim_buf_is_valid(bufnr)) then
    return false
  end
  local row0 = cursor_line - 1
  local marks = api.nvim_buf_get_extmarks(bufnr, NS, { row0, 0 }, { row0, -1 }, {})
  local mark = marks[1]
  if not mark then
    return false
  end
  local mark_id, mark_row = mark[1], mark[2]
  local entry = state[bufnr][mark_id]
  if not entry then
    return false
  end
  entry.expanded = not entry.expanded
  render(bufnr, mark_row, mark_id)
  return true
end

---Forget all tool output state/extmarks for a buffer (on chat close).
---@param bufnr number
function M.clear(bufnr)
  state[bufnr] = nil
  if api.nvim_buf_is_valid(bufnr) then
    pcall(api.nvim_buf_clear_namespace, bufnr, NS, 0, -1)
  end
end

return M
