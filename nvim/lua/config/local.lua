-- Meta's Neovim package puts bundled treesitter parsers in /usr/lib/nvim/parser/
-- but doesn't add /usr/lib/nvim to the runtimepath. The proxy also blocks
-- nvim-treesitter from downloading parsers directly from GitHub.
vim.opt.rtp:prepend("/usr/lib/nvim")

package.loaded["meta.hg"] = require("lib.meta-hg")
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
	local file = vim.fn.expand("%:p")
	if file == "" then
		vim.notify("No file in current buffer", vim.log.levels.ERROR)
		return
	end

	local result = vim.fn.systemlist("hg cat -r .^ " .. vim.fn.shellescape(file))
	if vim.v.shell_error ~= 0 then
		vim.notify("hg cat failed (file may be new or not in parent commit)", vim.log.levels.ERROR)
		return
	end

	local tmp = vim.fn.tempname()
	vim.fn.writefile(result, tmp)
	local orig_win = vim.api.nvim_get_current_win()
	vim.cmd("vertical diffsplit " .. vim.fn.fnameescape(tmp))
	local diff_win = vim.api.nvim_get_current_win()
	vim.wo[diff_win].scrollbind = true
	vim.wo[diff_win].relativenumber = false
	vim.wo[diff_win].statuscolumn = ""
	vim.wo[diff_win].foldenable = false
	vim.wo[orig_win].scrollbind = true
	vim.wo[orig_win].relativenumber = false
	vim.wo[orig_win].statuscolumn = ""
	vim.wo[orig_win].foldenable = false
	vim.cmd("syncbind")
end, { desc = "Side-by-side diff of current file against parent commit" })
vim.keymap.set("n", "<leader>hd", "<CMD>HgDiffSplit<CR>", { desc = "Hg diff split" })
vim.keymap.set("n", "<leader>hs", "<CMD>HgSsl<CR>", { desc = "Hg smartlog" })
vim.keymap.set("n", "<leader>hS", "<CMD>HgSslSplit<CR>", { desc = "Hg smartlog (vsplit)" })

vim.api.nvim_create_user_command("SlPull", function()
	vim.fn.jobstart("sl pull", {
		on_exit = function(_, code)
			if code == 0 then
				vim.notify("sl pull completed", vim.log.levels.INFO)
			else
				vim.notify("sl pull failed (exit " .. code .. ")", vim.log.levels.ERROR)
			end
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

