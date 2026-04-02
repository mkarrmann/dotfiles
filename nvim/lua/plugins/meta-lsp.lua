return {
	{
		dir = "/usr/share/fb-editor-support/nvim",
		name = "meta.nvim",
		cond = vim.fn.isdirectory("/usr/share/fb-editor-support/nvim") == 1,
		dependencies = { "nvimtools/none-ls.nvim" },
		config = function()
			require("meta").setup()
			vim.lsp.enable({
				"cppls@meta",
				"fb-pyright-ls@meta",
				"thriftlsp@meta",
				"rust-analyzer@meta",
				"hhvm",
			})
		end,
	},
}
