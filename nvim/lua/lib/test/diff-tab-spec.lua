-- Headless smoke test for lib.diff-tab engine.
-- nvim --headless -u NONE --cmd "set rtp+=$HOME/dotfiles/nvim" -c "lua require('lib.test.diff-tab-spec').run()" -c "qa!"

local M = {}

local function assert_eq(actual, expected, label)
	if actual ~= expected then
		error(string.format("%s: expected %s, got %s", label, vim.inspect(expected), vim.inspect(actual)))
	end
end

local function assert_list_eq(actual, expected, label)
	if type(actual) ~= "table" then
		error(string.format("%s: expected list, got %s", label, vim.inspect(actual)))
	end
	if #actual ~= #expected then
		error(string.format(
			"%s: expected length %d, got %d (%s vs %s)",
			label,
			#expected,
			#actual,
			vim.inspect(expected),
			vim.inspect(actual)
		))
	end
	for i = 1, #expected do
		if actual[i] ~= expected[i] then
			error(string.format(
				"%s[%d]: expected %s, got %s",
				label,
				i,
				vim.inspect(expected[i]),
				vim.inspect(actual[i])
			))
		end
	end
end

local function file_list_for(state)
	return state.mode == "turn" and state.turn_files or state.files
end

function M.run()
	local diff_tab = require("lib.diff-tab")
	local mgr = diff_tab.new({
		name = "test_diff",
		tab_var = "test_diff_session_id",
	})

	assert_eq(mgr.opts.diff_tab_var, "test_diff_session", "diff_tab_var default")

	mgr:add_file("sess1", "/tmp/test1.lua", {
		after_lines = { "a", "b" },
		turn_before_lines = { "a" },
		session_before_lines = {},
	})
	mgr:add_file("sess1", "/tmp/test2.lua", {
		after_lines = { "x", "y" },
		turn_before_lines = {},
		session_before_lines = {},
	})

	local state = mgr:get_state("sess1")
	assert_eq(state.mode, "turn", "default mode")
	assert_list_eq(
		state.files,
		{ "/tmp/test1.lua", "/tmp/test2.lua" },
		"session file list after two add_file"
	)
	assert_list_eq(
		state.turn_files,
		{ "/tmp/test1.lua", "/tmp/test2.lua" },
		"turn file list after two add_file"
	)
	assert_list_eq(
		file_list_for(state),
		{ "/tmp/test1.lua", "/tmp/test2.lua" },
		"file_list in turn mode"
	)

	state.mode = "session"
	assert_list_eq(
		file_list_for(state),
		{ "/tmp/test1.lua", "/tmp/test2.lua" },
		"file_list in session mode"
	)
	state.mode = "turn"

	mgr:new_turn("sess1")
	vim.wait(200, function()
		local s = mgr._sessions["sess1"]
		return s ~= nil and #s.turn_files == 0
	end)

	state = mgr._sessions["sess1"]
	if not state then
		error("session 'sess1' missing after new_turn")
	end
	assert_list_eq(state.turn_files, {}, "turn_files reset after new_turn")
	assert_list_eq(
		state.files,
		{ "/tmp/test1.lua", "/tmp/test2.lua" },
		"session files preserved after new_turn"
	)
	for path, data in pairs(state.file_data) do
		if data.turn_buf ~= nil then
			error(string.format("turn_buf for %s should be nil after new_turn, got %s", path, vim.inspect(data.turn_buf)))
		end
	end

	mgr:cleanup("sess1")
	vim.wait(200, function()
		return mgr._sessions["sess1"] == nil
	end)

	if mgr._sessions["sess1"] ~= nil then
		error("cleanup did not drop session; _sessions['sess1'] = " .. vim.inspect(mgr._sessions["sess1"]))
	end
end

return M
