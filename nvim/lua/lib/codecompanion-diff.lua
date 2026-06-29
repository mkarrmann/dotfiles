local mgr = require("lib.diff-tab").new({
	name = "codecompanion_diff",
	tab_var = "codecompanion_chat_bufnr",
})

local M = {}

local function read_file_lines(path)
	if not path or path == "" then
		return nil
	end
	local f = io.open(path, "r")
	if not f then
		return nil
	end
	local content = f:read("*a")
	f:close()
	return vim.split(content, "\n", { plain = true })
end

local _conn_to_chat = setmetatable({}, { __mode = "k" })

local _filepath_cache = {}

-- Per-chat pre-edit disk snapshots, keyed [chat_bufnr][abs_path]. Only used by
-- the tool_call fallback path (agents that mutate files but emit no ACP `diff`
-- content block). Cleared per turn in M.new_turn and per session in M.cleanup.
local _disk_before = {}

local EDIT_KINDS = { edit = true, delete = true }

-- Mirror of upstream acp/handler.lua merge_tool_call: tool_call_update params are
-- incremental, so we re-accumulate them ourselves (see the process_tool_call
-- patch in M.setup for why we can't read upstream's merged copy back).
local function merge_tool_call(prev, incoming)
	local out = vim.deepcopy(prev or {})
	for k, v in pairs(incoming or {}) do
		if v ~= vim.NIL then
			out[k] = v
		end
	end
	return out
end

-- All ACP `diff` content blocks on a tool call. This is the canonical, race-free
-- source for an edit's before/after (acp/formatters.lua relies on it too).
local function find_diffs(tool_call)
	local out = {}
	if type(tool_call.content) == "table" then
		for _, c in ipairs(tool_call.content) do
			if type(c) == "table" and c.type == "diff" and type(c.path) == "string" and c.path ~= "" then
				table.insert(out, c)
			end
		end
	end
	return out
end

-- Best-effort file path for a tool call with no `diff` block: ACP `locations`
-- first (spec-blessed), then an absolute path parsed out of the title (e.g.
-- "Editing /abs/path").
local function resolve_edit_path(tool_call)
	local locs = tool_call.locations
	if type(locs) == "table" then
		for _, loc in ipairs(locs) do
			if type(loc) == "table" and type(loc.path) == "string" and loc.path ~= "" then
				return loc.path
			end
		end
	end
	if type(tool_call.title) == "string" then
		local p = tool_call.title:match("(/[^%s]+)")
		if p then
			return p
		end
	end
	return nil
end

--- VCS reconciliation --------------------------------------------------------
-- Catches edits made OUTSIDE the agent's structured Edit/Write tools (e.g.
-- `sed -i`, shell redirects, `patch`, `mv`) by asking version control what
-- actually changed on disk, then diffing those files. We never scan the
-- filesystem: probes are scoped to the cwd repo plus any repo the agent
-- referenced via a tool-call path this turn, and each probe is O(changes)
-- (`sl status` is O(dirty) on EdenFS) or O(file) (`sl cat` / `git show`) — so
-- this stays viable in massive monorepos.
--
-- Baseline (the "before" pane):
--   * cwd repo (strict): files already dirty at turn start are content-
--     snapshotted then (bounded), so turn-before is exact; files clean at turn
--     start fall back to the committed parent, which IS their turn-start state.
--   * other repos (best-effort): always the committed parent — i.e. the diff is
--     shown against the last commit. Reasonable since we only discover them
--     mid-turn and cannot retroactively snapshot their turn-start state.
--
-- sl/git portability: we deliberately use only vanilla flags (no Meta-specific
-- `--reason`) so this works for Sapling, Mercurial-via-sl, git, and OSS clones.

local MAX_RECONCILE_FILES = 500 -- changed files diffed per repo per turn
local MAX_SNAPSHOT_FILES = 500 -- cwd turn-start dirty files snapshotted
local MAX_FILE_BYTES = 5 * 1024 * 1024

local uv = vim.uv or vim.loop

-- [chat_bufnr] = { [repo_root] = vcs }  repos touched this turn
local _turn_repos = {}
-- [chat_bufnr] = { ready=bool, root=string|nil, files={[abspath]=lines} }
local _turn_snapshot = {}
-- [chat_bufnr] = int  bumped each turn; lets in-flight async reconciles detect
-- that a newer turn started and drop their now-stale records.
local _turn_epoch = {}
-- [dir] = { root=string, vcs="sl"|"git" } | false   path→repo resolution cache
local _repo_cache = {}
-- [chat_bufnr] = { [abspath]=true }  files referenced by this session's tool
-- calls this turn (structured edit targets + shell command path tokens).
-- reconcile_turn only attributes VCS-changed files present here, so concurrent
-- sessions editing different files in one repo don't steal each other's diffs.
local _turn_paths = {}

