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
				"buck2@meta",
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
		"nvim-telescope/telescope.nvim",
		keys = {
			{
				"<leader>p",
				function()
					local meta_util = require("meta.util")
					if meta_util.arc.get_project_root(vim.uv.cwd()) then
						require("telescope").load_extension("myles")
						vim.cmd("Telescope myles")
					else
						require("lazyvim.util").pick().telescope("files")()
					end
				end,
				desc = "Find Files (Myles)",
			},
		},
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
