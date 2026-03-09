local META_NVIM_DIR = vim.fn.isdirectory("/usr/share/fb-editor-support/nvim") == 1
	and "/usr/share/fb-editor-support/nvim"
	or "/usr/local/share/fb-editor-support/nvim"

return {
	{ "nvimtools/none-ls.nvim", event = "LazyFile" },
	{ dir = META_NVIM_DIR, name = "meta.nvim", cond = function() return vim.fn.isdirectory(META_NVIM_DIR) == 1 end },
}
