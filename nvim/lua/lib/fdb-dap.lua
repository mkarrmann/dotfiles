local M = {}

local ENV_SKIP_PREFIXES = {
	"TMUX", "OTEL_", "CLAUDE_", "ANTHROPIC_", "CODEX_", "META_3PAI",
	"BUCK2_CLIENT", "FAST_MUX", "MCP_TIMEOUT", "ENABLE_LSP_TOOL",
	"ENABLE_AGENTS", "ENABLE_ENHANCED", "DISABLE_COST", "DISABLE_AUTO",
	"LINTTOOL_CALLER", "CLIENT_LOG_SESSION", "LOG_SESSION",
}

local DEBUGPY_DOTSLASH = vim.fn.expand("~/fbsource/fbcode/sand/python_debugging/adapter/dotslash/debugpy_adapter")

local function should_keep_env(key)
	for _, prefix in ipairs(ENV_SKIP_PREFIXES) do
		if key:sub(1, #prefix) == prefix then
			return false
		end
	end
	return true
end

local function find_json_in_output(text)
	for line in text:gmatch("[^\n]+") do
		if line:match('^{"spawn_dap_config"') then
			return line
		end
	end
	return nil
end

local function extract_error(text)
	for line in text:gmatch("[^\n]+") do
		if line:match("^Error:") or line:match("^Command failed:") then
			return line
		end
	end
	return text:sub(-500)
end

local function unwrap_python_test_runner(program, args)
	if not program:match("python") then
		return program, args
	end
	for i, arg in ipairs(args) do
		if arg == "--" and args[i + 1] then
			return args[i + 1], {}
		end
	end
	return program, args
end

local function filter_env_array(env_array)
	local env = {}
	for _, entry in ipairs(env_array) do
		local key, val = entry:match("^([^=]+)=(.*)")
		if key and should_keep_env(key) then
			env[key] = val
		end
	end
	return env
end

local function filter_env_dict(env_dict)
	local env = {}
	for key, val in pairs(env_dict) do
		if should_keep_env(key) then
			env[key] = val
		end
	end
	return env
end

local function resolve_dap_adapter(spawn_config)
	local cmd = spawn_config.cmd
	if cmd:match("debugpy") then
		if vim.fn.filereadable(cmd) == 0 then
			cmd = DEBUGPY_DOTSLASH
		end
	end
	return cmd, spawn_config.args or {}
end

local function parse_fdb_dap_config(json_str)
	local ok, config = pcall(vim.json.decode, json_str)
	if not ok then
		return nil, "Failed to parse fdb JSON output"
	end

	local debug_request = config.debug_request
	if not debug_request then
		return nil, "No debug_request in fdb output"
	end

	local spawn_config = config.spawn_dap_config
	if not spawn_config then
		return nil, "No spawn_dap_config in fdb output"
	end

	local adapter_cmd, adapter_args = resolve_dap_adapter(spawn_config)
	local is_python = adapter_cmd:match("debugpy")

	local source_map = {}
	if debug_request.sourceMap then
		for _, mapping in ipairs(debug_request.sourceMap) do
			table.insert(source_map, { mapping[1], mapping[2] })
		end
	end

	local env
	if debug_request.env then
		if vim.islist(debug_request.env) then
			env = filter_env_array(debug_request.env)
		else
			env = filter_env_dict(debug_request.env)
		end
	else
		env = {}
	end

	local program = debug_request.program
	local args = debug_request.args or {}
	if not is_python then
		program, args = unwrap_python_test_runner(program, args)
	end

	local adapter_name = is_python and "debugpy" or "lldb-dap"
	local dap = require("dap")
	dap.adapters[adapter_name] = {
		type = "executable",
		command = adapter_cmd,
		args = adapter_args,
	}

	local dap_config = {
		type = adapter_name,
		request = debug_request.request or "launch",
		name = debug_request.name or "fdb debug",
		program = program,
		args = args,
		cwd = debug_request.cwd,
		env = env,
		stopOnEntry = false,
	}

	if is_python then
		dap_config.console = debug_request.console or "internalConsole"
		dap_config.python = debug_request.python
	else
		dap_config.sourceMap = source_map
		dap_config.initCommands = debug_request.initCommands or {}
		dap_config.preRunCommands = debug_request.preRunCommands or {}
		dap_config.postRunCommands = debug_request.postRunCommands or {}
	end

	return dap_config
end

local function read_file(path)
	local f = io.open(path, "r")
	if not f then return nil end
	local content = f:read("*a")
	f:close()
	return content
end

local function run_fdb(buck_cmd, target, mode)
	local dap = require("dap")

	local label = mode ~= "" and (target .. " (" .. mode .. ")") or target
	vim.notify("fdb: building " .. label .. "...", vim.log.levels.INFO)

	local outfile = os.tmpname()
	local mode_arg = mode ~= "" and (mode .. " ") or ""
	local shell_cmd = string.format(
		"cd %s && fdb --dry-debug --launch-mode=dapconfig buck2 %s %s%s < /dev/null > %s 2>&1",
		vim.fn.shellescape(vim.fn.expand("~/fbsource/fbcode")),
		buck_cmd,
		mode_arg,
		vim.fn.shellescape(target),
		vim.fn.shellescape(outfile)
	)

	vim.system({ "sh", "-c", shell_cmd }, { text = true }, function()
		vim.schedule(function()
			local output = read_file(outfile) or ""
			os.remove(outfile)

			local json_line = find_json_in_output(output)
			if not json_line then
				vim.notify("fdb failed: " .. extract_error(output), vim.log.levels.ERROR)
				return
			end

			local config, err = parse_fdb_dap_config(json_line)
			if not config then
				vim.notify("fdb: " .. err, vim.log.levels.ERROR)
				return
			end

			vim.notify("fdb: launching debugger for " .. config.program, vim.log.levels.INFO)
			dap.run(config)
		end)
	end)
end

function M.fdb_debug()
	local target = vim.fn.input("Buck target: ", "fbcode//", "file")
	if target == "" then return end

	local mode = vim.fn.input("Buck mode (empty for none): ")

	run_fdb("run", target, mode)
end

return M
