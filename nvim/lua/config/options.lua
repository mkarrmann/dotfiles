local vim = vim

vim.opt.clipboard = "unnamedplus"

vim.opt.scrolloff = 25
vim.opt.tabstop = 4
vim.opt.shiftwidth = 4
-- 'colorcolumn' is applied per-buffer in autocmds.lua (opt-out by filetype),
-- not set globally. See the note there for why.
vim.g.virtcolumn_char = "▕"

vim.opt.statuscolumn = table.concat({
	"%s",
	"%C",
	"%=",
	"%{printf('%4d', v:lnum)}",
	" ",
	"%{printf('%3d', v:relnum)}",
	" ",
})

-- Disable concealment globally
vim.opt.conceallevel = 0
vim.opt.concealcursor = ""

-- Tabpage names and Omnigent-backed CodeCompanion activity indicators.
require("lib.omnigent-tab-state").setup()
require("lib.agent-tabline").setup()
vim.o.tabline = "%!v:lua._tabline()"
vim.o.showtabline = 2

-- Re-assert our tabline after all plugins load. LazyVim ships bufferline,
-- which sets tabline to nvim_bufferline() and toggles showtabline in its own
-- setup. We disable it (plugins/extras.lua), but this guard ensures any plugin
-- that grabs the tabline during startup can't leave us with the wrong one.
vim.api.nvim_create_autocmd("User", {
	pattern = "VeryLazy",
	once = true,
	callback = function()
		if vim.o.tabline ~= "%!v:lua._tabline()" then
			vim.o.tabline = "%!v:lua._tabline()"
		end
		vim.o.showtabline = 2
	end,
})

-- Load machine-local config (e.g. Meta-specific rtp and proxy fixes)
pcall(require, "config.local")
