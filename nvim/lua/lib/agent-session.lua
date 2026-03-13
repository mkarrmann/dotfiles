local M = {}

local LAST_SESSION_FILE = vim.fn.expand("~/.claude-last-session")
local CODEX_INDEX_PATH = vim.fn.expand("~/.codex/agents.tsv")
local UUID_PATTERN = "^%x%x%x%x%x%x%x%x%-%x%x%x%x%-%x%x%x%x%-%x%x%x%x%-%x%x%x%x%x%x%x%x%x%x%x%x$"

local function trim(value)
	return (tostring(value or ""):gsub("^%s+", ""):gsub("%s+$", ""))
end

function M.resolve_vault_root()
	if vim.g.obsidian_vault then
		return vim.g.obsidian_vault
	end
	local conf = vim.fn.expand("~/.claude/obsidian-vault.conf")
	if vim.fn.filereadable(conf) == 1 then
		for _, line in ipairs(vim.fn.readfile(conf)) do
			local val = line:match("^OBSIDIAN_VAULT_ROOT%s*=%s*(.+)$")
			if val then
				val = val:gsub('^"(.*)"$', "%1"):gsub("^'(.*)'$", "%1")
				val = val:gsub("%$HOME", vim.fn.expand("~"))
				local env_default = val:match('^${OBSIDIAN_VAULT:%-(.+)}$')
				if env_default then
					val = vim.env.OBSIDIAN_VAULT
					if not val then
						vim.schedule(function()
							vim.notify(
								"OBSIDIAN_VAULT not set — using default: " .. env_default
									.. "\nSet it in ~/.localrc to point to your vault.",
								vim.log.levels.WARN
							)
						end)
						val = env_default
					end
				end
				return val
			end
		end
	end
	return vim.fn.expand("~/obsidian")
end

local function resolve_agents_dir()
	if vim.env.CLAUDE_AGENTS_FILE then
		return vim.fn.fnamemodify(vim.env.CLAUDE_AGENTS_FILE, ":h")
	end
	local vault = M.resolve_vault_root()
	if vim.fn.isdirectory(vault) == 1 then
		return vault
	end
	return vim.fn.expand("~/.claude")
end

function M.resolve_local_agents_file()
	if vim.env.CLAUDE_AGENTS_FILE then
		return vim.env.CLAUDE_AGENTS_FILE
	end
	local hostname = vim.fn.hostname():match("^([^%.]+)")
	return resolve_agents_dir() .. "/AGENTS-" .. hostname .. ".md"
end

function M.resolve_all_agents_files()
	if vim.env.CLAUDE_AGENTS_FILE then
		local p = vim.env.CLAUDE_AGENTS_FILE
		if vim.fn.filereadable(p) == 1 then
			return { p }
		end
		return {}
	end
	local dir = resolve_agents_dir()
	local files = vim.fn.glob(dir .. "/AGENTS-*.md", true, true)
	local legacy = dir .. "/AGENTS.md"
	if vim.fn.filereadable(legacy) == 1 then
		table.insert(files, legacy)
	end
	return files
end

function M.resolve_agents_file()
	return M.resolve_local_agents_file()
end

function M.get_session_id()
	local g_sid = vim.g.claude_session_id
	if g_sid and g_sid ~= "" then
		return g_sid
	end

	local tmux_pane = vim.env.TMUX_PANE
	if tmux_pane and tmux_pane ~= "" then
		local safe_pane = tmux_pane:gsub("[^%w_]", "_")
		local pane_file = vim.fn.expand("~/.claude/agent-manager/pids/pane-" .. safe_pane)
		local f = io.open(pane_file, "r")
		if f then
			local sid = trim(f:read("*l"))
			f:close()
			if sid ~= "" then
				return sid
			end
		end
	end

	local f = io.open(LAST_SESSION_FILE, "r")
	if not f then
		return nil
	end
	local sid = trim(f:read("*l"))
	f:close()
	return sid ~= "" and sid or nil
end

