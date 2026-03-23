-- Claude Code tab-state integration for Neovim.
-- Manages per-tab Claude state indicators via tab-local variables:
--   vim.t.claude_state: "⚙" | "!" | "✓" | "~" | ""
--   vim.t.tab_name: display name for the tab
--
-- Global functions are called from hook shell scripts via:
--   nvim --server "$NVIM" --remote-expr "execute('lua _G.<fn>(...)')"

local M = {}

local function setup_highlights()
	vim.api.nvim_set_hl(0, "TablineClaudeWorking", { fg = "#61afef", bold = true })
	vim.api.nvim_set_hl(0, "TablineClaudeNeedsInput", { fg = "#e06c75", bold = true })
	vim.api.nvim_set_hl(0, "TablineClaudeDone", { fg = "#e5c07b", bold = true })
end

setup_highlights()
vim.api.nvim_create_autocmd("ColorScheme", { callback = setup_highlights })

-- Sets vim.t.claude_state on the given tab and redraws the tabline.
-- For "!" also sends a vim.notify warning; for "✓" an info notification.
function _G._claude_set_tab_state(tab_handle, state)
	if type(tab_handle) == "string" then
		tab_handle = tonumber(tab_handle)
	end
	if not tab_handle then
		return
	end
	pcall(vim.api.nvim_tabpage_set_var, tab_handle, "claude_state", state)
	vim.schedule(function()
		vim.cmd("redrawtabline")
		if state == "!" then
			local ok, name = pcall(vim.api.nvim_tabpage_get_var, tab_handle, "tab_name")
			local label = (ok and name ~= "") and name or ("tab " .. tostring(tab_handle))
			vim.notify("Claude needs input: " .. label, vim.log.levels.WARN)
		elseif state == "✓" then
			local ok, name = pcall(vim.api.nvim_tabpage_get_var, tab_handle, "tab_name")
			local label = (ok and name ~= "") and name or ("tab " .. tostring(tab_handle))
			vim.notify("Claude done: " .. label, vim.log.levels.INFO)
		end
	end)
end

-- Called on PreToolUse: if state is "!", transition to "⚙" (user answered, now working).
function _G._claude_on_pretooluse(tab_handle)
	if type(tab_handle) == "string" then
		tab_handle = tonumber(tab_handle)
	end
	if not tab_handle then
		return
	end
	local ok, state = pcall(vim.api.nvim_tabpage_get_var, tab_handle, "claude_state")
	if ok and state == "!" then
		_G._claude_set_tab_state(tab_handle, "⚙")
	end
end

-- Called on Stop: sets "✓" unless state is "!" (Claude still needs input).
function _G._claude_on_stop(tab_handle)
	if type(tab_handle) == "string" then
		tab_handle = tonumber(tab_handle)
	end
	if not tab_handle then
		return
	end
	local ok, state = pcall(vim.api.nvim_tabpage_get_var, tab_handle, "claude_state")
	if ok and state == "!" then
		return
	end
	_G._claude_set_tab_state(tab_handle, "✓")
end

-- Sets vim.t.tab_name on the tab identified by tab_handle and redraws the tabline.
-- Called from external daemons (e.g. agent-watcher) via --remote-expr.
function _G._claude_rename_tab(tab_handle, name)
	if type(tab_handle) == "string" then
		tab_handle = tonumber(tab_handle)
	end
	if not tab_handle then
		return
	end
	pcall(vim.api.nvim_tabpage_set_var, tab_handle, "tab_name", name)
	vim.schedule(function()
		vim.cmd("redrawtabline")
	end)
end

-- Switches to the tab identified by tab_handle. Returns true on success.
-- Called from external tools (e.g. dashboard) via --remote-expr.
function _G._claude_focus_tab_by_handle(tab_handle)
	if type(tab_handle) == "string" then
		tab_handle = tonumber(tab_handle)
	end
	if not tab_handle then
		return false
	end
	local ok, num = pcall(vim.api.nvim_tabpage_get_number, tab_handle)
	if ok and num then
		vim.schedule(function()
			vim.cmd("tabnext " .. num)
		end)
		return true
	end
	return false
end

-- Sets vim.t.tab_name on the current tab and redraws the tabline.
function _G._nvim_rename_current_tab(name)
	vim.api.nvim_tabpage_set_var(0, "tab_name", name)
	vim.schedule(function()
		vim.cmd("redrawtabline")
	end)
end

