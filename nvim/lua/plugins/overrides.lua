local _hostname = vim.uv.os_gethostname():gsub("%.facebook%.com$", "")

return {
	{
		"nvim-treesitter/nvim-treesitter",
		opts = {
			ensure_installed = { "vim" },
		},
	},
	{
		"nvim-treesitter/nvim-treesitter-context",
		opts = {
			max_lines = 5,
			trim_scope = "outer",
			separator = "─",
		},
	},
	{
		"lukas-reineke/indent-blankline.nvim",
		opts = {
			indent = { char = "▏" },
			scope = { enabled = true },
		},
	},
	{
		"nvim-lualine/lualine.nvim",
		opts = function(_, opts)
			local function cwd()
				return vim.fn.fnamemodify(vim.fn.getcwd(0), ":~")
			end
			local function custom_or_filename()
				local ok, text = pcall(vim.api.nvim_win_get_var, 0, "custom_winbar_text")
				if ok then
					return text
				end
				local name = vim.fn.expand("%:.")
				if name == "" then
					name = "[No Name]"
				end
				if vim.bo.modified then
					name = name .. " [+]"
				end
				return name
			end
			opts.winbar = { lualine_b = { cwd }, lualine_c = { custom_or_filename } }
			opts.inactive_winbar = { lualine_b = { cwd }, lualine_c = { custom_or_filename } }

			opts.sections = opts.sections or {}
			opts.sections.lualine_y = {
				{ "progress", separator = " ", padding = { left = 1, right = 0 } },
				{ "location", padding = { left = 0, right = 1 } },
			}
			opts.sections.lualine_z = {
				function() return _hostname end,
				function() return os.date("%H:%M") end,
			}
			opts.inactive_sections = opts.inactive_sections or {}
			opts.inactive_sections.lualine_z = opts.sections.lualine_z
		end,
	},
	{
		"coder/claudecode.nvim",
		opts = {
			terminal_cmd = "~/.claude/agent-manager/bin/claude-nvim-wrapper.sh",
			env = {
				EDITOR = "nvim",
				CLAUDECODE = "",
				http_proxy = "",
				https_proxy = "",
				HTTP_PROXY = "",
				HTTPS_PROXY = "",
			},
			terminal = {
				split_width_percentage = 0.45,
			},
		},
		config = function(_, opts)
			require("claudecode").setup(opts)

			-- Patch closeAllDiffTabs to only close diffs that claudecode.nvim
			-- itself created (tracked in its active_diffs table), rather than
			-- indiscriminately closing every window with diff mode on.
			local tools = require("claudecode.tools")
			local orig = tools.tools["closeAllDiffTabs"]
			if orig then
				orig.handler = function()
					local diff = require("claudecode.diff")
					local active = diff._get_active_diffs()
					local count = 0
					for _ in pairs(active) do
						count = count + 1
					end
					diff._cleanup_all_active_diffs("closeAllDiffTabs")
					return {
						content = {
							{ type = "text", text = "CLOSED_" .. count .. "_DIFF_TABS" },
						},
					}
				end
			end
		end,
	},
	{
		"folke/snacks.nvim",
		opts = {
			notifier = {
				timeout = 10000,
			},
		},
	},
	{
		"folke/flash.nvim",
		keys = {
			{ "s", mode = { "n", "x", "o" }, false },
			{ "S", mode = { "n", "o", "x" }, false },
			{
				"gs",
				mode = { "n", "x", "o" },
				function()
					require("flash").jump()
				end,
				desc = "Flash",
			},
			{
				"gS",
				mode = { "n", "o", "x" },
				function()
					require("flash").treesitter()
				end,
				desc = "Flash Treesitter",
			},
		},
	},
}
