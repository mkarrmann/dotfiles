local vim = vim

vim.api.nvim_create_autocmd("FileType", {
	pattern = "python",
	callback = function()
		vim.o.textwidth = 88
	end,
})

vim.api.nvim_create_autocmd("FileType", {
	pattern = { "python", "sql", "toml", "ini", "dockerfile", "sh" },
	callback = function()
		vim.bo.expandtab = true
		vim.bo.tabstop = 4
		vim.bo.shiftwidth = 4
	end,
})

vim.api.nvim_create_autocmd("TermOpen", {
	callback = function()
		vim.b.virtcolumn_items = {}
		vim.w.virtcolumn_items = {}
		vim.opt_local.colorcolumn = ""
		local ns = vim.api.nvim_create_namespace("virtcolumn")
		vim.api.nvim_buf_clear_namespace(0, ns, 0, -1)
	end,
})

-- Force full redraw on focus gain or terminal re-entry. Needed because:
-- 1. Tmux pane switches don't relay full screen content (FocusGained)
-- 2. Neovim doesn't fully repaint terminal buffers after overlapping
--    floats/splits close, e.g. Ctrl+g editor in Claude Code (TermEnter)
vim.api.nvim_create_autocmd({ "FocusGained", "TermEnter" }, {
	callback = function()
		vim.cmd("redraw!")
	end,
})

-- Copy every yank to the local clipboard via OSC52 (copy-only).
local osc52 = require("vim.ui.clipboard.osc52")
vim.api.nvim_create_autocmd("TextYankPost", {
	callback = function()
		if vim.v.event.operator == "y" then
			osc52.copy("+")(vim.v.event.regcontents, vim.v.event.regtype)
		end
	end,
})

pcall(require, "config.local")
