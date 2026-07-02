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

-- Custom tabline showing tab names and Claude state indicators.
-- State colors: blue=⚙ (working), red=! (needs input), yellow=✓ (done unread).
-- The "~" (seen) and "" (idle) states use default tabline colors.
-- TabEnter autocmd (autocmds.lua) downgrades ✓ → ~ when the tab is viewed.
require("lib.claude-tab-state")
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
