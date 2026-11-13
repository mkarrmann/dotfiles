-- Headless test for lib.meta-root against the installed meta.nvim.
-- nvim --headless -u NONE --cmd "set rtp+=$HOME/dotfiles/nvim,/usr/share/fb-editor-support/nvim" -c "lua require('lib.test.meta-root-spec').run()" -c "qa!"

local M = {}

local function assert_eq(actual, expected, label)
	if actual ~= expected then
		error(string.format("%s: expected %s, got %s", label, vim.inspect(expected), vim.inspect(actual)))
	end
end

function M.run()
	local tmp = vim.fn.tempname()
	local ok, err = xpcall(function()
		-- outer/.arcconfig is a real config; outer/inner/.arcconfig is a directory,
		-- the shape every lookup takes under /mnt/gvfs.
		vim.fn.mkdir(tmp .. "/outer/inner/.arcconfig", "p")
		vim.fn.writefile({ '{"project_id": "fbsource"}' }, tmp .. "/outer/.arcconfig")
		local file = tmp .. "/outer/inner/foo.h"

		require("lib.meta-root").setup()
		local arc = require("meta.util.arc")

		local config, root = arc.get_config_and_project_root(file)
		assert_eq(root, tmp .. "/outer", "skips the .arcconfig directory, finds the file above it")
		assert_eq(config.project_id, "fbsource", "reads the real config")

		vim.fn.delete(tmp .. "/outer/.arcconfig")
		assert_eq(arc.get_config_and_project_root(file), nil, "a directory alone is no arc root")
		assert_eq(arc.get_project_root("/"), nil, "stops at /")
		assert_eq(arc.get_project_root(""), nil, "empty buffer name has no root")
	end, debug.traceback)
	vim.fn.delete(tmp, "rf")
	if not ok then
		error(err)
	end
	print("meta-root-spec: all checks passed")
end

return M
