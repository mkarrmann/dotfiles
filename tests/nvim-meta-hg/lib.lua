-- Helpers for the full-config nvim meta-hg specs. Loaded by each spec with
-- dofile(os.getenv("NVIM_META_HG_LIB")); tests/test-nvim-meta-hg.sh sets the
-- environment. A spec is a file that calls H.run(function() ... end).

local H = {}

H.suite = assert(os.getenv("NVIM_META_HG_SUITE"), "run specs through tests/test-nvim-meta-hg.sh")
H.tmp = assert(os.getenv("NVIM_META_HG_TMP"))
H.failures = 0

local repo_count = 0

---Builds a throwaway repo (see make-repo.sh) and returns its root and named revisions.
---@param kind "diff"|"plain"
---@return string root
---@return table<string, string> revs
function H.repo(kind)
	repo_count = repo_count + 1
	local root = string.format("%s/repo-%s-%d", H.tmp, kind, repo_count)
	local res = vim.system({ H.suite .. "/make-repo.sh", root, kind }, { text = true }):wait()
	assert(res.code == 0, "make-repo.sh failed: " .. (res.stderr or ""))
	local revs = {}
	for name, hash in res.stdout:gmatch("(%w+)=(%x+)") do
		revs[name] = hash
	end
	return root, revs
end

---Runs a command synchronously in `cwd` and returns stdout, asserting success.
function H.sh(cmd, cwd)
	local res = vim.system(cmd, { cwd = cwd, text = true, env = { HGUSER = "test" } }):wait()
	assert(res.code == 0, table.concat(cmd, " ") .. " failed: " .. (res.stderr or ""))
	return res.stdout
end

function H.write(path, text)
	local f = assert(io.open(path, "wb"))
	f:write(text)
	f:close()
end

function H.read(path)
	local f = assert(io.open(path, "rb"))
	local text = f:read("*a")
	f:close()
	return text
end

---Puts a pass-through `hg` first on PATH that can misbehave per repo:
---H.slow delays the output of the next run of a subcommand, H.rewrite edits
---the output of every run of one, H.fail makes every run of one fail.
function H.fake_hg()
	local bin = H.tmp .. "/bin"
	vim.fn.mkdir(bin, "p")
	H.write(bin .. "/hg", table.concat({
		"#!/bin/sh",
		"real=" .. vim.fn.exepath("hg"),
		'dir="$(pwd -P)/.hg"',
		'slow="$dir/slow-$1"',
		'script="$dir/sed-$1"',
		'if [ -f "$dir/fail-$1" ]; then cat "$dir/fail-$1" >&2; exit 255; fi',
		'[ -f "$slow" ] || [ -f "$script" ] || exec "$real" "$@"',
		-- Run first and delay the output, so a slow run reports the state it read.
		'out=$("$real" "$@"); status=$?',
		'if [ -f "$slow" ]; then delay=$(cat "$slow"); rm -f "$slow"; sleep "$delay"; fi',
		"if [ -f \"$script\" ]; then out=$(printf '%s\\n' \"$out\" | sed -f \"$script\"); fi",
		"printf '%s\\n' \"$out\"",
		"exit $status",
		"",
	}, "\n"))
	vim.uv.fs_chmod(bin .. "/hg", 493)
	vim.env.PATH = bin .. ":" .. vim.env.PATH
end

function H.slow(root, sub, seconds)
	H.write(vim.uv.fs_realpath(root) .. "/.hg/slow-" .. sub, tostring(seconds))
end

function H.fail(root, sub, stderr)
	H.write(vim.uv.fs_realpath(root) .. "/.hg/fail-" .. sub, stderr .. "\n")
end

function H.rewrite(root, sub, sed_script)
	H.write(vim.uv.fs_realpath(root) .. "/.hg/sed-" .. sub, sed_script .. "\n")
end

function H.check(cond, msg)
	if not cond then
		H.failures = H.failures + 1
	end
	io.stdout:write((cond and "PASS " or "FAIL ") .. msg .. "\n")
end

function H.eq(actual, expected, msg)
	H.check(vim.deep_equal(actual, expected), string.format("%s (expected %s, got %s)", msg, vim.inspect(expected), vim.inspect(actual)))
end

function H.lines(buf)
	return table.concat(vim.api.nvim_buf_get_lines(buf, 0, -1, false), "\n")
end

---Non-floating windows of a tab; notifications open floating ones.
function H.wins(tab)
	return vim.tbl_filter(function(w)
		return vim.api.nvim_win_get_config(w).relative == ""
	end, vim.api.nvim_tabpage_list_wins(tab or 0))
end

function H.session()
	return require("lib.diff-session").sessions[vim.api.nvim_get_current_tabpage()]
end

function H.pair(session, file)
	for i, p in ipairs(session.pairs) do
		if p.file == file then
			return p, i
		end
	end
end

function H.files(session)
	return table.concat(
		vim.tbl_map(function(p)
			return p.file .. (p.old_file and ("<" .. p.old_file) or "")
		end, session.pairs),
		","
	)
end

---Messages passed to vim.notify since the spec started, newest last.
H.notifications = {}

function H.run(fn)
	-- Headless nvim never fires VeryLazy, and lib.meta-hg loads on it.
	vim.api.nvim_exec_autocmds("User", { pattern = "VeryLazy" })
	vim.wait(300)
	local notify = vim.notify
	vim.notify = function(msg, level, opts)
		table.insert(H.notifications, { msg = msg, level = level })
		return notify(msg, level, opts)
	end
	local ok, err = xpcall(fn, debug.traceback)
	if not ok then
		H.failures = H.failures + 1
		io.stdout:write("ERROR " .. tostring(err) .. "\n")
	end
	io.stdout:write(H.failures == 0 and "ALL PASSED\n" or (H.failures .. " FAILED\n"))
	io.stdout:flush()
	vim.cmd(H.failures == 0 and "qa!" or "cq!")
end

return H
