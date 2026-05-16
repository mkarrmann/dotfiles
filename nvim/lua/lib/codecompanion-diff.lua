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
end

return M
