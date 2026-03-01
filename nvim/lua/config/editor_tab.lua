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

		if term_win then
			vim.api.nvim_set_current_win(term_win)
			vim.cmd("belowright vsplit " .. vim.fn.fnameescape(file))
		else
			vim.cmd("vsplit " .. vim.fn.fnameescape(file))
		end

		local win = vim.api.nvim_get_current_win()
		local buf = vim.api.nvim_get_current_buf()

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
