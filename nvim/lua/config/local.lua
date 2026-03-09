-- Apply environment-specific configuration (Meta, etc.)
require("lib.env").setup()

vim.g.obsidian_vault = vim.fn.expand("~/Work")

local meta_hg = require("lib.meta-hg")
package.loaded["meta.hg"] = meta_hg
meta_hg.setup({ ssl = { status = true } })
local meta_ok, meta = pcall(require, "meta")
if meta_ok then
	meta.setup()
end

vim.lsp.enable({
	"cppls@meta",
	"fb-pyright-ls@meta",
	"pyre@meta",
	"buck2@meta",
	"linttool@meta",
})

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


local buck_ok, buck = pcall(require, "meta.buck")
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

	-- Patch: the plugin's library-target fallback only checks for _test targets,
	-- missing _binary targets. Wrap run_target to handle this case.
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
					local result = vim.fn.system(string.format("buck2 uquery '%s' --json -a buck.type 2>/dev/null", base))
					if vim.v.shell_error == 0 then
						local ok, decoded = pcall(vim.fn.json_decode, result)
						if ok and decoded and decoded[base] then
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

	local result = vim.fn.systemlist("hg cat -r .^ " .. vim.fn.shellescape(file))
	local old_ok = vim.v.shell_error == 0

	local tmp = vim.fn.tempname()
	vim.fn.writefile(old_ok and result or {}, tmp)
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

	local cwd = vim.uv.cwd() or vim.fn.getcwd()
	local repo_root = vim.fs.root(cwd, ".hg")
	if not repo_root then
		vim.notify("Not in an hg repo", vim.log.levels.ERROR)
		return
	end

	local out = vim.system({ "hg", "status" }, { text = true }):wait()
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

	vim.cmd("rightbelow vsplit")
	local left_win = vim.api.nvim_get_current_win()
	vim.cmd("rightbelow vsplit")
	local right_win = vim.api.nvim_get_current_win()

	local closing = false

	local session = {
		pairs = file_pairs,
		index = 1,
		left_win = left_win,
		right_win = right_win,
		commit = { is_current = true, hash = "." },
		parent_rev = ".",
		repo_root = repo_root,
		update_winbar = meta_hg.diff_split_update_winbar,
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
			meta_hg.diff_split_load_pair(session, p)
		end
	end

	diff_session.register(tab, session)

	meta_hg.diff_split_load_pair(session, file_pairs[1])
	if not file_pairs[1].old_buf then
		session.on_close()
		return
	end

	diff_session.show_pair(session, 1)
end, { desc = "Side-by-side diff of all uncommitted changes" })

vim.keymap.set("n", "<leader>hb", "<CMD>HgBlame<CR>", { desc = "Hg blame" })
vim.keymap.set("n", "<leader>hd", "<CMD>HgDiffSplit<CR>", { desc = "Hg diff split" })
vim.keymap.set("n", "<leader>hD", "<CMD>HgDiffSplitWorkingSet<CR>", { desc = "Hg diff split (working set)" })
vim.keymap.set("n", "<leader>hs", "<CMD>HgSsl<CR>", { desc = "Hg smartlog" })
vim.keymap.set("n", "<leader>hS", "<CMD>HgSslSplit<CR>", { desc = "Hg smartlog (vsplit)" })
vim.keymap.set("n", "<leader>hu", "<CMD>HgSuggest<CR>", { desc = "Hg suggest changes" })
vim.keymap.set("v", "<leader>hc", ":HgInlineComment<CR>", { desc = "Hg inline comment" })
vim.keymap.set("n", "<leader>hC", "<CMD>HgPublishDrafts<CR>", { desc = "Hg publish draft comments" })
vim.keymap.set("n", "<leader>hp", "<CMD>SlPull<CR>", { desc = "Hg pull" })

vim.api.nvim_create_user_command("SlPull", function()
	vim.fn.jobstart("sl pull", {
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

local telescope_ok, telescope = pcall(require, "telescope")
if telescope_ok then
	vim.keymap.set("n", "<leader>p", function()
		telescope.extensions.myles.myles({})
	end)
	vim.keymap.set("n", "<leader>sg", function()
		telescope.extensions.biggrep.s({})
	end)
	vim.keymap.set("n", "<leader>sr", function()
		telescope.extensions.biggrep.r({})
	end)
end

