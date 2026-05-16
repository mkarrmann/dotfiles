local mgr = require("lib.diff-tab").new({
	name = "claude_diff",
	tab_var = "claude_session_id",
	diff_tab_var = "claude_diff_session",
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

function M.file_edited(file_path, turn_snap_path, session_snap_path, session_id)
	if not session_id or session_id == "" then
		return true
	end

	vim.schedule(function()
		local ok, err = pcall(function()
			file_path = vim.fn.fnamemodify(file_path, ":p")
			mgr:add_file(session_id, file_path, {
				after_lines = read_file_lines(file_path) or {},
				turn_before_lines = read_file_lines(turn_snap_path),
				session_before_lines = read_file_lines(session_snap_path),
			})
		end)
		if not ok then
			vim.notify("claude-diff error: " .. tostring(err), vim.log.levels.ERROR)
		end
	end)
	return true
end

function M.new_turn(session_id)
	mgr:new_turn(session_id)
	return true
end

function M.cleanup(session_id)
	mgr:cleanup(session_id)
	return true
end

function M.toggle()
	mgr:toggle()
end

function M.debug()
	mgr:debug()
end

return M
