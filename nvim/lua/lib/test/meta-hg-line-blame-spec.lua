-- nvim --headless -u NONE --cmd "set rtp+=$HOME/dotfiles/nvim" -c "lua require('lib.test.meta-hg-line-blame-spec').run()" -c "qa!"

local M = {}

local function assert_eq(actual, expected, label)
	if actual ~= expected then
		error(string.format("%s: expected %s, got %s", label, vim.inspect(expected), vim.inspect(actual)))
	end
end

local function autocmd_count(event, bufnr)
	return #vim.api.nvim_get_autocmds({
		event = event,
		group = "hg_line_blame",
		buffer = bufnr,
	})
end

function M.run()
	local old_meta_util = package.loaded["meta.util"]
	local old_meta_util_hg = package.loaded["meta.util.hg"]
	local old_meta_hg = package.loaded["lib.meta-hg"]
	local old_system = vim.system
	local temp_dir = vim.fn.tempname()

	local ok, err = xpcall(function()
		package.loaded["meta.util"] = {
			log_to_scuba = function() end,
		}
		package.loaded["meta.util.hg"] = {}
		package.loaded["lib.meta-hg"] = nil

		local blame_calls = 0
		vim.system = function(cmd, _, callback)
			if cmd[1] == "hg" and cmd[2] == "blame" then
				blame_calls = blame_calls + 1
			end
			local result = { code = 1, stdout = "", stderr = "" }
			if callback then
				callback(result)
			end
			return {
				wait = function()
					return result
				end,
			}
		end

		vim.fn.mkdir(temp_dir .. "/.hg", "p")
		local filename = temp_dir .. "/example.py"
		vim.fn.writefile({ "value = 1" }, filename)

		local meta_hg = require("lib.meta-hg")
		meta_hg.setup()

		local bufnr = vim.api.nvim_create_buf(true, false)
		vim.api.nvim_buf_set_name(bufnr, filename)
		vim.api.nvim_set_current_buf(bufnr)

		local blame_calls_before_refreshes = blame_calls
		for _ = 1, 3 do
			for _, event in ipairs({ "BufEnter", "BufWritePost", "FileChangedShellPost" }) do
				vim.api.nvim_exec_autocmds(event, { buffer = bufnr })
			end
		end

		assert_eq(autocmd_count("CursorMoved", bufnr), 1, "CursorMoved handler count")
		assert_eq(autocmd_count("ModeChanged", bufnr), 1, "ModeChanged handler count")
		assert_eq(blame_calls - blame_calls_before_refreshes, 9, "blame cache refresh count")
	end, debug.traceback)

	vim.system = old_system
	package.loaded["meta.util"] = old_meta_util
	package.loaded["meta.util.hg"] = old_meta_util_hg
	package.loaded["lib.meta-hg"] = old_meta_hg
	vim.fn.delete(temp_dir, "rf")

	if not ok then
		error(err)
	end
	print("meta-hg-line-blame-spec: all checks passed")
end

return M
