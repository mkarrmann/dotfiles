return {
	{
		"epwalsh/obsidian.nvim",
		dependencies = { "nvim-lua/plenary.nvim" },
		event = { "BufReadPre *.md", "BufNewFile *.md" },
		cmd = {
			"ObsidianQuickSwitch",
			"ObsidianSearch",
			"ObsidianNew",
			"ObsidianToday",
			"ObsidianYesterday",
			"ObsidianTomorrow",
			"ObsidianToggleCheckbox",
		},
		config = function()
			vim.api.nvim_create_autocmd("FileType", {
				pattern = "markdown",
				callback = function()
					vim.opt_local.conceallevel = 2
				end,
			})

			local vault = vim.g.obsidian_vault or vim.fn.expand("~/obsidian")
			local mappings = require("obsidian.mappings")
			require("obsidian").setup({
				workspaces = { { name = "default", path = vault } },
				notes_subdir = "Pad",
				new_notes_location = "notes_subdir",
				disable_frontmatter = true,
				note_id_func = function(title)
					if title then
						return title:lower():gsub("%s+", "-"):gsub("[^%w%-_]", "")
					end
					return tostring(os.time())
				end,
				open_notes_in = "current",
				ui = { enable = true },
				picker = { name = "telescope.nvim" },
				completion = { nvim_cmp = true, min_chars = 2 },
				mappings = {
					["gf"] = mappings.gf_passthrough(),
					["<cr>"] = mappings.smart_action(),
					["<leader>ch"] = mappings.toggle_checkbox(),
				},
			})
		end,
	},
}
