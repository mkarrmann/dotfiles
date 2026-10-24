local M = {}

-- [0] scopes each option to the window's current buffer, like :setlocal.
-- Without it, setting an option on the current window also sets its global
-- value, which every window created afterwards inherits.
function M.apply(win)
	vim.wo[win][0].scrollbind = true
	vim.wo[win][0].foldenable = false
	vim.wo[win][0].relativenumber = true
end

---Shows two buffers diffed side by side, from the top. Shared by the meta-hg
---diff split (lib.diff-session) and the CodeCompanion diff tab (lib.diff-tab).
---@param left_win integer
---@param left_buf integer
---@param right_win integer
---@param right_buf integer
function M.show(left_win, left_buf, right_win, right_buf)
	for _, win in ipairs({ left_win, right_win }) do
		-- :diffoff only restores the pre-diff 'foldenable' while it is on, and
		-- the buffer leaving the pane remembers what it is left with
		vim.wo[win][0].foldenable = true
		vim.api.nvim_win_call(win, function()
			vim.cmd("diffoff")
		end)
	end
	vim.api.nvim_win_set_buf(left_win, left_buf)
	vim.api.nvim_win_set_buf(right_win, right_buf)
	for _, win in ipairs({ left_win, right_win }) do
		vim.api.nvim_win_call(win, function()
			vim.cmd("diffthis")
		end)
		M.apply(win)
	end
	vim.api.nvim_win_call(left_win, function()
		vim.cmd("syncbind")
	end)
	for _, win in ipairs({ left_win, right_win }) do
		vim.api.nvim_win_call(win, function()
			vim.cmd("normal! gg")
		end)
	end
end

return M
