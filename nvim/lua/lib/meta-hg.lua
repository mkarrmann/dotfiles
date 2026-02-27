local meta_util = require("meta.util")
local meta_util_hg = require("meta.util.hg")
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

---@class (exact) Hg.diff_file_pair
---@field file string
---@field old_buf integer?
---@field new_buf integer?
---@field is_live boolean

---@class (exact) Hg.diff_session
---@field pairs Hg.diff_file_pair[]
---@field index integer
---@field left_win integer
---@field right_win integer
---@field commit Hg.commit
---@field parent_rev string
---@field repo_root string

---@class (exact) Hg.preblame_opts
---@field scrollbind boolean
---@field wrap boolean
---@field foldenable boolean

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
  ---@type 'snacks'|'telescope'
  picker = "telescope",
  ---@type string? Base revision for diff and gutter (nil = parent commit)
  base_revision = nil,
}

local SSL_NS = vim.api.nvim_create_namespace("hg_ssl")
local SSL_STATE = {
  ---@type number? reuse the same buffer for the nvim process lifespan
  bufnr = nil,
  ---@type boolean
  blocked = false,
  ---@type boolean controls how current status is displayed and what actions are available
  merge_conflict = false,
}

---@type table<integer, Hg.diff_session>
local DIFF_SPLIT_SESSIONS = {}

hg_utils.on_blame_enter = function()
  local word_under_cursor = vim.fn.expand("<cword>")

  if vim.fn.match(word_under_cursor, "D\\d\\+") == -1 then
    return nil
  end

  meta_util.open_url("https://www.internalfb.com/diff/" .. word_under_cursor)
end
---@type table<integer, Hg.preblame_opts>
local BUF_OPTS_DICT = {}

---@param should_delete_buffer boolean
hg_utils.on_blame_exit = function(should_delete_buffer)
  -- remove blame buffer
  if should_delete_buffer then
    vim.cmd("bdelete")
  end
  local buf_nr = vim.api.nvim_buf_get_number(0)

  -- if we've set window options,
  -- let's reset them and clear dict
  if BUF_OPTS_DICT[buf_nr] ~= nil then
    for k, v in pairs(BUF_OPTS_DICT[buf_nr]) do
      vim.wo[k] = v
    end
    BUF_OPTS_DICT[buf_nr] = nil
  end
end

hg_utils.status = function()
  local result = {
    modified = {},
    added = {},
    removed = {},
    clean = {},
    missing = {},
    not_tracked = {},
    ignored = {},
  }
  local out = vim.system({ "hg", "status" }, { text = true }):wait()
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