function M.parse_agents()
	local entries = {}
	for _, agents_file in ipairs(M.resolve_all_agents_files()) do
		local f = io.open(agents_file, "r")
		if f then
			local line_num = 0
			for line in f:lines() do
				line_num = line_num + 1
				if line_num > 4 and line:match("^|") and not line:match("^|%-") then
					local fields = {}
					for field in line:gmatch("|([^|]*)") do
						fields[#fields + 1] = trim(field)
					end
					local name = fields[1] or ""
					local sid = fields[4] or ""
					if name ~= "" and name ~= "Name" and sid ~= "" and sid ~= "Session ID" then
						entries[#entries + 1] = {
							name = name,
							status = fields[2] or "",
							od = fields[3] or "",
							sid = sid,
							description = fields[5] or "",
							started = fields[6] or "",
							updated = fields[7] or "",
							dir = fields[8] or "",
						}
					end
				end
			end
			f:close()
		end
	end
	return entries
end

function M.lookup_agent_by_sid(sid)
	for _, agent in ipairs(M.parse_agents()) do
		if agent.sid == sid then
			return agent
		end
	end
	return nil
end

function M.resolve_cwd_for_sid(sid)
	sid = trim(sid):lower()
	if sid == "" then
		return nil
	end
	local projects_dir = vim.fn.expand("~/.claude/projects")
	local dirs = vim.fn.glob(projects_dir .. "/*", true, true)
	for _, dir in ipairs(dirs) do
		local jsonl = dir .. "/" .. sid .. ".jsonl"
		if vim.fn.filereadable(jsonl) == 1 then
			local f = io.open(jsonl, "r")
			if f then
				local first_line = f:read("*l")
				f:close()
				if first_line then
					local ok, data = pcall(vim.json.decode, first_line)
					if ok and type(data) == "table" and type(data.cwd) == "string" then
						return data.cwd
					end
				end
			end
		end
	end
	return nil
end

function M.get_current_agent()
	local sid = M.get_session_id()
	if not sid or sid == "" then
		return nil
	end
	return M.lookup_agent_by_sid(sid)
end

local function ensure_parent_dir(path)
	local parent = vim.fn.fnamemodify(path, ":h")
	if parent and parent ~= "" then
		vim.fn.mkdir(parent, "p")
	end
end

local function parse_codex_entry(line)
	local name, sid, cwd, updated = line:match("^([^\t]+)\t([^\t]+)\t([^\t]*)\t([^\t]+)$")
	if not name or not sid then
		return nil
	end
	return {
		name = trim(name),
		sid = trim(sid):lower(),
		cwd = trim(cwd),
		updated = tonumber(updated) or 0,
	}
end

local function read_codex_entries()
	ensure_parent_dir(CODEX_INDEX_PATH)
	local entries = {}
	local f = io.open(CODEX_INDEX_PATH, "r")
	if not f then
		return entries
	end
	for line in f:lines() do
		local entry = parse_codex_entry(line)
		if entry then
			table.insert(entries, entry)
		end
	end
	f:close()
	return entries
end

local function write_codex_entries(entries)
	ensure_parent_dir(CODEX_INDEX_PATH)
	table.sort(entries, function(a, b)
		return (a.updated or 0) > (b.updated or 0)
	end)
	local f = io.open(CODEX_INDEX_PATH, "w")
	if not f then
		return false
	end
	for _, entry in ipairs(entries) do
		f:write(("%s\t%s\t%s\t%d\n"):format(
			entry.name,
			entry.sid,
			entry.cwd or "",
			entry.updated or os.time()
		))
	end
	f:close()
	return true
end

local function codex_sid_from_path(path)
	if not path or path == "" then
		return nil
	end
	local sid = path:match("(%x%x%x%x%x%x%x%x%-%x%x%x%x%-%x%x%x%x%-%x%x%x%x%-%x%x%x%x%x%x%x%x%x%x%x%x)%.jsonl$")
	return sid and sid:lower() or nil
end

function M.is_uuid(value)
	return trim(value):lower():match(UUID_PATTERN) ~= nil
end

function M.rename_tmux_window(name)
	name = trim(name)
	if name == "" or not vim.env.TMUX then
		return false
	end
	vim.fn.system({ "tmux", "rename-window", name })
	return vim.v.shell_error == 0
end

function M.latest_codex_sid()
	local session_glob = vim.fn.expand("~/.codex/sessions/**/*.jsonl")
	local files = vim.fn.glob(session_glob, true, true)
	if type(files) ~= "table" or #files == 0 then
		return nil
	end

	local latest_path = nil
	local latest_mtime = -1
	for _, path in ipairs(files) do
		local mtime = vim.fn.getftime(path)
		if tonumber(mtime) and mtime > latest_mtime then
			latest_mtime = mtime
			latest_path = path
		end
	end
	return codex_sid_from_path(latest_path)
end

function M.upsert_codex_session(name, sid, cwd)
	name = trim(name)
	sid = trim(sid):lower()
	cwd = trim(cwd)
	if name == "" or sid == "" or not M.is_uuid(sid) then
		return false
	end

	local entries = read_codex_entries()
	local next_entries = {}
	for _, entry in ipairs(entries) do
		if entry.name ~= name and entry.sid ~= sid then
			table.insert(next_entries, entry)
		end
	end
	table.insert(next_entries, {
		name = name,
		sid = sid,
		cwd = cwd,
		updated = os.time(),
	})
	return write_codex_entries(next_entries)
end

function M.codex_sid_for_name(name)
	name = trim(name)
	if name == "" then
		return nil
	end
	for _, entry in ipairs(read_codex_entries()) do
		if entry.name == name then
			return entry.sid, entry.cwd
		end
	end
	return nil
end

function M.codex_name_for_sid(sid)
	sid = trim(sid):lower()
	if sid == "" then
		return nil
	end
	for _, entry in ipairs(read_codex_entries()) do
		if entry.sid == sid then
			return entry.name, entry.cwd
		end
	end
	return nil
end

function M.list_codex_sessions()
	return read_codex_entries()
end

function M.capture_new_codex_sid(name, cwd, baseline_sid, opts)
	opts = opts or {}
	local max_attempts = tonumber(opts.max_attempts) or 8
	local delay_ms = tonumber(opts.delay_ms) or 700
	local attempts = 0

	local function poll()
		attempts = attempts + 1
		local sid = M.latest_codex_sid()
		if sid and sid ~= baseline_sid then
			M.upsert_codex_session(name, sid, cwd)
			if type(opts.on_captured) == "function" then
				opts.on_captured(sid)
			end
			return
		end
		if attempts < max_attempts then
			vim.defer_fn(poll, delay_ms)
		end
	end

	vim.defer_fn(poll, delay_ms)
end

return M
