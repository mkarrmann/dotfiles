-- Headless smoke test for lib.codecompanion-diff wrapper.
-- nvim --headless -u NONE --cmd "set rtp+=$HOME/dotfiles/nvim" -c "lua require('lib.test.codecompanion-diff-spec').run()" -c "qa!"

local M = {}

local function assert_callable(value, label)
	if type(value) ~= "function" then
		error(string.format("%s: expected function, got %s", label, type(value)))
	end
end

local function sys(cmd, cwd)
	local res = vim.system(cmd, { text = true, cwd = cwd }):wait()
	if res.code ~= 0 then
		error("cmd failed: " .. table.concat(cmd, " ") .. "\n" .. (res.stderr or ""))
	end
	return res.stdout
end

local function write_file(path, content)
	local f = assert(io.open(path, "w"))
	f:write(content)
	f:close()
end

-- Scratch buffers created by diff-tab are named "<label>://<abspath>#<n>".
local function find_scratch(label, file)
	for _, b in ipairs(vim.api.nvim_list_bufs()) do
		local n = vim.api.nvim_buf_get_name(b)
		if n:find(label .. "://", 1, true) and n:find(file, 1, true) then
			return b
		end
	end
	return nil
end

local function buf_lines(buf)
	return vim.api.nvim_buf_get_lines(buf, 0, -1, false)
end

-- Exercises the omnigent live-flush path end to end: an edit tool call is only
-- staged, and the *next* tool call flushes it against VCS into the diff split.
local function test_omnigent_live_flush()
	if vim.fn.executable("git") ~= 1 then
		print("SKIP omnigent live flush (git unavailable)")
		return
	end
	local cc_diff = require("lib.codecompanion-diff")
	cc_diff.setup()

	-- Temp git repo, deliberately outside nvim's cwd so the baseline is always the
	-- committed parent (no turn-start snapshot in play) -- deterministic.
	local root = vim.fn.tempname()
	vim.fn.mkdir(root, "p")
	sys({ "git", "init", "-q" }, root)
	local file = root .. "/foo.txt"
	write_file(file, "one\n")
	sys({ "git", "add", "foo.txt" }, root)
	sys({ "git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "init" }, root)

	local chat = vim.api.nvim_create_buf(false, true)
	local function toolcall(name, args_tbl)
		vim.api.nvim_exec_autocmds("User", {
			pattern = "CodeCompanionOmnigentToolCall",
			data = { bufnr = chat, item = { name = name, arguments = vim.json.encode(args_tbl) } },
		})
	end

	vim.api.nvim_exec_autocmds("User", { pattern = "CodeCompanionRequestStarted", data = { bufnr = chat } })

	-- Tool call 1: edit foo.txt (the server-side write has already hit disk).
	write_file(file, "one\ntwo\n")
	toolcall("Edit", { file_path = file })

	-- Only staged so far -- nothing recorded until the next tool call.
	if find_scratch("after", file) ~= nil then
		error("edit was recorded before the next tool call (should only be staged)")
	end

	-- Tool call 2 arrives: call 1's write is now guaranteed landed, so it flushes.
	toolcall("Read", { file_path = root .. "/other.txt" })

	local ok = vim.wait(2000, function()
		return find_scratch("after", file) ~= nil
	end)
	if not ok then
		error("live flush did not record the edit within 2s")
	end

	local after = buf_lines(find_scratch("after", file))
	if not vim.deep_equal(after, { "one", "two", "" }) then
		error("after mismatch: " .. vim.inspect(after))
	end
	local turn_buf = find_scratch("turn-before", file)
	if not turn_buf then
		error("no turn-before buffer recorded")
	end
	local before = buf_lines(turn_buf)
	if not vim.deep_equal(before, { "one", "" }) then
		error("before mismatch: " .. vim.inspect(before))
	end

	-- Turn-end reconciliation re-records the same file; it must not duplicate the
	-- entry or corrupt the baseline (after stays current, before stays committed).
	vim.api.nvim_exec_autocmds("User", { pattern = "CodeCompanionRequestFinished", data = { bufnr = chat } })
	vim.wait(1000)
	local n_after = 0
	for _, b in ipairs(vim.api.nvim_list_bufs()) do
		local nm = vim.api.nvim_buf_get_name(b)
		if nm:find("after://", 1, true) and nm:find(file, 1, true) then
			n_after = n_after + 1
		end
	end
	if n_after ~= 1 then
		error("expected exactly 1 after-buffer for the file, got " .. n_after)
	end
	if not vim.deep_equal(buf_lines(find_scratch("turn-before", file)), { "one", "" }) then
		error("reconciliation corrupted the baseline")
	end

	cc_diff.cleanup(chat)
	vim.fn.delete(root, "rf")
	print("OK omnigent live flush")
end

function M.run()
	local cc_diff = require("lib.codecompanion-diff")

	assert_callable(cc_diff.record_write, "record_write")
	assert_callable(cc_diff.cleanup, "cleanup")
	assert_callable(cc_diff.new_turn, "new_turn")
	assert_callable(cc_diff.toggle, "toggle")
	assert_callable(cc_diff.debug, "debug")
	assert_callable(cc_diff.setup, "setup")

	local ok, err = pcall(cc_diff.record_write, 999, "/tmp/cc-test.lua", { "a" }, { "a", "b" })
	if not ok then
		error("record_write threw: " .. tostring(err))
	end

	local ok2, err2 = pcall(cc_diff.cleanup, 999)
	if not ok2 then
		error("cleanup threw: " .. tostring(err2))
	end

	local ok3, err3 = pcall(cc_diff.record_write, nil, "/tmp/x", nil, nil)
	if not ok3 then
		error("record_write(nil chat_bufnr) threw: " .. tostring(err3))
	end

	local ok4, err4 = pcall(cc_diff.record_write, 999, nil, {}, {})
	if not ok4 then
		error("record_write(nil path) threw: " .. tostring(err4))
	end

	test_omnigent_live_flush()

	print("codecompanion-diff-spec: all checks passed")
end

return M
