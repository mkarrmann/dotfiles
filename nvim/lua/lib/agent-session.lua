local M = {}

local LAST_SESSION_FILE = vim.fn.expand("~/.claude-last-session")

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
			local val = line:match("^OBSIDIAN_VAULT%s*=%s*(.+)$")
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

function M.get_session_id()
	local tab_handle = tostring(vim.api.nvim_get_current_tabpage())
	local tab_file = vim.fn.expand("~/.claude/agent-manager/pids/tab-" .. tab_handle)
	local f = io.open(tab_file, "r")
	if f then
		local sid = trim(f:read("*l"))
		f:close()
		if sid ~= "" then
			return sid
		end
	end

	local ok, t_sid = pcall(vim.api.nvim_tabpage_get_var, 0, "claude_session_id")
	if ok and t_sid and t_sid ~= "" then
		return t_sid
	end

	local f2 = io.open(LAST_SESSION_FILE, "r")
	if not f2 then
		return nil
	end
	local sid = trim(f2:read("*l"))
	f2:close()
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

function M.get_current_agent()
	local sid = M.get_session_id()
	if not sid or sid == "" then
		return nil
	end
	return M.lookup_agent_by_sid(sid)
end

return M
