local vim = vim

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
