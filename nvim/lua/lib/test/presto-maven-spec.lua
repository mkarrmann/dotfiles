-- Headless test for lib.presto-maven.
-- nvim --headless -u NONE --cmd "set rtp+=$HOME/dotfiles/nvim" -c "lua require('lib.test.presto-maven-spec').run()" -c "qa!"

local M = {}

local function assert_eq(actual, expected, label)
	if actual ~= expected then
		error(string.format("%s: expected %s, got %s", label, vim.inspect(expected), vim.inspect(actual)))
	end
end

function M.run()
	local orig = { termopen = vim.fn.termopen, jobstart = vim.fn.jobstart }
	local ok, err = xpcall(function()
		local calls = {}
		vim.fn.termopen = function(cmd, opts)
			table.insert(calls, { fn = "termopen", cmd = cmd, opts = opts })
			return 1
		end
		vim.fn.jobstart = function(cmd, opts)
			table.insert(calls, { fn = "jobstart", cmd = cmd, opts = opts })
			return 1
		end

		package.loaded["lib.presto-maven"] = nil
		local maven = require("lib.presto-maven")
		vim.api.nvim_buf_set_name(0, "/elsewhere/fbcode/github/presto-trunk/Foo.java")
		maven.full()

		assert_eq(#calls, 1, "one terminal job")
		local cmd = calls[1].cmd
		assert_eq(type(cmd), "string", "shell command")
		local cd = cmd:find("cd '[^']*/presto%-trunk'")
		local prefetch = cmd:find("eden prefetch", 1, true)
		assert_eq(cd ~= nil, true, "cds into the repo being built")
		assert_eq(prefetch ~= nil, true, "prefetches")
		assert_eq(cd < prefetch, true, "cds before prefetching, so eden resolves the built checkout")
		assert_eq(cmd:find("(eden prefetch", 1, true) ~= nil, true, "a failed cd is not masked by the prefetch's || true")
	end, debug.traceback)

	vim.fn.termopen = orig.termopen
	vim.fn.jobstart = orig.jobstart
	if not ok then
		io.stderr:write(err .. "\n")
		vim.cmd.cquit(1)
	end
	print("presto-maven-spec: all checks passed")
end

return M
