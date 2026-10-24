local M = {}

-- [0] scopes each option to the window's current buffer, like :setlocal.
-- Without it, setting an option on the current window also sets its global
-- value, which every window created afterwards inherits.
function M.apply(win)
	vim.wo[win][0].scrollbind = true
	vim.wo[win][0].foldenable = false
	vim.wo[win][0].relativenumber = true
end

return M
