local M = {}

local _state = nil

function M.dismiss()
	if not _state then
		return
	end
	local s = _state
	_state = nil

	local group_ok, _ = pcall(vim.api.nvim_del_augroup_by_name, "claude_edit_diff")
	if not group_ok then
		-- group already cleaned up
	end

	for _, win in ipairs({ s.before_win, s.after_win }) do
		if win and vim.api.nvim_win_is_valid(win) then
			vim.api.nvim_win_call(win, function()
				vim.cmd("diffoff")
			end)
			pcall(vim.api.nvim_win_close, win, true)
		end
	end
	if s.before_buf and vim.api.nvim_buf_is_valid(s.before_buf) then
		pcall(vim.api.nvim_buf_delete, s.before_buf, { force = true })
	end

	if s.term_win and vim.api.nvim_win_is_valid(s.term_win) then
		vim.api.nvim_set_current_win(s.term_win)
	end
end

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

function M.show(file_path, snapshot_path)
	vim.schedule(function()
		M.dismiss()

		local term_win = find_terminal_win()

		-- Build "before" content from the snapshot.
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

		-- Create a readonly scratch buffer for the before-state.
		local before_buf = vim.api.nvim_create_buf(false, true)
		vim.api.nvim_buf_set_lines(before_buf, 0, -1, false, before_lines)
		vim.bo[before_buf].modifiable = false
		vim.bo[before_buf].buftype = "nofile"
		vim.bo[before_buf].bufhidden = "wipe"
		local ft = vim.filetype.match({ filename = file_path })
		if ft then
			vim.bo[before_buf].filetype = ft
		end
		vim.api.nvim_buf_set_name(before_buf, "before://" .. vim.fn.fnamemodify(file_path, ":t"))

		-- Split to the right of the Claude Code terminal.
		if term_win then
			vim.api.nvim_set_current_win(term_win)
		end
		vim.cmd("belowright vsplit")
		local before_win = vim.api.nvim_get_current_win()
		vim.api.nvim_win_set_buf(before_win, before_buf)

		-- Open the current (after-edit) file in another split to the right.
		vim.cmd("belowright vsplit " .. vim.fn.fnameescape(file_path))
		vim.cmd("silent! checktime")
		vim.cmd("silent! edit!")
		local after_win = vim.api.nvim_get_current_win()
		local after_buf = vim.api.nvim_win_get_buf(after_win)

		-- Enable diff mode and apply shared window options.
		vim.api.nvim_set_current_win(before_win)
		vim.cmd("diffthis")
		vim.api.nvim_set_current_win(after_win)
		vim.cmd("diffthis")
		local display_name = vim.fn.fnamemodify(file_path, ":.")
		local diff_opts = require("lib.diff-opts")
		diff_opts.apply_pair(before_win, after_win, "before", "after", display_name)

		_state = {
			term_win = term_win,
			before_win = before_win,
			before_buf = before_buf,
			after_win = after_win,
			after_buf = after_buf,
			file_path = file_path,
		}

		-- Press q in either diff pane to dismiss.
		for _, buf in ipairs({ before_buf, after_buf }) do
			vim.keymap.set("n", "q", M.dismiss, { buffer = buf, nowait = true })
		end

		-- Auto-dismiss if either diff window is closed externally.
		local group = vim.api.nvim_create_augroup("claude_edit_diff", { clear = true })
		vim.api.nvim_create_autocmd("WinClosed", {
			group = group,
			callback = function(ev)
				if not _state then
					return true
				end
				local closed = tonumber(ev.match)
				if closed == _state.before_win or closed == _state.after_win then
					vim.schedule(M.dismiss)
					return true
				end
			end,
		})

		vim.notify("Edit diff: " .. vim.fn.fnamemodify(file_path, ":t") .. "  (q to dismiss)")
	end)
	return true
end

return M
