local mgr = require("lib.diff-tab").new({
	name = "codecompanion_diff",
	tab_var = "codecompanion_chat_bufnr",
})

local M = {}

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
