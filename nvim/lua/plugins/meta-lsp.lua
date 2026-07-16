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
				"pyrefly@meta",
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
			local action_state = require("telescope.actions.state")
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

			-- An unnamed, empty scratch buffer: safe to replace rather than split.
			local function buf_is_empty(buf)
				return vim.bo[buf].buftype == ""
					and vim.bo[buf].filetype == ""
					and vim.api.nvim_buf_get_name(buf) == ""
					and vim.api.nvim_buf_line_count(buf) == 1
					and vim.api.nvim_buf_get_lines(buf, 0, -1, false)[1] == ""
			end

			-- Window in the current tabpage already showing the selected entry's
			-- file, if any. Only the current tab is searched so we never hop tabs.
			local function win_showing(entry)
				local wins = vim.api.nvim_tabpage_list_wins(0)
				if entry.bufnr then
					for _, w in ipairs(wins) do
						if vim.api.nvim_win_get_buf(w) == entry.bufnr then
							return w
						end
					end
					return nil
				end
				local path = entry.path or entry.filename
				if not path then
					return nil
				end
				path = vim.fn.fnamemodify(path, ":p")
				for _, w in ipairs(wins) do
					if vim.api.nvim_buf_get_name(vim.api.nvim_win_get_buf(w)) == path then
						return w
					end
				end
				return nil
			end

			-- Open selections in a vertical split rather than replacing the
			-- current window. A split never clobbers the originating window (safe
			-- even from neo-tree/terminal panes) and keeps work in this tabpage.
			-- Two exceptions, in order:
			--   1. the file is already open in this tabpage -> just focus it.
			--   2. we launched from an empty scratch buffer -> replace it in place
			--      instead of leaving a useless empty split.
			local function select(prompt_bufnr)
				local entry = action_state.get_selected_entry()
				if entry then
					local w = win_showing(entry)
					if w then
						actions.close(prompt_bufnr)
						vim.api.nvim_set_current_win(w)
						return
					end
				end

				local picker = action_state.get_current_picker(prompt_bufnr)
				local win = picker and picker.original_win_id
				if win and vim.api.nvim_win_is_valid(win) and buf_is_empty(vim.api.nvim_win_get_buf(win)) then
					return actions.select_default(prompt_bufnr)
				end
				return actions.select_vertical(prompt_bufnr)
			end

			opts.defaults.mappings = vim.tbl_deep_extend("force", opts.defaults.mappings or {}, {
				i = { ["<CR>"] = select },
				n = { ["<CR>"] = select },
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
