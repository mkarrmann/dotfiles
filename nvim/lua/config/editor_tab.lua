local M = {}

local function find_terminal_win()
	for _, win in ipairs(vim.api.nvim_list_wins()) do
		local cfg = vim.api.nvim_win_get_config(win)
		if cfg.relative == "" then
			local buf = vim.api.nvim_win_get_buf(win)
			if vim.bo[buf].buftype == "terminal" then
				return win
			end
		end
	end
	return nil
end

function M.open_and_signal(file, sentinel)
	vim.schedule(function()
		local term_win = find_terminal_win()

		local scratch = vim.api.nvim_create_buf(false, true)
		local win

		if term_win then
			local width = vim.api.nvim_win_get_width(term_win)
			local height = vim.api.nvim_win_get_height(term_win)
			local pos = vim.api.nvim_win_get_position(term_win)
			win = vim.api.nvim_open_win(scratch, true, {
				relative = "editor",
				row = pos[1],
				col = pos[2],
				width = width,
				height = height,
			})
		else
			win = vim.api.nvim_open_win(scratch, true, {
				relative = "editor",
				row = 1,
				col = 1,
				width = vim.o.columns - 2,
				height = vim.o.lines - 3,
				border = "rounded",
			})
		end

		vim.cmd("edit " .. vim.fn.fnameescape(file))
		local buf = vim.api.nvim_get_current_buf()
		if scratch ~= buf and vim.api.nvim_buf_is_valid(scratch) then
			vim.api.nvim_buf_delete(scratch, { force = true })
		end

		vim.api.nvim_create_autocmd("WinClosed", {
			pattern = tostring(win),
			once = true,
			callback = function()
				if vim.api.nvim_buf_is_valid(buf) then
					vim.api.nvim_buf_delete(buf, { force = true })
				end
				local fh = io.open(sentinel, "w")
				if fh then
					fh:close()
				end
			end,
		})
	end)
	return true
end

return M
