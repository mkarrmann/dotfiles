-- Headless test for lib.repo-context.
-- nvim --headless -u NONE --cmd "set rtp+=$HOME/dotfiles/nvim" -c "lua require('lib.test.repo-context-spec').run()" -c "qa!"

local M = {}

local function assert_eq(actual, expected, label)
	if not vim.deep_equal(actual, expected) then
		error(string.format("%s: expected %s, got %s", label, vim.inspect(expected), vim.inspect(actual)))
	end
end

local function touch(path)
	vim.fn.mkdir(vim.fs.dirname(path), "p")
	vim.fn.writefile({}, path)
end

local function root()
	local context = require("lib.repo-context").current()
	return context and context.repo_root
end

function M.run()
	local orig = { home = vim.env.HOME, select = vim.ui.select, notify = vim.notify, cwd = vim.fn.getcwd() }
	local tmp = vim.fn.tempname()
	local ok, err = xpcall(function()
		vim.fn.mkdir(tmp, "p")
		local home = vim.uv.fs_realpath(tmp)
		local fbsource = home .. "/checkout1/fbsource"
		local configerator = home .. "/checkout1/configerator"
		for _, repo in ipairs({ fbsource, configerator, home .. "/checkout2/fbsource" }) do
			vim.fn.mkdir(repo .. "/.hg", "p")
		end
		vim.fn.mkdir(home .. "/notes", "p")
		touch(home .. "/checkout1.code-workspace")
		assert(vim.uv.fs_symlink(fbsource, home .. "/fbsource"))
		touch(fbsource .. "/a.txt")
		touch(fbsource .. "/a2.txt")
		touch(configerator .. "/b.txt")
		vim.env.HOME = home

		package.loaded["lib.repo-context"] = nil
		local rc = require("lib.repo-context")
		rc.setup()

		local notes = {}
		vim.notify = function(msg)
			table.insert(notes, msg)
		end
		local pick, shown
		vim.ui.select = function(items, opts, on_choice)
			shown = vim.tbl_map(opts.format_item, items)
			on_choice(pick)
		end

		-- Candidates from ~: symlinked repos and ~/checkout*/*, each repo once.
		vim.fn.chdir(home)
		assert_eq(
			rc.candidates(),
			{ configerator, home .. "/checkout2/fbsource", home .. "/fbsource" },
			"candidates from ~"
		)

		-- An explicit pick wins over the focused buffer until another repo's buffer is entered.
		vim.fn.chdir(fbsource)
		vim.cmd.edit(fbsource .. "/a.txt")
		local a = vim.api.nvim_get_current_buf()
		assert_eq(root(), fbsource, "focused buffer's repo")

		pick = configerator
		vim.cmd.MetaRepo()
		assert_eq(notes[#notes], "Active repository: " .. configerator, "pick is announced")
		assert_eq(vim.list_contains(shown, "~/checkout1/configerator"), true, "picker shows paths, not basenames")
		assert_eq(root(), configerator, "pick wins over the focused buffer")
		assert_eq(rc.repo_root(), configerator, "repo_root follows the pick")
		assert_eq(rc.current(a).repo_root, fbsource, "an explicit buffer still resolves to its own repo")
		rc.remember(fbsource)
		assert_eq(root(), configerator, "an implicit remember does not displace the pick")

		vim.cmd.edit(configerator .. "/b.txt")
		assert_eq(root(), configerator, "entering a buffer in the picked repo keeps it")
		vim.cmd.buffer(a)
		assert_eq(root(), fbsource, "entering a buffer in another repo follows it")

		vim.cmd.MetaRepo()
		assert_eq(root(), configerator, "picked again")
		vim.cmd.edit(fbsource .. "/a2.txt")
		assert_eq(root(), fbsource, "opening a file in another repo releases the pick")
	end, debug.traceback)

	vim.env.HOME, vim.ui.select, vim.notify = orig.home, orig.select, orig.notify
	vim.fn.chdir(orig.cwd)
	vim.fn.delete(tmp, "rf")
	if not ok then
		io.stderr:write(err .. "\n")
		vim.cmd.cquit(1)
	end
	print("repo-context-spec: all checks passed")
end

return M
