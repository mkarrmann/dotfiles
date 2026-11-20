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
		config = function(_, opts)
			require("treesitter-context").setup(opts)
			-- Failure means the plugin's internals moved under the HACK in
			-- lib.tscontext-perf: typing latency regresses to ~45ms/keystroke in
			-- large Python buffers. Say so rather than degrading silently.
			if not require("lib.tscontext-perf").setup() then
				vim.notify(
					"lib.tscontext-perf: could not adopt treesitter-context's DiagnosticChanged "
						.. "subscription; the typing-latency workaround is INACTIVE. See "
						.. "docs/nvim-typing-latency-investigation.md",
					vim.log.levels.WARN
				)
			end
		end,
	},
	{
		-- marksman (Markdown LSP) is installed by bin/marksman-ensure from
		-- init.sh, not by Mason: Mason pulls it from GitHub releases, which some
		-- hosts here cannot reach. Enable it only where the binary is actually
		-- present, so a host without it stays quiet rather than failing config
		-- validation on every startup with
		--   'invalid "marksman" config: cmd: expected ... executable'
		-- An absolute cmd is required, not a bare name: nvs servers inherit the
		-- systemd --user PATH (/usr/local/{s,}bin:/usr/{s,}bin), which does not
		-- include ~/.local/bin.
		"neovim/nvim-lspconfig",
		opts = function(_, opts)
			local bin = vim.fn.expand("~/.local/bin/marksman")
			opts.servers = opts.servers or {}
			opts.servers.marksman = vim.fn.executable(bin) == 1
					and { mason = false, cmd = { bin, "server" } }
				or { enabled = false }
			return opts
		end,
	},
	{
		-- Markdown is linted with rumdl, installed by bin/rumdl-ensure, in place
		-- of the markdownlint-cli2 the LazyVim markdown extra wires in: that
		-- needs Node >= 22 and Mason, neither available here, so nvim-lint failed
		-- with ENOENT on every read, write and InsertLeave. Like marksman above,
		-- lint only where the binary exists, and by absolute path because nvs
		-- servers do not have ~/.cargo/bin on PATH.
		"mfussenegger/nvim-lint",
		opts = function(_, opts)
			local bin = vim.fn.expand("~/.cargo/bin/rumdl")
			local available = vim.fn.executable(bin) == 1
			opts.linters_by_ft = opts.linters_by_ft or {}
			opts.linters_by_ft.markdown = available and { "rumdl" } or {}
			if available then
				opts.linters = opts.linters or {}
				-- nvim-lint's built-in rumdl definition reads stderr, but rumdl
				-- writes its JSON report (`[]` when clean) to stdout.
				opts.linters.rumdl = { cmd = bin, stream = "stdout" }
			end
			return opts
		end,
	},
	{
		"nvimtools/none-ls.nvim",
		optional = true,
		opts = function(_, opts)
			opts.sources = vim.tbl_filter(function(source)
				return source.name ~= "markdownlint-cli2"
			end, opts.sources or {})
			return opts
		end,
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
			-- lualine inserts component return values into the statusline format
			-- string verbatim, so any literal `%` must be doubled or Neovim will
			-- raise E539 when the next char isn't a valid stl format specifier.
			local function esc(s) return (s:gsub("%%", "%%%%")) end
			local function cwd()
				return esc(vim.fn.fnamemodify(vim.fn.getcwd(0), ":~"))
			end
			local function custom_or_filename()
				local ok, text = pcall(vim.api.nvim_win_get_var, 0, "custom_winbar_text")
				if ok then
					return esc(text)
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
				return esc(name)
			end

			-- Lualine may install a tabs fallback when bufferline is disabled. It
			-- refreshes that tabline on a timer, so a one-time option reset is not
			-- enough; remove its tabline sections entirely.
			opts.tabline = {}

			local winbar_color = { fg = "#888888", bg = require("lib.session-accent").winbar_bg() }
			local winbar_cwd = { cwd, color = winbar_color }
			local winbar_file = { custom_or_filename, color = winbar_color }
			opts.winbar = { lualine_b = { winbar_cwd }, lualine_c = { winbar_file } }
			opts.inactive_winbar = { lualine_b = { winbar_cwd }, lualine_c = { winbar_file } }

			opts.sections = opts.sections or {}
			opts.sections.lualine_y = {
				{ "progress", separator = " ", padding = { left = 1, right = 0 } },
				{ "location", padding = { left = 0, right = 1 } },
			}
			opts.sections.lualine_z = {
				{
					function()
						return require("lib.session-accent").session_name() or _hostname
					end,
					color = function()
						local accent = require("lib.session-accent")
						return { fg = "#000000", bg = accent.accent(), gui = "bold" }
					end,
				},
				function() return os.date("%H:%M") end,
			}
			opts.inactive_sections = opts.inactive_sections or {}
			opts.inactive_sections.lualine_z = opts.sections.lualine_z

			return opts
		end,
		config = function(_, opts)
			require("lualine").setup(opts)
			-- Lualine restores its cached tabline during setup. Activate ours only
			-- after that restore, once Lualine's tabline refresh is disabled.
			require("lib.agent-tabline").setup()
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
			-- Widen the `vim.ui.select` popup so long option labels aren't
			-- clipped. Overrides the built-in `select` layout preset (width 0.5,
			-- max_width 100) for every vim.ui.select caller.
			-- Snacks resolves per-source overrides from `picker.sources.<source>`
			-- (picker/config/init.lua get(): `global.sources[opts.source]`), NOT
			-- `picker.select` — placing it there is a silent no-op.
			picker = {
				sources = {
					select = {
						layout = {
							preset = "select",
							layout = { width = 0.7, min_width = 80, max_width = 140 },
						},
					},
				},
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
