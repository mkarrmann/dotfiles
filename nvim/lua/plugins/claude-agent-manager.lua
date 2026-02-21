-- Agent Manager integration for Claude Code
-- Adds :ClaudeCodeName and :ClaudeCodeAgents commands with keybindings

local agents_file = vim.env.CLAUDE_AGENTS_FILE or (vim.fn.expand("~/.claude/agents.md"))
local last_session_file = vim.fn.expand("~/.claude-last-session")

local function get_session_id()
	local f = io.open(last_session_file, "r")
	if not f then
		return nil
	end
	local sid = f:read("*l")
	f:close()
	return sid and sid:match("^%s*(.-)%s*$")
end

local next_name_file = vim.fn.expand("~/.claude-next-name")

local function start_named_session(name)
	local f = io.open(next_name_file, "w")
	if not f then
		vim.notify("Cannot write " .. next_name_file, vim.log.levels.ERROR)
		return
	end
	f:write(name)
	f:close()
	if vim.env.TMUX then
		vim.fn.system("tmux rename-window " .. vim.fn.shellescape(name))
	end
	vim.cmd("ClaudeCode")
end
	local sid = get_session_id()
	if not sid or sid == "" then
		vim.notify("No active Claude session found", vim.log.levels.WARN)
		return
	end

	-- Update AGENTS.md: replace the Name column for the row matching this session ID
	local f = io.open(agents_file, "r")
	if not f then
		vim.notify("Agents file not found: " .. agents_file, vim.log.levels.WARN)
		return
	end
	local lines = {}
	local found = false
	for line in f:lines() do
		if line:find("| " .. sid .. " |", 1, true) then
			-- Replace the name field (field 2 between first and second |)
			line = line:gsub("^(|)[^|]*(|)", "%1 " .. name .. " %2", 1)
			found = true
		end
		lines[#lines + 1] = line
	end
	f:close()

	if not found then
		vim.notify("Session " .. sid:sub(1, 8) .. " not found in agents file", vim.log.levels.WARN)
		return
	end

	f = io.open(agents_file, "w")
	if not f then
		vim.notify("Cannot write agents file", vim.log.levels.ERROR)
		return
	end
	f:write(table.concat(lines, "\n") .. "\n")
	f:close()

	-- Rename tmux window
	if vim.env.TMUX then
		vim.fn.system("tmux rename-window " .. vim.fn.shellescape(name))
	end

	vim.notify("Session renamed to '" .. name .. "'")
end

local function show_agents()
	local f = io.open(agents_file, "r")
	if not f then
		vim.notify("Agents file not found: " .. agents_file, vim.log.levels.WARN)
		return
	end
	local content = f:read("*a")
	f:close()

	local lines = vim.split(content, "\n", { trimempty = true })

	local buf = vim.api.nvim_create_buf(false, true)
	vim.api.nvim_buf_set_lines(buf, 0, -1, false, lines)
	vim.bo[buf].modifiable = false
	vim.bo[buf].bufhidden = "wipe"
	vim.bo[buf].filetype = "markdown"

	local width = math.min(120, vim.o.columns - 4)
	local height = math.min(#lines + 2, vim.o.lines - 4)
	vim.api.nvim_open_win(buf, true, {
		relative = "editor",
		width = width,
		height = height,
		col = math.floor((vim.o.columns - width) / 2),
		row = math.floor((vim.o.lines - height) / 2),
		style = "minimal",
		border = "rounded",
		title = " Claude Agents ",
		title_pos = "center",
	})

	-- Close on q or <Esc>
	vim.keymap.set("n", "q", "<cmd>close<cr>", { buffer = buf, silent = true })
	vim.keymap.set("n", "<Esc>", "<cmd>close<cr>", { buffer = buf, silent = true })
end

return {
	{
		"coder/claudecode.nvim",
		keys = {
			{
				"<leader>an",
				function()
					vim.ui.input({ prompt = "New session name: " }, function(name)
						if name and name ~= "" then
							start_named_session(name)
						end
					end)
				end,
				desc = "New named Claude session",
			},
			{
				"<leader>aN",
				function()
					vim.ui.input({ prompt = "Session name: " }, function(name)
						if name and name ~= "" then
							rename_session(name)
						end
					end)
				end,
				desc = "Name Claude session",
			},
			{
				"<leader>al",
				show_agents,
				desc = "List Claude agents",
			},
		},
		init = function()
			vim.api.nvim_create_user_command("ClaudeCodeNew", function(opts)
				local name = opts.args
				if name == "" then
					vim.ui.input({ prompt = "New session name: " }, function(input)
						if input and input ~= "" then
							start_named_session(input)
						end
					end)
				else
					start_named_session(name)
				end
			end, { nargs = "?", desc = "Start a new named Claude session" })

			vim.api.nvim_create_user_command("ClaudeCodeName", function(opts)
				local name = opts.args
				if name == "" then
					vim.ui.input({ prompt = "Session name: " }, function(input)
						if input and input ~= "" then
							rename_session(input)
						end
					end)
				else
					rename_session(name)
				end
			end, { nargs = "?", desc = "Name/rename current Claude session" })

			vim.api.nvim_create_user_command("ClaudeCodeAgents", show_agents, {
				desc = "Show all Claude agents",
			})
		end,
	},
}
