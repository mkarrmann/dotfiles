-- Headless test for lib.presto-maven.
-- nvim --headless -u NONE --cmd "set rtp+=$HOME/dotfiles/nvim" -c "lua require('lib.test.presto-maven-spec').run()" -c "qa!"

local M = {}

local function assert_eq(actual, expected, label)
	if actual ~= expected then
		error(string.format("%s: expected %s, got %s", label, vim.inspect(expected), vim.inspect(actual)))
	end
end

function M.run()
	local orig = { termopen = vim.fn.termopen, jobstart = vim.fn.jobstart, home = vim.env.HOME, build_root = vim.env.BUILD_ROOT }
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
		assert_eq(calls[1].fn, "jobstart", "uses jobstart, not the deprecated termopen")
		assert_eq(calls[1].opts.term, true, "runs in a terminal")
		assert_eq(type(calls[1].opts.on_exit), "function", "still reports the exit status")
		local cmd = calls[1].cmd
		assert_eq(type(cmd), "string", "shell command")
		local cd = cmd:find("cd '[^']*/presto%-trunk'")
		local prefetch = cmd:find("eden prefetch", 1, true)
		assert_eq(cd ~= nil, true, "cds into the repo being built")
		assert_eq(prefetch ~= nil, true, "prefetches")
		assert_eq(cd < prefetch, true, "cds before prefetching, so eden resolves the built checkout")
		assert_eq(cmd:find("(eden prefetch", 1, true) ~= nil, true, "a failed cd is not masked by the prefetch's || true")

		-- Builds stay in the file's workspace, with that workspace's build root
		-- and Maven repository, like ~/.localrc's mpi/mfi.
		local home = vim.fn.tempname()
		vim.env.HOME = home
		vim.env.BUILD_ROOT = "/builds"
		package.loaded["lib.checkout"] = nil
		package.loaded["lib.presto-maven"] = nil
		maven = require("lib.presto-maven")
		local function module(dir)
			vim.fn.mkdir(dir, "p")
			vim.fn.writefile({}, dir .. "/pom.xml")
		end
		local function build(path)
			calls = {}
			vim.api.nvim_buf_set_name(0, path)
			maven.install()
			assert_eq(#calls, 1, "one build for " .. path)
			return calls[1].cmd
		end
		module(home .. "/checkout2/fbsource/fbcode/github/presto-facebook-trunk/presto-foo")
		cmd = build(home .. "/checkout2/fbsource/fbcode/github/presto-facebook-trunk/presto-foo/Foo.java")
		assert_eq(cmd:find("cd '" .. home .. "/checkout2/fbsource/fbcode/github/presto-facebook-trunk'", 1, true) ~= nil, true, "cds into checkout2's repo: " .. cmd)
		assert_eq(cmd:find("-pl presto-foo", 1, true) ~= nil, true, "builds the file's module")
		assert_eq(cmd:find("-Dout-of-tree-build-root='/builds/presto-facebook-trunk-checkout2'", 1, true) ~= nil, true, "checkout2's build root")
		assert_eq(cmd:find("-Dmaven.repo.local='/builds/m2-repo-checkout2'", 1, true) ~= nil, true, "checkout2's Maven repository")

		module(home .. "/checkout1/fbsource/fbcode/github/presto-trunk/presto-main")
		cmd = build(home .. "/checkout1/fbsource/fbcode/github/presto-trunk/presto-main/Foo.java")
		assert_eq(cmd:find("cd '" .. home .. "/checkout1/fbsource/fbcode/github/presto-trunk'", 1, true) ~= nil, true, "cds into checkout1's repo")
		assert_eq(cmd:find("-Dout-of-tree-build-root='/builds/presto-trunk'", 1, true) ~= nil, true, "the primary build root")
		assert_eq(cmd:find("maven.repo.local", 1, true), nil, "the default Maven repository for checkout1")
	end, debug.traceback)

	vim.fn.termopen = orig.termopen
	vim.fn.jobstart = orig.jobstart
	vim.env.HOME = orig.home
	vim.env.BUILD_ROOT = orig.build_root
	if not ok then
		io.stderr:write(err .. "\n")
		vim.cmd.cquit(1)
	end
	print("presto-maven-spec: all checks passed")
end

return M
