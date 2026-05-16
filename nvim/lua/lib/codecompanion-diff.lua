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
	mgr:cleanup(chat_bufnr)
end

function M.new_turn(chat_bufnr)
	if not chat_bufnr then
		return
	end
	mgr:new_turn(chat_bufnr)
end

function M.toggle()
	mgr:toggle()
end

function M.debug()
	mgr:debug()
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
