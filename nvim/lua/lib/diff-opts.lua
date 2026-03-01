local M = {}

function M.apply(win)
	vim.wo[win].scrollbind = true
	vim.wo[win].foldenable = false
	vim.wo[win].relativenumber = false
	vim.wo[win].statuscolumn = ""
end

function M.apply_pair(left_win, right_win, left_label, right_label, display_name)
	M.apply(left_win)
	M.apply(right_win)
	if left_label and display_name then
		vim.wo[left_win].winbar = "%#Comment# " .. left_label .. " %* " .. display_name
	end
	if right_label and display_name then
		vim.wo[right_win].winbar = "%#DiagnosticOk# " .. right_label .. " %* " .. display_name
	end
	vim.cmd("syncbind")
end

return M