-- Finds a tab by vim.t.tab_name and switches to it. Returns true/false.
function _G._claude_focus_tab_by_name(name)
	for _, tab in ipairs(vim.api.nvim_list_tabpages()) do
		local ok, tab_name = pcall(vim.api.nvim_tabpage_get_var, tab, "tab_name")
		if ok and tab_name == name then
			vim.schedule(function()
				local tab_num = vim.api.nvim_tabpage_get_number(tab)
				vim.cmd("tabnext " .. tab_num)
			end)
			return true
		end
	end
	return false
end

-- Finds a tab by name, stops its terminal job, and closes it.
function _G._claude_kill_tab_by_name(name)
	for _, tab in ipairs(vim.api.nvim_list_tabpages()) do
		local ok, tab_name = pcall(vim.api.nvim_tabpage_get_var, tab, "tab_name")
		if ok and tab_name == name then
			local wins = vim.api.nvim_tabpage_list_wins(tab)
			for _, win in ipairs(wins) do
				local buf = vim.api.nvim_win_get_buf(win)
				if vim.bo[buf].buftype == "terminal" then
					local chan = vim.bo[buf].channel
					if chan and chan > 0 then
						pcall(vim.fn.jobstop, chan)
					end
				end
			end
			vim.schedule(function()
				pcall(vim.cmd, "tabclose " .. vim.api.nvim_tabpage_get_number(tab))
			end)
			return true
		end
	end
	return false
end

-- Writes tab list (handle, name, claude_state) to path for cbls.
function _G._claude_list_tabs_to_file(path)
	local f = io.open(path, "w")
	if not f then
		return false
	end
	for _, tab in ipairs(vim.api.nvim_list_tabpages()) do
		local ok_name, name = pcall(vim.api.nvim_tabpage_get_var, tab, "tab_name")
		local ok_state, state = pcall(vim.api.nvim_tabpage_get_var, tab, "claude_state")
		name = ok_name and name or ""
		state = ok_state and state or ""
		f:write(string.format("%d\t%s\t%s\n", tab, name, state))
	end
	f:close()
	return true
end

-- Creates a new Neovim tab, sets its name, and starts a terminal running
-- script_path (a bash script generated by _start_bg_session).
-- The terminal inherits NVIM_TAB_HANDLE, NVIM, and CLAUDE_BG_ACTIVE.
function _G._claude_open_bg_session(name, script_path)
	local orig_tab = vim.api.nvim_get_current_tabpage()
	vim.cmd("tabnew")
	local tab = vim.api.nvim_get_current_tabpage()
	vim.api.nvim_tabpage_set_var(tab, "tab_name", name)
	vim.cmd("redrawtabline")
	vim.fn.mkdir(vim.fn.expand("~/claude-logs"), "p")
	vim.fn.termopen({ "bash", script_path }, {
		env = {
			NVIM_TAB_HANDLE = tostring(tab),
			NVIM = vim.v.servername,
			CLAUDE_BG_ACTIVE = "1",
		},
	})
	local orig_num = vim.api.nvim_tabpage_get_number(orig_tab)
	vim.cmd("tabnext " .. orig_num)
end

function M.tabline()
	local current = vim.api.nvim_get_current_tabpage()
	local s = ""
	for i, tab in ipairs(vim.api.nvim_list_tabpages()) do
		local is_current = tab == current
		local ok_name, name = pcall(vim.api.nvim_tabpage_get_var, tab, "tab_name")
		local ok_state, state = pcall(vim.api.nvim_tabpage_get_var, tab, "claude_state")
		name = ok_name and name or ""
		state = ok_state and state or ""

		s = s .. (is_current and "%#TabLineSel#" or "%#TabLine#")
		local label = name ~= "" and (tostring(i) .. ":" .. name) or tostring(i)
		s = s .. " " .. label .. " "

		if state ~= "" then
			local state_hl
			if state == "⚙" then
				state_hl = "%#TablineClaudeWorking#"
			elseif state == "!" then
				state_hl = "%#TablineClaudeNeedsInput#"
			elseif state == "✓" then
				state_hl = "%#TablineClaudeDone#"
			end
			if state_hl then
				s = s .. state_hl .. state .. (is_current and "%#TabLineSel#" or "%#TabLine#")
			else
				s = s .. state
			end
			s = s .. " "
		end
	end
	s = s .. "%#TabLineFill#"
	return s
end

_G._claude_tabline = M.tabline

return M
