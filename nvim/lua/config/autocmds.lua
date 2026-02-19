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

-- Force full redraw on tmux pane switch. Without this, TUIs running in Neovim
-- terminal buffers (e.g. Claude Code) render with stale screen content overlapping.
vim.api.nvim_create_autocmd("FocusGained", {
	callback = function()
		vim.cmd("redraw!")
	end,
})

pcall(require, "config.local")
