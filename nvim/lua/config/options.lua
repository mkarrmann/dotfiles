local vim = vim

vim.opt.clipboard = "unnamedplus"

vim.opt.scrolloff = 25
vim.opt.tabstop = 4
vim.opt.shiftwidth = 4
vim.opt.colorcolumn = "79,80,88,100,120"
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
vim.o.tabline = "%!v:lua._claude_tabline()"
vim.o.showtabline = 2

-- Load machine-local config (e.g. Meta-specific rtp and proxy fixes)
pcall(require, "config.local")
