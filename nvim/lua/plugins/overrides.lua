local _hostname = vim.uv.os_gethostname():gsub("%.facebook%.com$", "")

return {
	{
		"nvim-treesitter/nvim-treesitter",
		opts = function(_, opts)
			vim.list_extend(opts.ensure_installed or {}, { "cpp", "rust", "thrift", "hack" })
			local install = require("nvim-treesitter.install")
			install.prefer_git = false
			if not vim.env.HTTP_PROXY then
				install.command_extra_args = {
					curl = { "--proxy", "http://fwdproxy:8080" },
				}
			end
		end,
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
				local autosave = require("lib.autosave").status()
				if autosave ~= "" then
					name = name .. " " .. autosave
				end
				return name
			end

			local function cc_session_id()
				if vim.bo.filetype ~= "codecompanion" then return nil end
				local ok, cc = pcall(require, "codecompanion")
				if not ok then return nil end
				local chat = cc.buf_get_chat(vim.api.nvim_get_current_buf())
				if not chat then return nil end
				return chat.acp_session_id or (chat.acp_connection and chat.acp_connection.session_id)
			end

			-- Mirrors fmt_tok in ~/dotfiles/claude_config/statusline.sh:
			-- 12500 -> "12.5k", 999 -> "999".
			local function fmt_tok(n)
				if n >= 1000 then
					return string.format("%d.%dk", math.floor(n / 1000), math.floor((n % 1000) / 100))
				end
				return tostring(n)
			end

			local function cc_context()
				local sid = cc_session_id()
				if not sid then return "" end
				local stats = require("lib.codecompanion-stats")
				local s = stats.get(sid)
				if not s or not s.size or s.size == 0 then return "" end
				local pct = math.floor(100 * s.used / s.size)
				local cost_str = ""
				if s.cost and s.cost.amount and s.cost.amount > 0 then
					cost_str = string.format(" 💰 $%.4f", s.cost.amount)
				end
				return string.format("📊 %d%% ctx:%s/%s%s", pct, fmt_tok(s.used), fmt_tok(s.size), cost_str)
			end

			local function cc_context_color()
				local sid = cc_session_id()
				if not sid then return nil end
				local pct = require("lib.codecompanion-stats").context_pct(sid) or 0
				if pct >= 85 then return { fg = "#ff5555" } end
				if pct >= 70 then return { fg = "#ff8800" } end
				if pct >= 50 then return { fg = "#ffcc00" } end
				return { fg = "#00cc00" }
			end

			opts.options = opts.options or {}
			opts.options.disabled_filetypes = opts.options.disabled_filetypes or {}
			opts.options.disabled_filetypes.winbar = opts.options.disabled_filetypes.winbar or {}
			opts.options.disabled_filetypes.statusline = opts.options.disabled_filetypes.statusline or {}
			vim.list_extend(opts.options.disabled_filetypes.winbar, { "codecompanion_input" })
			vim.list_extend(opts.options.disabled_filetypes.statusline, { "codecompanion_input" })

			local winbar_color = { fg = "#888888", bg = "#1a1a2e" }
			local winbar_cwd = { cwd, color = winbar_color }
			local winbar_file = { custom_or_filename, color = winbar_color }
			opts.winbar = { lualine_b = { winbar_cwd }, lualine_c = { winbar_file } }
			opts.inactive_winbar = { lualine_b = { winbar_cwd }, lualine_c = { winbar_file } }

			opts.sections = opts.sections or {}
			opts.sections.lualine_x = opts.sections.lualine_x or {}
			table.insert(opts.sections.lualine_x, 1, { cc_context, color = cc_context_color })
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

			return opts
		end,
	},
	{
		"coder/claudecode.nvim",
		opts = {
			terminal_cmd = vim.fn.expand("~/.claude/agent-manager/bin/claude-nvim-wrapper.sh"),
			env = {
				EDITOR = "nvim",
				CLAUDECODE = "",
			},
			terminal = {
				provider = require("lib.claude-per-tab-terminal"),
				split_width_percentage = 0.45,
			},
		},
		config = function(_, opts)
			-- Merge the original process environment (snapshotted before any
			-- vim.env modifications) into the terminal env so that Claude Code
			-- gets the same environment as a shell-launched session.
			-- opts.env values take priority ("keep"), then original_env fills
			-- in everything else; termopen merges both on top of environ().
			local env_mod = require("lib.env")
			if env_mod.original_env then
				opts.env = vim.tbl_extend("keep", opts.env or {}, env_mod.original_env)
			end

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
		"MeanderingProgrammer/render-markdown.nvim",
		opts = {
			file_types = { "markdown", "codecompanion" },
		},
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
