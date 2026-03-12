local M = {}

function M.apply(win)
	vim.wo[win].scrollbind = true
	vim.wo[win].foldenable = false
	vim.wo[win].relativenumber = true
end

function M.apply_pair(left_win, right_win, left_label, right_label, display_name)
	M.apply(left_win)
	M.apply(right_win)
	if left_label and display_name then
		vim.api.nvim_win_set_var(left_win, "custom_winbar_text",
			"%#Comment# " .. left_label .. " %* " .. display_name)
	end
	if right_label and display_name then
		vim.api.nvim_win_set_var(right_win, "custom_winbar_text",
			"%#DiagnosticOk# " .. right_label .. " %* " .. display_name)
	end
	require("lualine").refresh()
	vim.cmd("syncbind")
end

return M
