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
				package.loaded["meta.hg"] = m
				m.setup({ ssl = { status = true } })
			end
		end
		local meta_ok, meta = pcall(require, "meta")
		if meta_ok then
			meta.setup()
		end

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

			local original_run_target = buck.run_target
			buck.run_target = function(extra_args)
				local buck_util = require("meta.util.buck")
				local targets_map = buck_util.get_owning_targets(nil, nil, true, {})
				if targets_map then
					local has_non_library = false
					for name, _ in pairs(targets_map) do
						if not name:match("%-library$") then
							has_non_library = true
							break
						end
					end
					if not has_non_library then
						for name, _ in pairs(targets_map) do
							local base = name:gsub("%-library$", "")
							local cwd = repo_context.buck_root()
							local result = cwd
								and vim.system(
									{ "buck2", "uquery", base, "--json", "-a", "buck.type" },
									{ cwd = cwd, text = true }
								):wait()
							if result and result.code == 0 then
								local ok2, decoded = pcall(vim.fn.json_decode, result.stdout)
								if ok2 and decoded and decoded[base] then
									local buck_type = decoded[base]["buck.type"] or ""
									if buck_type:match("_binary$") then
										local terminal = require("meta.util.terminal")
										local cmd = buck_util.run(base, extra_args, {})
										local term_opts = { direction = "horizontal" }
										terminal.run_and_store(cmd, term_opts)
										return
									end
								end
							end
						end
					end
				end
				original_run_target(extra_args)
			end
		end

		vim.api.nvim_create_user_command("HgDiffSplit", function()
			if vim.bo.buftype ~= "" then
				vim.notify("Not a file buffer", vim.log.levels.WARN)
				return
			end
			local file = vim.fn.expand("%:p")
			if file == "" then
				vim.notify("No file in current buffer", vim.log.levels.ERROR)
				return
			end

			local repo_root = repo_context.repo_root(file)
			if not repo_root then
				vim.notify("File is not in a Sapling repository", vim.log.levels.ERROR)
				return
			end
			local result = vim.system({ "hg", "cat", "-r", ".^", file }, { text = true, cwd = repo_root }):wait()
			local old_ok = result.code == 0
			local old_lines = {}
			if old_ok and result.stdout and result.stdout ~= "" then
				old_lines = vim.split(result.stdout:gsub("\n$", ""), "\n", { plain = true })
			end

			local tmp = vim.fn.tempname()
			vim.fn.writefile(old_lines, tmp)
			local orig_win = vim.api.nvim_get_current_win()
			vim.cmd("rightbelow vertical diffsplit " .. vim.fn.fnameescape(tmp))
			local diff_win = vim.api.nvim_get_current_win()
			local display_name = vim.fn.fnamemodify(file, ":.")
			require("lib.diff-opts").apply_pair(diff_win, orig_win, ".^", "LIVE", display_name)
			vim.api.nvim_set_current_win(orig_win)
			vim.api.nvim_create_autocmd("WinClosed", {
				pattern = tostring(diff_win),
				once = true,
				callback = function()
					if vim.api.nvim_win_is_valid(orig_win) then
						pcall(vim.api.nvim_win_del_var, orig_win, "custom_winbar_text")
						vim.api.nvim_win_call(orig_win, function()
							vim.cmd("diffoff")
						end)
						require("lualine").refresh()
					end
				end,
			})
		end, { desc = "Side-by-side diff of current file against parent commit" })

		vim.api.nvim_create_user_command("HgDiffSplitWorkingSet", function()
			local diff_session = require("lib.diff-session")

			local context = repo_context.current()
			if not context then
				repo_context.with_context(function()
					vim.cmd.HgDiffSplitWorkingSet()
				end)
				return
			end
			local repo_root = context.repo_root

			local out = vim.system({ "hg", "status" }, { text = true, cwd = repo_root }):wait()
			if out.code ~= 0 then
				vim.notify("hg status failed", vim.log.levels.ERROR)
				return
			end

			local files = {}
			for _, line in ipairs(vim.split(vim.trim(out.stdout or ""), "\n")) do
				local status = line:sub(1, 1)
				if status == "M" or status == "A" then
					table.insert(files, line:sub(3))
				end
			end

			if #files == 0 then
				vim.notify("No uncommitted changes", vim.log.levels.INFO)
				return
			end

			local file_pairs = {}
			for _, file in ipairs(files) do
				table.insert(file_pairs, { file = file, is_live = false })
			end

			local tab = vim.api.nvim_get_current_tabpage()
			if diff_session.sessions[tab] then
				diff_session.close(diff_session.sessions[tab])
			end

			local origin_win = vim.api.nvim_get_current_win()

			local left_win, right_win = diff_session.create_pair_wins()

			local closing = false

			local session = {
				pairs = file_pairs,
				index = 1,
				left_win = left_win,
				right_win = right_win,
				commit = { is_current = true, hash = "." },
				parent_rev = ".",
				repo_root = repo_root,
				update_winbar = meta_hg and meta_hg.diff_split_update_winbar,
			}

			session.on_close = function()
				if closing then
					return
				end
				closing = true
				for _, win in ipairs({ left_win, right_win }) do
					if vim.api.nvim_win_is_valid(win) then
						pcall(vim.api.nvim_win_close, win, true)
					end
				end
				diff_session.cleanup(session)
				if vim.api.nvim_win_is_valid(origin_win) then
					vim.api.nvim_set_current_win(origin_win)
				end
			end

			for _, win in ipairs({ left_win, right_win }) do
				vim.api.nvim_create_autocmd("WinClosed", {
					pattern = tostring(win),
					once = true,
					callback = function()
						vim.schedule(function()
							diff_session.close(session)
						end)
					end,
				})
			end

			for _, pair in ipairs(file_pairs) do
				pair.load = function(p)
					if meta_hg then
						meta_hg.diff_split_load_pair(session, p)
					end
				end
			end

			diff_session.register(tab, session)

			if meta_hg then
				meta_hg.diff_split_load_pair(session, file_pairs[1])
			end
			if not file_pairs[1].old_buf then
				session.on_close()
				return
			end

			diff_session.show_pair(session, 1)
		end, { desc = "Side-by-side diff of all uncommitted changes" })

		vim.keymap.set("n", "<leader>hb", "<CMD>HgBlame<CR>", { desc = "Hg blame" })
		vim.keymap.set("n", "<leader>hB", "<CMD>HgLineBlameToggle<CR>", { desc = "Hg toggle inline blame" })
		vim.keymap.set("n", "<leader>ho", "<CMD>HgLineBlameCopyDiff<CR>", { desc = "Hg copy diff number for line" })
		vim.keymap.set("n", "<leader>hh", "<CMD>HgHistory<CR>", { desc = "Hg file history" })
		vim.keymap.set("n", "<leader>hd", "<CMD>HgDiffSplit<CR>", { desc = "Hg diff split" })
		vim.keymap.set("n", "<leader>hD", "<CMD>HgDiffSplitWorkingSet<CR>", { desc = "Hg diff split (working set)" })
		vim.keymap.set("n", "<leader>hs", "<CMD>HgSsl<CR>", { desc = "Hg smartlog" })
		vim.keymap.set("n", "<leader>hS", "<CMD>HgSslSplit<CR>", { desc = "Hg smartlog (vsplit)" })
		vim.keymap.set("n", "<leader>hu", "<CMD>HgSuggest<CR>", { desc = "Hg suggest changes" })
		vim.keymap.set("v", "<leader>hc", ":HgInlineComment<CR>", { desc = "Hg inline comment" })
		vim.keymap.set("n", "<leader>hC", "<CMD>HgPublishDrafts<CR>", { desc = "Hg publish draft comments" })
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
							require("lib.meta-hg").refresh_ssl()
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
