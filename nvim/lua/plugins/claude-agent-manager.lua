-- Agent Manager integration for Claude Code
-- Adds :ClaudeCodeName and :ClaudeCodeAgents commands with keybindings

local function resolve_agents_file()
	if vim.env.CLAUDE_AGENTS_FILE then
		return vim.env.CLAUDE_AGENTS_FILE
	end
	local gdrive = "/data/users/" .. (vim.env.USER or "unknown") .. "/gdrive/AGENTS.md"
	if vim.fn.filereadable(gdrive) == 1 then
		return gdrive
	end
	return vim.fn.expand("~/.claude/agents.md")
end
local agents_file = resolve_agents_file()
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

local function rename_session(name)
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
		vim.notify("No sessions found in " .. agents_file, vim.log.levels.WARN)
		return
	end

	local displayer = entry_display.create({
		separator = " ",
		items = {
			{ width = 14 },
			{ width = 30 },
			{ width = 12 },
			{ width = 12 },
			{ remaining = true },
		},
	})

	local function make_display(entry)
		return displayer({
			{ entry.value.status },
			{ entry.value.name, "TelescopeResultsIdentifier" },
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
						ordinal = agent.name .. " " .. agent.status .. " " .. agent.od .. " " .. agent.description .. " " .. agent.dir,
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
					-- Switch to the session's directory so Claude starts there
					if agent.dir and agent.dir ~= "" and vim.fn.isdirectory(agent.dir) == 1 then
						local cwd = vim.fn.getcwd()
						if agent.dir ~= cwd then
							vim.fn.chdir(agent.dir)
							vim.notify("cd " .. agent.dir)
						end
					end
					-- Write name so hook preserves it if session is compacted
					local nf = io.open(next_name_file, "w")
					if nf then
						nf:write(agent.name)
						nf:close()
					end
					if vim.env.TMUX then
						vim.fn.system("tmux rename-window " .. vim.fn.shellescape(agent.name))
					end
					vim.cmd("ClaudeCode --resume " .. agent.sid)
				end)
				return true
			end,
		})
		:find()
end

local function prompt_new_window()
	if not vim.env.TMUX then
		vim.notify("Not in tmux", vim.log.levels.WARN)
		return
	end
	vim.ui.input({ prompt = "Claude prompt: " }, function(text)
		if not text or text == "" then
			return
		end

		local prompt_file = string.format("/tmp/.claude-auto-prompt-%d", vim.uv.hrtime())
		local f = io.open(prompt_file, "w")
		if not f then
			vim.notify("Failed to write prompt file", vim.log.levels.ERROR)
			return
		end
		f:write(text)
		f:close()

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

		vim.fn.system({
			"tmux", "new-window", "-d",
			"-n", label,
			"-e", "CLAUDE_AUTO_PROMPT=" .. prompt_file,
			"--", "nvim",
		})
	end)
end

local function prompt_new_pane()
	if not vim.env.TMUX then
		vim.notify("Not in tmux", vim.log.levels.WARN)
		return
	end
	vim.ui.input({ prompt = "Claude prompt: " }, function(text)
		if not text or text == "" then
			return
		end

		local prompt_file = string.format("/tmp/.claude-auto-prompt-%d", vim.uv.hrtime())
		local f = io.open(prompt_file, "w")
		if not f then
			vim.notify("Failed to write prompt file", vim.log.levels.ERROR)
			return
		end
		f:write(text)
		f:close()

		local nf = io.open(next_name_file, "w")
		if nf then
			local label = text:sub(1, 15):gsub("[^%w%-_ ]", ""):match("^%s*(.-)%s*$") or ""
			if label == "" then
				label = "claude"
			end
			label = "qq: " .. label
			nf:write(label)
			nf:close()
		end

		vim.fn.system({
			"tmux", "split-window", "-h", "-l", "33%",
			"-e", "CLAUDE_AUTO_PROMPT=" .. prompt_file,
			"--", "nvim",
		})
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
							vim.cmd("ClaudeCode --resume " .. id)
						end
					end)
				end,
				desc = "Resume Claude session by ID",
			},
			{
				"<leader>ap",
				prompt_new_window,
				desc = "Prompt Claude in new tmux window",
			},
			{
				"<leader>aP",
				prompt_new_pane,
				desc = "Prompt Claude in new tmux pane",
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

			vim.api.nvim_create_user_command("ClaudeCodeResume", resume_by_name, {
				desc = "Resume Claude session by name (Telescope picker)",
			})

			if vim.env.CLAUDE_AUTO_PROMPT then
				vim.api.nvim_create_autocmd("VimEnter", {
					once = true,
					callback = function()
						vim.defer_fn(function()
							local prompt_file = vim.env.CLAUDE_AUTO_PROMPT
							local f = io.open(prompt_file, "r")
							local prompt = f and f:read("*a") or nil
							if f then
								f:close()
							end
							os.remove(prompt_file)

							require("lazy").load({ plugins = { "claudecode.nvim" } })
							vim.cmd("ClaudeCode")

							if prompt and prompt ~= "" then
								vim.defer_fn(function()
									for _, buf in ipairs(vim.api.nvim_list_bufs()) do
										if vim.api.nvim_buf_is_valid(buf) and vim.bo[buf].buftype == "terminal" then
											local name = vim.api.nvim_buf_get_name(buf)
											if name:lower():find("claude") then
												local chan = vim.bo[buf].channel
												if chan and chan > 0 then
													vim.api.nvim_chan_send(chan, prompt .. "\n")
												end
												break
											end
										end
									end
								end, 2000)
							end
						end, 200)
					end,
				})
			end
		end,
	},
}
