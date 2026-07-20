local M = {}

function _G._claude_rename_tab(tab_handle, name)
	tab_handle = type(tab_handle) == "string" and tonumber(tab_handle) or tab_handle
	if not tab_handle then
		return
	end
	pcall(vim.api.nvim_tabpage_set_var, tab_handle, "tab_name", name)
	vim.schedule(function()
		vim.cmd("redrawtabline")
	end)
end

function _G._claude_focus_tab_by_handle(tab_handle)
	tab_handle = type(tab_handle) == "string" and tonumber(tab_handle) or tab_handle
	if not tab_handle then
		return false
	end
	local ok, number = pcall(vim.api.nvim_tabpage_get_number, tab_handle)
	if ok and number then
		vim.schedule(function()
			vim.cmd("tabnext " .. number)
		end)
		return true
	end
	return false
end

function _G._nvim_rename_current_tab(name)
	vim.api.nvim_tabpage_set_var(0, "tab_name", name)
	vim.schedule(function()
		vim.cmd("redrawtabline")
	end)
end

function _G._claude_focus_tab_by_name(name)
	for _, tab in ipairs(vim.api.nvim_list_tabpages()) do
		local ok, tab_name = pcall(vim.api.nvim_tabpage_get_var, tab, "tab_name")
		if ok and tab_name == name then
			vim.schedule(function()
				vim.cmd("tabnext " .. vim.api.nvim_tabpage_get_number(tab))
			end)
			return true
		end
	end
	return false
end

function _G._claude_kill_tab_by_name(name)
	for _, tab in ipairs(vim.api.nvim_list_tabpages()) do
		local ok, tab_name = pcall(vim.api.nvim_tabpage_get_var, tab, "tab_name")
		if ok and tab_name == name then
			for _, win in ipairs(vim.api.nvim_tabpage_list_wins(tab)) do
				local buf = vim.api.nvim_win_get_buf(win)
				if vim.bo[buf].buftype == "terminal" then
					local channel = vim.bo[buf].channel
					if channel and channel > 0 then
						pcall(vim.fn.jobstop, channel)
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

function _G._claude_list_tabs_to_file(path)
	local file = io.open(path, "w")
	if not file then
		return false
	end
	for _, tab in ipairs(vim.api.nvim_list_tabpages()) do
		local ok_name, name = pcall(vim.api.nvim_tabpage_get_var, tab, "tab_name")
		name = ok_name and name or ""
		file:write(string.format("%d\t%s\t\n", tab, name))
	end
	file:close()
	return true
end

function _G._claude_open_bg_session(name, script_path)
	local original_tab = vim.api.nvim_get_current_tabpage()
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
	vim.cmd("tabnext " .. vim.api.nvim_tabpage_get_number(original_tab))
end

return M
