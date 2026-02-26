local M = {}

local BUILD_ROOT = vim.env.BUILD_ROOT or ("/data/users/" .. vim.env.USER .. "/builds")
local DEV_HOME = vim.uv.fs_realpath(vim.fn.expand("~/fbsource/fbcode/github")) or vim.fn.expand("~/fbsource/fbcode/github")

local COMMON = table.concat({
	"-Dmaven.gitcommitid.skip=true",
	"-Dlicense.report.skip=true",
	"-Djava.net.preferIPv6Addresses=true",
	"-DskipUI",
	"-Dos.detected.name=linux",
	"-Dos.detected.arch=x86_64",
	"-Dos.detected.classifier=linux-x86_64",
	"-Dout-of-tree-build=true",
	"-T 48",
}, " ")

local REPO_FLAGS = {
	oss = COMMON .. " -Dmaven.javadoc.skip=true"
		.. " -Dout-of-tree-build-root=" .. BUILD_ROOT .. "/presto-trunk",
	fb = COMMON .. " -DuseParallelDependencyResolution=false -nsu -DwithPlugins=true"
		.. " -Dout-of-tree-build-root=" .. BUILD_ROOT .. "/presto-facebook-trunk",
}

local function detect_repo(path)
	if path:find("presto%-facebook%-trunk") then
		return "fb", DEV_HOME .. "/presto-facebook-trunk"
	elseif path:find("presto%-trunk") then
		return "oss", DEV_HOME .. "/presto-trunk"
	end
end

local function detect_module(path, root)
	if not root then return nil end
	local rel = path:sub(#root + 2)
	local mod = rel:match("^([^/]+)")
	if mod and vim.uv.fs_stat(root .. "/" .. mod .. "/pom.xml") then
		return mod
	end
end

local build_bufnr

local function run(shell_cmd, title)
	if build_bufnr and vim.api.nvim_buf_is_valid(build_bufnr) then
		for _, w in ipairs(vim.fn.win_findbuf(build_bufnr)) do
			vim.api.nvim_win_close(w, true)
		end
		vim.api.nvim_buf_delete(build_bufnr, { force = true })
	end

	vim.cmd("botright new | resize 15")
	build_bufnr = vim.api.nvim_get_current_buf()

	vim.fn.termopen(shell_cmd, {
		on_exit = function(_, code)
			vim.schedule(function()
				local level = code == 0 and vim.log.levels.INFO or vim.log.levels.ERROR
				local status = code == 0 and "succeeded" or ("failed (exit " .. code .. ")")
				vim.notify("Maven: " .. title .. " " .. status, level)
			end)
		end,
	})
end

local function mvn_cmd(repo_type, root, goal, modules, opts)
	opts = opts or {}
	local parts = {
		"mkdir -p " .. vim.fn.shellescape(BUILD_ROOT .. "/presto-trunk")
			.. " " .. vim.fn.shellescape(BUILD_ROOT .. "/presto-facebook-trunk"),
		"eden prefetch 'fbcode/github/presto-facebook-trunk/**' 'fbcode/github/presto-trunk/**' 2>/dev/null || true",
		"cd " .. vim.fn.shellescape(root),
	}

	local mvn = "mvn"
	if not opts.incremental then mvn = mvn .. " clean" end
	mvn = mvn .. " " .. goal

	if goal == "test" then
		mvn = mvn .. " -P ci"
	elseif not goal:find("checkstyle") then
		mvn = mvn .. " -DskipTests"
	end

	if modules then
		mvn = mvn .. " -pl " .. modules .. " -am"
	end

	mvn = mvn .. " " .. REPO_FLAGS[repo_type]

	if opts.skip_checkstyle then
		mvn = mvn .. " -Dair.check.skip-all"
	end

	table.insert(parts, mvn)
	return table.concat(parts, " && ")
end

local function with_repo_and_module(callback)
	local fname = vim.api.nvim_buf_get_name(0)
	local repo_type, root = detect_repo(fname)
	if not repo_type then
		vim.notify("Not in a Presto repo", vim.log.levels.ERROR)
		return
	end
	local mod = detect_module(fname, root)
	if not mod then
		vim.notify("Cannot detect module from current file", vim.log.levels.ERROR)
		return
	end
	callback(repo_type, root, mod)
end

function M.install(opts)
	opts = opts or {}
	with_repo_and_module(function(repo_type, root, mod)
		run(mvn_cmd(repo_type, root, "install", mod, opts), "install " .. mod)
	end)
end

function M.test(opts)
	opts = opts or {}
	with_repo_and_module(function(repo_type, root, mod)
		run(mvn_cmd(repo_type, root, "test", mod, opts), "test " .. mod)
	end)
end

function M.full(opts)
	opts = opts or {}
	local fname = vim.api.nvim_buf_get_name(0)
	local repo_type, root = detect_repo(fname)
	if not repo_type then
		vim.notify("Not in a Presto repo", vim.log.levels.ERROR)
		return
	end
	run(mvn_cmd(repo_type, root, "install", nil, opts), "full build (" .. repo_type .. ")")
end

function M.checkstyle()
	with_repo_and_module(function(repo_type, root, mod)
		run(mvn_cmd(repo_type, root, "checkstyle:checkstyle", mod, {}), "checkstyle " .. mod)
	end)
end

function M.close()
	if build_bufnr and vim.api.nvim_buf_is_valid(build_bufnr) then
		for _, w in ipairs(vim.fn.win_findbuf(build_bufnr)) do
			vim.api.nvim_win_close(w, true)
		end
		vim.api.nvim_buf_delete(build_bufnr, { force = true })
		build_bufnr = nil
	end
end

return M
