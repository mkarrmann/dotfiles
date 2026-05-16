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

function M.setup() end

return M
