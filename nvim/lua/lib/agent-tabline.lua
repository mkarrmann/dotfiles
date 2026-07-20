local M = {}

local state = require("lib.omnigent-tab-state")

local marker_groups = {
	running = "Running",
	waiting = "Waiting",
	unread = "Unread",
	failed = "Failed",
}

local function color(name, field, fallback)
	local value = vim.api.nvim_get_hl(0, { name = name, link = false })[field]
	return value or fallback
end

local function setup_highlights()
	local normal_bg = color("Normal", "bg", 0x080C10)
	local normal_fg = color("Normal", "fg", 0xC5CBD3)
	local fill_bg = color("TabLineFill", "bg", normal_bg)
	local inactive_bg = color("TabLine", "bg", 0x242A32)
	local inactive_fg = color("TabLine", "fg", 0x878D96)
	local active_bg = color("Function", "fg", 0x61AFEF)
	if inactive_bg == fill_bg then
		inactive_bg = 0x242A32
	end

	vim.api.nvim_set_hl(0, "AgentTabFill", { fg = inactive_fg, bg = fill_bg })
	vim.api.nvim_set_hl(0, "AgentTabActive", { fg = normal_bg, bg = active_bg, bold = true })
	vim.api.nvim_set_hl(0, "AgentTabInactive", { fg = normal_fg, bg = inactive_bg })
	vim.api.nvim_set_hl(0, "AgentTabSeparatorActive", { fg = active_bg, bg = fill_bg })
	vim.api.nvim_set_hl(0, "AgentTabSeparatorInactive", { fg = inactive_bg, bg = fill_bg })

	local status_colors = {
		Running = { active = normal_bg, inactive = 0x61AFEF },
		Waiting = { active = 0x4D1018, inactive = 0xE06C75 },
		Unread = { active = 0x514000, inactive = 0xE5C07B },
		Failed = { active = 0x4D1018, inactive = 0xFF5555 },
	}
	for suffix, colors in pairs(status_colors) do
		vim.api.nvim_set_hl(
			0,
			"AgentTab" .. suffix .. "Active",
			{ fg = colors.active, bg = active_bg, bold = true }
		)
		vim.api.nvim_set_hl(
			0,
			"AgentTab" .. suffix .. "Inactive",
			{ fg = colors.inactive, bg = inactive_bg, bold = true }
		)
	end
end

local function tab_name(tab, index)
	local ok, name = pcall(vim.api.nvim_tabpage_get_var, tab, "tab_name")
	if not ok or type(name) ~= "string" or name == "" then
		return tostring(index)
	end
	return tostring(index) .. ":" .. name:gsub("%%", "%%%%")
end

function M.render()
	local current = vim.api.nvim_get_current_tabpage()
	local output = "%#AgentTabFill#"
	for index, tab in ipairs(vim.api.nvim_list_tabpages()) do
		local active = tab == current
		local activity_marker, marker_kind = state.marker(tab)
		local variant = active and "Active" or "Inactive"
		output = output .. "%" .. index .. "T"
		output = output .. "%#AgentTab" .. variant .. "#  " .. tab_name(tab, index) .. " "
		if activity_marker then
			output = output
				.. "%#AgentTab"
				.. marker_groups[marker_kind]
				.. variant
				.. "#"
				.. activity_marker
				.. " "
		end
		output = output
			.. "%#AgentTabSeparator"
			.. variant
			.. "#%T"
			.. "%#AgentTabFill# "
	end
	return output .. "%#AgentTabFill#%="
end

function M.setup()
	setup_highlights()
	vim.api.nvim_create_autocmd("ColorScheme", {
		group = vim.api.nvim_create_augroup("agent_tabline_highlights", { clear = true }),
		callback = setup_highlights,
	})
	_G._tabline = M.render
	vim.o.tabline = "%!v:lua._tabline()"
	vim.o.showtabline = 2
end

return M
