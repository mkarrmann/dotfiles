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
		opts = function(_, opts)
			local actions = require("telescope.actions")
			opts.defaults = opts.defaults or {}

			-- Never hop to another tabpage on select. LazyVim's telescope extra
			-- overrides get_selection_window to scan windows across ALL tabpages
			-- (nvim_list_wins) and focus the first real-file window it finds,
			-- which teleports us out of the current tab. Restrict the search to
			-- the current tabpage so a selection always resolves here.
			opts.defaults.get_selection_window = function()
				local cur = vim.api.nvim_get_current_win()
				if vim.bo[vim.api.nvim_win_get_buf(cur)].buftype == "" then
					return cur
				end
				for _, win in ipairs(vim.api.nvim_tabpage_list_wins(0)) do
					if vim.bo[vim.api.nvim_win_get_buf(win)].buftype == "" then
						return win
					end
				end
				return 0
			end

			-- Open selections in a vertical split rather than replacing the
			-- current window. A split never clobbers the originating window (safe
			-- even from neo-tree/terminal panes) and keeps work in this tabpage.
			opts.defaults.mappings = vim.tbl_deep_extend("force", opts.defaults.mappings or {}, {
				i = { ["<CR>"] = actions.select_vertical },
				n = { ["<CR>"] = actions.select_vertical },
			})

			return opts
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
