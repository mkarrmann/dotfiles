-- Use upstream jdtls (Meta's fork hardcodes Buck importing and skips Maven).
local JDTLS_BIN = vim.fn.expand("~/.local/share/jdtls/bin/jdtls")
local JAVA_21 = "/usr/local/java-runtime/impl/21"
local JAVA_DEBUG_JAR = vim.fn.expand("~/.local/share/java-debug/com.microsoft.java.debug.plugin.jar")

return {
	-- Override mason so it doesn't try to download jdtls (proxy blocks it).
	{
		"mason-org/mason.nvim",
		opts = {
			ensure_installed = {},
		},
	},

	-- Override nvim-lspconfig to suppress mason's auto-install of jdtls.
	{
		"neovim/nvim-lspconfig",
		opts = {
			servers = {
				jdtls = {},
			},
			setup = {
				jdtls = function()
					return true
				end,
			},
		},
	},

	-- Override nvim-jdtls to use upstream jdtls with Java 21.
	{
		"mfussenegger/nvim-jdtls",
		opts = function(_, opts)
			opts.cmd = { JDTLS_BIN }

			opts.root_dir = function(path)
				-- Walk up to find the topmost pom.xml (Maven reactor root).
				-- vim.fs.root finds the nearest match, but multi-module projects
				-- need the highest ancestor that still contains a pom.xml.
				local dir = vim.fs.root(path, "pom.xml")
				if not dir then
					return nil
				end
				while true do
					local parent = vim.fn.fnamemodify(dir, ":h")
					if parent == dir then
						break
					end
					if vim.uv.fs_stat(parent .. "/pom.xml") then
						dir = parent
					else
						break
					end
				end
				return dir
			end

			opts.full_cmd = function(o)
				local fname = vim.api.nvim_buf_get_name(0)
				local root_dir = o.root_dir(fname)
				local project_name = o.project_name(root_dir)
				local cmd = vim.deepcopy(o.cmd)
				if project_name then
					vim.list_extend(cmd, {
						"-configuration", o.jdtls_config_dir(project_name),
						"-data", o.jdtls_workspace_dir(project_name),
					})
				end
				return cmd
			end

			opts.settings = {
				java = {
					configuration = {
						runtimes = {
							-- Presto targets 17; jdtls runs on 21 but diagnoses against 17.
							{ name = "JavaSE-17", path = "/usr/local/java-runtime/impl/17", default = true },
							{ name = "JavaSE-21", path = JAVA_21 },
						},
					},
					inlayHints = {
						parameterNames = { enabled = "all" },
					},
					import = {
						maven = { enabled = true },
					},
					maven = {
						downloadSources = true,
					},
					autobuild = { enabled = true },
				},
			}
		end,
		config = function(_, opts)
			-- Set JAVA_HOME for the jdtls process (needs Java 21 to run).
			vim.env.JAVA_HOME = JAVA_21

			-- Let LazyVim's default config function handle the rest.
			-- We just need to ensure JAVA_HOME is set before it runs.
			local LazyVim = require("lazyvim.util")

			local java_filetypes = { "java" }

			local function extend_or_override(config, custom, ...)
				if type(custom) == "function" then
					config = custom(config, ...) or config
				elseif custom then
					config = vim.tbl_deep_extend("force", config, custom)
				end
				return config
			end

			local function attach_jdtls()
				local fname = vim.api.nvim_buf_get_name(0)
				local root_dir = opts.root_dir(fname)
				local config = extend_or_override({
					cmd = opts.full_cmd(opts),
					root_dir = root_dir,
					init_options = {
						bundles = vim.fn.filereadable(JAVA_DEBUG_JAR) == 1
								and { JAVA_DEBUG_JAR } or {},
					},
					settings = opts.settings,
					capabilities = LazyVim.has("cmp-nvim-lsp")
							and require("cmp_nvim_lsp").default_capabilities()
						or nil,
					handlers = {
						["language/status"] = function(_, result)
							if result and result.type == "ProjectConfigurationUpdate"
								and result.message and result.message:find("build path") then
								vim.notify("jdtls: " .. result.message, vim.log.levels.ERROR)
							end
						end,
					},
				}, opts.jdtls)

				require("jdtls").start_or_attach(config)
			end

			vim.api.nvim_create_autocmd("FileType", {
				pattern = java_filetypes,
				callback = attach_jdtls,
			})

			vim.api.nvim_create_autocmd("LspAttach", {
				callback = function(args)
					local client = vim.lsp.get_client_by_id(args.data.client_id)
					if client and client.name == "jdtls" then
						-- Set up DAP if java-debug bundle is available.
						if vim.fn.filereadable(JAVA_DEBUG_JAR) == 1 then
							require("jdtls.dap").setup_dap()
						end

						local wk = require("which-key")
						wk.add({
							{
								mode = "n",
								buffer = args.buf,
								{ "<leader>cx", group = "extract" },
								{ "<leader>cxv", require("jdtls").extract_variable_all, desc = "Extract Variable" },
								{ "<leader>cxc", require("jdtls").extract_constant, desc = "Extract Constant" },
								{ "<leader>cgs", require("jdtls").super_implementation, desc = "Goto Super" },
								{ "<leader>cgS", require("jdtls.tests").goto_subjects, desc = "Goto Subjects" },
								{ "<leader>co", require("jdtls").organize_imports, desc = "Organize Imports" },
							},
						})
						wk.add({
							{
								mode = "x",
								buffer = args.buf,
								{ "<leader>cx", group = "extract" },
								{
									"<leader>cxm",
									[[<ESC><CMD>lua require('jdtls').extract_method(true)<CR>]],
									desc = "Extract Method",
								},
								{
									"<leader>cxv",
									[[<ESC><CMD>lua require('jdtls').extract_variable_all(true)<CR>]],
									desc = "Extract Variable",
								},
								{
									"<leader>cxc",
									[[<ESC><CMD>lua require('jdtls').extract_constant(true)<CR>]],
									desc = "Extract Constant",
								},
							},
						})

						wk.add({
							{
								mode = "n",
								buffer = args.buf,
								{ "<leader>dj", function()
									require("jdtls.dap").setup_dap_main_class_configs()
									vim.notify("Discovering main classes... use <leader>dc to launch.", vim.log.levels.INFO)
								end, desc = "Discover Java Main Classes" },
								{ "<leader>da", function()
									local port = vim.fn.input("Debug port: ", "5005")
									if port == "" then return end
									require("dap").run({
										type = "java",
										request = "attach",
										name = "Attach to JVM",
										hostName = "localhost",
										port = tonumber(port),
									})
								end, desc = "Attach to JVM" },
							},
						})

						local maven = require("lib.presto-maven")
						wk.add({
							{
								mode = "n",
								buffer = args.buf,
								{ "<leader>m", group = "maven" },
								{ "<leader>mi", function() maven.install({ incremental = true }) end, desc = "Install Module (incremental)" },
								{ "<leader>mI", function() maven.install() end, desc = "Install Module (clean)" },
								{ "<leader>mt", function() maven.test() end, desc = "Test Module" },
								{ "<leader>mf", function() maven.full() end, desc = "Full Build" },
								{ "<leader>mc", function() maven.checkstyle() end, desc = "Checkstyle" },
								{ "<leader>mq", function() maven.close() end, desc = "Close Build Terminal" },
							},
						})

						if opts.on_attach then
							opts.on_attach(args)
						end
					end
				end,
			})

			attach_jdtls()
		end,
	},
}
