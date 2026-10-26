-- Apply environment-specific configuration (Meta, etc.)
require("lib.env").setup()
local repo_context = require("lib.repo-context")
repo_context.setup()

-- Meta's bundled parsers are minimal (c, lua, markdown, vim, etc.).
-- On devservers, set proxy env vars so that curl-based downloads (e.g.
-- nvim-treesitter parser installs) can reach external hosts like GitHub.
-- On macOS (local laptop), fwdproxy is unreachable — leave proxy unset.
if vim.fn.has("mac") == 0 then
	vim.env.http_proxy = vim.env.http_proxy or "http://fwdproxy:8080"
	vim.env.https_proxy = vim.env.https_proxy or "http://fwdproxy:8080"
end

vim.g.obsidian_vault = require("lib.agent-session").resolve_vault_root()

-- Canonical list of enabled Meta language servers.
--
-- This used to be split across here and plugins/meta-lsp.lua, with the two
-- lists disagreeing (thriftlsp, rust-analyzer and hhvm existed only there), so
-- editing either one looked authoritative while the union was what actually
-- ran. It lives here because this file is required from config/local.lua on
-- every Meta machine, whereas the plugin spec is gated on the Linux meta.nvim
-- path and never loads on a Mac.
--
-- hhvm resolves from nvim-lspconfig; the rest from meta.nvim's lsp/ directory.
vim.lsp.enable({
	"buck2@meta",
	"cppls@meta",
	"hhvm",
	"ids@meta",
	"linttool@meta",
	"pyrefly@meta",
	"rust-analyzer@meta",
	"thriftlsp@meta",
})

