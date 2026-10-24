-- Headless test for local/config/meta.lua on a machine without meta.nvim.
-- nvim --headless -u NONE --cmd "set rtp+=$HOME/dotfiles/nvim" -c "lua require('lib.test.local-meta-config-spec').run()" -c "qa!"

local M = {}

local function assert_eq(actual, expected, label)
	if actual ~= expected then
		error(string.format("%s: expected %s, got %s", label, vim.inspect(expected), vim.inspect(actual)))
	end
end

function M.run()
	local spec = vim.uv.fs_realpath(debug.getinfo(1, "S").source:sub(2))
	local root = vim.fs.dirname(vim.fs.dirname(vim.fs.dirname(vim.fs.dirname(spec))))
	dofile(root .. "/local/config/meta.lua")
	vim.api.nvim_exec_autocmds("User", { pattern = "VeryLazy" })

	assert_eq((pcall(require, "lib.meta-hg")), false, "lib.meta-hg can't load without meta.nvim")
	for _, lhs in ipairs({ "<leader>hb", "<leader>hd", "<leader>hD", "<leader>hs" }) do
		assert_eq(vim.fn.maparg(lhs, "n"), "", lhs .. " not mapped to a missing command")
	end
	assert_eq(vim.fn.exists(":HgDiffSplit"), 0, ":HgDiffSplit not defined")
	assert_eq(vim.fn.exists(":SlPull"), 2, ":SlPull still defined")
	print("local-meta-config-spec: all checks passed")
end

return M
