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

local function signal(sentinel)
	local fh = io.open(sentinel, "w")
	if fh then
		fh:close()
	end
end

function M.open_and_signal(file, sentinel)
	vim.schedule(function()
		local term_win = find_terminal_win()

		if not term_win then
			vim.cmd("vsplit " .. vim.fn.fnameescape(file))
			local edit_win = vim.api.nvim_get_current_win()
			local edit_buf = vim.api.nvim_get_current_buf()
			vim.api.nvim_create_autocmd("WinClosed", {
				pattern = tostring(edit_win),
				once = true,
				callback = function()
					if vim.api.nvim_buf_is_valid(edit_buf) then
						vim.api.nvim_buf_delete(edit_buf, { force = true })
					end
					signal(sentinel)
				end,
			})
			return
		end

		local term_buf = vim.api.nvim_win_get_buf(term_win)
		local term_width = vim.api.nvim_win_get_width(term_win)
		local saved_ea = vim.o.equalalways
		vim.o.equalalways = false

		if not pcall(vim.api.nvim_win_close, term_win, true) then
			vim.o.equalalways = saved_ea
			vim.api.nvim_set_current_win(term_win)
			vim.cmd("vsplit " .. vim.fn.fnameescape(file))
			local edit_win = vim.api.nvim_get_current_win()
			local edit_buf = vim.api.nvim_get_current_buf()
			vim.api.nvim_create_autocmd("WinClosed", {
				pattern = tostring(edit_win),
				once = true,
				callback = function()
					if vim.api.nvim_buf_is_valid(edit_buf) then
						vim.api.nvim_buf_delete(edit_buf, { force = true })
					end
					vim.o.equalalways = saved_ea
					signal(sentinel)
				end,
			})
			return
		end

		vim.cmd("botright " .. term_width .. "vsplit " .. vim.fn.fnameescape(file))
		local edit_win = vim.api.nvim_get_current_win()
		local edit_buf = vim.api.nvim_get_current_buf()

		vim.api.nvim_create_autocmd("WinClosed", {
			pattern = tostring(edit_win),
			once = true,
			callback = function()
				if vim.api.nvim_buf_is_valid(edit_buf) then
					vim.api.nvim_buf_delete(edit_buf, { force = true })
				end
				vim.schedule(function()
					if vim.api.nvim_buf_is_valid(term_buf) then
						vim.cmd("botright " .. term_width .. "vsplit")
						vim.api.nvim_win_set_buf(vim.api.nvim_get_current_win(), term_buf)
						vim.cmd("startinsert")
					end
					vim.o.equalalways = saved_ea
					signal(sentinel)
				end)
			end,
		})
	end)
	return true
end

return M
