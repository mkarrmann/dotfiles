-- Headless test for lib.meta-buck.
-- nvim --headless -u NONE --cmd "set rtp+=$HOME/dotfiles/nvim" -c "lua require('lib.test.meta-buck-spec').run()" -c "qa!"

local M = {}

local function assert_eq(actual, expected, label)
	if not vim.deep_equal(actual, expected) then
		error(string.format("%s: expected %s, got %s", label, vim.inspect(expected), vim.inspect(actual)))
	end
end

function M.run()
	local orig_system = vim.system
	local ok, err = xpcall(function()
		local buck = {}
		package.loaded["meta.util.buck"] = buck
		package.loaded["meta.util.terminal"] = { run_command = function() end }
		package.loaded["lib.repo-context"] = {
			buck_root = function()
				return "/repo"
			end,
		}
		package.loaded["lib.meta-buck"] = nil
		require("lib.meta-buck").setup()

		local queries = {}
		vim.system = function(cmd, opts)
			table.insert(queries, { query = cmd[3], cwd = opts.cwd })
			local stdout = cmd[3] == "fbcode//a:b" and '{"fbcode//a:b":{"buck.type":"python_unittest"}}'
				or '{"fbcode//a:b-library":{"buck.type":"python_library"}}'
			return {
				wait = function()
					return { code = 0, stdout = stdout, stderr = "" }
				end,
			}
		end

		local targets = buck.get_owning_targets("/repo/fbcode/a/b.py", nil, true)
		assert_eq(#queries, 2, "owner query plus one query for the base target")
		assert_eq(queries[2].query, "fbcode//a:b", "base target strips -library")
		assert_eq(queries[2].cwd, "/repo", "base query runs in the Buck root")
		assert_eq(targets["fbcode//a:b"], { ["buck.type"] = "python_unittest" }, "base target is added")
		assert_eq(targets["fbcode//a:b-library"], { ["buck.type"] = "python_library" }, "library target is kept")
	end, debug.traceback)

	vim.system = orig_system
	if not ok then
		io.stderr:write(err .. "\n")
		vim.cmd.cquit(1)
	end
	print("meta-buck-spec: all checks passed")
end

return M