hg_utils.can_commit = function()
  local status = hg_utils.status()

  return (#status.modified + #status.added + #status.removed) > 0
end

---Run a command in a new tab terminal buffer and exit the current buffer
---@param cmd string
---@param on_close ?function
---@param on_open ?function
function hg_utils.run_cmd_and_exit(cmd, on_close, on_open)
  vim.cmd("tabnew | term " .. cmd)

  if on_open then
    vim.schedule(on_open)
  end

  -- kills the tab and buffer on command exit
  local term_buf = vim.api.nvim_get_current_buf()
  vim.api.nvim_create_autocmd("TermClose", {
    buffer = term_buf,
    callback = function()
      vim.cmd.close()
      if vim.api.nvim_buf_is_valid(term_buf) then
        vim.api.nvim_buf_delete(term_buf, { force = true })
      end
      vim.cmd.doautocmd("BufEnter")
      if on_close then
        on_close()
      end
    end,
  })
end

local sign_util = {}

local function HgBlame()
  log_to_scuba({
    module = "hg",
    command = "HgBlame",
  })
  local buf_nr_for_blame = vim.api.nvim_buf_get_number(0)
  -- TODO:
  -- - [ ] check if file has been modified, if not -> proceed, if modified -> warn that lines may mismatch
  -- - [ ] check if file has been modified
  local cmd = table.concat({
    "hg blame",
    '-r "wdir()"', -- annotate changed lines
    "--user", -- include username
    "--date", -- include date
    "--phabdiff", -- include diff
    "-q", -- simple date format
    vim.fn.expand("%"), -- path to buffer file relative to `getcwd()`
  }, " ")

  local temp_filename = vim.fn.tempname()
  vim.cmd("silent !" .. cmd .. " > " .. temp_filename)
  local line = vim.fn.readfile(temp_filename, "", 1)[1]

  -- revisition info and content is split by `: `
  -- the width until `:` char is the width of the blame buffer we want
  -- add 2 to for `: `
  ---@type number
  local target_width = #vim.split(line, ":")[1] + 2

  ---@type Hg.preblame_opts
  local preblame_opts = {
    scrollbind = vim.wo.scrollbind,
    wrap = vim.wo.wrap,
    foldenable = vim.wo.foldenable,
  }
  -- current buffer num
  local buf_nr = vim.api.nvim_buf_get_number(0)
  BUF_OPTS_DICT[buf_nr] = preblame_opts

  local cursor = vim.api.nvim_win_get_cursor(0)
  local current_cursor_line = cursor[1]

  -- move cursor to the first line
  vim.api.nvim_win_set_cursor(0, { 1, 1 })

  vim.wo.scrollbind = true
  vim.wo.wrap = false
  vim.wo.foldenable = false

  vim.cmd("keepalt leftabove vsplit " .. temp_filename)
  vim.cmd("vertical resize " .. target_width) -- set split width

  -- set all buffer/window related settings
  vim.bo.filetype = "hgblame"
  vim.bo.modified = false
  vim.bo.modifiable = false
  vim.wo.number = false
  vim.wo.spell = false
  -- then fix the scroll
  vim.wo.scrollbind = true
  -- now that both windows have scroll binded we can scroll blame buffer
  -- and the code buffer scrolls too
  vim.api.nvim_win_set_cursor(0, { current_cursor_line, 1 })
  vim.wo.wrap = false
  vim.wo.foldcolumn = "0"
  vim.wo.foldenable = false

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
  vim.keymap.set("n", "gq", function()
    hg_utils.on_blame_exit(true)
  end, { buffer = 0 })
  vim.api.nvim_create_autocmd("WinClosed", {
    callback = function()
      vim.cmd("buf " .. buf_nr_for_blame)
      -- scheduling to wait for buffer to change
      vim.schedule(function()
        hg_utils.on_blame_exit(false)
      end)
    end,
    buffer = 0,
  })
end
local function HgPrev(opts)
  log_to_scuba({
    module = "hg",
    command = "HgPrev",
  })
  vim.cmd("!hg prev " .. opts.args)
end
local function HgNext(opts)
  log_to_scuba({
    module = "hg",
    command = "HgNext",
  })
  vim.cmd("!hg next " .. opts.args)
end
local function HgMove(opts)
  log_to_scuba({
    module = "hg",
    command = "HgMove",
  })
  -- TODO check if file exists already
  vim.cmd("silent !hg move % " .. opts.args)
end
local function HgRemove()
  log_to_scuba({
    module = "hg",
    command = "HgRemove",
  })
  vim.cmd("silent !hg remove %")
  vim.api.nvim_buf_delete(0, { force = true })
end
local function HgResolve()
  log_to_scuba({
    module = "hg",
    command = "HgResolve",
  })
  vim.cmd("silent !hg resolve --mark %")
  vim.api.nvim_buf_delete(0, { force = true })
end

---@param for_current_commit boolean
---@param yank boolean Yank instead of opening url or showing link to output
local function makeHgBrowse(for_current_commit, yank)
  return function(opts)
    local line1 = opts.line1
    local count = opts.count

    local repo_root = vim.fs.root(0, ".hg")
    if repo_root == nil then
      return vim.notify("Failed to locate repo root", vim.log.level.ERROR)
    end
    local repo_name = vim.fn.readfile(repo_root .. "/.hg/reponame")[1]
    if repo_name == nil or repo_name == "" then
      return vim.notify("Failed to infer repo name", vim.log.level.ERROR)
    end

    local buf_abs_path = vim.fn.expand("%:p")
    local buf_filepath_from_repo_root = buf_abs_path:sub(#repo_root + 1)

    local commit = ""
    if for_current_commit then
      local cmd = { "hg", "log", "-l", "1", "--template", "{node}" }
      local obj = vim.system(cmd, { text = true }):wait()
      if obj.code ~= 0 then
        return vim.notify("Failed to get current commit", vim.log.level.ERROR)
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
      elseif vim.g.clipboard or vim.opt.clipboard then
        vim.fn.setreg("*", url, "u")
      else
        vim.fn.setreg('"', url)
      end
    else
      meta_util.open_url(url)
    end
  end
end
local function HgHistory(opts)
  log_to_scuba({
    module = "hg",
    command = "HgHistory",
  })
  local count = tonumber(opts.args) or 50

  local current_file = vim.fn.expand("%")
  local template =
    "{date|shortdate}\t{phabdiff}\t{author|user}\t{desc|strip|firstline}"
  local cmd = "hg log "
    .. current_file
    .. ' --template "'
    .. template
    .. '\\n" -l '
    .. count
  local temp_filename = vim.fn.tempname()
  vim.cmd("silent !" .. cmd .. " > " .. temp_filename)

  vim.cmd("belowright split " .. temp_filename)

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
    for line = 0, total_commits, 1 do
      vim.api.nvim_buf_add_highlight(
        0, -- current buffer
        -1, -- ungrouped highlight
        "String", -- highlight group
        line,
        diff_id_start,
        diff_id_end
      )
    end
  end)
end
local function HgWrite()
  log_to_scuba({
    module = "hg",
    command = "HgWrite",
  })
  vim.cmd("write")
  vim.cmd("silent !hg add %")
end

local function HgHunkRevert()
  log_to_scuba({
    module = "hg",
    command = "HgHunkRevert",
  })
  meta_util_hg.revert_hunk_under_cursor(vim.fn.bufnr())
end

local ssl_utils = {}

local _ssl_file = nil
function ssl_utils.get_ssl_file()
  if _ssl_file ~= nil then
    return _ssl_file
  end

  local temp_filename = vim.fn.tempname()
  local temp_dir = vim.fs.dirname(temp_filename)
  local result = temp_dir .. "/smartlog"
  _ssl_file = result
  return result
end

---@param line string
---@return Hg.commit?
function ssl_utils.parse_diff_line(line)
  local commit_marker = "[@ox]  "
  local has_commit_info = string.match(line, commit_marker)
  if has_commit_info == nil then
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
---@param sl_lines string[]
function ssl_utils.annotate_hg_status(bufnr, sl_lines)
  vim.system({ "hg", "status" }, { text = true }, function(status_obj)
    if status_obj.code ~= 0 then
      vim.notify("Failed to check hg status", vim.log.levels.ERROR)
      return
    end
    local out = vim.trim(status_obj.stdout or "")
    SSL_STATE.merge_conflict = string.find(
      status_obj.stderr or "",
      "The repository is in an unfinished"
    ) ~= nil
    if out == "" then
      return
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
    if commit and commit.is_bookmark then
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

---@param bufnr number
function ssl_utils.refresh_buffer(bufnr)
  SSL_STATE.blocked = true

  -- first quickly fill the buffer with commits without diff status
  vim.system({ "hg", "sl" }, { text = true }, function(sl_obj)
    if sl_obj.code ~= 0 then
      vim.notify("Failed to run hg sl", vim.log.levels.ERROR)
      SSL_STATE.blocked = false
      return
    end

    local sl_lines = vim.split(sl_obj.stdout, "\n")
    sl_lines = filter_hidden_bookmarks(sl_lines)
    vim.schedule(function()
      vim.api.nvim_buf_set_lines(bufnr or 0, 0, -1, false, sl_lines)
    end)

    -- later async update content with diff statuses
    vim.system({ "hg", "ssl" }, { text = true }, function(ssl_obj)
      if ssl_obj.code ~= 0 then
        SSL_STATE.blocked = false
        vim.notify("Failed to run hg ssl", vim.log.levels.ERROR)
        return
      end

      local ssl_lines = vim.split(ssl_obj.stdout, "\n")
      ssl_lines = filter_hidden_bookmarks(ssl_lines)
      -- cannot call "nvim_buf_set_lines" inside a lua loop
      vim.schedule(function()
        SSL_STATE.blocked = false
        vim.api.nvim_buf_set_lines(bufnr or 0, 0, -1, false, ssl_lines)
        vim.notify("ssl updated", vim.log.levels.INFO)
      end)

      if CONFIG.ssl.status then
        ssl_utils.annotate_hg_status(bufnr, sl_lines)
      end
    end)
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

  SSL_STATE.blocked = true

  vim.system(cmd, { text = true }, function(obj)
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

      SSL_STATE.blocked = false
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

    if not reload then
      SSL_STATE.blocked = false
    end

    vim.schedule(function()
      ssl_utils.action_end({
        reload = reload,
        bufnr = bufnr,
        msg = msg,
      })
    end)
  end)
end

local DIFF_SPLIT_KEYMAPS = { "]f", "[f", "]F", "[F", "gf", "gq" }

---@param lines string[]
---@param name string
---@return integer
local function create_scratch_buf(lines, name)
  local buf = vim.api.nvim_create_buf(false, true)
  vim.api.nvim_buf_set_lines(buf, 0, -1, false, lines)
  vim.api.nvim_buf_set_name(buf, name)
  vim.bo[buf].buftype = "nofile"
  vim.bo[buf].modifiable = false
  vim.bo[buf].swapfile = false
  return buf
end

---@param session Hg.diff_session
local function diff_split_update_winbar(session)
  local pair = session.pairs[session.index]
  local pos = string.format("[%d/%d]", session.index, #session.pairs)

  vim.wo[session.right_win].winbar = "%#Comment# "
    .. session.parent_rev
    .. " %* "
    .. pair.file
    .. " "
    .. pos

  if pair.is_live then
    vim.wo[session.left_win].winbar = "%#DiagnosticOk# LIVE %* "
      .. pair.file
      .. " "
      .. pos
  else
    vim.wo[session.left_win].winbar = "%#Comment# "
      .. session.commit.hash
      .. " %* "
      .. pair.file
      .. " "
      .. pos
  end
end

local diff_split_set_keymaps

---@param session Hg.diff_session
---@param pair Hg.diff_file_pair
local function diff_split_load_pair(session, pair)
  if pair.old_buf then
    return
  end

  local escaped_file = vim.fn.shellescape(pair.file)

  local old_content = vim.fn.systemlist(
    "hg cat -r " .. session.parent_rev .. " " .. escaped_file
  )
  local old_ok = vim.v.shell_error == 0

  local is_live = false
  local new_buf

  if session.commit.is_current then
    local real_path = session.repo_root .. "/" .. pair.file
    if vim.fn.filereadable(real_path) == 1 then
      is_live = true
      new_buf = vim.fn.bufadd(real_path)
      vim.api.nvim_set_option_value("swapfile", false, { buf = new_buf })
      vim.fn.bufload(new_buf)
    end
  end

  if not is_live then
    local new_content = vim.fn.systemlist(
      "hg cat -r " .. session.commit.hash .. " " .. escaped_file
    )
    local new_ok = vim.v.shell_error == 0
    if not old_ok and not new_ok then
      vim.notify(
        "Skipping " .. pair.file .. " (unreadable at either revision)",
        vim.log.levels.WARN
      )
      return
    end
    new_buf = create_scratch_buf(
      new_ok and new_content or {},
      "hg-diff://" .. session.commit.hash .. "/" .. pair.file
    )
  end

  pair.old_buf = create_scratch_buf(
    old_ok and old_content or {},
    "hg-diff://" .. session.parent_rev .. "/" .. pair.file
  )
  pair.new_buf = new_buf
  pair.is_live = is_live

  diff_split_set_keymaps(session)
end

---@param session Hg.diff_session
---@param index integer
local function diff_split_show_pair(session, index)
  if
    not vim.api.nvim_win_is_valid(session.left_win)
    or not vim.api.nvim_win_is_valid(session.right_win)
  then
    vim.notify("Diff windows are no longer valid", vim.log.levels.WARN)
    local tab = vim.api.nvim_get_current_tabpage()
    DIFF_SPLIT_SESSIONS[tab] = nil
    return
  end

  local pair = session.pairs[index]
  if not pair.old_buf then
    diff_split_load_pair(session, pair)
    if not pair.old_buf then
      return
    end
  end

  session.index = index

  vim.api.nvim_win_call(session.left_win, function()
    vim.cmd("diffoff")
  end)
  vim.api.nvim_win_call(session.right_win, function()
    vim.cmd("diffoff")
  end)

  vim.api.nvim_win_set_buf(session.left_win, pair.new_buf)
  vim.api.nvim_win_set_buf(session.right_win, pair.old_buf)

  vim.api.nvim_win_call(session.left_win, function()
    vim.cmd("diffthis")
  end)
  vim.api.nvim_win_call(session.right_win, function()
    vim.cmd("diffthis")
  end)

  for _, win in ipairs({ session.left_win, session.right_win }) do
    vim.wo[win].scrollbind = true
    vim.wo[win].relativenumber = false
    vim.wo[win].statuscolumn = ""
    vim.wo[win].foldenable = false
  end

  diff_split_update_winbar(session)
  vim.cmd("syncbind")

  vim.api.nvim_win_call(session.left_win, function()
    vim.cmd("normal! gg")
  end)
  vim.api.nvim_win_call(session.right_win, function()
    vim.cmd("normal! gg")
  end)
  vim.api.nvim_set_current_win(session.left_win)
end

---@param direction 1|-1
local function diff_split_cycle(direction)
  local tab = vim.api.nvim_get_current_tabpage()
  local session = DIFF_SPLIT_SESSIONS[tab]
  if not session then
    return
  end
  if #session.pairs <= 1 then
    vim.notify("Only one file in this diff", vim.log.levels.INFO)
    return
  end

  local n = #session.pairs
  local new_index = ((session.index - 1 + direction) % n) + 1
  diff_split_show_pair(session, new_index)
end

local function diff_split_jump_to_file()
  local tab = vim.api.nvim_get_current_tabpage()
  local session = DIFF_SPLIT_SESSIONS[tab]
  if not session then
    return
  end

  ---@type {index: integer, file: string}[]
  local items = {}
  for i, pair in ipairs(session.pairs) do
    table.insert(items, { index = i, file = pair.file })
  end

  vim.ui.select(items, {
    prompt = "Jump to file:",
    format_item = function(item)
      local marker = item.index == session.index and " (current)" or ""
      return string.format(
        "[%d/%d] %s%s",
        item.index,
        #session.pairs,
        item.file,
        marker
      )
    end,
  }, function(choice)
    if choice then
      diff_split_show_pair(session, choice.index)
    end
  end)
end

---@param tab integer
local function diff_split_cleanup(tab)
  local session = DIFF_SPLIT_SESSIONS[tab]
  if not session then
    return
  end

  for _, pair in ipairs(session.pairs) do
    if pair.old_buf and vim.api.nvim_buf_is_valid(pair.old_buf) then
      vim.api.nvim_buf_delete(pair.old_buf, { force = true })
    end
    if pair.is_live then
      if pair.new_buf and vim.api.nvim_buf_is_valid(pair.new_buf) then
        for _, key in ipairs(DIFF_SPLIT_KEYMAPS) do
          pcall(vim.keymap.del, "n", key, { buffer = pair.new_buf })
        end
      end
    elseif pair.new_buf and vim.api.nvim_buf_is_valid(pair.new_buf) then
      vim.api.nvim_buf_delete(pair.new_buf, { force = true })
    end
  end

  DIFF_SPLIT_SESSIONS[tab] = nil
end

local function diff_split_close()
  local tab = vim.api.nvim_get_current_tabpage()
  if not DIFF_SPLIT_SESSIONS[tab] then
    return
  end
  vim.cmd("tabclose")
end

---@param session Hg.diff_session
diff_split_set_keymaps = function(session)
  ---@type table<integer, boolean>
  local seen = {}
  for _, pair in ipairs(session.pairs) do
    if not pair.old_buf then
      goto continue
    end
    for _, b in ipairs({ pair.old_buf, pair.new_buf }) do
      if not seen[b] then
        seen[b] = true
        vim.keymap.set("n", "]f", function()
          diff_split_cycle(1)
        end, { buffer = b, desc = "Next diff file" })
        vim.keymap.set("n", "[f", function()
          diff_split_cycle(-1)
        end, { buffer = b, desc = "Previous diff file" })
        vim.keymap.set("n", "]F", function()
          local tab = vim.api.nvim_get_current_tabpage()
          local s = DIFF_SPLIT_SESSIONS[tab]
          if s then
            diff_split_show_pair(s, #s.pairs)
          end
        end, { buffer = b, desc = "Last diff file" })
        vim.keymap.set("n", "[F", function()
          local tab = vim.api.nvim_get_current_tabpage()
          local s = DIFF_SPLIT_SESSIONS[tab]
          if s then
            diff_split_show_pair(s, 1)
          end
        end, { buffer = b, desc = "First diff file" })
        vim.keymap.set(
          "n",
          "gf",
          diff_split_jump_to_file,
          { buffer = b, desc = "Jump to diff file" }
        )
        vim.keymap.set(
          "n",
          "gq",
          diff_split_close,
          { buffer = b, desc = "Close diff tab" }
        )
      end
    end
    ::continue::
  end
end

vim.api.nvim_create_autocmd("TabClosed", {
  callback = function()
    for tab, _ in pairs(DIFF_SPLIT_SESSIONS) do
      if not vim.api.nvim_tabpage_is_valid(tab) then
        diff_split_cleanup(tab)
      end
    end
  end,
  desc = "Clean up diff split sessions on tab close",
})

---@class (exact) Hg.ssl_command
---@field desc string
---@field action fun(commit: Hg.commit, bufnr: number)

-- Here will be the command in the future diffs
---@type table<string, Hg.ssl_command>
local SSL_COMMANDS = {
  show = {
    desc = "Show commit message and changes",
    action = function(commit, bufnr)
      local cmd =
        { "hg", "show", "--color=always", commit.hash, "|", "less", "-R" }
      hg_utils.run_cmd_and_exit(table.concat(cmd, " "), function()
        ssl_utils.action_end({
          reload = true,
          bufnr = bufnr,
        })
      end, function()
        vim.cmd("startinsert")
      end)
    end,
  },
  diff_split = {
    desc = "View file diffs (side-by-side split, cycle with ]f/[f)",
    action = function(commit)
      local template = '{files % "{file}\\n"}'
      local cmd = "hg log -r "
        .. commit.hash
        .. " --template "
        .. vim.fn.shellescape(template)
      local files_raw = vim.fn.systemlist(cmd)
      local files = vim.tbl_filter(function(f)
        return f ~= ""
      end, files_raw)

      if #files == 0 then
        vim.notify("No files changed in this commit", vim.log.levels.WARN)
        return
      end

      local parent_rev = commit.hash .. "^"
      local cwd = vim.uv.cwd() or vim.fn.getcwd()
      local repo_root = vim.fs.root(cwd, ".hg") or cwd

      ---@type Hg.diff_file_pair[]
      local file_pairs = {}
      for _, file in ipairs(files) do
        table.insert(file_pairs, {
          file = file,
          is_live = false,
        })
      end

      vim.cmd("tabnew")
      local tab = vim.api.nvim_get_current_tabpage()
      local left_win = vim.api.nvim_get_current_win()
      vim.cmd("rightbelow vsplit")
      local right_win = vim.api.nvim_get_current_win()

      ---@type Hg.diff_session
      local session = {
        pairs = file_pairs,
        index = 1,
        left_win = left_win,
        right_win = right_win,
        commit = commit,
        parent_rev = parent_rev,
        repo_root = repo_root,
      }
      DIFF_SPLIT_SESSIONS[tab] = session

      diff_split_load_pair(session, file_pairs[1])
      if not file_pairs[1].old_buf then
        vim.cmd("tabclose")
        return
      end

      diff_split_show_pair(session, 1)
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

      hg_utils.run_cmd_and_exit(table.concat(cmd, " "), function()
        ssl_utils.action_end({
          reload = true,
          bufnr = bufnr,
        })
      end)
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

      hg_utils.run_cmd_and_exit(table.concat(cmd, " "), function()
        ssl_utils.action_end({
          reload = true,
          bufnr = bufnr,
        })
      end)
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

      hg_utils.run_cmd_and_exit(table.concat(cmd, " "), function()
        ssl_utils.action_end({
          reload = true,
          bufnr = bufnr,
        })
      end)
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
    action = function(commit)
      meta_util.open_url("https://www.internalfb.com/diff/" .. commit.diff)
    end,
  },
  submit = {
    desc = "Submit to phabricator",
    action = function(commit, bufnr)
      local target = commit.diff and commit.diff or commit.hash
      local cmd = { "jf", "submit" }
      if not commit.is_current then
        table.insert(cmd, target)
      end
      ssl_utils.run_cmd(cmd, bufnr, true)
    end,
  },
  submit_stack = {
    desc = "Submit stack to phabricator",
    action = function(commit, bufnr)
      local cmd = { "jf", "submit", "--stack" }
      if not commit.is_current then
        table.insert(cmd, commit.hash)
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
      local cmd = { "jf", "submit", "--draft", "--stack" }
      if not commit.is_current then
        table.insert(cmd, commit.hash)
      end
      ssl_utils.run_cmd(cmd, bufnr, true)
    end,
  },
  author_internal_profile = {
    desc = "View author's internal profile",
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
        SSL_STATE.blocked = true

        vim.system(cmd, { text = true }, function(obj)
          SSL_STATE.blocked = false
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
            if string.find(obj.stderr, "hit merge conflicts") then
              SSL_STATE.merge_conflict = true

              -- won't rebase due to local changes
              if
                string.find(obj.stderr, "but you have working copy changes")
              then
                return ssl_utils.action_end({
                  reload = true,
                  bufnr = bufnr,
                  msg = "Rebased failed due to local changes, please commit or amend them before rebasing",
                })
              end

              -- user needs to resolve merge conflicts
              if string.find(obj.stderr, " conflicts while merging ") then
                return ssl_utils.action_end({
                  reload = true,
                  bufnr = bufnr,
                  msg = "Rebased failed due to merge conflicts",
                })
              end

              return ssl_utils.action_end({
                reload = true,
                bufnr = bufnr,
                msg = string.format(
                  "Rebase failed unexpectedly\n\nSTDOUT:\n%s\n\nSTDERR:\n%s",
                  obj.stdout or "",
                  obj.stderr or ""
                ),
              })
            end
          end)
        end)
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
            ssl_utils.action_end({
              reload = true,
              bufnr = bufnr,
              msg = "Rebase cancelled",
            })
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

  if SSL_STATE.bufnr and vim.api.nvim_buf_is_valid(SSL_STATE.bufnr) then
    local win = vim.fn.win_findbuf(SSL_STATE.bufnr)[1]
    if win then
      vim.api.nvim_set_current_win(win)
    elseif split then
      vim.cmd("botright vnew")
      vim.api.nvim_set_current_buf(SSL_STATE.bufnr)
    else
      vim.api.nvim_set_current_buf(SSL_STATE.bufnr)
    end

    return
  end

  -- check if the buffer is already created
  if not SSL_STATE.bufnr or not vim.api.nvim_buf_is_valid(SSL_STATE.bufnr) then
    SSL_STATE.bufnr = vim.api.nvim_create_buf(
      true, -- listed
      true -- scratch
    )
    vim.schedule(function()
      ssl_utils.refresh_buffer(SSL_STATE.bufnr)
    end)
    local cwd = vim.uv.cwd() or vim.fn.getcwd()
    local repo_root = vim.fs.root(cwd, ".hg") or cwd
    vim.api.nvim_buf_set_name(SSL_STATE.bufnr, "hgssl [" .. repo_root .. "]")
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
      if SSL_STATE.blocked then
        vim.notify("SSL is updating, please wait...", vim.log.levels.WARN)
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

  local wk = require("which-key")
  local buf = SSL_STATE.bufnr
  wk.add({
    buffer = buf,
    { "<CR>", group = "actions" },
    { "<CR>s", ssl_action("show"), desc = "Show commit" },
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
    { "<CR>x", ssl_action("hide"), desc = "Hide commit" },
    { "<CR>b", ssl_action("rebase"), desc = "Rebase onto..." },
    { "<CR>p", ssl_action("arc_pull"), desc = "Arc pull" },
    { "<CR>gx", ssl_action("open_in_phabricator", function(c)
      if not c.diff then
        vim.notify("No diff associated with this commit", vim.log.levels.WARN)
        return false
      end
      return true
    end), desc = "Open in Phabricator" },
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
      -- BufEnter fires before an ssl command which leads to a race condition
      -- This delay allows ssl commands to block ssl buffer before this autocmd
      vim.defer_fn(function()
        if not SSL_STATE.blocked then
          ssl_utils.refresh_buffer(SSL_STATE.bufnr)
        end
      end, 30)
    end,
    desc = "Update ssl buffer when focused",
  })

  vim.keymap.set("n", "?", function()
    local lines = {
      "HG SSL Buffer",
      "===================================================",
      "Navigate to next/prev commit with j/k",
      "",
      "Keymaps:",
      "<cr>  select available commands for selected commit",
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

  local nowait_opt = { buffer = true, nowait = true }
  vim.keymap.set(
    "n",
    CONFIG.ssl.keys.current,
    ssl_utils.go_to_current_commit,
    nowait_opt
  )
  vim.keymap.set("n", "C", ssl_utils.go_to_current_commit, nowait_opt)

  vim.keymap.set("n", CONFIG.ssl.keys.show, function()
    local commit = get_current_commit()
    if commit == nil then
      vim.notify("No commit under cursor")
      return
    end
    SSL_COMMANDS["show"].action(commit, SSL_STATE.bufnr)
  end, nowait_opt)

  vim.keymap.set("n", CONFIG.ssl.keys.open, function()
    local commit = ssl_utils.parse_diff_line(vim.api.nvim_get_current_line())
    if commit and commit.diff then
      meta_util.open_url("https://www.internalfb.com/diff/" .. commit.diff)
    end
  end, { buffer = true, desc = "Open diff in phabricator" })
end

local function HgCommit()
  log_to_scuba({
    module = "hg",
    command = "HgCommit",
  })

  local has_changes = hg_utils.can_commit()
  if has_changes == false then
    vim.notify("No changes to commit", vim.log.levels.ERROR)
    return
  end

  local commit_filepath = vim.fn.tempname() .. ".commit"

  -- populate temp file with commit template
  vim.fn.system("hg debugcommitmessage >> " .. commit_filepath)

  vim.cmd("tabnew " .. commit_filepath)
  vim.bo.filetype = "hgcommit"

  local buf_nr = vim.api.nvim_get_current_buf()

  vim.api.nvim_create_autocmd("WinClosed", {
    buffer = 0,
    callback = function()
      local function cleanup()
        vim.schedule(function()
          vim.fn.delete(commit_filepath)
          vim.api.nvim_buf_delete(buf_nr, { force = true })
          -- update signs for the buffer that we returned to
          sign_util.infer_signs(0)
        end)
      end
      local lines = {}
      local raw_lines = vim.api.nvim_buf_get_lines(buf_nr, 0, -1, true)
      for _, line in ipairs(raw_lines) do
        if not vim.startswith(line, "HG:") then
          table.insert(lines, line)
        end
      end
      local concat_lines = table.concat(lines, "")
      if vim.trim(concat_lines) == "" then
        vim.notify("Commit aborted", vim.log.levels.INFO)
        cleanup()
      else
        local cmd = { "hg", "commit", "--logfile", commit_filepath }
        vim.system(cmd, { text = true }, function(commit_obj)
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
            end)
          end
          cleanup()
        end)
      end
    end,
  })
end

local function HgAmend()
  log_to_scuba({
    module = "hg",
    command = "HgAmend",
  })

  local has_changes = hg_utils.can_commit()
  if has_changes == false then
    vim.notify("No changes to amend", vim.log.levels.ERROR)
    return
  end

  hg_utils.run_cmd_and_exit("hg amend")
end

local function HgCommitInteractive()
  log_to_scuba({
    module = "hg",
    command = "HgCommitInteractive",
  })

  local has_changes = hg_utils.can_commit()
  if has_changes == false then
    vim.notify("No changes to commit", vim.log.levels.ERROR)
    return
  end

  hg_utils.run_cmd_and_exit("hg commit --interactive")
end

local function HgAbsorb()
  log_to_scuba({
    module = "hg",
    command = "HgAbsorb",
  })

  local has_changes = hg_utils.can_commit()
  if has_changes == false then
    vim.notify("No changes to absorb", vim.log.levels.ERROR)
    return
  end

  hg_utils.run_cmd_and_exit("hg absorb")
end

local function HgSuggest(opts)
  log_to_scuba({
    module = "hg",
    command = "HgSuggest",
  })

  local has_changes = hg_utils.can_commit()
  if has_changes == false then
    vim.notify("No changes to suggest", vim.log.levels.ERROR)
    return
  end

  local cmd = { "jf", "suggest", "--no-commit" }

  local args = vim.trim(opts.args or "")
  if args ~= "" then
    vim.list_extend(cmd, vim.split(args, "%s+"))
  end

  hg_utils.run_cmd_and_exit(table.concat(cmd, " "))
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
local function get_commit_info(revision, on_success, on_error)
  vim.system({
    "hg",
    "log",
    "-r",
    revision,
    "-l",
    "1",
    "--template",
    "{node|short}\n{phabdiff} {desc|firstline}",
  }, { text = true }, function(obj)
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

local function HgStatus()
  log_to_scuba({
    module = "hg",
    command = "HgStatus",
  })

  local function update_status(buf)
    local out = vim.system({ "hg", "status" }):wait()
    if out.code ~= 0 then
      return vim.notify(
        "Failed to run git status\n" .. out.stderr,
        vim.log.levels.ERROR
      )
    end
    local stdout = vim.trim(out.stdout)
    if stdout == "" then
      stdout = "No changes"
    end
    -- "XY foo/bar.baz"
    -- X shows the status of the index
    -- Y shows the status of the working tree
    local lines = vim.split(out.stdout, "\n")
    vim.api.nvim_buf_set_lines(buf, 0, -1, false, lines)
    local mode_to_hl = {
      M = "@diff.plus", -- modified
      A = "@diff.plus", -- added
      R = "@diff.minus", -- removed
      C = "@diff.minus", -- clean
      I = "@diff.delta", -- ignored
      ["!"] = "@diff.delta", -- missing
      ["?"] = "@diff.delta", -- not tracked
    }
    for i, line in ipairs(lines) do
      local mode = line:sub(1, 1)
      if mode_to_hl[mode] then
        vim.api.nvim_buf_add_highlight(buf, -1, mode_to_hl[mode], i - 1, 0, 1)
      end
    end
  end

  local buf = vim.api.nvim_create_buf(false, true)
  update_status(buf)

  -- initial buffer setup
  vim.api.nvim_set_option_value("buftype", "nofile", { buf = buf })
  vim.api.nvim_set_option_value("bufhidden", "wipe", { buf = buf })
  vim.api.nvim_set_option_value("swapfile", false, { buf = buf })
  vim.api.nvim_buf_set_name(buf, "hg status")

  -- open a new window and set it to the buffer
  vim.cmd.split()
  vim.api.nvim_win_set_buf(0, buf)
  vim.api.nvim_win_set_height(0, 10)

  -- add keymaps
  do
    local char_to_command = {
      a = "add",
      s = "add", --stage (like fugitive)
      X = "reset", -- discard change under cursor
      -- TODO show diff for file under cursor
      u = "forget", -- unstage (like fugitive)
      d = "forget",
    }
    for char, command in pairs(char_to_command) do
      -- TODO support ranges
      vim.keymap.set("n", char, function()
        local line = vim.api.nvim_get_current_line()
        local path = line:match("..%s*(.*)")

        local out = vim.system({ "hg", command, path }):wait()
        if out.code ~= 0 then
          return vim.notify(
            'Failed to run "hg '
              .. command
              .. " "
              .. path
              .. '"\n'
              .. out.stderr,
            vim.log.levels.ERROR
          )
        end

        update_status(buf)
      end, { desc = "hg " .. command .. " file", buffer = buf })
    end
    vim.keymap.set("n", "?", function()
      return vim.print(table.join({
        "a - add (hg add)",
        "s - add (fugitive keymap) (hg add)",
        "X - discard change under cursor (hg reset)",
        "u - unstage (fugitive keymap) (hg forget)",
        "d - unstage (hg forget)",
        "? - show help",
      }, "\n"))
    end, { desc = "show help", buffer = buf })
  end
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
  ---@param current_lines string[]
  function sign_util.apply_signs(bufnr, hunks, current_lines)
    sign_util.clear_namespace(bufnr, true)
    local buf_lines_total = vim.api.nvim_buf_line_count(bufnr)

    if buf_lines_total < 1 then
      return
    end

    ---@diagnostic disable-next-line: param-type-mismatch
    for _, hunk in ipairs(hunks) do
      local _old_line, removed, new_file_line, added = unpack(hunk)

      -- last line is not annotated in vscode or `hg diff` command
      if removed == 1 and added == 1 and new_file_line == #current_lines then
        goto continue
      end

      local above_removed_line = new_file_line - 2
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

      ::continue::
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

    local current_lines = vim.api.nvim_buf_get_lines(bufnr, 0, -1, true)
    vim.system(
      { "hg", "status", filepath },
      { text = true },
      function(status_obj)
        if status_obj.code ~= 0 then
          clear_hg_diff(bufnr)
          sign_util.clear_namespace(bufnr)
          return
        end

        local stdout_lines = vim.split(status_obj.stdout, "\n")
        local file_state = ""
        if #stdout_lines > 0 and stdout_lines[1] ~= "" then
          file_state = stdout_lines[1]:sub(1, 1)
        end

        -- If no base_revision is set, only show diffs for locally modified files
        -- If base_revision IS set, show diff between current version and that base (even for unmodified files)
        if not CONFIG.base_revision and file_state ~= "M" then
          clear_hg_diff(bufnr)
          sign_util.clear_namespace(bufnr)
          return
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
          { text = true },
          function(cat_obj)
            if cat_obj.code ~= 0 or not cat_obj.stdout then
              clear_hg_diff(bufnr)
              sign_util.clear_namespace(bufnr)
              return
            end

            ---@type string
            local old_content = cat_obj.stdout

            ---@example { { 1179, 0, 1180, 2 }, { 1298, 0, 1301, 1 }, { 1307, 0, 1311, 6 }, { 1622, 1, 1631, 1 } }
            local hunks = vim.diff(
              old_content,
              table.concat(current_lines, "\n"),
              { result_type = "indices" }
            )
            if not hunks then
              clear_hg_diff(bufnr)
              sign_util.clear_namespace(bufnr)
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
                return h[3]
              end,
              ---@diagnostic disable-next-line: param-type-mismatch
              hunks
            )

            vim.schedule(function()
              sign_util.apply_signs(bufnr, hunks, current_lines)
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
    -- when user runs commands inline, ie :!hg amend
    "CmdlineLeave",
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

  vim.api.nvim_create_autocmd({
    "TextChanged",
    "TextChangedI",
  }, {
    pattern = "*",
    callback = meta_util.debounce(function(arg)
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
      -- last hunk is never displayed
      if not hunks or #hunks < 2 then
        return
      end

      local cursor = vim.api.nvim_win_get_cursor(0)
      local total_lines = #vim.api.nvim_buf_get_lines(0, 0, -1, true)

      if direction == 1 then
        for _, start_line in ipairs(hunks) do
          -- last line never has signs
          -- go to the very first hunk
          if start_line == total_lines then
            return go_to_line(hunks[1])
          elseif start_line > cursor[1] then
            return go_to_line(start_line)
          end
        end
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
    -- last hunk is never displayed
    if not hunks or #hunks < 2 then
      return
    end

    return go_to_line(hunks[#hunks - 1])
  end, { desc = "Go to last hg hunk" })
  vim.keymap.set({ "x", "n" }, "[H", function()
    local hunks = cache[vim.api.nvim_get_current_buf()]
    -- last hunk is never displayed
    if not hunks or #hunks > 0 then
      return
    end

    return go_to_line(hunks[1])
  end, { desc = "Go to first hg hunk" })
end

local LINE_BLAME_NS = vim.api.nvim_create_namespace("hg_line_blame")
local LINE_BLAME_GROUP =
  vim.api.nvim_create_augroup("hg_line_blame", { clear = true })
local function setup_line_blame()
  ---@type table<string, string[]> Filepath to blame lines
  local cache = {}

  local function set_line_blame(bufnr)
    local cursor = vim.api.nvim_win_get_cursor(0)

    local rel_filepath = vim.fn.expand("%")
    local blame_lines = cache[rel_filepath]
    if not blame_lines then
      return
    end
    local blame_line = blame_lines[cursor[1]]
    -- not committed line
    if blame_line == "" then
      return
    end

    vim.api.nvim_buf_set_extmark(bufnr, LINE_BLAME_NS, cursor[1] - 1, 0, {
      hl_mode = "combine",
      virt_text = {
        { blame_line, CONFIG.line_blame.highlight },
      },
    })
  end

  vim.api.nvim_create_autocmd(
    { "BufEnter", "BufWritePost", "FileChangedShellPost" },
    {
      pattern = "*",
      callback = function(arg)
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

        local handle_cursor_moved = meta_util.debounce(
          function(cursor_moved_arg)
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

        local rel_filepath = vim.fn.expand("%")
        local cmd = {
          "hg",
          "blame",
          "-r",
          "wdir()", -- annotate changed lines
          "--user", -- include username
          "--date", -- include date
          "--phabdiff", -- include diff
          "-q", -- simple date format
          rel_filepath,
        }
        vim.system(cmd, { text = true }, function(blame_obj)
          if blame_obj.code ~= 0 then
            return
          end
          local blame_out_lines = vim.split(vim.trim(blame_obj.stdout), "\n")
          if not blame_out_lines then
            return
          end

          cache[rel_filepath] = vim.tbl_map(function(line)
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

          -- schedule since cannot call nvim_buf_get_extmarks inside lua loop
          vim.schedule(function()
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
      end,
      desc = "Update hg line blame cache",
    }
  )
  vim.api.nvim_create_user_command("HgLineBlameToggle", function()
    if CONFIG.line_blame.enable then
      -- clear all extmarks
      vim.api.nvim_buf_clear_namespace(0, LINE_BLAME_NS, 0, -1)
    end
    CONFIG.line_blame.enable = not CONFIG.line_blame.enable
  end, { desc = "Toggle display of line blame" })
end

local function hg_diff_snacks_picker(opts)
  meta_util.log_to_scuba({
    module = "hg",
    command = "hg_diff",
  })
  opts = opts or {}
  local additional_args = opts.additional_args or {}
  local cmd = { "hg", "diff" }
  -- Add base revision if configured
  if CONFIG.base_revision then
    table.insert(cmd, "--rev")
    table.insert(cmd, CONFIG.base_revision)
  end
  vim.list_extend(cmd, additional_args)
  local hunks = meta_util_hg.get_diff_hunks({ cmd = cmd })

  local items = vim.tbl_map(function(hunk)
    return {
      text = hunk.filename .. ":" .. hunk.lnum,
      item = {},
      file = hunk.filename,
      pos = { hunk.lnum, 0 },
      preview = {
        text = table.concat(hunk.raw_lines, "\n"),
        ft = "diff",
        loc = false,
      },
    }
  end, hunks)

  require("snacks.picker").pick("hg_diff", {
    items = items,
    preview = "preview",
  })
end

local function hg_diff_current_commit_snacks_picker()
  meta_util.log_to_scuba({
    module = "hg",
    command = "hg_diff_current_commit",
  })
  local cmd = { "hg", "diff", "-c", "." }
  local hunks = meta_util_hg.get_diff_hunks({ cmd = cmd })

  local items = vim.tbl_map(function(hunk)
    return {
      text = hunk.filename .. ":" .. hunk.lnum,
      item = {},
      file = hunk.filename,
      pos = { hunk.lnum, 0 },
      preview = {
        text = table.concat(hunk.raw_lines, "\n"),
        ft = "diff",
        loc = false,
      },
    }
  end, hunks)

  require("snacks.picker").pick("hg_diff_current_commit", {
    items = items,
    preview = "preview",
  })
end

local _is_setup = false
return {
  ssl_state = SSL_STATE,
  ssl_commands = SSL_COMMANDS,
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
      CONFIG.picker == "snacks"
          and function()
            hg_diff_snacks_picker()
          end
        or ":Telescope hg diff",
      { desc = "Hg diff picker" }
    )
    vim.api.nvim_create_user_command(
      "HgDiffIgnoreAllSpace",
      CONFIG.picker == "snacks"
          and function()
            hg_diff_snacks_picker({ additional_args = { "--ignore-all-space" } })
          end
        or ":Telescope hg diff_ignore_all_space",
      { desc = "Hg diff picker ignoring space changes" }
    )
    vim.api.nvim_create_user_command(
      "HgDiffCurrentCommit",
      CONFIG.picker == "snacks"
          and function()
            hg_diff_current_commit_snacks_picker()
          end
        or ":Telescope hg current_commit",
      { desc = "Hg diff picker for current commit changes" }
    )

    vim.api.nvim_create_user_command("HgPrev", HgPrev, { nargs = "?" })
    vim.api.nvim_create_user_command("HgNext", HgNext, { nargs = "?" })

    vim.api.nvim_create_user_command("HgWrite", HgWrite, {})
    vim.api.nvim_create_user_command("HgAdd", HgWrite, {})

    vim.api.nvim_create_user_command("HgHunkRevert", HgHunkRevert, {
      desc = "Revert the hunk beneath the cursor",
    })

    vim.api.nvim_create_user_command(
      "HgRead",
      "%! hg cat %",
      { desc = "Restore content of current buffer to the current commit" }
    )

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

    vim.api.nvim_create_user_command(
      "HgMove",
      HgMove,
      { nargs = 1, complete = "file" }
    )

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
      function() HgSsl() end,
      { desc = "Interactive smartlog for mercurial" }
    )

    vim.api.nvim_create_user_command(
      "HgSslSplit",
      function() HgSsl({ split = true }) end,
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

    vim.api.nvim_create_user_command(
      "HgStatus",
      HgStatus,
      { desc = "Interactively stage files" }
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
