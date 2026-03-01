local diff_session = require("lib.diff-session")

local M = {}

local SESSION_KEY = "claude_edit_diff"
local _term_win = nil

local function find_terminal_win()
	for _, win in ipairs(vim.api.nvim_tabpage_list_wins(0)) do
		local cfg = vim.api.nvim_win_get_config(win)
		if cfg.relative == "" then
			local buf = vim.api.nvim_win_get_buf(win)
			if vim.bo[buf].buftype == "terminal" then
				local name = vim.api.nvim_buf_get_name(buf)
				if name:lower():find("claude") then
					return win
				end
			end
		end
	end
	return nil
end

local function get_session()
	return diff_session.sessions[SESSION_KEY]
end

local function update_winbar(session)
	local pair = session.pairs[session.index]
	local pos = string.format("[%d/%d]", session.index, #session.pairs)
	local display_name = vim.fn.fnamemodify(pair.file, ":.")
	vim.wo[session.right_win].winbar = "%#Comment# before " .. pos .. " %* " .. display_name
	vim.wo[session.left_win].winbar = "%#DiagnosticOk# after " .. pos .. " %* " .. display_name
end

local function ensure_session()
	local session = get_session()
	if session then
		return session
	end

	_term_win = find_terminal_win()
	if _term_win then
		vim.api.nvim_set_current_win(_term_win)
	end

	vim.cmd("belowright vsplit")
	local right_win = vim.api.nvim_get_current_win()
	vim.cmd("belowright vsplit")
	local left_win = vim.api.nvim_get_current_win()

	session = {
		pairs = {},
		index = 0,
		left_win = left_win,
		right_win = right_win,
		update_winbar = update_winbar,
		on_close = function()
			local s = get_session()
			if s then
				diff_session.cleanup(s)
			end
			for _, win in ipairs({ left_win, right_win }) do
				if vim.api.nvim_win_is_valid(win) then
					vim.api.nvim_win_call(win, function()
						vim.cmd("diffoff")
					end)
					pcall(vim.api.nvim_win_close, win, true)
				end
			end
			if _term_win and vim.api.nvim_win_is_valid(_term_win) then
				vim.api.nvim_set_current_win(_term_win)
			end
		end,
	}

	diff_session.register(SESSION_KEY, session)

	local group = vim.api.nvim_create_augroup("claude_edit_diff", { clear = true })
	vim.api.nvim_create_autocmd("WinClosed", {
		group = group,
		callback = function(ev)
			local s = get_session()
			if not s then
				return true
			end
			local closed = tonumber(ev.match)
			if closed == s.left_win or closed == s.right_win then
				vim.schedule(function()
					diff_session.close(s)
				end)
				return true
			end
		end,
	})

	return session
end

function M.show(file_path, snapshot_path)
	vim.schedule(function()
		local ok, err = pcall(function()
			local before_lines = {}
			if snapshot_path and snapshot_path ~= "" then
				local f = io.open(snapshot_path, "r")
				if f then
					local content = f:read("*a")
					f:close()
					before_lines = vim.split(content, "\n", { plain = true })
					os.remove(snapshot_path)
				end
			end

			local old_buf = vim.api.nvim_create_buf(false, true)
			vim.api.nvim_buf_set_lines(old_buf, 0, -1, false, before_lines)
			vim.bo[old_buf].modifiable = false
			vim.bo[old_buf].buftype = "nofile"
			vim.bo[old_buf].bufhidden = "hide"
			local ft = vim.filetype.match({ filename = file_path })
			if ft then
				vim.bo[old_buf].filetype = ft
			end
			vim.api.nvim_buf_set_name(old_buf, "before://" .. vim.fn.fnamemodify(file_path, ":t"))

			local new_buf = vim.fn.bufadd(file_path)
			vim.fn.bufload(new_buf)
			vim.api.nvim_buf_call(new_buf, function()
				vim.cmd("silent! checktime")
				vim.cmd("silent! edit!")
			end)

			local session = ensure_session()
			table.insert(session.pairs, {
				file = file_path,
				old_buf = old_buf,
				new_buf = new_buf,
				is_live = true,
			})

			diff_session.register(SESSION_KEY, session)
			diff_session.show_pair(session, #session.pairs)
		end)
		if not ok then
			vim.notify("claude-edit-diff error: " .. tostring(err), vim.log.levels.ERROR)
		end
	end)
	return true
end

return M
