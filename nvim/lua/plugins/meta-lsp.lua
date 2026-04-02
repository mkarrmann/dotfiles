return {
	{
		dir = "/usr/share/fb-editor-support/nvim",
		name = "meta.nvim",
		cond = vim.fn.isdirectory("/usr/share/fb-editor-support/nvim") == 1,
		dependencies = { "nvimtools/none-ls.nvim" },
		config = function()
			local meta = require("meta")
			meta.setup()

			vim.lsp.enable({
				"cppls@meta",
				"fb-pyright-ls@meta",
				"thriftlsp@meta",
				"rust-analyzer@meta",
				"linttool@meta",
				"hhvm",
			})

			local null_ls = require("null-ls")
			null_ls.register({
				meta.null_ls.diagnostics.arclint,
				meta.null_ls.formatting.arclint,
			})
		end,
	},

	{
		"neovim/nvim-lspconfig",
		opts = {
			format = {
				filter = function(client)
					if #vim.lsp.get_clients({ name = "linttool@meta" }) > 0 then
						return client.name ~= "rust-analyzer@meta"
					end
					return true
				end,
			},
		},
	},
}
