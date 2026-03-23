local vim = vim

vim.g.clipboard = {
	name = "OSC 52",
	copy = {
		["+"] = require("vim.ui.clipboard.osc52").copy("+"),
		["*"] = require("vim.ui.clipboard.osc52").copy("*"),
	},
	paste = {
		["+"] = require("vim.ui.clipboard.osc52").paste("+"),
		["*"] = require("vim.ui.clipboard.osc52").paste("*"),
	},
}
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

-- Load machine-local config (e.g. Meta-specific rtp and proxy fixes)
pcall(require, "config.local")
