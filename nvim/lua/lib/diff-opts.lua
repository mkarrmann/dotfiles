local M = {}

-- [0] scopes each option to the window's current buffer, like :setlocal.
-- Without it, setting an option on the current window also sets its global
-- value, which every window created afterwards inherits.
function M.apply(win)
	vim.wo[win][0].scrollbind = true
	vim.wo[win][0].foldenable = false
	vim.wo[win][0].relativenumber = true
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
