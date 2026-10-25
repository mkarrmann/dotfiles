local meta_util = require("meta.util")
local meta_util_hg = require("meta.util.hg")
local repo_context = require("lib.repo-context")
local log_to_scuba = meta_util.log_to_scuba
---@class (exact) Hg.commit
---@field is_current boolean
---@field is_bookmark ?boolean
---@field bookmarks ?string
---@field hash string
---@field hash_meta ?string
---@field diff ?string
---@field date string
---@field author ?string
---@field message string

---@class (exact) Hg.diff_file_spec
---@field file string
---@field old_file string? Path on the old side, when renamed or copied from elsewhere.

---@class (exact) Hg.diff_file_pair : DiffPair
---@field file string
---@field old_file string?
---@field old_buf integer?
---@field new_buf integer?
---@field is_live boolean

---@class (exact) Hg.diff_target
---@field hash string Revision on the new side; "." for the working copy.
---@field is_current boolean New side shows the working-copy file when it exists.
---@field diff string? Phabricator diff, for inline comments.

---@class (exact) Hg.rev_cache
---@field contents table<string, string[]|false> Path -> lines; false when absent at the revision.
---@field pending table<string, true> Paths with a fetch in flight.
---@field errors table<string, string> Paths that couldn't be read, and why.

---@class (exact) Hg.diff_session : DiffSession
---@field pairs Hg.diff_file_pair[]
---@field commit Hg.diff_target Its hash is pinned (see `pin_revs`).
---@field parent_rev string Pinned (see `pin_revs`).
---@field labels { old: string, new: string } The revisions as requested, for display.
---@field repo_root string
---@field return_win integer Window focused again when the split closes.
---@field cache table<string, Hg.rev_cache> File contents keyed by revision.
---@field closed boolean

---@class (exact) Hg.diff_split_opts
---@field files Hg.diff_file_spec[] In display order.
---@field target Hg.diff_target
---@field parent_rev string
---@field repo_root string
---@field split_from integer? Window to open the diff panes beside; defaults to the current one.

---@class (exact) Hg.preblame_opts
---@field scrollbind boolean
---@field wrap boolean
---@field foldenable boolean

---@class (exact) Hg.preblame
---@field opts Hg.preblame_opts
---@field count integer Blame windows open beside it.

local hg_utils = {}

local CONFIG = {
  signs = {
    add = {
      char = "+",
      hl = "DiffAdd",
    },
    delete = {
      char = "_",
      hl = "DiffDelete",
    },
    debounce_ms = 500,
  },
  line_blame = {
    enable = true,
    highlight = "Comment",
    prefix = string.rep(" ", 4),
    debounce_ms = 300,
  },
  ssl = {
    status = false,
    keys = {
      show = "s",
      current = "c",
      open = "gx",
      refresh = "r",
    },
    hidden_bookmarks = {
      "whatsapp_server/stable",
      "waios/stable",
      "igios/stable",
      "ig4a/stable",
      "waandroid/stable",
      "fbobjc/stable",
      "fbandroid/stable",
      "fbsource/stable",
      "fbcode/stable",
    },
  },
  ---@type string? Base revision for diff and gutter (nil = parent commit)
  base_revision = nil,
}

local SSL_NS = vim.api.nvim_create_namespace("hg_ssl")
local SSL_CURRENT_NS = vim.api.nvim_create_namespace("hg_ssl_current")
local SSL_HOP_NS = vim.api.nvim_create_namespace("hg_ssl_hop")
vim.api.nvim_set_hl(0, "HgSslCurrentLine", { default = true, link = "Visual" })
vim.api.nvim_set_hl(0, "HgSslHopLabel", { default = true, link = "DiagnosticOk" })

