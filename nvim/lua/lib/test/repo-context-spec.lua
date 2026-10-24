-- Headless test for lib.repo-context: one nvim session per workspace.
-- nvim --headless -u NONE --cmd "set rtp+=$HOME/dotfiles/nvim" -c "lua require('lib.test.repo-context-spec').run()" -c "qa!"
--
-- Each nvs session runs in a workspace root (~/checkoutN) holding that
-- workspace's fbsource and configerator. The repo picker offers only those,
-- and a file buffer's own repo always wins.

local M = {}

local function assert_eq(actual, expected, label)
	if not vim.deep_equal(actual, expected) then
		error(string.format("%s: expected %s, got %s", label, vim.inspect(expected), vim.inspect(actual)))
	end
end

function M.run()
	local home = vim.fn.tempname()
	for _, repo in ipairs({ "checkout1/fbsource", "checkout1/configerator", "checkout2/fbsource" }) do
		vim.fn.mkdir(home .. "/" .. repo .. "/.hg", "p")
	end
	vim.fn.writefile({ "x" }, home .. "/checkout1/fbsource/a.py")
	local repo_context = require("lib.repo-context")
	local ok, err = pcall(function()
		vim.cmd.cd(home .. "/checkout1")
		local expected = { home .. "/checkout1/configerator", home .. "/checkout1/fbsource" }
		assert_eq(repo_context.candidates(), expected, "picker offers only this workspace's repos")
		vim.cmd.cd(home .. "/checkout1/fbsource")
		assert_eq(repo_context.candidates(), expected, "same from inside one of them")

		repo_context.remember(home .. "/checkout1/configerator")
		vim.cmd.edit(home .. "/checkout1/fbsource/a.py")
		assert_eq(repo_context.current().repo_root, home .. "/checkout1/fbsource", "the buffer's repo wins")
	end)
	vim.fn.delete(home, "rf")
	if not ok then
		error(err)
	end
	print("repo-context-spec: all checks passed")
end

return M