-- HACK: start buck2@meta, rust-analyzer@meta and thriftlsp@meta in their
-- root_dir. nvim launches a server in `cmd_cwd`, which defaults to nvim's own
-- cwd and is unrelated to root_dir; configs that care pass
-- `cwd = config.root_dir` from a `cmd` function (meta.nvim's cppls, pyrefly
-- and linttool, lspconfig's hhvm), but these three give `cmd` as a list. In an
-- nvs session nvim's cwd is the workspace root (~/checkoutN), which holds the
-- repos but isn't one, and `buck2 lsp` exits there: "not in a Buck project".
-- The command itself is still meta.nvim's, read from its lsp/*.lua the way
-- vim.lsp.config merges them, when the server first starts.
for _, name in ipairs({ "buck2@meta", "rust-analyzer@meta", "thriftlsp@meta" }) do
	local shipped_cmd
	vim.lsp.config(name, {
		cmd = function(dispatchers, config)
			if not shipped_cmd then
				local shipped = {}
				for _, file in ipairs(vim.api.nvim_get_runtime_file(("lsp/%s.lua"):format(name), true)) do
					shipped = vim.tbl_deep_extend("force", shipped, assert(loadfile(file))())
				end
				shipped_cmd = assert(shipped.cmd, ("no cmd in lsp/%s.lua on the runtimepath"):format(name))
			end
			if type(shipped_cmd) == "function" then
				return shipped_cmd(dispatchers, config)
			end
			return vim.lsp.rpc.start(shipped_cmd, dispatchers, {
				cwd = config.cmd_cwd or config.root_dir,
				env = config.cmd_env,
				detached = config.detached,
			})
		end,
	})
end

-- pyrefly@meta cannot report type errors in the IDE (fbcode/pyrefly.toml sets
-- disable-type-errors-in-ide), so recover them from the CLI on save.
require("lib.pyrefly-check").setup()

vim.api.nvim_create_autocmd("User", {
	pattern = "VeryLazy",
	once = true,
	desc = "Configure Meta tooling",
	callback = function()
		local meta_hg
		do
			local ok, m = pcall(require, "lib.meta-hg")
			if ok then
				meta_hg = m
				m.setup({ ssl = { status = true } })
			end
		end
		local meta_ok = pcall(require, "meta")

		local metamate_ok, metamate = pcall(require, "meta.metamate")
		if metamate_ok then
			metamate.init({
				virtualTextHighlightGroup = "Comment",
				filetypes = {
					"bash", "buck", "chef", "cpp", "css", "gitcommit", "go",
					"hack", "hgcommit", "java", "javascript", "javascriptreact",
					"json", "jsonc", "lua", "markdown", "mdx", "php", "python",
					"rust", "sh", "sql", "thrift", "typescript",
					"typescriptreact", "zsh",
				},
			})
		end

		local buck_ok, buck
		if meta_ok then
			require("lib.meta-buck").setup()
			buck_ok, buck = pcall(require, "meta.buck")
		end
		if buck_ok then
			buck.setup({
				keybindings = {
					enabled = true,
					test_current = "<leader>Bt",
					test_target = "<leader>BT",
					test_file = "<leader>Bf",
					test_last = "<leader>Bl",
					build_target = "<leader>Bb",
					run_target = "<leader>Br",
					toggle_terminal = "<leader>Bg",
				},
			})
		end

		-- These need lib.meta-hg, which needs meta.nvim.
		if meta_hg then
			vim.api.nvim_create_user_command("HgDiffSplit", function()
				if vim.bo.buftype ~= "" then
					vim.notify("Not a file buffer", vim.log.levels.WARN)
					return
				end
				local file = vim.api.nvim_buf_get_name(0)
				if file == "" then
					vim.notify("No file in current buffer", vim.log.levels.ERROR)
					return
				end
				local repo_root = repo_context.repo_root(file)
				if not repo_root then
					vim.notify("File is not in a Sapling repository", vim.log.levels.ERROR)
					return
				end
				meta_hg.open_file_diff_split(repo_root, file, ".^")
			end, { desc = "Side-by-side diff of the current file against .^ (its commit plus uncommitted changes)" })

			vim.api.nvim_create_user_command("HgDiffSplitWorkingSet", function()
				local context = repo_context.current()
				if not context then
					repo_context.with_context(function()
						vim.cmd.HgDiffSplitWorkingSet()
					end)
					return
				end
				meta_hg.open_working_copy_diff_split(context.repo_root)
			end, { desc = "Side-by-side diff of all uncommitted changes against ." })

			vim.keymap.set("n", "<leader>hb", "<CMD>HgBlame<CR>", { desc = "Hg blame" })
			vim.keymap.set("n", "<leader>hB", "<CMD>HgLineBlameToggle<CR>", { desc = "Hg toggle inline blame" })
			vim.keymap.set("n", "<leader>ho", "<CMD>HgLineBlameCopyDiff<CR>", { desc = "Hg copy diff number for line" })
			vim.keymap.set("n", "<leader>hh", "<CMD>HgHistory<CR>", { desc = "Hg file history" })
			vim.keymap.set("n", "<leader>hd", "<CMD>HgDiffSplit<CR>", { desc = "Hg diff split: file vs .^" })
			vim.keymap.set("n", "<leader>hD", "<CMD>HgDiffSplitWorkingSet<CR>", { desc = "Hg diff split: uncommitted vs ." })
			vim.keymap.set("n", "<leader>hs", "<CMD>HgSsl<CR>", { desc = "Hg smartlog" })
			vim.keymap.set("n", "<leader>hS", "<CMD>HgSslSplit<CR>", { desc = "Hg smartlog (vsplit)" })
			vim.keymap.set("n", "<leader>hu", "<CMD>HgSuggest<CR>", { desc = "Hg suggest changes" })
			vim.keymap.set("n", "<leader>hc", "<CMD>HgInlineComment<CR>", { desc = "Hg inline comment" })
			vim.keymap.set("v", "<leader>hc", ":HgInlineComment<CR>", { desc = "Hg inline comment" })
			vim.keymap.set("n", "<leader>hC", "<CMD>HgPublishDrafts<CR>", { desc = "Hg publish draft comments" })
		end

		vim.keymap.set("n", "<leader>hp", "<CMD>SlPull<CR>", { desc = "Hg pull" })

		vim.api.nvim_create_user_command("SlPull", function()
			local context = repo_context.current()
			if not context then
				repo_context.with_context(function()
					vim.cmd.SlPull()
				end)
				return
			end
			vim.fn.jobstart({ "sl", "pull" }, {
				cwd = context.repo_root,
				on_exit = function(_, code)
					vim.schedule(function()
						if code == 0 then
							vim.notify("sl pull completed", vim.log.levels.INFO)
							if meta_hg then
								meta_hg.refresh_ssl()
							end
						else
							vim.notify("sl pull failed (exit " .. code .. ")", vim.log.levels.ERROR)
						end
					end)
				end,
			})
		end, { desc = "Run sl pull" })

		local telescope_ok2, telescope = pcall(require, "telescope")
		if meta_ok and telescope_ok2 then
			-- TODO: lua/plugins/meta-lsp.lua also binds <leader>p (with a non-arc-root
			-- fallback to the LazyVim files picker); this VeryLazy mapping shadows it.
			local myles = require("lib.myles")
			myles.setup()
			local biggrep = telescope.extensions.biggrep
			if not biggrep._repo_context_originals then
				biggrep._repo_context_originals = {}
				local function wrap_search(name)
					local original = biggrep[name]
					biggrep._repo_context_originals[name] = original
					biggrep[name] = function(opts)
						local search = biggrep._repo_context_originals[name]
						opts = opts or {}
						if opts.cwd then
							search(opts)
							return
						end
						repo_context.with_context(function(context)
							search(vim.tbl_deep_extend("force", { cwd = context.workdir }, opts))
						end)
					end
				end
				for _, name in ipairs({ "s", "r", "f" }) do
					wrap_search(name)
				end
			end
			vim.keymap.set("n", "<leader>p", function()
				myles.pick()
			end, { desc = "Find files (Myles)" })
			vim.keymap.set("n", "<leader>sg", function()
				telescope.extensions.biggrep.s({})
			end)
			vim.keymap.set("n", "<leader>sr", function()
				telescope.extensions.biggrep.r({})
			end)
		end
	end,
})
