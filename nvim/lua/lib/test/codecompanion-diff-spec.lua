-- Headless smoke test for lib.codecompanion-diff wrapper.
-- nvim --headless -u NONE --cmd "set rtp+=$HOME/dotfiles/nvim" -c "lua require('lib.test.codecompanion-diff-spec').run()" -c "qa!"

local M = {}

local function assert_callable(value, label)
	if type(value) ~= "function" then
		error(string.format("%s: expected function, got %s", label, type(value)))
	end
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
end

return M