local function path_exists(p)
	return uv.fs_stat(p) ~= nil
end

-- Absolute, normalized path. Relative inputs resolve against `base` (a tool
-- call's cwd when known) so shell edits like `sed -i subdir/f` attribute to the
-- right repo even when nvim's cwd differs from the agent's working directory.
local function to_abs(path, base)
	if path:sub(1, 1) == "/" then
		return vim.fn.fnamemodify(path, ":p")
	end
	base = (base and base ~= "") and base or (uv.cwd() or ".")
	return vim.fn.fnamemodify(base .. "/" .. path, ":p")
end

-- Walk up from a path to the nearest VCS marker. O(depth), memoized per dir so
-- repeated lookups under one root in a huge tree are free after the first.
local function find_repo(path)
	if type(path) ~= "string" or path == "" then
		return nil
	end
	local dir = vim.fn.fnamemodify(path, ":p")
	local st = uv.fs_stat(dir)
	if not st or st.type ~= "directory" then
		dir = vim.fn.fnamemodify(dir, ":h")
	end
	dir = (dir:gsub("/+$", ""))

	local visited = {}
	for _ = 1, 256 do
		if dir == "" then
			break
		end
		local cached = _repo_cache[dir]
		if cached ~= nil then
			for _, d in ipairs(visited) do
				_repo_cache[d] = cached
			end
			return cached or nil
		end
		table.insert(visited, dir)
		local res
		if path_exists(dir .. "/.sl") or path_exists(dir .. "/.hg") then
			res = { root = dir, vcs = "sl" }
		elseif path_exists(dir .. "/.git") then
			res = { root = dir, vcs = "git" }
		end
		if res then
			for _, d in ipairs(visited) do
				_repo_cache[d] = res
			end
			return res
		end
		local parent = vim.fn.fnamemodify(dir, ":h")
		if parent == dir then
			break
		end
		dir = parent
	end
	for _, d in ipairs(visited) do
		_repo_cache[d] = false
	end
	return nil
end

local function note_repo_for_path(chat_bufnr, path, base)
	if not chat_bufnr or type(path) ~= "string" or path == "" then
		return
	end
	local abs = to_abs(path, base)
	local repo = find_repo(abs)
	if not repo then
		return
	end
	local set = _turn_repos[chat_bufnr]
	if not set then
		set = {}
		_turn_repos[chat_bufnr] = set
	end
	set[repo.root] = repo.vcs
	-- Attribute this path to the session so turn-end reconciliation only claims
	-- files this session touched. Keyed to match vcs_status's f.abs.
	local paths = _turn_paths[chat_bufnr]
	if not paths then
		paths = {}
		_turn_paths[chat_bufnr] = paths
	end
	paths[abs] = true
end

-- Discover which repos a tool call touched, from any path-shaped field. Over-
-- collecting is harmless (we just run `status` in an extra repo); under-
-- collecting risks missing a repo, so we cast wide: structured fields plus any
-- slash-containing token in rawInput.command / title (covers `sed -i path`).
--
-- LIMITATION: relative path tokens resolve against nvim's cwd (find_repo → :p),
-- not the shell's working dir, so a *second* repo addressed only by a relative
-- shell path can be missed. The cwd repo is always reconciled regardless, so
-- this only loses out-of-tree repos referenced relatively — uncommon, and not
-- fixable here since the tool call doesn't reliably carry the shell's cwd.
local function collect_repos_from_tool_call(chat_bufnr, tool_call)
	for _, d in ipairs(find_diffs(tool_call)) do
		note_repo_for_path(chat_bufnr, d.path)
	end
	if type(tool_call.locations) == "table" then
		for _, loc in ipairs(tool_call.locations) do
			if type(loc) == "table" and type(loc.path) == "string" then
				note_repo_for_path(chat_bufnr, loc.path)
			end
		end
	end
	local ri = tool_call.rawInput
	-- Resolve relative command/title path tokens against the tool call's own cwd
	-- when it reports one, not nvim's cwd.
	local cwd = type(ri) == "table" and type(ri.cwd) == "string" and ri.cwd ~= "" and ri.cwd or nil
	if type(ri) == "table" then
		for _, k in ipairs({ "file_path", "filePath", "abs_path", "absPath", "path", "filename", "cwd" }) do
			if type(ri[k]) == "string" then
				note_repo_for_path(chat_bufnr, ri[k], cwd)
			end
		end
		if type(ri.command) == "string" then
			for tok in ri.command:gmatch("%S+") do
				if tok:find("/", 1, true) then
					note_repo_for_path(chat_bufnr, (tok:gsub("[\"'`]", "")), cwd)
				end
			end
		end
	end
	if type(tool_call.title) == "string" then
		for tok in tool_call.title:gmatch("%S+") do
			if tok:find("/", 1, true) then
				note_repo_for_path(chat_bufnr, (tok:gsub("[\"'`]", "")), cwd)
			end
		end
	end
end

-- Run a command async; cb is invoked on the main loop (safe for vim.fn/api).
local function vcs_run(cmd, cwd, cb)
	vim.system(cmd, { text = true, cwd = cwd }, function(res)
		vim.schedule(function()
			cb(res.code or 1, res.stdout or "", res.stderr or "")
		end)
	end)
end

-- List changed files in a repo as { abs, rel, removed }. git uses NUL-delimited
-- output (-z) so paths with spaces/tabs/unicode survive verbatim (plain
-- --porcelain C-quotes such paths); sl prints paths raw, so newline splitting
-- with a single-status-char prefix is safe.
local function vcs_status(repo, cb)
	local is_sl = repo.vcs == "sl"
	local cmd = is_sl and { "sl", "status" }
		or { "git", "status", "--porcelain", "--no-renames", "-z" }
	vcs_run(cmd, repo.root, function(code, stdout)
		local files = {}
		if code ~= 0 then
			cb(files)
			return
		end
		local entries = vim.split(stdout, is_sl and "\n" or "\0", { plain = true })
		for _, entry in ipairs(entries) do
			local st, rel
			if is_sl then
				st, rel = entry:match("^(%S)%s+(.+)$")
			elseif #entry > 3 then
				-- porcelain -z entry: 2 status chars, a space, then the raw path.
				st, rel = entry:sub(1, 2), entry:sub(4)
			end
			if st and rel and rel ~= "" then
				local removed
				if is_sl then
					removed = st == "R" or st == "!"
				else
					removed = st:find("D", 1, true) ~= nil
				end
				table.insert(files, {
					abs = vim.fn.fnamemodify(repo.root .. "/" .. rel, ":p"),
					rel = rel,
					removed = removed,
				})
			end
		end
		cb(files)
	end)
end

-- Committed (parent revision) content of a file, or {} if absent (new/untracked).
local function vcs_cat_committed(repo, file, cb)
	local cmd = repo.vcs == "sl" and { "sl", "cat", "--rev", ".", "--", file.rel }
		or { "git", "show", "HEAD:" .. file.rel }
	vcs_run(cmd, repo.root, function(code, stdout)
		if code ~= 0 then
			cb({})
		else
			cb(vim.split(stdout, "\n", { plain = true }))
		end
	end)
end

local function read_disk_bounded(abs)
	local st = uv.fs_stat(abs)
	if not st then
		return {}
	end
	if st.size and st.size > MAX_FILE_BYTES then
		return nil -- signal: skip (too large to diff)
	end
	return read_file_lines(abs) or {}
end

local function lines_equal(a, b)
	if a == b then
		return true
	end
	if type(a) ~= "table" or type(b) ~= "table" or #a ~= #b then
		return false
	end
	for i = 1, #a do
		if a[i] ~= b[i] then
			return false
		end
	end
	return true
end

-- Snapshot the cwd repo's already-dirty files at turn start, so turn-before is
-- exact even for files the agent re-touches via shell. Bounded; clean files are
-- omitted (their turn-start content equals the committed parent, fetched lazily).
local function snapshot_cwd_turn_start(chat_bufnr)
	if not vim.system then
		return
	end
	local repo = find_repo(uv.cwd())
	local snap = { ready = false, root = repo and repo.root or nil, files = {} }
	_turn_snapshot[chat_bufnr] = snap
	if not repo then
		snap.ready = true
		return
	end
	-- Always reconcile the cwd repo, even if no tool-call path pointed at it.
	local set = _turn_repos[chat_bufnr]
	if not set then
		set = {}
		_turn_repos[chat_bufnr] = set
	end
	set[repo.root] = repo.vcs

	vcs_status(repo, function(files)
		local n = 0
		for _, f in ipairs(files) do
			if n >= MAX_SNAPSHOT_FILES then
				vim.notify(
					("[codecompanion-diff] turn baseline capped at %d dirty files; older changes diff vs commit"):format(
						MAX_SNAPSHOT_FILES
					),
					vim.log.levels.WARN
				)
				break
			end
			if not f.removed then
				local lines = read_disk_bounded(f.abs)
				if lines ~= nil then
					snap.files[f.abs] = lines
					n = n + 1
				end
			end
		end
		snap.ready = true
	end)
end

-- At turn end, diff everything VCS reports as changed across all touched repos.
local function reconcile_turn(chat_bufnr)
	if not vim.system or not chat_bufnr or not vim.api.nvim_buf_is_valid(chat_bufnr) then
		return
	end
	local repos = _turn_repos[chat_bufnr]
	if not repos then
		return
	end
	local snap = _turn_snapshot[chat_bufnr]
	local cwd_root = snap and snap.root or nil
	local epoch = _turn_epoch[chat_bufnr]

	for root, vcs in pairs(repos) do
		local repo = { root = root, vcs = vcs }
		local is_cwd = root == cwd_root
		vcs_status(repo, function(files)
			if #files > MAX_RECONCILE_FILES then
				vim.notify(
					("[codecompanion-diff] %s: %d changed files, diffing first %d"):format(
						root,
						#files,
						MAX_RECONCILE_FILES
					),
					vim.log.levels.WARN
				)
			end
			-- Only attribute files this session referenced this turn. A nil set means
			-- the session made no path-bearing tool calls, so nothing is attributable
			-- to it — skip rather than claim another agent's concurrent changes.
			local scoped = _turn_paths[chat_bufnr]
			for i, f in ipairs(files) do
				if i > MAX_RECONCILE_FILES then
					break
				end
				local after = f.removed and {} or read_disk_bounded(f.abs)
				if after ~= nil and scoped and scoped[f.abs] then
					local function finish(before)
						-- Drop stragglers: a newer turn started (epoch advanced) or the
						-- chat buffer closed while this async probe was in flight.
						if _turn_epoch[chat_bufnr] ~= epoch or not vim.api.nvim_buf_is_valid(chat_bufnr) then
							return
						end
						before = before or {}
						-- Skip files VCS lists that didn't actually change this turn
						-- (pre-existing dirty/untracked noise). Edits already captured
						-- by the structured paths are deduped downstream by add_file's
						-- set-if-nil baseline, so re-recording here is harmless.
						if not lines_equal(before, after) then
							pcall(M.record_write, chat_bufnr, f.abs, before, after)
						end
					end
					local snapped = (is_cwd and snap and snap.ready) and snap.files[f.abs] or nil
					if snapped ~= nil then
						finish(snapped)
					else
						vcs_cat_committed(repo, f, finish)
					end
				end
			end
		end)
	end
end

local function chat_bufnr_for_connection(conn)
	local cached = _conn_to_chat[conn]
	if cached and vim.api.nvim_buf_is_valid(cached) then
		return cached
	end
	local ok, codecompanion = pcall(require, "codecompanion")
	if not ok or not codecompanion.chats then
		return nil
	end
	for _, chat in pairs(codecompanion.chats or {}) do
		if chat.acp_connection == conn and chat.bufnr then
			_conn_to_chat[conn] = chat.bufnr
			return chat.bufnr
		end
	end
	return nil
end

function M.record_write(chat_bufnr, path, before_lines, after_lines)
	if not chat_bufnr or not path then
		return
	end
	path = vim.fn.fnamemodify(path, ":p")
	mgr:add_file(chat_bufnr, path, {
		after_lines = after_lines or before_lines or {},
		turn_before_lines = before_lines,
		session_before_lines = before_lines,
	})
end

function M.cleanup(chat_bufnr)
	if not chat_bufnr then
		return
	end
	_disk_before[chat_bufnr] = nil
	_turn_repos[chat_bufnr] = nil
	_turn_paths[chat_bufnr] = nil
	_turn_snapshot[chat_bufnr] = nil
	_turn_epoch[chat_bufnr] = nil
	mgr:cleanup(chat_bufnr)
end

function M.new_turn(chat_bufnr)
	if not chat_bufnr then
		return
	end
	-- Drop last turn's pre-edit snapshots so the first edit of the new turn
	-- re-reads disk and records this turn's true before-state.
	_disk_before[chat_bufnr] = nil
	_turn_repos[chat_bufnr] = nil
	_turn_paths[chat_bufnr] = nil
	_turn_snapshot[chat_bufnr] = nil
	_turn_epoch[chat_bufnr] = (_turn_epoch[chat_bufnr] or 0) + 1
	-- Re-resolve repo roots each turn so a repo created/removed mid-session isn't
	-- served stale; intra-turn memoization (the hot path) is preserved.
	_repo_cache = {}
	snapshot_cwd_turn_start(chat_bufnr)
	mgr:new_turn(chat_bufnr)
end

function M.toggle()
	mgr:toggle()
end

function M.debug()
	mgr:debug()
end

-- Record an edit from a (fully-merged) ACP tool call. Prefers the structured
-- `diff` content block; falls back to before/after disk reads bracketed by the
-- tool call's pending → completed status transition.
local function record_from_tool_call(chat_bufnr, tool_call)
	if not chat_bufnr or type(tool_call) ~= "table" then
		return
	end

	-- Note any repo this tool call touched (incl. read/execute), so the turn-end
	-- VCS reconciliation pass knows where to look for shell-driven edits.
	pcall(collect_repos_from_tool_call, chat_bufnr, tool_call)

	local diffs = find_diffs(tool_call)
	if #diffs > 0 then
		for _, d in ipairs(diffs) do
			local path = vim.fn.fnamemodify(d.path, ":p")
			local before = vim.split(d.oldText or "", "\n", { plain = true })
			local after = vim.split(d.newText or "", "\n", { plain = true })
			vim.schedule(function()
				pcall(M.record_write, chat_bufnr, path, before, after)
			end)
		end
		return
	end

	if not EDIT_KINDS[tool_call.kind] then
		return
	end
	local path = resolve_edit_path(tool_call)
	if not path then
		return
	end
	path = vim.fn.fnamemodify(path, ":p")

	local store = _disk_before[chat_bufnr]
	if not store then
		store = {}
		_disk_before[chat_bufnr] = store
	end

	local status = tool_call.status
	if status ~= "completed" and status ~= "failed" then
		-- Pre-write: snapshot the file once, before the agent mutates it.
		if store[path] == nil then
			store[path] = read_file_lines(path) or {}
		end
		return
	end

	local before = store[path]
	if before == nil then
		before = read_file_lines(path) or {}
	end
	store[path] = nil
	local after = read_file_lines(path) or {}
	vim.schedule(function()
		pcall(M.record_write, chat_bufnr, path, before, after)
	end)
end

function M.setup()
	-- HACK: monkey-patches codecompanion.acp.init Connection:handle_fs_write_file_request
	-- (~/.local/share/nvim/lazy/codecompanion.nvim/lua/codecompanion/acp/init.lua:815).
	-- Per design doc §1.4 (write-path chokepoint chosen over show_diff to catch
	-- bypassPermissions/auto-approved/small-diff/unfocused-chat writes) and §5.4
	-- (single record_write entry). If upstream wires prompt_builder:on_write_text_file
	-- (acp/prompt_builder.lua:57) into ACPHandler, pivot to that designed extension
	-- point per §8.1.
	local acp_ok, Connection = pcall(require, "codecompanion.acp.init")
	if acp_ok and type(Connection) == "table" and type(Connection.handle_fs_write_file_request) == "function" then
		local orig_fs_write = Connection.handle_fs_write_file_request
		function Connection:handle_fs_write_file_request(id, params)
			local chat_bufnr, before_lines
			if type(params) == "table" and type(params.path) == "string" then
				chat_bufnr = chat_bufnr_for_connection(self)
				if chat_bufnr then
					before_lines = read_file_lines(params.path) or {}
				end
			end
			local result = orig_fs_write(self, id, params)
			if chat_bufnr and type(params.content) == "string" then
				local after_lines = vim.split(params.content, "\n", { plain = true })
				vim.schedule(function()
					pcall(M.record_write, chat_bufnr, params.path, before_lines, after_lines)
				end)
			end
			return result
		end
	end

	-- HACK: monkey-patches codecompanion.interactions.chat.tools.builtin.insert_edit_into_file.diff
	-- (~/.local/share/nvim/lazy/codecompanion.nvim/lua/codecompanion/interactions/chat/tools/builtin/insert_edit_into_file/diff.lua:131).
	-- `diff.review` is called unconditionally from insert_edit_into_file/init.lua:164
	-- before any approval-branch dispatch, so patching here catches all HTTP-mode
	-- edits (including auto-approved, inline, and display.diff-disabled cases).
	-- Per design doc §1.4 (write-path chokepoint) and §8.3 option (b): `opts` has
	-- no `filepath`, so we stash the tool's `args.filepath` from
	-- `User CodeCompanionToolStarted` (fired by orchestrator.lua:376) keyed by chat
	-- bufnr, then resolve against the cache here. Fallback resolves `opts.title`
	-- (the source's display_name) against cwd. If upstream adds `opts.filepath`,
	-- drop the cache and read it directly.
	local diff_ok, diff_review = pcall(require, "codecompanion.interactions.chat.tools.builtin.insert_edit_into_file.diff")
	if diff_ok and type(diff_review) == "table" and type(diff_review.review) == "function" then
		local cache_group = vim.api.nvim_create_augroup("codecompanion_diff_tool_cache", { clear = true })
		vim.api.nvim_create_autocmd("User", {
			pattern = "CodeCompanionToolStarted",
			group = cache_group,
			callback = function(args)
				local data = args.data
				if not data or not data.bufnr then
					return
				end
				local targs = data.args
				if type(targs) ~= "table" or type(targs.filepath) ~= "string" then
					return
				end
				_filepath_cache[data.bufnr] = targs.filepath
			end,
		})

		local orig_review = diff_review.review
		function diff_review.review(opts)
			local chat_bufnr = opts and opts.chat_bufnr
			local path = (chat_bufnr and _filepath_cache[chat_bufnr])
				or (opts and opts.title and vim.fn.fnamemodify(opts.title, ":p"))
			if chat_bufnr and path and opts and opts.from_lines then
				local from_lines = opts.from_lines
				local to_lines = opts.to_lines
				vim.schedule(function()
					pcall(M.record_write, chat_bufnr, path, from_lines, to_lines)
				end)
			end
			return orig_review(opts)
		end
	end

	-- HACK: monkey-patches codecompanion.interactions.chat.acp.handler
	-- ACPHandler:process_tool_call (handler.lua:238). The fs/write_text_file
	-- chokepoint above only fires for agents that DELEGATE writes to the editor
	-- over ACP (Gemini, Codex, Goose, ...). Claude-family agents (incl. the
	-- dvsc-core broker) write files themselves and merely *notify* the client via
	-- session/update tool_call / tool_call_update — so nothing was ever recorded
	-- and the diff split came up empty. This patches the notification chokepoint
	-- instead, which is agent-agnostic: every edit surfaces here regardless of who
	-- performed the write. We re-merge incrementally because upstream clears
	-- self.tools[id] on the terminal "completed" event before we could read its
	-- merged copy back; at patch-entry self.tools[id] is still the pre-merge state.
	local handler_ok, ACPHandler = pcall(require, "codecompanion.interactions.chat.acp.handler")
	if handler_ok and type(ACPHandler) == "table" and type(ACPHandler.process_tool_call) == "function" then
		local orig_process = ACPHandler.process_tool_call
		function ACPHandler:process_tool_call(tool_call)
			if type(tool_call) == "table" then
				local id = tool_call.toolCallId
				local prev = id and self.tools and self.tools[id] or nil
				local merged = merge_tool_call(prev, tool_call)
				pcall(record_from_tool_call, self.chat and self.chat.bufnr, merged)
			end
			return orig_process(self, tool_call)
		end
	end

	local lifecycle_group = vim.api.nvim_create_augroup("codecompanion_diff_lifecycle", { clear = true })

	vim.api.nvim_create_autocmd("User", {
		pattern = "CodeCompanionRequestStarted",
		group = lifecycle_group,
		callback = function(args)
			local bufnr = args.data and args.data.bufnr
			if bufnr then
				M.new_turn(bufnr)
			end
		end,
	})

	vim.api.nvim_create_autocmd("User", {
		pattern = "CodeCompanionRequestFinished",
		group = lifecycle_group,
		callback = function(args)
			local bufnr = args.data and args.data.bufnr
			if bufnr then
				reconcile_turn(bufnr)
			end
		end,
	})

	vim.api.nvim_create_autocmd("User", {
		pattern = { "CodeCompanionChatClosed", "CodeCompanionChatCleared" },
		group = lifecycle_group,
		callback = function(args)
			local bufnr = args.data and args.data.bufnr
			if bufnr then
				M.cleanup(bufnr)
			end
		end,
	})
end

return M
