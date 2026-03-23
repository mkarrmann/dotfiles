-- Agent Manager integration for Claude Code
-- Adds :ClaudeCodeName and :ClaudeCodeAgents commands with keybindings

local agent_session = require("lib.agent-session")

local next_name_file = vim.fn.expand("~/.claude-next-name")

local function start_named_session(name)
	local f = io.open(next_name_file, "w")
	if not f then
		vim.notify("Cannot write " .. next_name_file, vim.log.levels.ERROR)
		return
	end
	f:write(name)
	f:close()
	agent_session.rename_current_tab(name)
	vim.env.NVIM_TAB_HANDLE = tostring(vim.api.nvim_get_current_tabpage())
	vim.cmd("ClaudeCode")
end

local function has_claude_terminal()
	for _, buf in ipairs(vim.api.nvim_list_bufs()) do
		if vim.api.nvim_buf_is_valid(buf) and vim.bo[buf].buftype == "terminal" then
			local bname = vim.api.nvim_buf_get_name(buf)
			if bname:lower():find("claude") then
				return true
			end
		end
	end
	return false
end

local function rename_session(name)
	if not has_claude_terminal() then
		vim.notify("No active Claude Code session open", vim.log.levels.ERROR)
		return
	end

	local sid = agent_session.get_session_id()
	if not sid or sid == "" then
		vim.notify("No active Claude session found", vim.log.levels.WARN)
		return
	end

	-- Capture old name before overwriting AGENTS.md
	local old_agent = agent_session.lookup_agent_by_sid(sid)
	local old_name = old_agent and old_agent.name or nil

	-- Update local agents file: replace the Name column for the row matching this session ID
	local agents_file = agent_session.resolve_local_agents_file()
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

	-- Persist name so hook preserves it if session is compacted
	local nf = io.open(next_name_file, "w")
	if nf then
		nf:write(name)
		nf:close()
	end

	-- Rename current tab
	agent_session.rename_current_tab(name)

	vim.notify("Session renamed to '" .. name .. "'")

	-- Rename the associated pad file if it exists
	if old_name and old_name ~= "" then
		require("lib.scratch-notes").rename_pad(old_name, name)
	end
end

