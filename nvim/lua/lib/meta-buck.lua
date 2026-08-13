local M = {}

local repo_context = require("lib.repo-context")

local function current_file(path)
	if path and path ~= "" then
		return vim.fn.fnamemodify(path, ":p")
	end
	path = vim.api.nvim_buf_get_name(0)
	if path == "" then
		vim.notify("No file in current buffer", vim.log.levels.WARN)
		return nil
	end
	return vim.fn.fnamemodify(path, ":p")
end

local function run(cmd, cwd, no_coro)
	if no_coro then
		return vim.system(cmd, { cwd = cwd, text = true }):wait()
	end

	local result
	local coro = require("meta.util").get_coroutine()
	vim.system(cmd, { cwd = cwd, text = true }, function(obj)
		result = obj
		vim.schedule(function()
			coroutine.resume(coro)
		end)
	end)
	coroutine.yield()
	return result
end

local function decode_query(result, cmd)
	if result.code ~= 0 then
		vim.notify(
			string.format("[meta.util.buck] cmd (%q) failed:\n%s", table.concat(cmd, " "), result.stderr or ""),
			vim.log.levels.WARN
		)
		return nil
	end

	local ok, decoded = pcall(vim.json.decode, result.stdout or "")
	if not ok then
		vim.notify("Could not decode Buck query output", vim.log.levels.WARN)
		return nil
	end
	return decoded
end

local function add_runnable_base_targets(targets, cwd, no_coro, options)
	local bases = {}
	for target in pairs(targets) do
		if target:match("%-library$") then
			table.insert(bases, target:gsub("%-library$", ""))
		else
			return targets
		end
	end

	for _, target in ipairs(bases) do
		local cmd = { "buck2" }
		if options.use_isolation_dir then
			table.insert(cmd, "--isolation-dir=" .. (options.isolation_dir or "neovim"))
		end
		vim.list_extend(cmd, { "uquery", target, "--json", "-a", "buck.type" })
		local decoded = decode_query(run(cmd, cwd, no_coro), cmd)
		if decoded and decoded[target] then
			targets[target] = decoded[target]
		end
	end
	return targets
end

local function get_owning_targets(path, kinds, no_coro, opts)
	local options = opts or {}
	if type(no_coro) == "table" then
		options = no_coro
		no_coro = options.no_coro
	end

	path = current_file(path)
	if not path then
		return {}
	end
	local cwd = repo_context.buck_root(path)
	if not cwd then
		vim.notify("File is not in a Buck project", vim.log.levels.WARN)
		return nil
	end

	local query = "attrregexfilter(srcs, .*, owner('" .. path .. "'))"
	if kinds then
		query = string.format("kind('%s', %s)", table.concat(kinds, "|"), query)
	end

	local cmd = { "buck2" }
	if options.use_isolation_dir then
		table.insert(cmd, "--isolation-dir=" .. (options.isolation_dir or "neovim"))
	end
	vim.list_extend(cmd, { "uquery", query, "--json", "-a", "buck.type" })

	local decoded = decode_query(run(cmd, cwd, no_coro), cmd)
	if not decoded or vim.tbl_isempty(decoded) then
		return nil
	end
	return add_runnable_base_targets(decoded, cwd, no_coro, options)
end

local function get_buck_root(kind, no_coro)
	local cwd = repo_context.buck_root()
	if not cwd then
		return nil, "Not in a Buck project"
	end
	local cmd = { "buck2", "root", "-k", kind or "cell" }
	local result = run(cmd, cwd, no_coro)
	if result.code == 0 then
		return vim.trim(result.stdout or ""), nil
	end
	return nil, string.format("cmd (%q) failed:\n%s", table.concat(cmd, " "), result.stderr or "")
end

local function is_buck_command(cmd)
	if type(cmd) ~= "table" or type(cmd[1]) ~= "string" then
		return false
	end
	local executable = vim.fs.basename(cmd[1])
	return executable == "buck" or executable == "buck2"
end

function M.setup()
	local buck = require("meta.util.buck")
	buck.get_owning_targets = get_owning_targets
	buck.get_buck_root = get_buck_root
	buck.get_root_path_with_cli = function()
		local root = get_buck_root("project", true)
		return root
	end

	local terminal = require("meta.util.terminal")
	if not terminal._repo_context_run_command then
		terminal._repo_context_run_command = terminal.run_command
		terminal.run_command = function(cmd, opts)
			local cwd = is_buck_command(cmd) and repo_context.buck_root() or nil
			if cwd then
				local scoped = { "cd", vim.fn.shellescape(cwd), "&&" }
				vim.list_extend(scoped, cmd)
				cmd = scoped
			end
			return terminal._repo_context_run_command(cmd, opts)
		end
	end

	if not M._io_popen then
		M._io_popen = io.popen
		io.popen = function(command, mode)
			if type(command) == "string" and command:match("^%s*buck2%s+log%s+show") then
				local cwd = repo_context.buck_root()
				if cwd then
					-- HACK: meta.buck does not expose a cwd for its post-test log query.
					command = "cd " .. vim.fn.shellescape(cwd) .. " && " .. command
				end
			end
			return M._io_popen(command, mode)
		end
	end
end

return M