-- Single-key labels reserved for "special" commits (the current commit and
-- bookmark commits). These chars are NEVER used as the first character of a
-- two-char label, so a single keypress after the trigger is unambiguous.
local HOP_SPECIAL_CHARS = "jkl;asdg"
-- Building blocks for the two-char labels used by every other commit. Kept
-- disjoint from HOP_SPECIAL_CHARS so single-key hops can never be ambiguous.
local HOP_CHARS = "hqwertyuiopzxcvbnm"
local HOP_LABELS = {}
for i = 1, #HOP_CHARS do
  for j = 1, #HOP_CHARS do
    HOP_LABELS[#HOP_LABELS + 1] = HOP_CHARS:sub(i, i) .. HOP_CHARS:sub(j, j)
  end
end

-- Focusing the panel refreshes it unless a refresh finished this recently.
local SSL_REFOCUS_REFRESH_MS = 5000

local SSL_STATE = {
  ---@type number? reuse the same buffer for the nvim process lifespan
  bufnr = nil,
  ---@type integer Bumped by every refresh. A refresh's results are shown only
  --- while it is still the latest one, so one that finishes late (or was
  --- started for another repo) can't overwrite the panel.
  refresh_gen = 0,
  ---@type boolean
  refreshing = false,
  ---@type integer? vim.uv.now() when the last refresh finished
  refreshed_at = nil,
  ---@type integer Commands launched from the panel that change the repo and
  --- are still running. Others are refused meanwhile.
  busy = 0,
  ---@type boolean controls how current status is displayed and what actions are available
  merge_conflict = false,
  ---@type table<string, integer> hop label -> target line (1-indexed)
  hop_map = {},
  ---@type string? Sapling repo the panel is pinned to. Every command the panel
  --- launches runs here, so actions never leak into nvim's (possibly unrelated)
  --- global cwd. Pinned from the launching buffer each time the panel opens.
  repo_root = nil,
}

--- Debounce `fn` by `delay` ms.
---
--- Local copy of `meta_util.debounce`, which races: its cleanup re-reads the
--- shared `timer` upvalue from inside a `vim.schedule_wrap`ped callback, so a
--- call arriving between a timer firing and its callback running makes that
--- callback stop/close the *successor* timer (dropping a pending update) and
--- nil out the upvalue, erroring the next callback with "attempt to index
--- upvalue 'timer'". Here each callback owns the handle it was started with.
---@param fn function
---@param delay number
---@return function
local function debounce(fn, delay)
  local timer = nil
  return function(...)
    local args = { ... }
    if timer and not timer:is_closing() then
      timer:stop()
      timer:close()
    end
    local this = vim.uv.new_timer()
    timer = this
    this:start(
      delay,
      0,
      vim.schedule_wrap(function()
        if not this:is_closing() then
          this:stop()
          this:close()
        end
        if timer == this then
          timer = nil
        end
        fn(unpack(args))
      end)
    )
  end
end

--- Nearest ancestor of `path` containing a `.hg` dir, or nil. `path` may be a
--- file or directory (absolute or relative to nvim's cwd).
---@param path string?
---@return string?
local function repo_root_of(path)
  if not path or path == "" then
    return nil
  end
  return vim.fs.root(path, ".hg")
end

--- Repo root for the buffer we're acting on (its file's repo), falling back to
--- nvim's cwd repo, then cwd itself. Sapling/jf commands must run here rather
--- than in nvim's global cwd, which may point at an unrelated repo.
---@param bufnr integer?
---@return string
local function buf_repo_root(bufnr)
  local root = repo_root_of(vim.api.nvim_buf_get_name(bufnr or 0))
  if root then
    repo_context.remember(root)
    return root
  end
  local context = repo_context.current()
  if context then
    return context.repo_root
  end
  local cwd = vim.uv.cwd() or vim.fn.getcwd()
  return repo_root_of(cwd) or cwd
end

--- The repo root a command should target. When we act from the SSL panel (its
--- buffer is current), use its pinned root; the panel is a virtual buffer with
--- no file, so `buf_repo_root` can't resolve it. Otherwise resolve from the
--- current buffer's file.
---@return string
local function active_repo_root()
  if
    SSL_STATE.bufnr
    and vim.api.nvim_get_current_buf() == SSL_STATE.bufnr
    and SSL_STATE.repo_root
  then
    return SSL_STATE.repo_root
  end
  return buf_repo_root()
end

---Loaded buffer whose name is exactly `path` (vim.fn.bufnr pattern-matches).
---@param path string absolute path
---@return integer?
local function loaded_buf_for_path(path)
  path = vim.fs.normalize(path)
  for _, b in ipairs(vim.api.nvim_list_bufs()) do
    if vim.api.nvim_buf_is_loaded(b) and vim.api.nvim_buf_get_name(b) == path then
      return b
    end
  end
end

---@type table<integer, Hg.diff_session>
local DIFF_SPLIT_SESSIONS = require("lib.diff-session").sessions

hg_utils.on_blame_enter = function()
  local word_under_cursor = vim.fn.expand("<cword>")

  if vim.fn.match(word_under_cursor, "D\\d\\+") == -1 then
    return nil
  end

  meta_util.open_url("https://www.internalfb.com/diff/" .. word_under_cursor)
end
---@type table<string, Hg.preblame> "<code window>:<buffer>" -> the buffer's
--- options in that window before the first blame window opened beside it.
local PREBLAME = {}

---@param cwd string? repo root to run in (defaults to the active repo)
hg_utils.status = function(cwd)
  local result = {
    modified = {},
    added = {},
    removed = {},
    clean = {},
    missing = {},
    not_tracked = {},
    ignored = {},
  }
  local out = vim
    .system({ "hg", "status" }, { text = true, cwd = cwd or active_repo_root() })
    :wait()
  SSL_STATE.merge_conflict = string.find(
    out.stderr or "",
    "The repository is in an unfinished"
  ) ~= nil
  for _, line in ipairs(vim.split(vim.trim(out.stdout or ""), "\n")) do
    local first_char = string.sub(line, 1, 1)
    local file_path = string.sub(line, 3)
    if "M" == first_char then
      table.insert(result.modified, file_path)
    elseif "A" == first_char then
      table.insert(result.added, file_path)
    elseif "R" == first_char then
      table.insert(result.removed, file_path)
    elseif "C" == first_char then
      table.insert(result.clean, file_path)
    elseif "!" == first_char then
      table.insert(result.missing, file_path)
    elseif "?" == first_char then
      table.insert(result.not_tracked, file_path)
    elseif "I" == first_char then
      table.insert(result.ignored, file_path)
    end
  end

  return result
end

---@param cwd string? repo root to run in (defaults to the active repo)
hg_utils.can_commit = function(cwd)
  local status = hg_utils.status(cwd)

  return (#status.modified + #status.added + #status.removed) > 0
end

---@param data string
---@return string[]
local function split_file_lines(data)
  local lines = vim.split(data, "\n", { plain = true })
  if lines[#lines] == "" then
    table.remove(lines)
  end
  return lines
end

---Run `hg <args>` to completion in the repo of `bufnr`'s file; notify on failure.
---@param args string[]
---@param bufnr integer?
---@return vim.SystemCompleted
function hg_utils.run_sync(args, bufnr)
  local cmd = vim.list_extend({ "hg" }, args)
  local obj = vim.system(cmd, { text = true, cwd = buf_repo_root(bufnr) }):wait()
  if obj.code ~= 0 then
    vim.notify(
      table.concat(cmd, " ") .. " failed:\n" .. vim.trim((obj.stderr or "") .. "\n" .. (obj.stdout or "")),
      vim.log.levels.ERROR
    )
  end
  return obj
end

---Run a command in a terminal in a new tab. The tab closes when the command
---succeeds and stays open on failure so its output can be read.
---@param cmd string[]
---@param on_close ?function called when the command exits
---@param on_open ?function
---@param cwd string? directory to run the terminal command in
function hg_utils.run_cmd_and_exit(cmd, on_close, on_open, cwd)
  vim.cmd("tabnew")
  local term_buf = vim.api.nvim_get_current_buf()
  vim.fn.jobstart(cmd, {
    term = true,
    cwd = cwd ~= "" and cwd or nil,
    on_exit = function(_, code)
      vim.schedule(function()
        if code ~= 0 then
          vim.notify(
            table.concat(cmd, " ") .. " failed (exit " .. code .. ")",
            vim.log.levels.WARN
          )
        elseif vim.api.nvim_buf_is_valid(term_buf) then
          for _, win in ipairs(vim.fn.win_findbuf(term_buf)) do
            if #vim.api.nvim_list_wins() > 1 then
              vim.api.nvim_win_close(win, true)
            end
          end
          vim.api.nvim_buf_delete(term_buf, { force = true })
        end
        vim.cmd.doautocmd("BufEnter")
        if on_close then
          on_close()
        end
      end)
    end,
  })

  if on_open then
    vim.schedule(on_open)
  end
end


local sign_util = {}

local function HgBlame()
  log_to_scuba({
    module = "hg",
    command = "HgBlame",
  })
  local code_win = vim.api.nvim_get_current_win()
  local code_buf = vim.api.nvim_get_current_buf()
  local obj = hg_utils.run_sync({
    "blame",
    "-r", "wdir()", -- annotate changed lines
    "--user", -- include username
    "--date", -- include date
    "--phabdiff", -- include diff
    "-q", -- simple date format
    vim.api.nvim_buf_get_name(0),
  })
  if obj.code ~= 0 then
    return
  end
  local blame_lines = split_file_lines(obj.stdout)
  local temp_filename = vim.fn.tempname()
  vim.fn.writefile(blame_lines, temp_filename)

  -- revisition info and content is split by `: `
  -- the width until `:` char is the width of the blame buffer we want
  -- add 2 to for `: `
  ---@type number
  local target_width = #vim.split(blame_lines[1] or "", ":")[1] + 2

  local preblame_key = code_win .. ":" .. code_buf
  PREBLAME[preblame_key] = PREBLAME[preblame_key] or {
    opts = {
      scrollbind = vim.wo[code_win].scrollbind,
      wrap = vim.wo[code_win].wrap,
      foldenable = vim.wo[code_win].foldenable,
    },
    count = 0,
  }
  PREBLAME[preblame_key].count = PREBLAME[preblame_key].count + 1

  local current_cursor_line = vim.api.nvim_win_get_cursor(0)[1]

  -- move cursor to the first line
  vim.api.nvim_win_set_cursor(0, { 1, 1 })

  vim.wo[code_win][0].scrollbind = true
  vim.wo[code_win][0].wrap = false
  vim.wo[code_win][0].foldenable = false

  vim.cmd("keepalt leftabove vsplit " .. vim.fn.fnameescape(temp_filename))
  local blame_win = vim.api.nvim_get_current_win()
  vim.cmd("vertical resize " .. target_width) -- set split width

  -- set all buffer/window related settings
  vim.bo.filetype = "hgblame"
  vim.bo.modified = false
  vim.bo.modifiable = false
  vim.wo[blame_win][0].number = false
  vim.wo[blame_win][0].spell = false
  -- then fix the scroll
  vim.wo[blame_win][0].scrollbind = true
  -- now that both windows have scroll binded we can scroll blame buffer
  -- and the code buffer scrolls too
  vim.api.nvim_win_set_cursor(0, { current_cursor_line, 1 })
  vim.wo[blame_win][0].wrap = false
  vim.wo[blame_win][0].foldcolumn = "0"
  vim.wo[blame_win][0].foldenable = false

  -- disable indentline if present
  if vim.fn.exists(":IndentBlanklineDisable") == 1 then
    require("indent_blankline.commands").disable(true)
  end

  vim.keymap.set(
    "", -- all modes
    "<cr>", -- on enter
    hg_utils.on_blame_enter,
    { buffer = 0 }
  )
  -- delete blame buffer
  vim.keymap.set("n", "gq", "<Cmd>bdelete<CR>", { buffer = 0 })
  vim.api.nvim_create_autocmd("WinClosed", {
    pattern = tostring(blame_win),
    once = true,
    callback = function()
      local preblame = PREBLAME[preblame_key]
      preblame.count = preblame.count - 1
      if preblame.count > 0 then
        return
      end
      PREBLAME[preblame_key] = nil
      local function restore()
        for k, v in pairs(preblame.opts) do
          vim.wo[code_win][0][k] = v
        end
      end
      if not vim.api.nvim_win_is_valid(code_win) then
        return
      end
      if vim.api.nvim_win_get_buf(code_win) == code_buf then
        return restore()
      end
      -- the options are code_buf's; another buffer there has its own
      vim.api.nvim_create_autocmd("BufWinEnter", {
        buffer = code_buf,
        callback = function()
          if not vim.api.nvim_win_is_valid(code_win) then
            return true
          end
          if vim.api.nvim_get_current_win() == code_win then
            restore()
            return true
          end
        end,
      })
    end,
  })
end
---@param direction "prev"|"next"
local function hg_step(direction, opts)
  local args = vim.split(opts.args or "", "%s+", { trimempty = true })
  local obj = hg_utils.run_sync(vim.list_extend({ direction }, args))
  if obj.code == 0 then
    vim.notify(vim.trim((obj.stdout or "") .. "\n" .. (obj.stderr or "")), vim.log.levels.INFO)
  end
  -- :! used to reload changed files on its way out
  vim.cmd.checktime()
end
local function HgPrev(opts)
  log_to_scuba({
    module = "hg",
    command = "HgPrev",
  })
  hg_step("prev", opts)
end
local function HgNext(opts)
  log_to_scuba({
    module = "hg",
    command = "HgNext",
  })
  hg_step("next", opts)
end
local function HgMove(opts)
  log_to_scuba({
    module = "hg",
    command = "HgMove",
  })
  local buf = vim.api.nvim_get_current_buf()
  local src = vim.api.nvim_buf_get_name(buf)
  -- relative to the repo root, like the paths hg prints; nvim's cwd is
  -- usually a workspace holding several repos
  local dest = vim.fs.normalize(opts.args)
  if not vim.startswith(dest, "/") then
    dest = vim.fs.joinpath(buf_repo_root(buf), dest)
  end
  if vim.fn.isdirectory(dest) == 1 then
    dest = vim.fs.joinpath(dest, vim.fs.basename(src))
  end
  dest = vim.fs.normalize(dest)
  if vim.bo[buf].modified then
    vim.cmd("write")
  end
  if hg_utils.run_sync({ "move", src, dest }, buf).code ~= 0 then
    return
  end
  local ok, err = pcall(vim.api.nvim_buf_set_name, buf, dest)
  if not ok then
    return vim.notify("Moved to " .. dest .. ", but couldn't rename the buffer: " .. err, vim.log.levels.WARN)
  end
  vim.cmd("edit!")
end
local function HgRemove()
  log_to_scuba({
    module = "hg",
    command = "HgRemove",
  })
  local buf = vim.api.nvim_get_current_buf()
  if hg_utils.run_sync({ "remove", vim.api.nvim_buf_get_name(buf) }, buf).code == 0 then
    vim.api.nvim_buf_delete(buf, { force = true })
  end
end
local function HgResolve()
  log_to_scuba({
    module = "hg",
    command = "HgResolve",
  })
  local buf = vim.api.nvim_get_current_buf()
  local function resolve()
    if vim.bo[buf].modified then
      vim.api.nvim_buf_call(buf, function()
        vim.cmd("write")
      end)
    end
    if hg_utils.run_sync({ "resolve", "--mark", vim.api.nvim_buf_get_name(buf) }, buf).code == 0 then
      vim.api.nvim_buf_delete(buf, { force = true })
    end
  end

  -- Sapling marks a file resolved even with conflict markers left in it
  local has_markers = vim.iter(vim.api.nvim_buf_get_lines(buf, 0, -1, false)):any(function(line)
    return line:find("^<<<<<<< ") ~= nil or line:find("^>>>>>>> ") ~= nil
  end)
  if not has_markers then
    return resolve()
  end
  vim.ui.select({ "Yes", "No" }, {
    prompt = "This file still has conflict markers. Mark it resolved anyway?",
  }, function(choice)
    if choice == "Yes" then
      resolve()
    end
  end)
end

---@param for_current_commit boolean
---@param yank boolean Yank instead of opening url or showing link to output
local function makeHgBrowse(for_current_commit, yank)
  return function(opts)
    local line1 = opts.line1
    local count = opts.count

    local repo_root = vim.fs.root(0, ".hg")
    if repo_root == nil then
      return vim.notify("Failed to locate repo root", vim.log.levels.ERROR)
    end
    local reponame_file = repo_root .. "/.hg/reponame"
    local repo_name = vim.fn.filereadable(reponame_file) == 1
      and vim.fn.readfile(reponame_file)[1]
    if not repo_name or repo_name == "" then
      return vim.notify("Failed to infer repo name", vim.log.levels.ERROR)
    end

    local buf_abs_path = vim.fn.expand("%:p")
    local buf_filepath_from_repo_root = buf_abs_path:sub(#repo_root + 1)

    local commit = ""
    if for_current_commit then
      local cmd = { "hg", "log", "-l", "1", "--template", "{node}" }
      local obj = vim.system(cmd, { text = true, cwd = repo_root }):wait()
      if obj.code ~= 0 then
        return vim.notify("Failed to get current commit", vim.log.levels.ERROR)
      else
        local current_commit = obj.stdout and vim.trim(obj.stdout) or nil
        -- first line is the command itself
        commit = "/[" .. current_commit .. "]"
      end
    end
    local url = "https://www.internalfb.com/code/"
      .. repo_name
      .. commit
      .. buf_filepath_from_repo_root
    if count >= 0 then
      url = url .. "?lines=" .. line1
      if count > line1 then
        url = url .. "-" .. count
      end
    end

    if yank then
      if opts.reg ~= "" then
        vim.fn.setreg(opts.reg, url)
      elseif vim.o.clipboard ~= "" then
        vim.fn.setreg("*", url, "u")
      else
        vim.fn.setreg('"', url)
      end
    else
      meta_util.open_url(url)
    end
  end
end
local HISTORY_NS = vim.api.nvim_create_namespace("hg_history")

local function HgHistory(opts)
  log_to_scuba({
    module = "hg",
    command = "HgHistory",
  })
  local count = tonumber(opts.args) or 50

  local template =
    "{date|shortdate}\t{phabdiff}\t{author|user}\t{desc|strip|firstline}\\n"
  local obj = hg_utils.run_sync({
    "log",
    vim.api.nvim_buf_get_name(0),
    "--template",
    template,
    "-l",
    tostring(count),
  })
  if obj.code ~= 0 then
    return
  end
  local temp_filename = vim.fn.tempname()
  vim.fn.writefile(split_file_lines(obj.stdout), temp_filename)

  vim.cmd("belowright split " .. vim.fn.fnameescape(temp_filename))

  vim.schedule(function()
    vim.bo.filetype = "hghistory"
    vim.bo.modified = false
    vim.bo.modifiable = false

    local total_commits = vim.api.nvim_buf_line_count(0)
    if total_commits <= count then
      vim.cmd("resize " .. total_commits)
    end

    vim.keymap.set("", "<cr>", hg_utils.on_blame_enter, { buffer = 0 })

    local diff_id_start = 11
    local diff_id_end = 20
    for line = 0, total_commits - 1 do
      vim.hl.range(0, HISTORY_NS, "String", { line, diff_id_start }, { line, diff_id_end })
    end
  end)
end
local function HgWrite()
  log_to_scuba({
    module = "hg",
    command = "HgWrite",
  })
  vim.cmd("write")
  hg_utils.run_sync({ "add", vim.api.nvim_buf_get_name(0) })
end

local function HgHunkRevert()
  log_to_scuba({
    module = "hg",
    command = "HgHunkRevert",
  })
  meta_util_hg.revert_hunk_under_cursor(vim.fn.bufnr())
end

local ssl_utils = {}

---@param line string
---@return Hg.commit?
function ssl_utils.parse_diff_line(line)
  -- graph (box-drawing characters and spaces), node marker, two spaces, hash;
  -- unanchored, a message line like "│  second  fox  commit" matched too
  if not line:find("^[%s\128-\255]*[@ox]  %x%x%x%x%x%x%x%x") then
    return nil
  end

  local commit_table = vim.split(line, "  ", { trimempty = true })
  ---@type Hg.commit
  local commit = {
    is_current = vim.endswith(commit_table[1], "@"),
    hash = commit_table[2],
    date = commit_table[3],
  }
  -- hash can contain the commit sha like "1234567890abcdef"
  -- or also have other metadata like "1234567890abcdef [Landed as 1234567890abcdef]
  -- we only want the first part
  local hash_space_idx = commit.hash:find(" ")
  if hash_space_idx then
    commit.hash_meta = commit.hash:sub(hash_space_idx + 1)
    commit.hash = commit.hash:sub(1, hash_space_idx - 1)
  end
  local maybe_author = commit_table[4]
  if maybe_author then
    if vim.startswith(maybe_author, "remote/") then
      -- this is bookmark commit
      -- ie master, stable, warm
      commit.is_bookmark = true
      commit.bookmarks = maybe_author
    else
      commit.author = maybe_author
      local maybe_diff = commit_table[5]
      if maybe_diff ~= nil then
        commit.diff = vim.split(maybe_diff, " ")[1]
      end
    end
  end

  return commit
end

---@return Hg.commit[]
function ssl_utils.collect_commits()
  ---@type Hg.commit[]
  local result = {}
  local lines = vim.api.nvim_buf_get_lines(0, 0, 999999, false)
  for i, line in ipairs(lines) do
    local diff = ssl_utils.parse_diff_line(line)
    if diff ~= nil then
      -- now we know we have a commit,
      -- next line may contain the first line of commit msg
      local next_line = lines[i + 1]
      if next_line then
        local parts = vim.split(vim.trim(next_line), "  ")
        if parts[2] then
          diff.msg = parts[2]
        end
      end
      table.insert(result, diff)
    end
  end
  return result
end

-- moves cursor to the next line contains commit information
---@param direction "next" | "prev"
function ssl_utils.mapping_navigate_to_commit(direction)
  return function()
    local lines = vim.api.nvim_buf_get_lines(0, 0, 999999, false)
    local cursor = vim.api.nvim_win_get_cursor(0)
    local inc = direction == "next" and 1 or -1
    local current_line = cursor[1] + inc
    local current_column = cursor[2]
    while lines[current_line] ~= nil do
      local line = lines[current_line]
      local commit = ssl_utils.parse_diff_line(line)
      if commit ~= nil then
        vim.api.nvim_win_set_cursor(0, { current_line, current_column })
        return
      end

      current_line = current_line + inc
    end
  end
end

---@param opts {reload: boolean, msg?: string}
function ssl_utils.action_end(opts)
  opts = opts or {}
  if opts.reload then
    ssl_utils.refresh_buffer(opts.bufnr)
  end

  if opts.msg then
    vim.schedule(function()
      vim.notify(opts.msg)
    end)
  end
end

---@param bufnr number
---@param sl_lines string[] lines shown in the panel
---@param is_latest fun(): boolean false once a newer refresh has started
function ssl_utils.annotate_hg_status(bufnr, sl_lines, is_latest)
  vim.system(
    { "hg", "status" },
    { text = true, cwd = SSL_STATE.repo_root },
    function(status_obj)
    -- replacing the lines moves the previous annotation instead of deleting it
    local function clear_previous()
      vim.schedule(function()
        if is_latest() then
          vim.api.nvim_buf_clear_namespace(bufnr, SSL_NS, 0, -1)
        end
      end)
    end
    if status_obj.code ~= 0 then
      clear_previous()
      vim.schedule(function()
        vim.notify("Failed to check hg status", vim.log.levels.ERROR)
      end)
      return
    end
    local out = vim.trim(status_obj.stdout or "")
    SSL_STATE.merge_conflict = string.find(
      status_obj.stderr or "",
      "The repository is in an unfinished"
    ) ~= nil
    if out == "" then
      return clear_previous()
    end

    local status_lines = vim.split(out, "\n")

    local current_commit_line_idx = 0
    for i, line in ipairs(sl_lines) do
      local commit = ssl_utils.parse_diff_line(line)
      if commit ~= nil and commit.is_current then
        current_commit_line_idx = i
        break
      end
    end
    if current_commit_line_idx == 0 then
      return clear_previous()
    end

    -- Get the decoration graph lines (mimics that diff tree continues)
    -- should work for all commits but the topmost one
    ---@type string
    local graph_lines
    if current_commit_line_idx >= 2 then
      local prev_line = sl_lines[current_commit_line_idx - 1]
      graph_lines = vim.trim(prev_line:match("^[╷ │]+") or "")
    else
      graph_lines = ""
    end

    -- find what index is "@" in current commit line
    local current_commit_line = sl_lines[current_commit_line_idx]
    local current_commit_line_at = current_commit_line:find("@")

    -- vim.fn.* & vim.api.nvim_buf_set_extmark
    -- cannot be canned inside lua fast function
    vim.schedule(function()
      if not is_latest() then
        return
      end
      local status_pad = graph_lines
        .. string.rep(
          " ",
          current_commit_line_at
            + 2
            -- use `strdisplaywidth` for the dispayed width
            - vim.fn.strdisplaywidth(graph_lines)
        )

      -- lua_ls doesn't recognise virt lines type: {{string ,string}}[]
      -- Each line consists of individually highlighted parts
      ---@type {[1]: string, [2]: string}[][]
      local virtual_status_lines = {}
      if SSL_STATE.merge_conflict then
        table.insert(virtual_status_lines, {
          { status_pad, "Normal" },
          { "Rebase failed due to merge conflicts", "@error" },
        })
        table.insert(virtual_status_lines, {
          {
            status_pad,
            "Normal",
          },
          {
            "Use :HgDiff to locate conflicts and :HgResolve to mark files as resolved",
            "Normal",
          },
        })
        table.insert(virtual_status_lines, { { status_pad, "Normal" } })
      end

      table.insert(
        virtual_status_lines,
        { { status_pad, "Normal" }, { "Status:", "Title" } }
      )

      for _, status_line in ipairs(status_lines) do
        local hl = "Normal"
        local char = status_line:sub(1, 1)
        if char == "M" then
          hl = "Normal"
        elseif char == "A" then
          hl = "@diff.added"
        elseif char == "R" then
          hl = "@diff.deleted"
        elseif char == "?" then
          hl = "@diff.delta"
        elseif char == "I" then
          hl = "Comment"
        elseif char == "!" then
          hl = "@diff.delta"
        end
        table.insert(
          virtual_status_lines,
          { { status_pad, "Normal" }, { status_line, hl } }
        )
      end
      table.insert(virtual_status_lines, { { status_pad, "Normal" } })

      vim.api.nvim_buf_clear_namespace(bufnr, SSL_NS, 0, -1)
      vim.api.nvim_buf_set_extmark(
        bufnr,
        SSL_NS,
        math.max(current_commit_line_idx - 2, 0),
        0,
        {
          hl_mode = "combine",
          virt_lines = virtual_status_lines,
        }
      )
    end)
  end)
end

---@param lines string[]
---@return string[]
local function filter_hidden_bookmarks(lines)
  local hidden = CONFIG.ssl.hidden_bookmarks
  if #hidden == 0 then
    return lines
  end

  local result = {}
  local skip_next_connector = false
  for _, line in ipairs(lines) do
    if skip_next_connector then
      skip_next_connector = false
      -- drop bare connector lines (e.g. "╷") that follow a hidden bookmark
      if line:match("^%s*╷%s*$") then
        goto continue
      end
    end

    local commit = ssl_utils.parse_diff_line(line)
    if commit and commit.is_bookmark and not commit.is_current then
      local dominated = true
      for _, bm in ipairs(vim.split(commit.bookmarks, " ")) do
        local is_hidden = false
        for _, pat in ipairs(hidden) do
          if bm:find(pat, 1, true) then
            is_hidden = true
            break
          end
        end
        if not is_hidden then
          dominated = false
          break
        end
      end
      if dominated then
        skip_next_connector = true
        goto continue
      end
    end

    table.insert(result, line)
    ::continue::
  end
  return result
end

---Assigns hop labels and draws them as virtual text. "Special" commits -- the
---current commit and bookmark commits -- get single-key labels; every other
---commit gets a two-char label. Labels are inert decorations: no keymaps are
---bound. The jump itself is performed transiently by `ssl_start_hop`.
---@param bufnr number
---@param lines string[]
local function ssl_place_hop_labels(bufnr, lines)
  vim.api.nvim_buf_clear_namespace(bufnr, SSL_HOP_NS, 0, -1)
  SSL_STATE.hop_map = {}

  local current_line
  local bookmark_lines = {}
  local normal_lines = {}
  for i, line in ipairs(lines) do
    local commit = ssl_utils.parse_diff_line(line)
    if commit then
      if commit.is_current then
        current_line = i
      elseif commit.is_bookmark then
        bookmark_lines[#bookmark_lines + 1] = i
      else
        normal_lines[#normal_lines + 1] = i
      end
    end
  end

  -- special commits, current first then bookmarks, each get a single-key label
  local special_lines = {}
  if current_line then
    special_lines[#special_lines + 1] = current_line
  end
  for _, ln in ipairs(bookmark_lines) do
    special_lines[#special_lines + 1] = ln
  end

  local function place(line_nr, label)
    vim.api.nvim_buf_set_extmark(bufnr, SSL_HOP_NS, line_nr - 1, 0, {
      virt_text = { { " [" .. label .. "]", "HgSslHopLabel" } },
      virt_text_pos = "eol",
    })
    SSL_STATE.hop_map[label] = line_nr
  end

  for idx, line_nr in ipairs(special_lines) do
    local label = HOP_SPECIAL_CHARS:sub(idx, idx)
    if label == "" then
      -- ran out of single-key labels; demote to a two-char label
      normal_lines[#normal_lines + 1] = line_nr
    else
      place(line_nr, label)
    end
  end

  for idx, line_nr in ipairs(normal_lines) do
    local label = HOP_LABELS[idx]
    if not label then
      break
    end
    place(line_nr, label)
  end
end

---Transient hop triggered by the `f` key. Reads the label under the cursor's
---intent and jumps to its commit. Single-key (special) labels resolve in one
---keypress; all others read a second character. Nothing stays bound afterward,
---so no buffer keys are shadowed outside of this call.
local function ssl_start_hop()
  local hop_map = SSL_STATE.hop_map or {}
  if vim.tbl_isempty(hop_map) then
    return
  end

  local ok, c1 = pcall(vim.fn.getcharstr)
  if not ok or c1 == "" or c1 == "\27" then
    return
  end

  local target = hop_map[c1]
  if target == nil then
    -- not a single-key label; it must be the first char of a two-char label
    local has_prefix = false
    for label in pairs(hop_map) do
      if #label == 2 and label:sub(1, 1) == c1 then
        has_prefix = true
        break
      end
    end
    if not has_prefix then
      vim.notify("No hop label '" .. c1 .. "'", vim.log.levels.WARN)
      return
    end
    local ok2, c2 = pcall(vim.fn.getcharstr)
    if not ok2 or c2 == "" or c2 == "\27" then
      return
    end
    target = hop_map[c1 .. c2]
    if target == nil then
      vim.notify("No hop label '" .. c1 .. c2 .. "'", vim.log.levels.WARN)
      return
    end
  end

  vim.api.nvim_win_set_cursor(0, { target, 0 })
end

local function ssl_highlight_current_line(bufnr, lines)
  vim.api.nvim_buf_clear_namespace(bufnr, SSL_CURRENT_NS, 0, -1)
  for i, line in ipairs(lines) do
    local commit = ssl_utils.parse_diff_line(line)
    if commit and commit.is_current then
      vim.api.nvim_buf_set_extmark(bufnr, SSL_CURRENT_NS, i - 1, 0, {
        line_hl_group = "HgSslCurrentLine",
      })
      break
    end
  end
end

---@param bufnr number?
function ssl_utils.refresh_buffer(bufnr)
  bufnr = bufnr or SSL_STATE.bufnr
  SSL_STATE.refresh_gen = SSL_STATE.refresh_gen + 1
  SSL_STATE.refreshing = true
  local gen, root = SSL_STATE.refresh_gen, SSL_STATE.repo_root

  local function is_latest()
    return gen == SSL_STATE.refresh_gen
      and root == SSL_STATE.repo_root
      and bufnr ~= nil
      and vim.api.nvim_buf_is_valid(bufnr)
  end

  local function fail(msg)
    if is_latest() then
      SSL_STATE.refreshing = false
      vim.notify(msg, vim.log.levels.ERROR)
    end
  end

  local function show(lines)
    vim.api.nvim_buf_set_lines(bufnr, 0, -1, false, lines)
    ssl_highlight_current_line(bufnr, lines)
    ssl_place_hop_labels(bufnr, lines)
  end

  ---@param cmd string[]
  ---@param on_exit fun(obj: vim.SystemCompleted)
  local function run(cmd, on_exit)
    local ok, err = pcall(vim.system, cmd, { text = true, cwd = root }, function(obj)
      vim.schedule(function()
        on_exit(obj)
      end)
    end)
    if not ok then
      fail("Failed to run " .. table.concat(cmd, " ") .. ": " .. tostring(err))
    end
  end

  -- hg sl (~1 s in fbsource) fills the panel while hg ssl (~10 s, it asks
  -- Phabricator for diff statuses) is still running; both start now
  local ssl_done = false
  run({ "hg", "sl" }, function(sl_obj)
    if sl_obj.code ~= 0 or ssl_done or not is_latest() then
      return
    end
    show(filter_hidden_bookmarks(vim.split(sl_obj.stdout, "\n")))
  end)

  run({ "hg", "ssl" }, function(ssl_obj)
    if ssl_obj.code ~= 0 then
      -- leave hg sl's output up
      return fail("Failed to run hg ssl")
    end
    ssl_done = true
    if not is_latest() then
      return
    end
    SSL_STATE.refreshing = false
    SSL_STATE.refreshed_at = vim.uv.now()
    local ssl_lines = filter_hidden_bookmarks(vim.split(ssl_obj.stdout, "\n"))
    show(ssl_lines)
    vim.notify("ssl updated", vim.log.levels.INFO)

    if CONFIG.ssl.status then
      ssl_utils.annotate_hg_status(bufnr, ssl_lines, is_latest)
    end
  end)
end

-- using manual search to avoid polluting jump list
function ssl_utils.go_to_current_commit()
  local lines = vim.api.nvim_buf_get_lines(0, 0, 999999, false)
  for i, v in ipairs(lines) do
    local commit = ssl_utils.parse_diff_line(v)
    if commit and commit.is_current then
      return vim.api.nvim_win_set_cursor(0, { i, 0 })
    end
  end
end

--- Used by most SSL commands to run commands and refresh buffer
---@param cmd string[]
---@param bufnr number
---@param reload boolean
---@param msg ?string
function ssl_utils.run_cmd(cmd, bufnr, reload, msg)
  if (cmd[1] == "hg" and cmd[2] ~= "ssl") or cmd[1] ~= "hg" then
    -- similar to vscode always display
    -- what command is running in the background
    vim.notify("Running: " .. table.concat(cmd, " "), vim.log.levels.INFO)
  end

  SSL_STATE.busy = SSL_STATE.busy + 1

  local ok, err = pcall(vim.system, cmd, { text = true, cwd = SSL_STATE.repo_root }, function(obj)
    SSL_STATE.busy = SSL_STATE.busy - 1
    -- in case of an error we surface full output to the user
    if obj.code ~= 0 then
      local stderr = vim
        .iter(vim.split(vim.trim(obj.stderr or ""), "\n"))
        :map(function(line)
          return "STDERR: " .. line
        end)
        :totable()

      local stdout = vim
        .iter(vim.split(vim.trim(obj.stdout or ""), "\n"))
        :map(function(line)
          return "STDOUT: " .. line
        end)
        :totable()

      vim.schedule(function()
        vim.notify(
          "Failed to '"
            .. table.concat(cmd, " ")
            .. "':\n"
            .. table.concat(stderr, "\n")
            .. "\n\n"
            .. table.concat(stdout, "\n"),
          vim.log.levels.ERROR
        )
      end)
      return
    end

    vim.schedule(function()
      ssl_utils.action_end({
        reload = reload,
        bufnr = bufnr,
        msg = msg,
      })
    end)
  end)
  if not ok then
    SSL_STATE.busy = SSL_STATE.busy - 1
    vim.notify("Failed to run " .. table.concat(cmd, " ") .. ": " .. tostring(err), vim.log.levels.ERROR)
  end
end

local diff_session = require("lib.diff-session")

local scratch_buf_counter = 0

---@param lines string[]
---@param name string Suffixed with a counter so every call gets its own buffer.
---@param file_path string
---@return integer
local function create_scratch_buf(lines, name, file_path)
  scratch_buf_counter = scratch_buf_counter + 1
  -- A CRLF file comes out of hg cat with "\r" on every line, but loads into a
  -- file buffer as fileformat=dos without them; match that, or every line
  -- differs from the live side. A last line without a newline has no "\r".
  local crlf = vim.list_slice(lines, 1, #lines - 1)
  if #lines > 0 and lines[#lines]:sub(-1) == "\r" then
    table.insert(crlf, lines[#lines])
  end
  local dos = #crlf > 0 and vim.iter(crlf):all(function(line)
    return line:sub(-1) == "\r"
  end)
  if dos then
    lines = vim.tbl_map(function(line)
      return (line:gsub("\r$", ""))
    end, lines)
  end
  local buf = vim.api.nvim_create_buf(false, true)
  vim.api.nvim_buf_set_lines(buf, 0, -1, false, lines)
  if dos then
    vim.bo[buf].fileformat = "dos"
  end
  vim.api.nvim_buf_set_name(buf, name .. "#" .. scratch_buf_counter)
  vim.bo[buf].buftype = "nofile"
  vim.bo[buf].modifiable = false
  vim.bo[buf].swapfile = false
  local ft = vim.filetype.match({ filename = file_path, contents = lines })
  if ft then
    vim.bo[buf].filetype = ft
  end
  return buf
end

---@param session Hg.diff_session
local function diff_split_update_winbar(session)
  local pair = session.pairs[session.index]
  local pos = string.format("[%d/%d]", session.index, #session.pairs)

  local function rev_label(label, rev)
    return label == rev and rev or (label .. " " .. rev)
  end

  vim.api.nvim_win_set_var(session.right_win, "custom_winbar_text",
    "%#Comment# " .. rev_label(session.labels.old, session.parent_rev) .. " %* "
      .. (pair.old_file or pair.file) .. " " .. pos)

  if pair.is_live then
    vim.api.nvim_win_set_var(session.left_win, "custom_winbar_text",
      "%#DiagnosticOk# LIVE %* " .. pair.file .. " " .. pos)
  else
    vim.api.nvim_win_set_var(session.left_win, "custom_winbar_text",
      "%#Comment# " .. rev_label(session.labels.new, session.commit.hash) .. " %* "
        .. pair.file .. " " .. pos)
  end
  require("lualine").refresh()
end

local DIFF_PREFETCH_BATCH = 50
local DIFF_FETCH_TIMEOUT_MS = 60000

---@param session Hg.diff_session
---@param rev string
---@return Hg.rev_cache
local function diff_rev_cache(session, rev)
  local cache = session.cache[rev]
  if not cache then
    cache = { contents = {}, pending = {}, errors = {} }
    session.cache[rev] = cache
  end
  return cache
end

--- Fetches `paths` at `rev` into the session cache with a single `hg cat`.
--- If hg fails the paths are recorded as errors.
---@param session Hg.diff_session
---@param rev string
---@param paths string[]
---@param on_done fun()?
local function diff_fetch(session, rev, paths, on_done)
  local cache = diff_rev_cache(session, rev)
  local cmd = { "hg", "cat", "-r", rev, "-T", "json" }
  for _, path in ipairs(paths) do
    cache.pending[path] = true
    table.insert(cmd, "path:" .. path)
  end
  vim.system(cmd, { cwd = session.repo_root }, function(out)
    -- exits 1 when none of the paths exist at rev
    local ok, entries = pcall(vim.json.decode, out.stdout or "")
    if (out.code == 0 or out.code == 1) and ok and type(entries) == "table" then
      for _, path in ipairs(paths) do
        cache.contents[path] = false
      end
      for _, entry in ipairs(entries) do
        cache.contents[entry.abspath] = split_file_lines(entry.data)
      end
    else
      local err = vim.trim(out.stderr or "")
      for _, path in ipairs(paths) do
        cache.errors[path] = err ~= "" and err or ("hg cat exited " .. out.code)
      end
    end
    for _, path in ipairs(paths) do
      cache.pending[path] = nil
    end
    if on_done then
      vim.schedule(on_done)
    end
  end)
end

--- Fetches `paths` at `rev` in the background, in order, one batch at a time.
---@param session Hg.diff_session
---@param rev string
---@param paths string[]
local function diff_prefetch(session, rev, paths)
  local start = 1
  local function next_batch()
    if session.closed or start > #paths then
      return
    end
    -- the first file goes alone so the first diff can show before the rest arrive
    local size = start == 1 and 1 or DIFF_PREFETCH_BATCH
    local batch = vim.list_slice(paths, start, start + size - 1)
    start = start + size
    diff_fetch(session, rev, batch, next_batch)
  end
  next_batch()
end

--- Blocks until every wanted file is cached or known unreadable, fetching any
--- that no batch has claimed yet.
---@param session Hg.diff_session
---@param wants { rev: string, path: string }[]
local function diff_await(session, wants)
  for _, want in ipairs(wants) do
    local cache = diff_rev_cache(session, want.rev)
    if cache.contents[want.path] == nil and not cache.pending[want.path] then
      cache.errors[want.path] = nil
      diff_fetch(session, want.rev, { want.path })
    end
  end
  local done = vim.wait(DIFF_FETCH_TIMEOUT_MS, function()
    return vim.iter(wants):all(function(want)
      return not session.cache[want.rev].pending[want.path]
    end)
  end, 10)
  if not done then
    for _, want in ipairs(wants) do
      local cache = session.cache[want.rev]
      if cache.pending[want.path] then
        cache.errors[want.path] = "timed out after " .. DIFF_FETCH_TIMEOUT_MS / 1000 .. " s"
      end
    end
  end
end

--- Lines to show for `path` at `rev`: its content, nothing when it doesn't
--- exist there, or a line saying why it couldn't be read.
---@param session Hg.diff_session
---@param rev string
---@param path string
---@return string[]
local function diff_side_lines(session, rev, path)
  local cache = session.cache[rev]
  local lines = cache.contents[path]
  if lines then
    return lines
  end
  local err = cache.errors[path]
  if err then
    return { string.format("<failed to read %s at %s: %s>", path, rev, err) }
  end
  return {}
end

---@param session Hg.diff_session
---@param pair Hg.diff_file_pair
local function diff_split_load_pair(session, pair)
  if pair.old_buf then
    return
  end

  local new_rev = session.commit.hash
  local old_file = pair.old_file or pair.file
  local real_path = session.repo_root .. "/" .. pair.file
  local is_live = session.commit.is_current and vim.fn.filereadable(real_path) == 1

  local wants = { { rev = session.parent_rev, path = old_file } }
  if not is_live then
    table.insert(wants, { rev = new_rev, path = pair.file })
  end
  diff_await(session, wants)

  local new_buf
  if is_live then
    new_buf = vim.fn.bufadd(real_path)
    if not vim.api.nvim_buf_is_loaded(new_buf) then
      -- loading here would stop on a swap-exists prompt
      vim.bo[new_buf].swapfile = false
      vim.fn.bufload(new_buf)
    end
  else
    new_buf = create_scratch_buf(
      diff_side_lines(session, new_rev, pair.file),
      "hg-diff://" .. new_rev .. "/" .. pair.file,
      pair.file
    )
  end

  pair.old_buf = create_scratch_buf(
    diff_side_lines(session, session.parent_rev, old_file),
    "hg-diff://" .. session.parent_rev .. "/" .. old_file,
    old_file
  )
  pair.new_buf = new_buf
  pair.is_live = is_live

  diff_session.attach_pair(session, pair)
end

---Hashes for revisions relative to the working copy ("." or ".^"), which
---move when you commit or amend; other revisions are returned unchanged.
---@param repo_root string
---@param revs string[]
---@return string[]
local function pin_revs(repo_root, revs)
  local cmd = { "hg", "log", "-T", "{node|short}\n" }
  ---@type string[]
  local relative = {}
  for _, rev in ipairs(revs) do
    if rev:find("^%.") and not vim.tbl_contains(relative, rev) then
      table.insert(relative, rev)
      vim.list_extend(cmd, { "-r", rev })
    end
  end
  if #relative == 0 then
    return revs
  end
  -- one hg call for all of them (~0.3 s each in fbsource); output keeps -r order
  local out = vim.system(cmd, { text = true, cwd = repo_root }):wait()
  local hashes = vim.split(vim.trim(out.stdout or ""), "\n", { trimempty = true })
  if out.code ~= 0 or #hashes ~= #relative then
    return revs
  end
  return vim.tbl_map(function(rev)
    local i = vim.fn.index(relative, rev) + 1
    return i > 0 and hashes[i] or rev
  end, revs)
end

---@param opts Hg.diff_split_opts
local function open_diff_split(opts)
  -- the file list can be stale (HgChanges after a revert elsewhere)
  if #opts.files == 0 then
    vim.notify("No changed files to diff", vim.log.levels.INFO)
    return
  end
  local tab = vim.api.nvim_get_current_tabpage()
  local return_win = vim.api.nvim_get_current_win()
  local previous = DIFF_SPLIT_SESSIONS[tab]
  if previous then
    -- reopened from the previous split's own panes: take its place
    if return_win == previous.left_win or return_win == previous.right_win then
      return_win = previous.return_win
    end
    diff_session.close(previous)
  end
  local split_from = opts.split_from or return_win
  if not vim.api.nvim_win_is_valid(split_from) then
    split_from = vim.api.nvim_get_current_win()
  end
  vim.api.nvim_set_current_win(split_from)
  local left_win, right_win = diff_session.create_pair_wins()
  local pinned = pin_revs(opts.repo_root, { opts.parent_rev, opts.target.hash })

  ---@type Hg.diff_session
  local session = {
    pairs = vim.tbl_map(function(spec)
      return { file = spec.file, old_file = spec.old_file, is_live = false }
    end, opts.files),
    index = 1,
    left_win = left_win,
    right_win = right_win,
    commit = vim.tbl_extend("force", opts.target, { hash = pinned[2] }),
    parent_rev = pinned[1],
    labels = { old = opts.parent_rev, new = opts.target.hash },
    repo_root = opts.repo_root,
    return_win = return_win,
    update_winbar = diff_split_update_winbar,
    cache = {},
    closed = false,
  }

  session.on_close = function()
    if session.closed then
      return
    end
    session.closed = true
    for _, win in ipairs({ left_win, right_win }) do
      if vim.api.nvim_win_is_valid(win) then
        pcall(vim.api.nvim_win_close, win, true)
      end
    end
    diff_session.cleanup(session)
    if vim.api.nvim_win_is_valid(return_win) then
      vim.api.nvim_set_current_win(return_win)
    end
  end

  -- Clean up if either diff window is closed externally
  for _, win in ipairs({ left_win, right_win }) do
    vim.api.nvim_create_autocmd("WinClosed", {
      pattern = tostring(win),
      once = true,
      callback = function()
        vim.schedule(function()
          diff_session.close(session)
        end)
      end,
    })
  end

  for _, pair in ipairs(session.pairs) do
    pair.load = function(p)
      diff_split_load_pair(session, p)
    end
  end

  diff_session.register(tab, session)

  diff_prefetch(session, session.parent_rev, vim.tbl_map(function(pair)
    return pair.old_file or pair.file
  end, session.pairs))
  if not session.commit.is_current then
    diff_prefetch(session, session.commit.hash, vim.tbl_map(function(pair)
      return pair.file
    end, session.pairs))
  end

  diff_split_load_pair(session, session.pairs[1])
  if not session.pairs[1].old_buf then
    session.on_close()
    return
  end

  diff_session.show_pair(session, 1)
end

--- Changed files, each paired with its copy source. Sources that were renamed
--- away are dropped, so a rename shows as one diff instead of a delete and an add.
---@param repo_root string
---@param opts { rev: string?, base: string?, paths: string[]? } `rev` lists
--- that commit's changes (M/A/R); otherwise the working copy's modified and
--- added files, relative to `base` (default: the working-copy parent).
--- `paths` restricts the listing to those repo-relative paths.
---@return Hg.diff_file_spec[]? nil when hg fails
local function changed_files(repo_root, opts)
  if opts.paths and #opts.paths == 0 then
    return {}
  end
  local cmd = { "hg", "status", "--copies" }
  if opts.rev then
    vim.list_extend(cmd, { "--change", opts.rev })
  else
    if opts.base then
      vim.list_extend(cmd, { "--rev", opts.base })
    end
    vim.list_extend(cmd, { "--modified", "--added" })
  end
  for _, path in ipairs(opts.paths or {}) do
    table.insert(cmd, "path:" .. path)
  end
  local out = vim.system(cmd, { text = true, cwd = repo_root }):wait()
  if out.code ~= 0 then
    return nil
  end

  local files, sources, removed = {}, {}, {}
  for _, line in ipairs(vim.split(out.stdout or "", "\n", { trimempty = true })) do
    -- `--copies` prints an added file's source indented on the next line
    local source = line:match("^  (.+)")
    if source then
      sources[files[#files]] = source
    else
      local path = line:sub(3)
      table.insert(files, path)
      if line:sub(1, 1) == "R" then
        removed[path] = true
      end
    end
  end

  local renamed_away = {}
  for _, source in pairs(sources) do
    if removed[source] then
      renamed_away[source] = true
    end
  end
  local specs = {}
  for _, file in ipairs(files) do
    if not renamed_away[file] then
      table.insert(specs, { file = file, old_file = sources[file] })
    end
  end
  -- status groups by kind (M, A, R); show files in path order instead
  table.sort(specs, function(a, b)
    return a.file < b.file
  end)
  return specs
end

---@param repo_root string
local function open_working_copy_diff_split(repo_root)
  local files = changed_files(repo_root, {})
  if not files then
    vim.notify("hg status failed", vim.log.levels.ERROR)
    return
  end
  if #files == 0 then
    vim.notify("No uncommitted changes", vim.log.levels.INFO)
    return
  end

  open_diff_split({
    files = files,
    target = { hash = ".", is_current = true },
    parent_rev = ".",
    repo_root = repo_root,
  })
end

---Diff split of one file: the working copy against `base`.
---@param repo_root string
---@param path string absolute
---@param base string
local function open_file_diff_split(repo_root, path, base)
  local rel = vim.fs.relpath(repo_root, path)
  if not rel then
    vim.notify(path .. " is not in " .. repo_root, vim.log.levels.ERROR)
    return
  end
  local files = changed_files(repo_root, { base = base, paths = { rel } })
  if not files then
    vim.notify("hg status failed", vim.log.levels.ERROR)
    return
  end
  open_diff_split({
    -- an unchanged file still opens, showing no differences
    files = #files > 0 and files or { { file = rel } },
    target = { hash = ".", is_current = true },
    parent_rev = base,
    repo_root = repo_root,
  })
end

---@class (exact) Hg.ssl_command
---@field desc string
---@field action fun(commit: Hg.commit, bufnr: number)
---@field readonly ?boolean Doesn't change the repo, so it may run while
--- another panel command is still running (see `ssl_action`).

local HgChanges -- forward declaration; defined after HgStatus

-- Here will be the command in the future diffs
---@type table<string, Hg.ssl_command>
local SSL_COMMANDS = {
  show = {
    desc = "Show commit message and changes",
    readonly = true,
    action = function(commit, bufnr)
      local cmd = {
        "sh",
        "-c",
        'hg show --color=always "$1" | less -R',
        "sh",
        commit.hash,
      }
      hg_utils.run_cmd_and_exit(cmd, function()
        ssl_utils.action_end({
          reload = true,
          bufnr = bufnr,
        })
      end, function()
        vim.cmd("startinsert")
      end, SSL_STATE.repo_root)
    end,
  },
  diff_split = {
    desc = "View file diffs (side-by-side split, cycle with ]f/[f)",
    readonly = true,
    action = function(commit)
      local repo_root = SSL_STATE.repo_root or buf_repo_root()
      local files = changed_files(repo_root, { rev = commit.hash })
      if not files then
        vim.notify("Failed to list files changed in " .. commit.hash, vim.log.levels.ERROR)
        return
      end

      if #files == 0 then
        vim.notify("No files changed in this commit", vim.log.levels.WARN)
        return
      end

      open_diff_split({
        files = files,
        target = { hash = commit.hash, is_current = commit.is_current, diff = commit.diff },
        parent_rev = commit.hash .. "^",
        repo_root = repo_root,
      })
    end,
  },
  uncommit = {
    desc = "Uncommit all of the current commit",
    action = function(commit, bufnr)
      if not commit.is_current then
        vim.notify("Cannot uncommit non-current commit", vim.log.levels.ERROR)
        return
      end
      ssl_utils.run_cmd({ "hg", "uncommit" }, bufnr, true)
    end,
  },
  changes = {
    desc = "Select files to amend or discard",
    action = function()
      HgChanges()
    end,
  },
  hide = {
    desc = "Hide commit from smartlog",
    action = function(commit, bufnr)
      -- we can have multiple commits with the same diff
      -- yet different hashes. In this case we most likely
      -- want to hide a commit by hash, not diff number
      local target = commit.hash and commit.hash or commit.diff
      ssl_utils.run_cmd(
        { "hg", "hide", target },
        bufnr,
        true,
        "Commit has been hidden: " .. target
      )
    end,
  },
  -- showmeta = {
  --   desc = "Show parsed commit data",
  --   action = function(commit, bufnr)
  --     vim.print(vim.inspect(commit))
  --   end,
  -- },
  metaedit = {
    desc = "Edit commit message (metaedit / reword)",
    action = function(commit, bufnr)
      local cmd = { "hg", "metaedit" }

      if not commit.is_current then
        table.insert(cmd, commit.hash)
      end

      hg_utils.run_cmd_and_exit(cmd, function()
        ssl_utils.action_end({
          reload = true,
          bufnr = bufnr,
        })
      end, nil, SSL_STATE.repo_root)
    end,
  },
  histedit = {
    desc = "Interactively reorder, combine, or delete commits (histedit)",
    action = function(commit, bufnr)
      local cmd = { "hg", "histedit" }

      if not commit.is_current then
        vim.notify("Cannot histedit non-current commit", vim.log.levels.ERROR)
        return
      end

      hg_utils.run_cmd_and_exit(cmd, function()
        ssl_utils.action_end({
          reload = true,
          bufnr = bufnr,
        })
      end, nil, SSL_STATE.repo_root)
    end,
  },
  split = {
    desc = "Split a commit into smaller commits",
    action = function(commit, bufnr)
      local cmd = { "hg", "split" }

      if not commit.is_current then
        table.insert(cmd, "--rev")
        table.insert(cmd, commit.hash)
      end

      hg_utils.run_cmd_and_exit(cmd, function()
        ssl_utils.action_end({
          reload = true,
          bufnr = bufnr,
        })
      end, nil, SSL_STATE.repo_root)
    end,
  },
  checkout = {
    desc = "Checkout this commit",
    action = function(commit, bufnr)
      ssl_utils.run_cmd(
        { "hg", "checkout", commit.hash },
        bufnr,
        true,
        "You are currently on " .. commit.hash
      )
    end,
  },
  arc_pull = {
    desc = "arc pull to update repository",
    action = function(_, bufnr)
      ssl_utils.run_cmd({ "arc", "pull" }, bufnr, true)
    end,
  },
  open_in_phabricator = {
    desc = "Open in phabricator",
    readonly = true,
    action = function(commit)
      meta_util.open_url("https://www.internalfb.com/diff/" .. commit.diff)
    end,
  },
  submit = {
    desc = "Submit to phabricator",
    action = function(commit, bufnr)
      local cmd = { "jf", "submit" }
      if not commit.is_current then
        table.insert(cmd, commit.hash)
      end
      ssl_utils.run_cmd(cmd, bufnr, true)
    end,
  },
  submit_stack = {
    desc = "Submit stack to phabricator",
    action = function(commit, bufnr)
      local cmd = { "jf", "submit" }
      if commit.is_current then
        table.insert(cmd, "--stack")
      else
        table.insert(cmd, "--rev")
        table.insert(cmd, "::" .. commit.hash)
      end
      ssl_utils.run_cmd(cmd, bufnr, true)
    end,
  },
  submit_draft = {
    desc = "Submit as a draft to phabricator",
    action = function(commit, bufnr)
      local cmd = { "jf", "submit", "--draft" }
      if not commit.is_current then
        table.insert(cmd, commit.hash)
      end
      ssl_utils.run_cmd(cmd, bufnr, true)
    end,
  },
  submit_draft_stack = {
    desc = "Submit stack as drafts to phabricator",
    action = function(commit, bufnr)
      local cmd = { "jf", "submit", "--draft" }
      if commit.is_current then
        table.insert(cmd, "--stack")
      else
        table.insert(cmd, "--rev")
        table.insert(cmd, "::" .. commit.hash)
      end
      ssl_utils.run_cmd(cmd, bufnr, true)
    end,
  },
  author_internal_profile = {
    desc = "View author's internal profile",
    readonly = true,
    action = function(commit)
      meta_util.open_url(
        "https://www.internalfb.com/intern/bunny/?q=ip+" .. commit.author
      )
    end,
  },
  rebase_abort = {
    desc = "Abort rebase",
    action = function(_, bufnr)
      ssl_utils.run_cmd({ "hg", "rebase", "--abort" }, bufnr, true)
    end,
  },
  rebase_continue = {
    desc = "Continue rebase",
    action = function(_, bufnr)
      ssl_utils.run_cmd({ "hg", "rebase", "--continue" }, bufnr, true)
    end,
  },
  rebase = {
    desc = "Rebase onto another commit",
    action = function(commit, bufnr)
      ---@type string[]
      local choices = { "custom" }
      ---@type {[string]: Hg.commit}
      local commit_dict = {}
      for _, c in ipairs(ssl_utils.collect_commits()) do
        if commit.hash ~= c.hash then
          local key = c.hash or c.bookmarks
          commit_dict[key] = c
          table.insert(choices, key)
        end
      end

      ---@param destination_commit_id string
      local function on_select_destination(destination_commit_id)
        local source = commit.hash

        local cmd =
          { "hg", "rebase", "-s", source, "-d", destination_commit_id }

        -- ssl_utils.run_cmd(cmd, bufnr, true, "Rebase completed")
        vim.notify("Running: " .. table.concat(cmd, " "), vim.log.levels.INFO)
        SSL_STATE.busy = SSL_STATE.busy + 1

        local ok, err = pcall(vim.system, cmd, { text = true, cwd = SSL_STATE.repo_root }, function(obj)
          SSL_STATE.busy = SSL_STATE.busy - 1
          -- success
          if obj.code == 0 then
            SSL_STATE.merge_conflict = false
            return ssl_utils.action_end({
              reload = true,
              bufnr = bufnr,
              msg = "Rebased completed",
            })
          end

          -- fail
          vim.schedule(function()
            local stderr = obj.stderr or ""
            if string.find(stderr, "hit merge conflicts") then
              SSL_STATE.merge_conflict = true

              -- won't rebase due to local changes
              if string.find(stderr, "but you have working copy changes") then
                return ssl_utils.action_end({
                  reload = true,
                  bufnr = bufnr,
                  msg = "Rebased failed due to local changes, please commit or amend them before rebasing",
                })
              end

              -- user needs to resolve merge conflicts
              if string.find(stderr, " conflicts while merging ") then
                return ssl_utils.action_end({
                  reload = true,
                  bufnr = bufnr,
                  msg = "Rebased failed due to merge conflicts",
                })
              end
            end

            return ssl_utils.action_end({
              reload = true,
              bufnr = bufnr,
              msg = string.format(
                "Rebase failed unexpectedly\n\nSTDOUT:\n%s\n\nSTDERR:\n%s",
                obj.stdout or "",
                stderr
              ),
            })
          end)
        end)
        if not ok then
          SSL_STATE.busy = SSL_STATE.busy - 1
          vim.notify("Failed to run " .. table.concat(cmd, " ") .. ": " .. tostring(err), vim.log.levels.ERROR)
        end
      end

      vim.ui.select(choices, {
        prompt = "Pick destination:",
        format_item = function(v)
          local c = commit_dict[v]
          if c then
            local line = {}
            if c.diff then
              table.insert(line, c.diff)
            end
            if c.is_bookmark then
              table.insert(line, c.bookmarks)
            end
            if c.author then
              table.insert(line, c.author)
            end
            if c.date then
              table.insert(line, c.date)
            end
            if c.msg then
              table.insert(line, c.msg)
            end
            return table.concat(line, " ")
          end

          return v
        end,
      }, function(choice)
        if choice == nil then
          return vim.schedule(function()
            ssl_utils.action_end({ msg = "Rebase cancelled" })
          end)
        elseif choice == "custom" then
          vim.ui.input(
            { prompt = "Enter destination commit: " },
            function(input)
              if input == nil then
                return
              end

              on_select_destination(input)
            end
          )
        else
          on_select_destination(choice)
        end
      end)
    end,
  },
}

local function HgSsl(opts)
  opts = opts or {}
  local split = opts.split or false

  log_to_scuba({
    module = "hg",
    command = "HgSsl",
  })

  -- Pin the panel to the repo of the buffer we're launching from, so every
  -- action runs against that repo regardless of nvim's global cwd. Launching
  -- from a file in another repo re-targets the panel. When re-invoked while the
  -- panel itself is focused (a virtual buffer with no file), keep the current
  -- pin rather than falling back to cwd.
  local launch_root
  if SSL_STATE.bufnr and vim.api.nvim_get_current_buf() == SSL_STATE.bufnr then
    launch_root = SSL_STATE.repo_root or buf_repo_root()
  else
    launch_root = buf_repo_root()
  end

  -- :bdelete unloads the panel but keeps it valid, without its options and
  -- mappings; wipe it so it gets rebuilt below.
  if
    SSL_STATE.bufnr
    and vim.api.nvim_buf_is_valid(SSL_STATE.bufnr)
    and not vim.api.nvim_buf_is_loaded(SSL_STATE.bufnr)
  then
    vim.api.nvim_buf_delete(SSL_STATE.bufnr, { force = true })
  end

  if SSL_STATE.bufnr and vim.api.nvim_buf_is_valid(SSL_STATE.bufnr) then
    local retargeted = SSL_STATE.repo_root ~= launch_root
    SSL_STATE.repo_root = launch_root

    -- Only reuse a window showing the panel in the *current* tabpage. Using
    -- vim.fn.win_findbuf() here would match a window in another tab and warp us
    -- there; we want the panel to appear in the tab we invoked from.
    local win
    for _, w in ipairs(vim.api.nvim_tabpage_list_wins(0)) do
      if vim.api.nvim_win_get_buf(w) == SSL_STATE.bufnr then
        win = w
        break
      end
    end
    if win then
      vim.api.nvim_set_current_win(win)
    elseif split then
      vim.cmd("botright vnew")
      vim.api.nvim_set_current_buf(SSL_STATE.bufnr)
    else
      vim.api.nvim_set_current_buf(SSL_STATE.bufnr)
    end

    if retargeted then
      vim.api.nvim_buf_set_name(SSL_STATE.bufnr, "hgssl [" .. launch_root .. "]")
      -- the old repo's commits would be acted on in the new one
      vim.api.nvim_buf_set_lines(SSL_STATE.bufnr, 0, -1, false, {})
      for _, ns in ipairs({ SSL_NS, SSL_CURRENT_NS, SSL_HOP_NS }) do
        vim.api.nvim_buf_clear_namespace(SSL_STATE.bufnr, ns, 0, -1)
      end
      vim.schedule(function()
        ssl_utils.refresh_buffer(SSL_STATE.bufnr)
      end)
    end

    return
  end

  SSL_STATE.repo_root = launch_root

  -- check if the buffer is already created
  if not SSL_STATE.bufnr or not vim.api.nvim_buf_is_valid(SSL_STATE.bufnr) then
    SSL_STATE.bufnr = vim.api.nvim_create_buf(
      true, -- listed
      true -- scratch
    )
    vim.schedule(function()
      ssl_utils.refresh_buffer(SSL_STATE.bufnr)
    end)
    vim.api.nvim_buf_set_name(SSL_STATE.bufnr, "hgssl [" .. launch_root .. "]")
    vim.api.nvim_set_option_value("swapfile", false, { buf = SSL_STATE.bufnr })
    vim.api.nvim_set_option_value(
      "buftype",
      "nofile",
      { buf = SSL_STATE.bufnr }
    )
    -- do not wrap lines
    -- stable commits have long bookmarks
    vim.api.nvim_set_option_value("wrap", false, { win = 0 })
    vim.api.nvim_set_option_value(
      "filetype",
      "hgssl",
      { buf = SSL_STATE.bufnr }
    )
  end

  if split then
    vim.cmd("botright vnew")
  end
  vim.api.nvim_set_current_buf(SSL_STATE.bufnr)

  local function get_current_commit()
    local current_line = vim.api.nvim_get_current_line()
    local commit = ssl_utils.parse_diff_line(current_line)
    return commit
  end

  local function ssl_action(name, guard)
    return function()
      if SSL_STATE.busy > 0 and not SSL_COMMANDS[name].readonly then
        vim.notify("A smartlog command is still running, please wait...", vim.log.levels.WARN)
        return
      end
      local commit = get_current_commit()
      if commit == nil then
        vim.notify("No commit under cursor")
        return
      end
      if guard and not guard(commit) then
        return
      end
      log_to_scuba({ module = "hg", command = "HgSsl." .. name })
      SSL_COMMANDS[name].action(commit, SSL_STATE.bufnr)
    end
  end

  local show_commit = ssl_action("show")
  local open_in_phabricator = ssl_action("open_in_phabricator", function(c)
    if not c.diff then
      vim.notify("No diff associated with this commit", vim.log.levels.WARN)
      return false
    end
    return true
  end)

  local wk = require("which-key")
  local buf = SSL_STATE.bufnr
  wk.add({
    buffer = buf,
    { "<CR>", group = "actions" },
    { "<CR>s", show_commit, desc = "Show commit" },
    { "<CR>d", ssl_action("diff_split"), desc = "Diff split" },
    { "<CR>o", ssl_action("checkout", function(c)
      if c.is_current then
        vim.notify("Already on this commit", vim.log.levels.WARN)
        return false
      end
      return true
    end), desc = "Checkout" },
    { "<CR>e", ssl_action("metaedit"), desc = "Edit commit message" },
    { "<CR>E", ssl_action("histedit", function(c)
      if not c.is_current then
        vim.notify("Can only histedit current commit", vim.log.levels.WARN)
        return false
      end
      return true
    end), desc = "Histedit" },
    { "<CR>S", ssl_action("split"), desc = "Split commit" },
    { "<CR>u", ssl_action("uncommit", function(c)
      if not c.is_current then
        vim.notify("Can only uncommit current commit", vim.log.levels.WARN)
        return false
      end
      return true
    end), desc = "Uncommit" },
    { "<CR>w", ssl_action("changes", function(c)
      if not c.is_current then
        vim.notify("Can only manage changes on current commit", vim.log.levels.WARN)
        return false
      end
      return true
    end), desc = "Working changes" },
    { "<CR>x", ssl_action("hide"), desc = "Hide commit" },
    { "<CR>b", ssl_action("rebase"), desc = "Rebase onto..." },
    { "<CR>p", ssl_action("arc_pull"), desc = "Arc pull" },
    { "<CR>gx", open_in_phabricator, desc = "Open in Phabricator" },
    { "<CR>ga", ssl_action("author_internal_profile", function(c)
      if not c.author then
        vim.notify("No author on this commit", vim.log.levels.WARN)
        return false
      end
      return true
    end), desc = "Author profile" },
    { "<CR>f", group = "submit" },
    { "<CR>fs", ssl_action("submit"), desc = "Submit" },
    { "<CR>fS", ssl_action("submit_stack"), desc = "Submit stack" },
    { "<CR>fd", ssl_action("submit_draft"), desc = "Submit draft" },
    { "<CR>fD", ssl_action("submit_draft_stack"), desc = "Submit draft stack" },
    { "<CR>R", group = "rebase conflict" },
    { "<CR>Ra", ssl_action("rebase_abort"), desc = "Abort rebase" },
    { "<CR>Rc", ssl_action("rebase_continue"), desc = "Continue rebase" },
  })

  vim.api.nvim_create_autocmd("BufEnter", {
    buffer = SSL_STATE.bufnr,
    callback = function()
      local recent = SSL_STATE.refreshed_at
        and vim.uv.now() - SSL_STATE.refreshed_at < SSL_REFOCUS_REFRESH_MS
      if not SSL_STATE.refreshing and not recent then
        ssl_utils.refresh_buffer(SSL_STATE.bufnr)
      end
    end,
    desc = "Update ssl buffer when focused",
  })

  vim.keymap.set("n", "?", function()
    local lines = {
      "HG SSL Buffer",
      "===================================================",
      "Navigate to next/prev commit with j/k",
      "press f then a label to hop; special commits (current + bookmarks) hop with a single key",
      "",
      "Keymaps:",
      "<cr>  select available commands for selected commit",
      "f     hop to a labeled commit",
      "r     refresh buffer",
      "c/C   select current commit",
      "?     show this help",
    }
    vim.notify(table.concat(lines, "\n"), vim.log.levels.INFO)
  end, { buffer = true })

  vim.keymap.set(
    "n",
    "j",
    ssl_utils.mapping_navigate_to_commit("next"),
    { buffer = true }
  )
  vim.keymap.set(
    "n",
    "k",
    ssl_utils.mapping_navigate_to_commit("prev"),
    { buffer = true }
  )
  vim.keymap.set("n", CONFIG.ssl.keys.refresh, function()
    ssl_utils.refresh_buffer(SSL_STATE.bufnr)
  end, { buffer = true })

  vim.keymap.set(
    "n",
    "f",
    ssl_start_hop,
    { buffer = true, nowait = true, desc = "Hop to commit" }
  )

  local nowait_opt = { buffer = true, nowait = true }
  vim.keymap.set(
    "n",
    CONFIG.ssl.keys.current,
    ssl_utils.go_to_current_commit,
    nowait_opt
  )
  vim.keymap.set("n", "C", ssl_utils.go_to_current_commit, nowait_opt)

  vim.keymap.set("n", CONFIG.ssl.keys.show, show_commit, nowait_opt)
  vim.keymap.set("n", CONFIG.ssl.keys.open, open_in_phabricator, { buffer = true, desc = "Open diff in phabricator" })

end

---Commit message lines with the template's comment lines and trailing blanks removed.
---hg strips its `HG:` lines only when it runs the editor itself, not for --logfile.
---@param lines string[]
---@return string[]
local function commit_message_lines(lines)
  local message = vim.tbl_filter(function(line)
    return not (vim.startswith(line, "HG:") or vim.startswith(line, "SL:"))
  end, lines)
  while #message > 0 and vim.trim(message[#message]) == "" do
    table.remove(message)
  end
  return message
end

---Open a commit-message editor in a new tab and commit when the window closes.
---The message is what was last written to the file; closing without changing
---the template aborts.
---@param paths string[]? specific files to commit (nil = all pending changes)
---@param on_done fun()? called after a successful commit
---@param repo_root string? repo to commit in (defaults to the active repo)
local function commit_with_message(paths, on_done, repo_root)
  repo_root = repo_root or active_repo_root()
  local commit_filepath = vim.fn.tempname() .. ".commit"

  local template = vim.system(
    { "hg", "debugcommitmessage" },
    { text = true, cwd = repo_root }
  ):wait().stdout or ""
  local template_lines = split_file_lines(template)
  vim.fn.writefile(template_lines, commit_filepath)

  vim.cmd.tabnew(vim.fn.fnameescape(commit_filepath))
  vim.bo.filetype = "hgcommit"

  local buf_nr = vim.api.nvim_get_current_buf()

  vim.api.nvim_create_autocmd("WinClosed", {
    buffer = 0,
    once = true,
    callback = function()
      local function cleanup()
        vim.schedule(function()
          vim.fn.delete(commit_filepath)
          if vim.api.nvim_buf_is_valid(buf_nr) then
            vim.api.nvim_buf_delete(buf_nr, { force = true })
          end
          -- update signs for the buffer that we returned to
          sign_util.infer_signs(0)
        end)
      end
      local message = commit_message_lines(vim.fn.readfile(commit_filepath))
      local untouched = vim.deep_equal(message, commit_message_lines(template_lines))
      if untouched or vim.trim(table.concat(message, "")) == "" then
        vim.notify("Commit aborted", vim.log.levels.INFO)
        cleanup()
      else
        vim.fn.writefile(message, commit_filepath)
        local cmd = { "hg", "commit", "--logfile", commit_filepath }
        if paths and #paths > 0 then
          -- include explicitly named untracked/missing files in the commit
          table.insert(cmd, "--addremove")
          vim.list_extend(cmd, paths)
        end
        vim.system(cmd, { text = true, cwd = repo_root }, function(commit_obj)
          if commit_obj.code ~= 0 then
            local msg = "Commit failed"
            if commit_obj.stderr then
              msg = msg .. ":\n" .. commit_obj.stderr
            end
            vim.schedule(function()
              vim.notify(msg, vim.log.levels.ERROR)
            end)
          else
            vim.schedule(function()
              vim.notify("Changes committed", vim.log.levels.INFO)
              if on_done then
                on_done()
              end
            end)
          end
          cleanup()
        end)
      end
    end,
  })
end

local function HgCommit()
  log_to_scuba({
    module = "hg",
    command = "HgCommit",
  })

  local root = active_repo_root()
  local has_changes = hg_utils.can_commit(root)
  if has_changes == false then
    vim.notify("No changes to commit", vim.log.levels.ERROR)
    return
  end

  commit_with_message(nil, nil, root)
end

local function HgAmend()
  log_to_scuba({
    module = "hg",
    command = "HgAmend",
  })

  local root = active_repo_root()
  local has_changes = hg_utils.can_commit(root)
  if has_changes == false then
    vim.notify("No changes to amend", vim.log.levels.ERROR)
    return
  end

  hg_utils.run_cmd_and_exit({ "hg", "amend" }, nil, nil, root)
end

local function HgCommitInteractive()
  log_to_scuba({
    module = "hg",
    command = "HgCommitInteractive",
  })

  local root = active_repo_root()
  local has_changes = hg_utils.can_commit(root)
  if has_changes == false then
    vim.notify("No changes to commit", vim.log.levels.ERROR)
    return
  end

  hg_utils.run_cmd_and_exit({ "hg", "commit", "--interactive" }, nil, nil, root)
end

local function HgAbsorb()
  log_to_scuba({
    module = "hg",
    command = "HgAbsorb",
  })

  local root = active_repo_root()
  local has_changes = hg_utils.can_commit(root)
  if has_changes == false then
    vim.notify("No changes to absorb", vim.log.levels.ERROR)
    return
  end

  hg_utils.run_cmd_and_exit({ "hg", "absorb" }, nil, nil, root)
end

local function HgSuggest(opts)
  log_to_scuba({
    module = "hg",
    command = "HgSuggest",
  })

  local unsaved = vim.tbl_filter(function(buf)
    return vim.bo[buf].modified and vim.bo[buf].buflisted
  end, vim.api.nvim_list_bufs())
  if #unsaved > 0 then
    local names = vim.tbl_map(function(buf)
      return vim.fn.fnamemodify(vim.api.nvim_buf_get_name(buf), ":~:.")
    end, unsaved)
    vim.notify(
      "Unsaved buffers (save first):\n  " .. table.concat(names, "\n  "),
      vim.log.levels.ERROR
    )
    return
  end

  local root = active_repo_root()
  local has_changes = hg_utils.can_commit(root)
  if has_changes == false then
    vim.notify("No changes to suggest", vim.log.levels.ERROR)
    return
  end

  local cmd = { "jf", "suggest", "--no-commit" }

  -- the arguments are typed as shell words (-m "a message")
  local args = vim.trim(opts.args or "")
  if args ~= "" then
    cmd = { "sh", "-c", table.concat(cmd, " ") .. " " .. args }
  end

  hg_utils.run_cmd_and_exit(cmd, nil, nil, root)
end

local function jf_graphql(query, variables, on_success, on_error, cwd)
  vim.system(
    { "jf", "graphql", "--query", query, "--variables", vim.json.encode(variables) },
    { text = true, cwd = cwd or active_repo_root() },
    function(obj)
      if obj.code ~= 0 then
        local msg = vim.trim(obj.stderr or "")
        if on_error then
          vim.schedule(function() on_error(msg) end)
        else
          vim.schedule(function()
            vim.notify("GraphQL error: " .. msg, vim.log.levels.ERROR)
          end)
        end
        return
      end
      local ok, result = pcall(vim.json.decode, obj.stdout)
      if not ok then
        vim.schedule(function()
          vim.notify("Failed to parse GraphQL response", vim.log.levels.ERROR)
        end)
        return
      end
      vim.schedule(function() on_success(result) end)
    end
  )
end

local function get_current_diff_number(callback, cwd)
  vim.system(
    { "hg", "log", "--limit", "1", "--template", "{phabdiff}" },
    { text = true, cwd = cwd or active_repo_root() },
    function(obj)
      if obj.code ~= 0 then
        vim.schedule(function()
          vim.notify("Failed to get diff number from current commit", vim.log.levels.ERROR)
        end)
        return
      end
      local phabdiff = vim.trim(obj.stdout or "")
      if phabdiff == "" then
        vim.schedule(function()
          vim.notify("No Phabricator diff linked to current commit", vim.log.levels.ERROR)
        end)
        return
      end
      local diff_number = phabdiff:gsub("^D", "")
      vim.schedule(function() callback(diff_number) end)
    end
  )
end

-- phabricator_versions answers with no versions; this lists them, oldest first
local GET_VERSIONS_QUERY = [[
query GetVersions($query_params: [PhabricatorDiffQueryParams!]!) {
  phabricator_diff_query(query_params: $query_params) {
    results {
      nodes {
        latest_phabricator_version {
          id
        }
        author_relevant_phabricator_versions(limit: 500) {
          id
          commit_hash_best_effort
        }
      }
    }
  }
}
]]

---The version of a diff submitted from commit `node`, whose lines are that
---commit's. Without one (an amend or rebase not submitted yet, or a diff
---updated elsewhere) its latest version, with `matched` false.
---@param diff_number string
---@param node string full hash
---@param callback fun(version_id: string, matched: boolean)
---@param cwd string
local function get_version_id(diff_number, node, callback, cwd)
  jf_graphql(GET_VERSIONS_QUERY, {
    query_params = { {
      numbers = { diff_number },
      client_caller = "neovim",
      queryID = "1",
    } },
  }, function(result)
    local diff = vim.tbl_get(result or {}, "phabricator_diff_query", 1, "results", "nodes", 1)
    if not (diff and diff.latest_phabricator_version) then
      vim.notify("Could not find versions of D" .. diff_number, vim.log.levels.ERROR)
      return
    end
    for _, version in ipairs(diff.author_relevant_phabricator_versions or {}) do
      if version.commit_hash_best_effort == node then
        return callback(version.id, true)
      end
    end
    callback(diff.latest_phabricator_version.id, false)
  end, nil, cwd)
end

---Full hash and Phabricator diff number (nil if none) of `rev`.
---@param repo_root string
---@param rev string
---@param callback fun(node: string, diff_number: string?)
local function get_commit(repo_root, rev, callback)
  vim.system({ "hg", "log", "-r", rev, "-T", "{node} {phabdiff}" }, { text = true, cwd = repo_root }, function(obj)
    local node, phabdiff = (obj.stdout or ""):match("^(%x+) ?(%S*)")
    vim.schedule(function()
      if obj.code ~= 0 or not node then
        return vim.notify("Failed to look up " .. rev .. ": " .. vim.trim(obj.stderr or ""), vim.log.levels.ERROR)
      end
      callback(node, phabdiff ~= "" and phabdiff:gsub("^D", "") or nil)
    end)
  end)
end

---Why the lines of `buf`, a file buffer, may not be its commit's: unsaved or
---uncommitted changes. nil when they are.
---@param buf integer
---@param repo_root string
---@param callback fun(reason: string?)
local function live_changes(buf, repo_root, callback)
  if vim.bo[buf].modified then
    return callback("unsaved changes")
  end
  vim.system({ "hg", "status", "--", vim.api.nvim_buf_get_name(buf) }, { text = true, cwd = repo_root }, function(obj)
    vim.schedule(function()
      callback(vim.trim(obj.stdout or "") ~= "" and "uncommitted changes" or nil)
    end)
  end)
end

local CREATE_INLINE_DRAFT_MUTATION = [[
mutation CreateInlineDraft($data: DifferentialInlineDraftCreateData!) {
  differential_inline_draft_create(data: $data) {
    comment {
      id
    }
  }
}
]]

local PUBLISH_DRAFTS_MUTATION = [[
mutation PublishDrafts($data: UpdatePhabricatorDiffData!) {
  update_phabricator_diff(data: $data) {
    phabricator_diff {
      number
      url
    }
  }
}
]]

---The diff split whose pane is the current window, if any. Other windows in
---its tab show ordinary files, which belong to their own repo and commit.
---@return Hg.diff_session?
local function focused_diff_split()
  local session = DIFF_SPLIT_SESSIONS[vim.api.nvim_get_current_tabpage()]
  local win = vim.api.nvim_get_current_win()
  if session and (win == session.left_win or win == session.right_win) then
    return session
  end
end

---@class (exact) Hg.inline_comment_target
---@field repo_root string
---@field file_path string Relative to `repo_root`.
---@field rev string The commit whose diff the comment goes on.
---@field is_new_file boolean Whether the lines are from `rev` (else from its parent).
---@field live_buf integer? File buffer the lines are from, which may differ from `rev`.

---Where a comment on the current window's lines goes.
---@return Hg.inline_comment_target?
local function resolve_inline_comment_context()
  local session = focused_diff_split()
  if session then
    local pair = session.pairs[session.index]
    local on_left = vim.api.nvim_get_current_win() == session.left_win
    return {
      repo_root = session.repo_root,
      file_path = pair.file,
      rev = session.commit.hash,
      -- the right pane shows parent_rev, which an "uncommitted vs ." split
      -- pins to the commit itself
      is_new_file = on_left or session.parent_rev == session.commit.hash,
      live_buf = on_left and pair.is_live and pair.new_buf or nil,
    }
  end

  local repo_root = vim.fs.root(0, ".hg")
  if not repo_root then
    vim.notify("Not in an hg repository", vim.log.levels.ERROR)
    return nil
  end

  return {
    repo_root = repo_root,
    file_path = vim.fn.expand("%:p"):sub(#repo_root + 2),
    rev = ".",
    is_new_file = true,
    live_buf = vim.api.nvim_get_current_buf(),
  }
end

local function HgInlineComment(opts)
  log_to_scuba({
    module = "hg",
    command = "HgInlineComment",
  })

  local ctx = resolve_inline_comment_context()
  if not ctx then
    return
  end

  local line1 = opts.line1
  local line2 = opts.count > 0 and opts.count or line1
  local line_length = line2 - line1

  local comment_buf = vim.api.nvim_create_buf(false, true)
  vim.bo[comment_buf].buftype = "nofile"
  vim.bo[comment_buf].filetype = "markdown"
  vim.bo[comment_buf].swapfile = false

  local width = math.min(80, vim.o.columns - 4)
  local height = 10
  local win = vim.api.nvim_open_win(comment_buf, true, {
    relative = "cursor",
    row = 1,
    col = 0,
    width = width,
    height = height,
    style = "minimal",
    border = "rounded",
    title = string.format(" Inline comment: %s:%d", ctx.file_path, line1),
    title_pos = "left",
  })
  vim.wo[win].wrap = true

  vim.cmd("startinsert")

  local function submit_comment(content)
    get_commit(ctx.repo_root, ctx.rev, function(node, diff_number)
      if not diff_number then
        return vim.notify("No Phabricator diff linked to " .. node:sub(1, 12), vim.log.levels.ERROR)
      end
      local function create(changes)
        get_version_id(diff_number, node, function(version_id, matched)
          jf_graphql(CREATE_INLINE_DRAFT_MUTATION, {
            data = {
              content = content,
              phabricator_version_id = version_id,
              file_name = ctx.file_path,
              is_new_file = ctx.is_new_file,
              line_number = line1,
              line_length = line_length,
              skip_draft = false,
            },
          }, function()
            local msg = string.format("Draft comment added on D%s %s:%d", diff_number, ctx.file_path, line1)
            local caveats = {}
            if not matched then
              table.insert(caveats, string.format(
                "no version of D%s was submitted from %s, so it went on the latest", diff_number, node:sub(1, 12)))
            end
            if changes then
              table.insert(caveats, "the file has " .. changes)
            end
            if #caveats > 0 then
              return vim.notify(msg .. ". Its line may be off: " .. table.concat(caveats, "; "), vim.log.levels.WARN)
            end
            vim.notify(msg, vim.log.levels.INFO)
          end, nil, ctx.repo_root)
        end, ctx.repo_root)
      end
      if ctx.live_buf then
        live_changes(ctx.live_buf, ctx.repo_root, create)
      else
        create(nil)
      end
    end)
  end

  vim.api.nvim_create_autocmd("WinClosed", {
    buffer = comment_buf,
    once = true,
    callback = function()
      local lines = vim.api.nvim_buf_get_lines(comment_buf, 0, -1, false)
      local content = vim.trim(table.concat(lines, "\n"))

      vim.schedule(function()
        if vim.api.nvim_buf_is_valid(comment_buf) then
          vim.api.nvim_buf_delete(comment_buf, { force = true })
        end
      end)

      if content == "" then
        vim.schedule(function()
          vim.notify("Comment cancelled", vim.log.levels.INFO)
        end)
        return
      end

      submit_comment(content)
    end,
  })
end

local function HgPublishDrafts()
  log_to_scuba({
    module = "hg",
    command = "HgPublishDrafts",
  })

  local function publish(diff_number, root)
    jf_graphql(PUBLISH_DRAFTS_MUTATION, {
      data = {
        client_caller = "neovim",
        client_mutation_id = "1",
        number = diff_number,
        action = "none",
        attach_inlines = true,
      },
    }, function(result)
      local diff = result
        and result.update_phabricator_diff
        and result.update_phabricator_diff.phabricator_diff
      local url = diff and diff.url or ("D" .. diff_number)
      vim.notify("Draft comments published on " .. url, vim.log.levels.INFO)
    end, nil, root)
  end

  -- from a diff split pane, :HgInlineComment drafts on the split's diff; publish there too
  local session = focused_diff_split()
  if session and session.commit.diff then
    return publish((session.commit.diff:gsub("^D", "")), session.repo_root)
  end
  local root = active_repo_root()
  get_current_diff_number(function(diff_number)
    publish(diff_number, root)
  end, root)
end

-- Refresh signs for all loaded buffers
local function refresh_all_signs()
  for _, bufnr in ipairs(vim.api.nvim_list_bufs()) do
    if vim.api.nvim_buf_is_loaded(bufnr) then
      sign_util.infer_signs(bufnr)
    end
  end
end

-- Show error notification asynchronously
local function notify_error(message)
  vim.schedule(function()
    vim.notify(message, vim.log.levels.ERROR)
  end)
end

-- Get commit info from hg log
-- @param revision: revision specifier for hg log
-- @param on_success: function(commit_hash, commit_display) called on success
-- @param on_error: function(stderr) called on error (optional)
local function get_commit_info(revision, on_success, on_error, cwd)
  vim.system({
    "hg",
    "log",
    "-r",
    revision,
    "-l",
    "1",
    "--template",
    "{node|short}\n{phabdiff} {desc|firstline}",
  }, { text = true, cwd = cwd or active_repo_root() }, function(obj)
    if obj.code ~= 0 then
      if on_error then
        on_error(obj.stderr or "")
      else
        notify_error("Failed to get commit info:\n" .. (obj.stderr or ""))
      end
      return
    end

    local output_lines = vim.split(vim.trim(obj.stdout or ""), "\n")
    if #output_lines == 0 or output_lines[1] == "" then
      if on_error then
        on_error("Empty output from hg log")
      else
        notify_error("Could not parse commit info")
      end
      return
    end

    local commit_hash = output_lines[1]
    local commit_display = output_lines[2] or ""

    vim.schedule(function()
      on_success(commit_hash, commit_display)
    end)
  end)
end

local function HgChangeBase(opts)
  log_to_scuba({
    module = "hg",
    command = "HgChangeBase",
  })

  local new_base = vim.trim(opts.args or "")

  if new_base == "" then
    CONFIG.base_revision = nil
    vim.notify("Reset base revision to parent commit", vim.log.levels.INFO)
    refresh_all_signs()
  else
    -- Validate revision and get commit info
    get_commit_info(new_base, function(commit_hash, commit_display)
      CONFIG.base_revision = new_base
      local display_msg = commit_hash
      if commit_display ~= "" then
        display_msg = display_msg .. " " .. commit_display
      end
      vim.notify("Set base revision to: " .. display_msg, vim.log.levels.INFO)
      refresh_all_signs()
    end, function(stderr)
      notify_error("Invalid revision: " .. new_base .. "\n" .. stderr)
    end)
  end
end

local STATUS_HL_NS = vim.api.nvim_create_namespace("hg_status_hl")

---Highlights the status letter of `hg status` lines.
---@param buf integer
---@param lines string[]
local function highlight_status_lines(buf, lines)
  local mode_to_hl = {
    M = "@diff.plus", -- modified
    A = "@diff.plus", -- added
    R = "@diff.minus", -- removed
    C = "@diff.minus", -- clean
    I = "@diff.delta", -- ignored
    ["!"] = "@diff.delta", -- missing
    ["?"] = "@diff.delta", -- not tracked
  }
  vim.api.nvim_buf_clear_namespace(buf, STATUS_HL_NS, 0, -1)
  for i, line in ipairs(lines) do
    local hl = mode_to_hl[line:sub(1, 1)]
    if hl then
      vim.hl.range(buf, STATUS_HL_NS, hl, { i - 1, 0 }, { i - 1, 1 })
    end
  end
end

local function HgStatus()
  log_to_scuba({
    module = "hg",
    command = "HgStatus",
  })

  -- Pin to the repo we launch from; the picker's own buffer has no file, so
  -- later keymap actions can't re-resolve it.
  local repo_root = active_repo_root()

  local function update_status(buf)
    local out = vim.system({ "hg", "status" }, { cwd = repo_root, text = true }):wait()
    if out.code ~= 0 then
      return vim.notify(
        "Failed to run hg status\n" .. (out.stderr or ""),
        vim.log.levels.ERROR
      )
    end
    local stdout = vim.trim(out.stdout or "")
    if stdout == "" then
      stdout = "No changes"
    end
    -- "M foo/bar.baz"
    local lines = vim.split(stdout, "\n")
    vim.api.nvim_buf_set_lines(buf, 0, -1, false, lines)
    highlight_status_lines(buf, lines)
  end

  local buf = vim.api.nvim_create_buf(false, true)
  update_status(buf)

  -- initial buffer setup
  vim.api.nvim_set_option_value("buftype", "nofile", { buf = buf })
  vim.api.nvim_set_option_value("bufhidden", "wipe", { buf = buf })
  vim.api.nvim_set_option_value("swapfile", false, { buf = buf })
  -- a still-open status window from an earlier :HgStatus holds the name
  local previous = vim.fn.bufnr("^hg status$")
  if previous ~= -1 then
    vim.api.nvim_buf_delete(previous, { force = true })
  end
  vim.api.nvim_buf_set_name(buf, "hg status")

  -- open a new window and set it to the buffer
  vim.cmd.split()
  vim.api.nvim_win_set_buf(0, buf)
  vim.api.nvim_win_set_height(0, 10)

  -- add keymaps
  do
    ---@type table<string, {args: string[], confirm: boolean?}>
    local char_to_command = {
      a = { args = { "add" } },
      s = { args = { "add" } }, --stage (like fugitive)
      X = { args = { "revert", "--no-backup" }, confirm = true }, -- discard change under cursor
      -- TODO show diff for file under cursor
      u = { args = { "forget" } }, -- unstage (like fugitive)
      d = { args = { "forget" } },
    }
    for char, command in pairs(char_to_command) do
      -- TODO support ranges
      vim.keymap.set("n", char, function()
        local line = vim.api.nvim_get_current_line()
        local path = line:match("..%s*(.*)")
        if not path or path == "" or line == "No changes" then
          return
        end
        local cmd = vim.list_extend({ "hg" }, command.args)
        table.insert(cmd, path)

        local function run()
          local out = vim.system(cmd, { cwd = repo_root, text = true }):wait()
          if out.code ~= 0 then
            return vim.notify(
              'Failed to run "' .. table.concat(cmd, " ") .. '"\n' .. (out.stderr or ""),
              vim.log.levels.ERROR
            )
          end
          update_status(buf)
          local file_buf = loaded_buf_for_path(vim.fs.joinpath(repo_root, path))
          if file_buf and not vim.bo[file_buf].modified then
            vim.api.nvim_buf_call(file_buf, function()
              vim.cmd("checktime")
            end)
          end
        end

        if not command.confirm then
          return run()
        end
        vim.ui.select({ "Yes", "No" }, { prompt = "Discard changes to " .. path .. "?" }, function(choice)
          if choice == "Yes" then
            run()
          end
        end)
      end, { desc = table.concat(vim.list_extend({ "hg" }, command.args), " ") .. " file", buffer = buf })
    end
    vim.keymap.set("n", "?", function()
      vim.notify(table.concat({
        "a - add (hg add)",
        "s - add (fugitive keymap) (hg add)",
        "X - discard change under cursor (hg revert)",
        "u - unstage (fugitive keymap) (hg forget)",
        "d - unstage (hg forget)",
        "? - show help",
      }, "\n"), vim.log.levels.INFO)
    end, { desc = "show help", buffer = buf })
  end
end

local CHANGES_NS = vim.api.nvim_create_namespace("hg_changes")

HgChanges = function()
  log_to_scuba({
    module = "hg",
    command = "HgChanges",
  })

  -- Pin to the repo we launch from (the SSL panel's repo when invoked as an
  -- action, else the current file's). The picker's own buffer has no file, so
  -- its keymap actions rely on this captured root rather than nvim's cwd.
  local repo_root = active_repo_root()

  local out = vim.system({ "hg", "status" }, { cwd = repo_root }):wait()
  if out.code ~= 0 then
    vim.notify(
      "Failed to run hg status\n" .. (out.stderr or ""),
      vim.log.levels.ERROR
    )
    return
  end
  local stdout = vim.trim(out.stdout or "")
  if stdout == "" then
    vim.notify("No uncommitted changes", vim.log.levels.INFO)
    return
  end

  local lines = vim.tbl_filter(function(l)
    return l ~= ""
  end, vim.split(stdout, "\n"))

  if #lines == 0 then
    vim.notify("No uncommitted changes", vim.log.levels.INFO)
    return
  end

  local selected = {}

  local buf = vim.api.nvim_create_buf(false, true)
  vim.api.nvim_buf_set_lines(buf, 0, -1, false, lines)
  vim.api.nvim_set_option_value("buftype", "nofile", { buf = buf })
  vim.api.nvim_set_option_value("bufhidden", "wipe", { buf = buf })
  vim.api.nvim_set_option_value("swapfile", false, { buf = buf })
  vim.api.nvim_set_option_value("modifiable", false, { buf = buf })
  vim.api.nvim_buf_set_name(buf, "hg changes")

  local function apply_highlights()
    highlight_status_lines(buf, lines)
  end

  apply_highlights()

  vim.cmd.split()
  local win = vim.api.nvim_get_current_win()
  vim.api.nvim_win_set_buf(win, buf)
  vim.api.nvim_win_set_height(win, math.min(#lines + 1, 15))
  vim.wo[win].signcolumn = "yes:1"
  vim.wo[win].number = false
  vim.wo[win].relativenumber = false
  vim.wo[win].winbar = "%#Comment# <Space>%* toggle"
    .. "  %#Comment#A%* all"
    .. "  %#Comment#a%* amend"
    .. "  %#Comment#c%* commit"
    .. "  %#Comment#d%* diff"
    .. "  %#Comment#X%* discard"
    .. "  %#Comment#q%* close"
    .. "  %#Comment#?%* help"

  vim.notify("Press ? for help", vim.log.levels.INFO)

  local function update_signs()
    vim.api.nvim_buf_clear_namespace(buf, CHANGES_NS, 0, -1)
    for idx in pairs(selected) do
      vim.api.nvim_buf_set_extmark(buf, CHANGES_NS, idx - 1, 0, {
        sign_text = "●",
        sign_hl_group = "DiagnosticOk",
      })
    end
  end

  local function get_status(line)
    return line:sub(1, 1)
  end

  local function get_path(line)
    return line:match("^.%s+(.*)")
  end

  local function get_action_entries()
    local entries = {}
    if next(selected) then
      for idx in pairs(selected) do
        local path = get_path(lines[idx])
        if path then
          table.insert(entries, { status = get_status(lines[idx]), path = path })
        end
      end
    else
      local lnum = vim.api.nvim_win_get_cursor(0)[1]
      local path = get_path(lines[lnum])
      if path then
        entries = { { status = get_status(lines[lnum]), path = path } }
      end
    end
    return entries
  end

  local function get_action_paths()
    return vim.tbl_map(function(e) return e.path end, get_action_entries())
  end

  local help_win = nil

  local function refresh()
    local new_out = vim.system({ "hg", "status" }, { cwd = repo_root }):wait()
    if new_out.code ~= 0 then
      return
    end
    local new_stdout = vim.trim(new_out.stdout or "")
    if new_stdout == "" then
      if help_win and vim.api.nvim_win_is_valid(help_win) then
        vim.api.nvim_win_close(help_win, true)
        help_win = nil
      end
      if vim.api.nvim_win_is_valid(win) then
        vim.api.nvim_win_close(win, true)
      end
      vim.notify("No remaining changes", vim.log.levels.INFO)
      if SSL_STATE.bufnr and vim.api.nvim_buf_is_valid(SSL_STATE.bufnr) then
        ssl_utils.refresh_buffer(SSL_STATE.bufnr)
      end
      return
    end
    lines = vim.tbl_filter(function(l)
      return l ~= ""
    end, vim.split(new_stdout, "\n"))
    selected = {}
    vim.api.nvim_set_option_value("modifiable", true, { buf = buf })
    vim.api.nvim_buf_set_lines(buf, 0, -1, false, lines)
    vim.api.nvim_set_option_value("modifiable", false, { buf = buf })
    apply_highlights()
    update_signs()
    if vim.api.nvim_win_is_valid(win) then
      vim.api.nvim_win_set_height(win, math.min(#lines + 1, 15))
    end
    if SSL_STATE.bufnr and vim.api.nvim_buf_is_valid(SSL_STATE.bufnr) then
      ssl_utils.refresh_buffer(SSL_STATE.bufnr)
    end
  end

  vim.keymap.set("n", "<Space>", function()
    local lnum = vim.api.nvim_win_get_cursor(0)[1]
    if selected[lnum] then
      selected[lnum] = nil
    else
      selected[lnum] = true
    end
    update_signs()
  end, { buffer = buf, desc = "Toggle selection" })

  vim.keymap.set("v", "<Space>", function()
    local start_line = vim.fn.line("v")
    local end_line = vim.fn.line(".")
    if start_line > end_line then
      start_line, end_line = end_line, start_line
    end
    for lnum = start_line, end_line do
      if selected[lnum] then
        selected[lnum] = nil
      else
        selected[lnum] = true
      end
    end
    vim.api.nvim_feedkeys(
      vim.api.nvim_replace_termcodes("<Esc>", true, false, true),
      "n",
      false
    )
    update_signs()
  end, { buffer = buf, desc = "Toggle selection" })

  vim.keymap.set("n", "A", function()
    local all_selected = true
    for i = 1, #lines do
      if not selected[i] then
        all_selected = false
        break
      end
    end
    if all_selected then
      selected = {}
    else
      for i = 1, #lines do
        selected[i] = true
      end
    end
    update_signs()
  end, { buffer = buf, desc = "Toggle all" })

  vim.keymap.set("n", "a", function()
    local paths = get_action_paths()
    if #paths == 0 then
      vim.notify("No files selected", vim.log.levels.WARN)
      return
    end
    -- --addremove so explicitly named untracked/missing files are included
    local cmd = vim.list_extend({ "hg", "amend", "--addremove" }, paths)
    vim.notify("Amending " .. #paths .. " file(s)...", vim.log.levels.INFO)
    vim.system(cmd, { text = true, cwd = repo_root }, function(result)
      vim.schedule(function()
        if result.code ~= 0 then
          vim.notify(
            "Amend failed:\n" .. (result.stderr or ""),
            vim.log.levels.ERROR
          )
          return
        end
        vim.notify("Amended " .. #paths .. " file(s)", vim.log.levels.INFO)
        refresh()
      end)
    end)
  end, { buffer = buf, desc = "Amend selected files" })

  vim.keymap.set("n", "c", function()
    local paths = get_action_paths()
    if #paths == 0 then
      vim.notify("No files selected", vim.log.levels.WARN)
      return
    end
    -- close the picker; the commit message editor opens in its own tab
    if help_win and vim.api.nvim_win_is_valid(help_win) then
      vim.api.nvim_win_close(help_win, true)
      help_win = nil
    end
    if vim.api.nvim_win_is_valid(win) then
      vim.api.nvim_win_close(win, true)
    end
    commit_with_message(paths, function()
      if SSL_STATE.bufnr and vim.api.nvim_buf_is_valid(SSL_STATE.bufnr) then
        ssl_utils.refresh_buffer(SSL_STATE.bufnr)
      end
    end, repo_root)
  end, { buffer = buf, desc = "Commit selected files (new commit)" })

  vim.keymap.set("n", "X", function()
    local entries = get_action_entries()
    if #entries == 0 then
      vim.notify("No files selected", vim.log.levels.WARN)
      return
    end
    local all_paths = vim.tbl_map(function(e) return e.path end, entries)
    vim.ui.select(
      { "Yes", "No" },
      { prompt = "Discard changes to " .. #entries .. " file(s)?" },
      function(choice)
        if choice ~= "Yes" then
          return
        end

        local tracked = {}
        local untracked = {}
        -- hg revert un-adds an added file but leaves it on disk
        local added = {}
        for _, e in ipairs(entries) do
          if e.status == "?" then
            table.insert(untracked, e.path)
          else
            table.insert(tracked, e.path)
            if e.status == "A" then
              table.insert(added, e.path)
            end
          end
        end

        local pending = 0
        local errors = {}

        local function on_done()
          pending = pending - 1
          if pending > 0 then
            return
          end
          vim.schedule(function()
            if #errors > 0 then
              vim.notify(
                "Discard failed:\n" .. table.concat(errors, "\n"),
                vim.log.levels.ERROR
              )
              return
            end
            vim.notify(
              "Discarded " .. #entries .. " file(s)",
              vim.log.levels.INFO
            )
            for _, path in ipairs(all_paths) do
              local abs = vim.fs.joinpath(repo_root, path)
              local b = loaded_buf_for_path(abs)
              if b and vim.uv.fs_stat(abs) then
                vim.api.nvim_buf_call(b, function()
                  vim.cmd("edit!")
                end)
              end
            end
            refresh()
          end)
        end

        if #tracked > 0 then
          pending = pending + 1
          local cmd = vim.list_extend({ "hg", "revert", "--no-backup" }, tracked)
          vim.system(cmd, { text = true, cwd = repo_root }, function(result)
            if result.code ~= 0 then
              table.insert(errors, result.stderr or "hg revert failed")
            else
              for _, path in ipairs(added) do
                local ok, err = os.remove(vim.fs.joinpath(repo_root, path))
                if not ok and vim.uv.fs_stat(vim.fs.joinpath(repo_root, path)) then
                  table.insert(errors, err)
                end
              end
            end
            on_done()
          end)
        end

        if #untracked > 0 then
          pending = pending + 1
          -- untracked paths are relative to the repo root
          local cmd = vim.list_extend({ "rm", "-f", "--" }, untracked)
          vim.system(cmd, { text = true, cwd = repo_root }, function(result)
            if result.code ~= 0 then
              table.insert(errors, result.stderr or "rm failed")
            end
            on_done()
          end)
        end
      end
    )
  end, { buffer = buf, desc = "Discard selected files" })

  vim.keymap.set("n", "d", function()
    local entries = get_action_entries()
    if #entries == 0 then
      vim.notify("No files selected", vim.log.levels.WARN)
      return
    end

    local diffable = vim.tbl_filter(function(e)
      return e.status == "M" or e.status == "A"
    end, entries)

    if #diffable == 0 then
      vim.notify("No diffable files selected (need M or A status)", vim.log.levels.WARN)
      return
    end

    -- Split from the HgSsl window (not the small changes split)
    local split_from
    if SSL_STATE.bufnr then
      for _, w in ipairs(vim.api.nvim_tabpage_list_wins(0)) do
        if vim.api.nvim_win_get_buf(w) == SSL_STATE.bufnr then
          split_from = w
          break
        end
      end
    end

    -- reuse the repo_root captured at HgChanges entry (the picker buffer has no
    -- file, so it can't be re-resolved here)
    local files = changed_files(repo_root, {
      paths = vim.tbl_map(function(e)
        return e.path
      end, diffable),
    })
    if not files then
      vim.notify("hg status failed", vim.log.levels.ERROR)
      return
    end
    open_diff_split({
      files = files,
      target = { hash = ".", is_current = true },
      parent_rev = ".",
      repo_root = repo_root,
      split_from = split_from,
    })
  end, { buffer = buf, desc = "Diff split selected files" })

  local help_lines = {
    " Hg Changes",
    " ═══════════",
    " <Space>  toggle file selection",
    " V+Space  toggle range selection",
    " A        toggle all",
    " a        amend selected into current commit",
    " c        commit selected into a new commit on top",
    " d        diff split selected files",
    " X        discard selected changes",
    " q        close",
    " ?        show/dismiss this help",
  }

  local function show_help()
    if help_win and vim.api.nvim_win_is_valid(help_win) then
      vim.api.nvim_win_close(help_win, true)
      help_win = nil
      return
    end
    local help_buf = vim.api.nvim_create_buf(false, true)
    vim.api.nvim_buf_set_lines(help_buf, 0, -1, false, help_lines)
    vim.api.nvim_set_option_value("modifiable", false, { buf = help_buf })
    vim.api.nvim_set_option_value("bufhidden", "wipe", { buf = help_buf })
    local width = 0
    for _, l in ipairs(help_lines) do
      width = math.max(width, #l)
    end
    help_win = vim.api.nvim_open_win(help_buf, false, {
      relative = "win",
      win = win,
      anchor = "NE",
      row = 0,
      col = vim.api.nvim_win_get_width(win),
      width = width + 2,
      height = #help_lines,
      style = "minimal",
      border = "rounded",
    })
    vim.keymap.set("n", "q", function()
      if help_win and vim.api.nvim_win_is_valid(help_win) then
        vim.api.nvim_win_close(help_win, true)
        help_win = nil
      end
    end, { buffer = help_buf })
  end

  vim.keymap.set("n", "q", function()
    if help_win and vim.api.nvim_win_is_valid(help_win) then
      vim.api.nvim_win_close(help_win, true)
      help_win = nil
    end
    vim.api.nvim_win_close(win, true)
  end, { buffer = buf, desc = "Close" })

  vim.keymap.set("n", "?", show_help, { buffer = buf, desc = "Show help" })
end

local SIGN_NS = vim.api.nvim_create_namespace("hg_signcolumn")

-- schedule is required to avoid calling nvim_buf_is_valid from a fast context
---@type fun(bufnr: number)
local clear_hg_diff = vim.schedule_wrap(function(bufnr)
  if vim.api.nvim_buf_is_valid(bufnr) then
    vim.b[bufnr].hg_diff = nil
  end
end)

local function setup_hg_signcolumn()
  ---@type table<number, number[]> from bufnr to first_hunk_line[]
  local cache = {}
  ---@type table<number, integer> from bufnr to its latest sign refresh; an
  --- older refresh that finishes late is dropped
  local generations = {}
  ---@param bufnr number
  ---@param now ?boolean
  function sign_util.clear_namespace(bufnr, now)
    if now then
      if vim.api.nvim_buf_is_valid(bufnr) then
        vim.api.nvim_buf_clear_namespace(bufnr, SIGN_NS, 0, -1)
      end
    else
      -- can be called from a lua loop
      vim.schedule(function()
        if vim.api.nvim_buf_is_valid(bufnr) then
          vim.api.nvim_buf_clear_namespace(bufnr, SIGN_NS, 0, -1)
        end
      end)
    end
  end

  ---@param filepath string
  ---@return boolean
  function sign_util.is_file(filepath)
    local stat = vim.uv.fs_stat(filepath)

    return stat ~= nil and stat.type == "file"
  end

  ---@param bufnr number
  ---@param hunks string|integer[][]
  function sign_util.apply_signs(bufnr, hunks)
    sign_util.clear_namespace(bufnr, true)
    local buf_lines_total = vim.api.nvim_buf_line_count(bufnr)

    if buf_lines_total < 1 then
      return
    end

    ---@diagnostic disable-next-line: param-type-mismatch
    for _, hunk in ipairs(hunks) do
      local _old_line, removed, new_file_line, added = unpack(hunk)

      -- The delete mark goes on the line above the change. For a pure
      -- deletion vim.diff reports the line after which lines were removed
      -- (0 at the top of the file); for a change, its first line.
      local above_removed_line = added == 0 and math.max(new_file_line - 1, 0)
        or new_file_line - 2
      if
        removed > 0
        and above_removed_line >= 0
        and above_removed_line <= (buf_lines_total - 1)
      then
        vim.api.nvim_buf_set_extmark(bufnr, SIGN_NS, above_removed_line, -1, {
          sign_hl_group = CONFIG.signs.delete.hl,
          -- Ideally we would use a minus sign `-`.
          -- This is the closest thing to content between current and next line
          sign_text = CONFIG.signs.delete.char,
          priority = 0,
        })
      end

      if added > 0 then
        for i = 1, added do
          local added_line = new_file_line + i - 2
          if added_line > (buf_lines_total - 1) then
            break
          end
          if added_line >= 0 then
            vim.api.nvim_buf_set_extmark(bufnr, SIGN_NS, added_line, -1, {
              sign_hl_group = CONFIG.signs.add.hl,
              sign_text = CONFIG.signs.add.char,
              priority = 0,
            })
          end
        end
      end
    end
  end

  ---@param bufnr number
  function sign_util.infer_signs(bufnr)
    if not vim.api.nvim_buf_is_valid(bufnr) then
      clear_hg_diff(bufnr)
      return
    end
    local filepath = vim.api.nvim_buf_get_name(bufnr)
    local is_file = sign_util.is_file(filepath)
    if not is_file then
      sign_util.clear_namespace(bufnr)
      clear_hg_diff(bufnr)
      return
    end

    -- hg resolves the repo from cwd, not from the file argument, so pin to the
    -- file's own repo to keep signs correct when nvim's cwd is elsewhere.
    local file_repo_root = repo_root_of(filepath)
    generations[bufnr] = (generations[bufnr] or 0) + 1
    local generation = generations[bufnr]
    local function clear_if_latest()
      vim.schedule(function()
        if generations[bufnr] == generation and vim.api.nvim_buf_is_valid(bufnr) then
          vim.b[bufnr].hg_diff = nil
          cache[bufnr] = nil
          vim.api.nvim_buf_clear_namespace(bufnr, SIGN_NS, 0, -1)
        end
      end)
    end
    local current_lines = vim.api.nvim_buf_get_lines(bufnr, 0, -1, true)
    -- what :write would put on disk, final newline included, so it compares
    -- like for like with hg cat
    local current_text = table.concat(current_lines, "\n")
    local writes_eol = vim.bo[bufnr].eol or (vim.bo[bufnr].fixeol and not vim.bo[bufnr].binary)
    if writes_eol and current_text ~= "" then
      current_text = current_text .. "\n"
    end
    vim.system(
      { "hg", "status", filepath },
      { text = true, cwd = file_repo_root },
      function(status_obj)
        if status_obj.code ~= 0 then
          return clear_if_latest()
        end

        local stdout_lines = vim.split(status_obj.stdout, "\n")
        local file_state = ""
        if #stdout_lines > 0 and stdout_lines[1] ~= "" then
          file_state = stdout_lines[1]:sub(1, 1)
        end

        -- If no base_revision is set, only show diffs for locally modified files
        -- If base_revision IS set, show diff between current version and that base (even for unmodified files)
        if not CONFIG.base_revision and file_state ~= "M" then
          return clear_if_latest()
        end

        -- Build the hg cat command with optional base revision
        local cat_cmd = { "hg", "cat" }
        if CONFIG.base_revision then
          local base_rev = CONFIG.base_revision
          -- Strip range syntax (: or ::) for hg cat since it needs a single revision
          if base_rev:find(":") then
            base_rev = base_rev:match("^([^:]+)")
          end
          table.insert(cat_cmd, "--rev")
          table.insert(cat_cmd, base_rev)
        end
        table.insert(cat_cmd, filepath)

        vim.system(
          -- get file content from the specified revision (or parent if not configured)
          cat_cmd,
          { text = true, cwd = file_repo_root },
          function(cat_obj)
            if cat_obj.code ~= 0 or not cat_obj.stdout then
              return clear_if_latest()
            end

            ---@type string
            local old_content = cat_obj.stdout

            ---@example { { 1179, 0, 1180, 2 }, { 1298, 0, 1301, 1 }, { 1307, 0, 1311, 6 }, { 1622, 1, 1631, 1 } }
            local hunks = vim.diff(
              old_content,
              current_text,
              { result_type = "indices" }
            )
            if not hunks then
              return clear_if_latest()
            end

            vim.schedule(function()
              if generations[bufnr] ~= generation or not vim.api.nvim_buf_is_valid(bufnr) then
                return
              end

              -- Count total added/removed lines for statusline
              local total_added = 0
              local total_removed = 0
              for _, hunk in ipairs(hunks) do
                local _old_start, removed, _new_start, added = unpack(hunk)
                total_added = total_added + added
                total_removed = total_removed + removed
              end

              -- Store in buffer variable for statusline
              if total_added == 0 and total_removed == 0 then
                vim.b[bufnr].hg_diff = nil
              else
                vim.b[bufnr].hg_diff = {
                  added = total_added,
                  removed = total_removed,
                }
              end

              cache[bufnr] = vim.tbl_map(
                function(h)
                  return math.max(h[3], 1)
                end,
                ---@diagnostic disable-next-line: param-type-mismatch
                hunks
              )

              sign_util.apply_signs(bufnr, hunks)
            end)
          end
        )
      end
    )
  end

  vim.api.nvim_create_autocmd({
    "BufEnter",
    "BufWritePost",
    "FileChangedShellPost",
  }, {
    pattern = "*",
    callback = function(arg)
      if vim.api.nvim_buf_is_valid(arg.buf) then
        vim.schedule(function()
          sign_util.infer_signs(arg.buf)
        end)
      end
    end,
    desc = "Update hg column signs for any buffer",
  })

  vim.api.nvim_create_autocmd("CmdlineLeave", {
    pattern = ":",
    callback = function(arg)
      -- shell commands like :!hg amend change the status without touching
      -- the file; any other : command or search needn't spawn hg
      if not vim.fn.getcmdline():find("!", 1, true) then
        return
      end
      vim.schedule(function()
        if vim.api.nvim_buf_is_valid(arg.buf) then
          sign_util.infer_signs(arg.buf)
        end
      end)
    end,
    desc = "Update hg column signs after shell commands",
  })

  vim.api.nvim_create_autocmd({
    "TextChanged",
    "TextChangedI",
  }, {
    pattern = "*",
    callback = debounce(function(arg)
      if vim.api.nvim_buf_is_valid(arg.buf) then
        sign_util.infer_signs(arg.buf)
      end
    end, CONFIG.signs.debounce_ms),
    desc = "Update hg column signs on text changes (debounced)",
  })

  local function go_to_line(line)
    -- to make <C-o> work
    vim.cmd("normal! m'")
    vim.api.nvim_win_set_cursor(0, { line, 0 })
  end

  ---@param direction 1 | -1
  local function go_to_hunk(direction)
    return function()
      local hunks = cache[vim.api.nvim_get_current_buf()]
      if not hunks or #hunks == 0 then
        return
      end

      local cursor = vim.api.nvim_win_get_cursor(0)

      if direction == 1 then
        for _, start_line in ipairs(hunks) do
          if start_line > cursor[1] then
            return go_to_line(start_line)
          end
        end
        -- wrap around to the first hunk
        return go_to_line(hunks[1])
      else
        local i = #hunks
        while i >= 1 do
          local start_line = hunks[i]
          if start_line < cursor[1] then
            return go_to_line(start_line)
          end

          i = i - 1
        end
      end
    end
  end

  -- ]h and [h for hunk navigation
  vim.keymap.set(
    { "x", "n" },
    "]h",
    go_to_hunk(1),
    { desc = "Go to next hg hunk" }
  )
  vim.keymap.set(
    { "x", "n" },
    "[h",
    go_to_hunk(-1),
    { desc = "Go to previous hg hunk" }
  )

  vim.keymap.set({ "x", "n" }, "]H", function()
    local hunks = cache[vim.api.nvim_get_current_buf()]
    if not hunks or #hunks == 0 then
      return
    end

    return go_to_line(hunks[#hunks])
  end, { desc = "Go to last hg hunk" })
  vim.keymap.set({ "x", "n" }, "[H", function()
    local hunks = cache[vim.api.nvim_get_current_buf()]
    if not hunks or #hunks == 0 then
      return
    end

    return go_to_line(hunks[1])
  end, { desc = "Go to first hg hunk" })
end

local LINE_BLAME_NS = vim.api.nvim_create_namespace("hg_line_blame")
local LINE_BLAME_GROUP =
  vim.api.nvim_create_augroup("hg_line_blame", { clear = true })
---Maps each line of `new` to the line of `old` it is an unchanged copy of.
---@param old string[]
---@param new string[]
---@return table<integer, integer> new line number -> old line number
local function map_unchanged_lines(old, new)
  local hunks = vim.diff(
    table.concat(old, "\n") .. "\n",
    table.concat(new, "\n") .. "\n",
    { result_type = "indices" }
  )
  ---@cast hunks integer[][]
  local map = {}
  local old_i, new_i = 1, 1
  for _, hunk in ipairs(hunks) do
    local _, old_count, new_start, new_count = unpack(hunk)
    -- a pure deletion reports the line after which lines were removed
    local unchanged_until = new_count == 0 and new_start or new_start - 1
    while new_i <= unchanged_until do
      map[new_i] = old_i
      new_i, old_i = new_i + 1, old_i + 1
    end
    new_i, old_i = new_i + new_count, old_i + old_count
  end
  while new_i <= #new do
    map[new_i] = old_i
    new_i, old_i = new_i + 1, old_i + 1
  end
  return map
end

---@class (exact) Hg.blame_entry
---@field key string? What was blamed (see `blame_key`); unchanged means reusable.
---@field text string[] Blame annotation per line of the file as blamed.
---@field lines string[] The file's lines as blamed (on disk).
---@field map table<integer, integer>? Buffer line -> blamed line, while modified.
---@field map_tick integer? changedtick `map` was computed at.

---Identifies the blame of `filepath`: its mtime and the working-copy parent.
---@param filepath string
---@param repo_root string
---@return string?
local function blame_key(filepath, repo_root)
  local stat = vim.uv.fs_stat(filepath)
  -- HACK: Sapling keeps the working-copy parent in the first 20 bytes of
  -- .hg/dirstate (eden checkouts included). Reading it avoids spawning
  -- hg log on every BufEnter just to learn that nothing was committed.
  local f = io.open(repo_root .. "/.hg/dirstate", "rb")
  local parent = f and f:read(20)
  if f then
    f:close()
  end
  if not stat or not parent or #parent ~= 20 then
    return nil
  end
  return string.format("%d.%d:%s", stat.mtime.sec, stat.mtime.nsec, parent)
end

---@param filepath string absolute (relative would resolve against cwd, not the repo)
local function blame_cmd(filepath)
  return {
    "hg",
    "blame",
    "-r",
    "wdir()", -- annotate changed lines
    "--user", -- include username
    "--date", -- include date
    "--phabdiff", -- include diff
    "-q", -- simple date format
    filepath,
  }
end

---@param key string?
---@param stdout string output of `blame_cmd`
---@return Hg.blame_entry
local function parse_blame(key, stdout)
  local blame_out_lines = split_file_lines(stdout)
  local text = vim.tbl_map(function(line)
    -- example output from hg blame
    -- lack of diff means the line was not submitted yet
    -- "     antonk52           2024-09-25: end"
    -- "     antonk52 D40184872 2022-10-11: return {"
    local username, diff, date =
      line:match("^ *(.*) (D%d*) (....-..-..): .*")

    if not diff then
      return CONFIG.line_blame.prefix .. "Not submitted yet"
    end

    return CONFIG.line_blame.prefix
      .. username
      .. " "
      .. date
      .. " "
      .. diff
  end, blame_out_lines)
  return {
    key = key,
    text = text,
    lines = vim.tbl_map(function(line)
      return line:match("^.-%d%d%d%d%-%d%d%-%d%d: (.*)$") or ""
    end, blame_out_lines),
  }
end

local function setup_line_blame()
  ---@type table<string, Hg.blame_entry> Absolute path to blame
  local cache = {}
  ---@type table<string, {generation: integer, job: vim.SystemObj?, key: string?}> Latest blame run per path
  local runs = {}

  ---Blame for line `lnum` of `bufnr`. While the buffer has unsaved changes,
  ---its lines are matched to the blamed ones through a diff; lines that only
  ---exist in the buffer have no blame.
  ---@return string?
  local function blame_for_line(bufnr, lnum)
    local entry = cache[vim.api.nvim_buf_get_name(bufnr)]
    if not entry then
      return nil
    end
    if not vim.bo[bufnr].modified then
      return entry.text[lnum]
    end
    local tick = vim.api.nvim_buf_get_changedtick(bufnr)
    if entry.map_tick ~= tick then
      entry.map = map_unchanged_lines(entry.lines, vim.api.nvim_buf_get_lines(bufnr, 0, -1, false))
      entry.map_tick = tick
    end
    local blamed_lnum = entry.map[lnum]
    return blamed_lnum and entry.text[blamed_lnum]
  end

  ---Draws blame for the cursor line of `bufnr` if it is the current buffer;
  ---async callbacks may land after you've moved elsewhere.
  local function set_line_blame(bufnr)
    if not vim.api.nvim_buf_is_valid(bufnr) or vim.api.nvim_get_current_buf() ~= bufnr then
      return
    end
    local cursor = vim.api.nvim_win_get_cursor(0)

    local blame_line = blame_for_line(bufnr, cursor[1])
    -- not committed line or line not in blame output (e.g. newly added)
    if not blame_line or blame_line == "" then
      return
    end

    vim.api.nvim_buf_clear_namespace(bufnr, LINE_BLAME_NS, 0, -1)
    vim.api.nvim_buf_set_extmark(bufnr, LINE_BLAME_NS, cursor[1] - 1, 0, {
      hl_mode = "combine",
      virt_text = {
        { blame_line, CONFIG.line_blame.highlight },
      },
    })
  end

  ---Registers the cursor handlers for a buffer and refreshes its blame.
  ---@param arg {buf: integer}
  local function attach(arg)
    if not CONFIG.line_blame.enable then
      return
    end

    -- buf is inside hg repository
    if not vim.fs.root(vim.api.nvim_buf_get_name(arg.buf), ".hg") then
      return
    end

    if not vim.api.nvim_buf_is_valid(arg.buf) then
      return
    end

    local filepath = vim.api.nvim_buf_get_name(arg.buf)
    local is_file = sign_util.is_file(filepath)
    local buflisted =
      vim.api.nvim_get_option_value("buflisted", { buf = arg.buf })
    local readonly =
      vim.api.nvim_get_option_value("readonly", { buf = arg.buf })
    local is_blameable_buffer = is_file and buflisted and not readonly
    if not is_blameable_buffer then
      return
    end

    -- Refresh events revisit the same buffer, so replace its handlers
    -- instead of accumulating another pair on every event.
    vim.api.nvim_clear_autocmds({
      group = LINE_BLAME_GROUP,
      buffer = arg.buf,
    })

    local handle_cursor_moved = debounce(
      function(cursor_moved_arg)
        if not CONFIG.line_blame.enable or not vim.api.nvim_buf_is_valid(cursor_moved_arg.buf) then
          return
        end
        -- clear all extmarks
        vim.api.nvim_buf_clear_namespace(
          cursor_moved_arg.buf,
          LINE_BLAME_NS,
          0,
          -1
        )

        set_line_blame(cursor_moved_arg.buf)
      end,
      CONFIG.line_blame.debounce_ms
    )

    vim.api.nvim_create_autocmd("CursorMoved", {
      desc = "Update hg line blame in Normal mode only",
      buffer = arg.buf,
      group = LINE_BLAME_GROUP,
      callback = handle_cursor_moved,
    })
    vim.api.nvim_create_autocmd("ModeChanged", {
      desc = "Hide blame in non-normal modes",
      buffer = arg.buf,
      group = LINE_BLAME_GROUP,
      callback = function(mode_changed_arg)
        if not CONFIG.line_blame.enable then
          return
        end

        vim.api.nvim_buf_clear_namespace(
          mode_changed_arg.buf,
          LINE_BLAME_NS,
          0,
          -1
        )
        if not vim.startswith(vim.fn.mode(), "n") then
          return
        end

        set_line_blame(mode_changed_arg.buf)
      end,
    })

    local repo_root = repo_root_of(filepath)
    local key = blame_key(filepath, repo_root)
    if key and cache[filepath] and cache[filepath].key == key then
      set_line_blame(arg.buf)
      return
    end

    -- one blame per file at a time: one already running for the same file
    -- state finishes (and draws); one for a stale state is replaced
    local run = runs[filepath] or { generation = 0 }
    runs[filepath] = run
    if run.job then
      if key and run.key == key then
        return
      end
      pcall(run.job.kill, run.job, 15)
    end
    run.generation = run.generation + 1
    run.key = key
    local generation = run.generation
    run.job = vim.system(
      blame_cmd(filepath),
      { text = true, cwd = repo_root },
      function(blame_obj)
      if run.generation ~= generation then
        return
      end
      run.job = nil
      if blame_obj.code ~= 0 then
        return
      end
      cache[filepath] = parse_blame(key, blame_obj.stdout)

      -- schedule since cannot call nvim_buf_get_extmarks inside lua loop
      vim.schedule(function()
        if not vim.api.nvim_buf_is_valid(arg.buf) then
          return
        end
        -- if there is already an extmarks no need to set it
        -- means cursor has moved already
        if
          vim.api.nvim_buf_get_extmarks(
            arg.buf,
            LINE_BLAME_NS,
            0,
            -1,
            { details = true }
          )[1]
        then
          return
        end

        -- display could have been disabled during loading blame
        if CONFIG.line_blame.enable then
          set_line_blame(arg.buf)
        end
      end)
    end)
  end

  vim.api.nvim_create_autocmd(
    { "BufEnter", "BufWritePost", "FileChangedShellPost" },
    {
      pattern = "*",
      callback = attach,
      desc = "Update hg line blame cache",
    }
  )
  vim.api.nvim_create_user_command("HgLineBlameToggle", function()
    CONFIG.line_blame.enable = not CONFIG.line_blame.enable
    if CONFIG.line_blame.enable then
      attach({ buf = vim.api.nvim_get_current_buf() })
    else
      for _, buf in ipairs(vim.api.nvim_list_bufs()) do
        vim.api.nvim_buf_clear_namespace(buf, LINE_BLAME_NS, 0, -1)
      end
    end
  end, { desc = "Toggle display of line blame" })

  -- The diff shown in the ghost text is virtual text, so the cursor can never
  -- land on it to yank it. Resolve the diff for the cursor line and copy its
  -- URL to the clipboard instead -- the only way to grab it over SSH.
  ---@param bufnr integer
  ---@param lnum integer 1-indexed
  ---@return string? diff phabdiff id (e.g. "D12345") or nil
  local function diff_for_line(bufnr, lnum)
    local filepath = vim.api.nvim_buf_get_name(bufnr)
    if not cache[filepath] then
      -- blame disabled or not yet loaded: query hg directly
      local repo_root = repo_root_of(filepath)
      local out = vim.system(blame_cmd(filepath), { text = true, cwd = repo_root }):wait()
      if out.code ~= 0 then
        return nil
      end
      cache[filepath] = parse_blame(blame_key(filepath, repo_root), out.stdout)
    end
    local blame = blame_for_line(bufnr, lnum)
    return blame and blame:match("D%d+")
  end

  vim.api.nvim_create_user_command("HgLineBlameCopyDiff", function()
    local bufnr = vim.api.nvim_get_current_buf()
    local lnum = vim.api.nvim_win_get_cursor(0)[1]
    local diff = diff_for_line(bufnr, lnum)
    if not diff then
      vim.notify(
        "No diff for this line (uncommitted or not tracked)",
        vim.log.levels.WARN
      )
      return
    end
    vim.fn.setreg("+", diff)
    vim.notify("Copied " .. diff, vim.log.levels.INFO)
  end, { desc = "Copy the diff number for the line under the cursor to clipboard" })
end

local _is_setup = false
return {
  ssl_state = SSL_STATE,
  ssl_commands = SSL_COMMANDS,
  open_working_copy_diff_split = open_working_copy_diff_split,
  open_file_diff_split = open_file_diff_split,
  refresh_ssl = function()
    if SSL_STATE.bufnr and vim.api.nvim_buf_is_valid(SSL_STATE.bufnr) then
      ssl_utils.refresh_buffer(SSL_STATE.bufnr)
    end
  end,
  get_base_revision = function()
    return CONFIG.base_revision
  end,
  setup = function(opts)
    -- this plugin uses functionality that is unique to 0.10 or newer
    if vim.fn.has("nvim-0.10") ~= 1 then
      vim.notify(
        "HG functionality is disabled on neovim versions below 0.10",
        vim.log.levels.WARN
      )
      return
    end
    CONFIG = vim.tbl_deep_extend("force", CONFIG, opts or {})

    -- avoid re-setting autocommands/commands again
    -- users can call meta.hg.setup() again to change CONFIG
    -- all autocommands/commands will be available after the initial call
    if _is_setup then
      return
    else
      _is_setup = true
    end

    setup_hg_signcolumn()
    setup_line_blame()

    vim.api.nvim_create_user_command("HgBlame", HgBlame, {})

    vim.api.nvim_create_user_command(
      "HgDiff",
      ":Telescope hg diff",
      { desc = "Hg diff picker" }
    )
    vim.api.nvim_create_user_command(
      "HgDiffIgnoreAllSpace",
      ":Telescope hg diff_ignore_all_space",
      { desc = "Hg diff picker ignoring space changes" }
    )
    vim.api.nvim_create_user_command(
      "HgDiffCurrentCommit",
      ":Telescope hg current_commit",
      { desc = "Hg diff picker for current commit changes" }
    )

    vim.api.nvim_create_user_command("HgPrev", HgPrev, { nargs = "?" })
    vim.api.nvim_create_user_command("HgNext", HgNext, { nargs = "?" })

    vim.api.nvim_create_user_command("HgWrite", HgWrite, {})
    vim.api.nvim_create_user_command("HgAdd", HgWrite, {})

    vim.api.nvim_create_user_command("HgHunkRevert", HgHunkRevert, {
      desc = "Revert the hunk beneath the cursor",
    })

    vim.api.nvim_create_user_command("HgRead", function()
      local buf = vim.api.nvim_get_current_buf()
      local obj = hg_utils.run_sync({ "cat", "-r", ".", vim.api.nvim_buf_get_name(buf) }, buf)
      if obj.code == 0 then
        vim.api.nvim_buf_set_lines(buf, 0, -1, false, split_file_lines(obj.stdout))
      end
    end, { desc = "Restore content of current buffer to the current commit" })

    vim.api.nvim_create_user_command(
      "HgRemove",
      HgRemove,
      { desc = "Remove the current file and the corresponding buffer" }
    )

    vim.api.nvim_create_user_command(
      "HgResolve",
      HgResolve,
      { desc = "Resolve file during merge conflict" }
    )

    vim.api.nvim_create_user_command("HgHistory", HgHistory, {
      nargs = "?",
      desc = [[
HgHistory          show 20 recent diffs to the current buffer
HgHistory 50       show 50 recent diffs to the current buffer
]],
    })

    vim.api.nvim_create_user_command("HgMove", HgMove, {
      nargs = 1,
      -- paths relative to the repo root, as HgMove reads them
      complete = function(arglead)
        local root = buf_repo_root()
        return vim.tbl_map(function(path)
          return path:sub(#root + 2)
        end, vim.fn.getcompletion(vim.fs.joinpath(root, arglead), "file"))
      end,
      desc = "Move the current file (destination relative to the repo root)",
    })

    vim.api.nvim_create_user_command("HgBrowse", makeHgBrowse(false, false), {
      nargs = 0,
      range = -1,
      desc = [[
HgBrowse           shows current buffer in phabricator
'<,'>HgBrowse      shows visually selected lines in phabricator
    ]],
    })

    vim.api.nvim_create_user_command(
      "HgBrowseYank",
      makeHgBrowse(false, true),
      {
        nargs = 0,
        range = -1,
        desc = [[
HgBrowseYank        yanks current buffer in phabricator
'<,'>HgBrowseYank   yanks visually selected lines in phabricator
    ]],
      }
    )

    vim.api.nvim_create_user_command("HgBrowseRev", makeHgBrowse(true, false), {
      nargs = 0,
      range = -1,
      desc = [[
HgBrowseRev        shows current buffer in phabricator for a current commit
'<,'>HgBrowseRev   shows current buffer in phabricator for a current commit
    ]],
    })

    vim.api.nvim_create_user_command(
      "HgBrowseRevYank",
      makeHgBrowse(true, true),
      {
        nargs = 0,
        range = -1,
        desc = [[
HgBrowseRevYank        yanks current buffer in phabricator for a current commit
'<,'>HgBrowseRevYank   yanks current buffer in phabricator for a current commit
    ]],
      }
    )

    vim.api.nvim_create_user_command(
      "HgSsl",
      function()
        repo_context.with_context(function()
          HgSsl()
        end)
      end,
      { desc = "Interactive smartlog for mercurial" }
    )

    vim.api.nvim_create_user_command(
      "HgSslSplit",
      function()
        repo_context.with_context(function()
          HgSsl({ split = true })
        end)
      end,
      { desc = "Interactive smartlog for mercurial (vsplit)" }
    )

    vim.api.nvim_create_user_command(
      "HgCommit",
      HgCommit,
      { desc = "Commit changes" }
    )

    vim.api.nvim_create_user_command(
      "HgAmend",
      HgAmend,
      { desc = "Amend changes into the current commit" }
    )

    vim.api.nvim_create_user_command(
      "HgCommitInteractive",
      HgCommitInteractive,
      { desc = "Commit changes interactevely" }
    )

    vim.api.nvim_create_user_command(
      "HgAbsorb",
      HgAbsorb,
      { desc = "Intelligently integrate pending changes into current stack" }
    )

    vim.api.nvim_create_user_command("HgSuggest", HgSuggest, {
      nargs = "*",
      desc = [[
HgSuggest                submit uncommitted changes as suggested changes on the current diff
HgSuggest --draft        submit as draft suggestions
HgSuggest -m "msg"       submit with a message
]],
    })

    vim.api.nvim_create_user_command("HgInlineComment", HgInlineComment, {
      range = true,
      desc = "Post a draft inline comment on the current diff at the selected lines",
    })

    vim.api.nvim_create_user_command("HgPublishDrafts", HgPublishDrafts, {
      desc = "Publish all draft inline comments on the current diff",
    })

    vim.api.nvim_create_user_command(
      "HgStatus",
      HgStatus,
      { desc = "Interactively stage files" }
    )

    vim.api.nvim_create_user_command(
      "HgChanges",
      HgChanges,
      { desc = "Select uncommitted files to amend or discard" }
    )

    vim.api.nvim_create_user_command("HgChangeBase", HgChangeBase, {
      nargs = "?",
      desc = [[
HgChangeBase           reset base revision to parent commit
HgChangeBase .         use current commit as base
HgChangeBase main      use main bookmark as base
HgChangeBase D123456   use diff as base
HgChangeBase abc123    use commit hash as base
]],
    })

    vim.api.nvim_create_user_command("HgShowBase", function()
      local base = CONFIG.base_revision
      if base then
        -- Get commit info
        get_commit_info(base, function(commit_hash, commit_display)
          local display_msg = commit_hash
          if commit_display ~= "" then
            display_msg = display_msg .. " " .. commit_display
          end
          vim.notify(
            "Current base revision: " .. display_msg,
            vim.log.levels.INFO
          )
        end)
      else
        vim.notify("Using default base (parent commit)", vim.log.levels.INFO)
      end
    end, { desc = "Show current base revision for HgDiff and gutter" })

    vim.api.nvim_create_user_command("HgChangeBaseStack", function()
      log_to_scuba({
        module = "hg",
        command = "HgChangeBaseStack",
      })

      -- Find the last public ancestor (base of the stack) and get its info
      get_commit_info(
        "last(ancestors(.) and public())",
        function(stack_base, commit_info)
          CONFIG.base_revision = stack_base
          local display_msg = "Set base to stack base (last public commit): "
            .. stack_base
          if commit_info ~= "" then
            display_msg = display_msg .. " " .. commit_info
          end
          vim.notify(display_msg, vim.log.levels.INFO)
          refresh_all_signs()
        end,
        function(stderr)
          if
            stderr:find("empty revision set")
            or stderr == "Empty output from hg log"
          then
            vim.schedule(function()
              vim.notify(
                "Could not find public ancestor. Are you on a public commit?",
                vim.log.levels.WARN
              )
            end)
          else
            notify_error("Failed to find stack base:\n" .. stderr)
          end
        end
      )
    end, { desc = "Set base revision to stack base (last public commit)" })
  end,
}
