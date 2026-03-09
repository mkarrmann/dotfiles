local M = {}

local last_session_file = vim.fn.expand("~/.claude-last-session")

function M.resolve_agents_file()
	if vim.env.CLAUDE_AGENTS_FILE then
		return vim.env.CLAUDE_AGENTS_FILE
	end
	local gdrive = "/data/users/" .. (vim.env.USER or "unknown") .. "/gdrive/AGENTS.md"
	if vim.fn.filereadable(gdrive) == 1 then
		return gdrive
	end
	return vim.fn.expand("~/.claude/agents.md")
end

function M.get_session_id()
	-- Primary: set by the SessionStart hook via nvim --remote-expr.
	-- This is authoritative for the Claude session in THIS Neovim instance.
	local g_sid = vim.g.claude_session_id
	if g_sid and g_sid ~= "" then
		return g_sid
	end

	-- Fallback: per-pane file written by agent-tracker.sh
	local tmux_pane = vim.env.TMUX_PANE
	if tmux_pane and tmux_pane ~= "" then
		local safe_pane = tmux_pane:gsub("[^%w_]", "_")
		local pane_file = vim.fn.expand("~/.claude/agent-manager/pids/pane-" .. safe_pane)
		local f = io.open(pane_file, "r")
		if f then
			local sid = f:read("*l")
			f:close()
			if sid and sid:match("^%s*(.-)%s*$") ~= "" then
				return sid:match("^%s*(.-)%s*$")
			end
		end
	end

	-- Last resort: global file (unreliable with multiple sessions)
	local f = io.open(last_session_file, "r")
	if not f then
		return nil
	end
	local sid = f:read("*l")
	f:close()
	return sid and sid:match("^%s*(.-)%s*$")
end

function M.parse_agents()
	local agents_file = M.resolve_agents_file()
	local f = io.open(agents_file, "r")
	if not f then
		return {}
	end
	local entries = {}
	for line in f:lines() do
		-- Data rows: | Name | Status | OD | Session ID | Description | Started | Updated | Dir |
		-- Skip header/separator lines (contain "---" or "Name" in the name column)
		if line:match("^|") and not line:match("^|%-") then
			local fields = {}
			for field in line:gmatch("|([^|]*)") do
				fields[#fields + 1] = vim.trim(field)
			end
			-- fields: [1]=Name [2]=Status [3]=OD [4]=Session ID [5]=Description [6]=Started [7]=Updated [8]=Dir
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