local function show_agents()
	local all_files = agent_session.resolve_all_agents_files()
	if #all_files == 0 then
		vim.notify("No agents files found", vim.log.levels.WARN)
		return
	end

	-- Merge: header from first file, data rows from all
	local lines = {}
	local header_done = false
	for _, agents_file in ipairs(all_files) do
		local f = io.open(agents_file, "r")
		if f then
			local line_num = 0
			for line in f:lines() do
				line_num = line_num + 1
				if not header_done and line_num <= 4 then
					lines[#lines + 1] = line
				elseif line_num > 4 then
					lines[#lines + 1] = line
				end
			end
			f:close()
			header_done = true
		end
	end

	local buf = vim.api.nvim_create_buf(false, true)
	vim.api.nvim_buf_set_lines(buf, 0, -1, false, lines)
	vim.bo[buf].modifiable = false
	vim.bo[buf].bufhidden = "wipe"
	vim.bo[buf].filetype = "markdown"

	local width = math.min(180, vim.o.columns - 4)
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

local function parse_agents()
	return agent_session.parse_agents()
end

local function lookup_agent_by_sid(sid)
	return agent_session.lookup_agent_by_sid(sid)
end

local function chdir_if_needed(dir)
	if dir and dir ~= "" and vim.fn.isdirectory(dir) == 1 then
		local cwd = vim.fn.getcwd()
		if dir ~= cwd then
			vim.fn.chdir(dir)
			vim.notify("cd " .. dir)
		end
	end
end

local function shorten_path(p)
	if not p or p == "" then
		return ""
	end
	local home = vim.env.HOME or ""
	local user = vim.env.USER or ""
	-- Replace home-like prefixes with ~
	if home ~= "" and p:sub(1, #home) == home then
		p = "~" .. p:sub(#home + 1)
	elseif user ~= "" then
		local alt = "/data/users/" .. user
		if p:sub(1, #alt) == alt then
			p = "~" .. p:sub(#alt + 1)
		end
	end
	-- If still long, show …/last-2-components
	if #p > 35 then
		local parts = vim.split(p, "/", { trimempty = true })
		if #parts >= 2 then
			p = "…/" .. parts[#parts - 1] .. "/" .. parts[#parts]
		end
	end
	return p
end

local function resume_by_name()
	local ok, telescope = pcall(require, "telescope")
	if not ok then
		vim.notify("Telescope is required for resume-by-name", vim.log.levels.ERROR)
		return
	end

	local pickers = require("telescope.pickers")
	local finders = require("telescope.finders")
	local conf = require("telescope.config").values
	local actions = require("telescope.actions")
	local action_state = require("telescope.actions.state")
	local entry_display = require("telescope.pickers.entry_display")

	local entries = parse_agents()
	if #entries == 0 then
		vim.notify("No sessions found", vim.log.levels.WARN)
		return
	end

	local displayer = entry_display.create({
		separator = " ",
		items = {
			{ width = 14 },
			{ width = 30 },
			{ width = 38 },
			{ width = 12 },
			{ width = 12 },
			{ remaining = true },
		},
	})

	local function make_display(entry)
		return displayer({
			{ entry.value.status },
			{ entry.value.name, "TelescopeResultsIdentifier" },
			{ entry.value.sid, "TelescopeResultsComment" },
			{ entry.value.updated, "TelescopeResultsComment" },
			{ entry.value.od, "TelescopeResultsComment" },
			{ shorten_path(entry.value.dir), "TelescopeResultsComment" },
		})
	end

	pickers
		.new({}, {
			prompt_title = "Resume Claude Session",
			finder = finders.new_table({
				results = entries,
				entry_maker = function(agent)
					return {
						value = agent,
						display = make_display,
						ordinal = agent.name .. " " .. agent.sid .. " " .. agent.status .. " " .. agent.od .. " " .. agent.description .. " " .. agent.dir,
					}
				end,
			}),
			sorter = conf.generic_sorter({}),
			attach_mappings = function(prompt_bufnr)
				actions.select_default:replace(function()
					actions.close(prompt_bufnr)
					local selection = action_state.get_selected_entry()
					if not selection then
						return
					end
					local agent = selection.value
					chdir_if_needed(agent.dir)
					-- Write name so hook preserves it if session is compacted
					local nf = io.open(next_name_file, "w")
					if nf then
						nf:write(agent.name)
						nf:close()
					end
					agent_session.rename_current_tab(agent.name)
					vim.env.NVIM_TAB_HANDLE = tostring(vim.api.nvim_get_current_tabpage())
					vim.cmd("ClaudeCode --resume " .. agent.sid .. " --fork-session")
					-- Open the session's pad note
					if agent.name ~= "" then
						require("lib.scratch-notes").open_for_session(agent.name)
					end
				end)
				return true
			end,
		})
		:find()
end

local function fork_to_window()
	local sid = agent_session.get_session_id()
	if not sid or sid == "" then
		vim.notify("No active Claude session to fork", vim.log.levels.WARN)
		return
	end
	local agent = lookup_agent_by_sid(sid)
	local name = agent and agent.name or nil
	vim.cmd("tabnew")
	if name then
		vim.api.nvim_tabpage_set_var(0, "tab_name", name)
		vim.cmd("redrawtabline")
		local nf = io.open(next_name_file, "w")
		if nf then
			nf:write(name)
			nf:close()
		end
	end
	vim.env.NVIM_TAB_HANDLE = tostring(vim.api.nvim_get_current_tabpage())
	vim.cmd("ClaudeCode --resume " .. sid .. " --fork-session")
end

local function fork_to_pane()
	local sid = agent_session.get_session_id()
	if not sid or sid == "" then
		vim.notify("No active Claude session to fork", vim.log.levels.WARN)
		return
	end
	vim.env.NVIM_TAB_HANDLE = tostring(vim.api.nvim_get_current_tabpage())
	vim.cmd("vsplit")
	vim.cmd("ClaudeCode --resume " .. sid .. " --fork-session")
end

local function prompt_new_window()
	local cwd = vim.fn.getcwd()
	vim.ui.input({ prompt = "Claude prompt: " }, function(text)
		if not text or text == "" then
			return
		end

		local label = text:sub(1, 15):gsub("[^%w%-_ ]", ""):match("^%s*(.-)%s*$") or ""
		if label == "" then
			label = "claude"
		end
		label = "qq: " .. label

		local nf = io.open(next_name_file, "w")
		if nf then
			nf:write(label)
			nf:close()
		end

		vim.cmd("tabnew")
		local target_tab = vim.api.nvim_get_current_tabpage()
		vim.api.nvim_tabpage_set_var(target_tab, "tab_name", label)
		vim.cmd("redrawtabline")
		vim.env.NVIM_TAB_HANDLE = tostring(target_tab)
		vim.fn.chdir(cwd)
		vim.cmd("ClaudeCode")

		vim.defer_fn(function()
			for _, win in ipairs(vim.api.nvim_tabpage_list_wins(target_tab)) do
				local buf = vim.api.nvim_win_get_buf(win)
				if vim.api.nvim_buf_is_valid(buf) and vim.bo[buf].buftype == "terminal" then
					local bname = vim.api.nvim_buf_get_name(buf)
					if bname:lower():find("claude") then
						local chan = vim.bo[buf].channel
						if chan and chan > 0 then
							vim.api.nvim_chan_send(chan, text .. "\n")
						end
						break
					end
				end
			end
		end, 2000)
	end)
end

local function prompt_new_pane()
	local cwd = vim.fn.getcwd()
	vim.ui.input({ prompt = "Claude prompt: " }, function(text)
		if not text or text == "" then
			return
		end

		local label = text:sub(1, 15):gsub("[^%w%-_ ]", ""):match("^%s*(.-)%s*$") or ""
		if label == "" then
			label = "claude"
		end
		label = "qq: " .. label

		local nf = io.open(next_name_file, "w")
		if nf then
			nf:write(label)
			nf:close()
		end

		local target_tab = vim.api.nvim_get_current_tabpage()
		vim.env.NVIM_TAB_HANDLE = tostring(target_tab)
		vim.cmd("vsplit")
		vim.fn.chdir(cwd)
		vim.cmd("ClaudeCode")

		vim.defer_fn(function()
			for _, win in ipairs(vim.api.nvim_tabpage_list_wins(target_tab)) do
				local buf = vim.api.nvim_win_get_buf(win)
				if vim.api.nvim_buf_is_valid(buf) and vim.bo[buf].buftype == "terminal" then
					local bname = vim.api.nvim_buf_get_name(buf)
					if bname:lower():find("claude") then
						local chan = vim.bo[buf].channel
						if chan and chan > 0 then
							vim.api.nvim_chan_send(chan, text .. "\n")
						end
						break
					end
				end
			end
		end, 2000)
	end)
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
					if not has_claude_terminal() then
						vim.notify("No active Claude Code session open", vim.log.levels.ERROR)
						return
					end
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
			{
				"<leader>ar",
				resume_by_name,
				desc = "Resume Claude session by name",
			},
			{
				"<leader>aR",
				function()
					vim.ui.input({ prompt = "Session ID: " }, function(id)
						if id and id ~= "" then
							local agent = lookup_agent_by_sid(id)
							if agent then
								chdir_if_needed(agent.dir)
								local nf = io.open(next_name_file, "w")
								if nf then
									nf:write(agent.name)
									nf:close()
								end
								agent_session.rename_current_tab(agent.name)
							else
								local resolved = agent_session.resolve_cwd_for_sid(id)
								chdir_if_needed(resolved)
							end
							vim.env.NVIM_TAB_HANDLE = tostring(vim.api.nvim_get_current_tabpage())
							vim.cmd("ClaudeCode --resume " .. id .. " --fork-session")
						end
					end)
				end,
				desc = "Resume Claude session by ID",
			},
			{
				"<leader>af",
				fork_to_window,
				desc = "Fork Claude session in new tab",
			},
			{
				"<leader>aF",
				fork_to_pane,
				desc = "Fork Claude session in vertical split",
			},
			{
				"<leader>ap",
				prompt_new_window,
				desc = "Prompt Claude in new tab",
			},
			{
				"<leader>aP",
				prompt_new_pane,
				desc = "Prompt Claude in vertical split",
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
				if not has_claude_terminal() then
					vim.notify("No active Claude Code session open", vim.log.levels.ERROR)
					return
				end
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

			vim.api.nvim_create_user_command("ClaudeCodeResume", resume_by_name, {
				desc = "Resume Claude session by name (Telescope picker)",
			})
		end,
	},
}
