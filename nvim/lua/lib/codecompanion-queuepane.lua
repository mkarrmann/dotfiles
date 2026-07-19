-- Floating queue-manager pane for the per-tab CodeCompanion queue.
--
-- Renders `state.queue` (the FIFO of pending message strings held in
-- lib.codecompanion-queue) as a scrollable float and exposes line-based
-- operations — delete, reorder, edit, send-now, clear — that call back into
-- the queue module via the `ops` table passed to `toggle`. The queue module
-- owns the source-of-truth list and performs every mutation; this module is
-- pure view + input, so the two never disagree about queue contents.
--
-- One pane per state (per tab). Handles live on the state table
-- (`state.pane_bufnr` / `state.pane_winnr`) so the queue module's teardown
-- can close the pane as part of the per-tab UI unit.

local M = {}

local ns = vim.api.nvim_create_namespace("codecompanion_queuepane")

-- state -> { [buffer line] = item index }. Weak keys so a dropped state can
-- be collected. Rebuilt on every render.
local line_maps = setmetatable({}, { __mode = "k" })

local HELP = "dd del · C-j/C-k move · e edit · C-s send now · D clear · q close"

local function is_open(state)
  return state.pane_winnr and vim.api.nvim_win_is_valid(state.pane_winnr)
end
M.is_open = is_open

-- Buffer line under the cursor -> queued item index, or nil on a separator.
local function index_under_cursor(state)
  if not is_open(state) then return nil end
  local line = vim.api.nvim_win_get_cursor(state.pane_winnr)[1]
  return (line_maps[state] or {})[line]
end

-- Render `state.queue` into the pane buffer and size the window to fit.
-- Each item is a gutter-numbered block; multi-line messages keep a blank
-- continuation gutter. Returns the buffer line of the first row of item
-- `focus_index` (for cursor restoration), or nil.
local function render(state)
  local buf = state.pane_bufnr
  if not (buf and vim.api.nvim_buf_is_valid(buf)) then return end

  local queue = state.queue or {}
  local lines = {}
  local hls = {} -- { line0, col_end } gutter spans
  local line_map = {}
  local first_line_of = {}

  if #queue == 0 then
    lines[1] = "  (queue empty)"
  else
    for i, msg in ipairs(queue) do
      local msg_lines = vim.split(msg, "\n", { plain = true })
      for j, ml in ipairs(msg_lines) do
        local gutter = (j == 1) and string.format(" %2d │ ", i) or "    │ "
        lines[#lines + 1] = gutter .. ml
        line_map[#lines] = i
        hls[#lines] = #gutter
        if j == 1 then first_line_of[i] = #lines end
      end
      lines[#lines + 1] = ""
    end
  end

  line_maps[state] = line_map

  vim.bo[buf].modifiable = true
  vim.api.nvim_buf_set_lines(buf, 0, -1, false, lines)
  vim.bo[buf].modifiable = false

  vim.api.nvim_buf_clear_namespace(buf, ns, 0, -1)
  for line0, col_end in pairs(hls) do
    vim.api.nvim_buf_add_highlight(buf, ns, "CCQueuedBorder", line0 - 1, 0, col_end)
  end

  if is_open(state) then
    local ui_h = vim.o.lines
    local height = math.max(1, math.min(#lines, ui_h - 8))
    pcall(vim.api.nvim_win_set_config, state.pane_winnr, {
      height = height,
      title = string.format(" Queue (%d) — %s ", #queue, HELP),
      title_pos = "center",
    })
  end

  return first_line_of
end

function M.refresh(state)
  if not is_open(state) then return end
  local prev = vim.api.nvim_win_get_cursor(state.pane_winnr)
  render(state)
  local total = vim.api.nvim_buf_line_count(state.pane_bufnr)
  local row = math.min(prev[1], math.max(1, total))
  pcall(vim.api.nvim_win_set_cursor, state.pane_winnr, { row, 0 })
end

function M.close(state)
  if state.pane_winnr and vim.api.nvim_win_is_valid(state.pane_winnr) then
    pcall(vim.api.nvim_win_close, state.pane_winnr, true)
  end
  if state.pane_bufnr and vim.api.nvim_buf_is_valid(state.pane_bufnr) then
    pcall(vim.api.nvim_buf_delete, state.pane_bufnr, { force = true })
  end
  state.pane_winnr = nil
  state.pane_bufnr = nil
  line_maps[state] = nil
end

-- Wire the line-based keymaps. `ops` supplies the mutations, each keyed by the
-- item index resolved from the cursor: delete(i), move(i, dir), edit(i),
-- flush(), clear(). Mutations refresh the pane via the queue module, so these
-- handlers don't re-render themselves (except `edit`, which closes the pane).
local function set_keymaps(buf, state, ops)
  local function map(lhs, fn)
    vim.keymap.set("n", lhs, fn, { buffer = buf, nowait = true, silent = true })
  end

  map("q", function() M.close(state) end)
  map("<Esc>", function() M.close(state) end)

  local function delete()
    local i = index_under_cursor(state)
    if i then ops.delete(i) end
  end
  map("dd", delete)
  map("x", delete)

  map("<C-j>", function()
    local i = index_under_cursor(state)
    if i then ops.move(i, 1) end
  end)
  map("<C-k>", function()
    local i = index_under_cursor(state)
    if i then ops.move(i, -1) end
  end)

  local function edit()
    local i = index_under_cursor(state)
    if not i then return end
    M.close(state)
    ops.edit(i)
  end
  map("<CR>", edit)
  map("e", edit)

  map("<C-s>", function() ops.flush() end)
  map("D", function() ops.clear() end)
end

function M.open(state, ops)
  if is_open(state) then
    M.refresh(state)
    vim.api.nvim_set_current_win(state.pane_winnr)
    return
  end

  local buf = vim.api.nvim_create_buf(false, true)
  vim.bo[buf].buftype = "nofile"
  vim.bo[buf].bufhidden = "wipe"
  vim.bo[buf].swapfile = false
  vim.bo[buf].filetype = "codecompanion_queue"
  state.pane_bufnr = buf

  render(state)

  local ui_w, ui_h = vim.o.columns, vim.o.lines
  local width = math.min(100, math.max(40, ui_w - 8))
  local total = math.max(1, vim.api.nvim_buf_line_count(buf))
  local height = math.max(1, math.min(total, ui_h - 8))
  local win = vim.api.nvim_open_win(buf, true, {
    relative = "editor",
    width = width,
    height = height,
    row = math.floor((ui_h - height) / 2 - 1),
    col = math.floor((ui_w - width) / 2),
    style = "minimal",
    border = "rounded",
    title = string.format(" Queue (%d) — %s ", #(state.queue or {}), HELP),
    title_pos = "center",
  })
  state.pane_winnr = win
  vim.wo[win].wrap = false
  vim.wo[win].cursorline = true

  set_keymaps(buf, state, ops)
  M.refresh(state)
end

function M.toggle(state, ops)
  if is_open(state) then
    M.close(state)
  else
    M.open(state, ops)
  end
end

return M
