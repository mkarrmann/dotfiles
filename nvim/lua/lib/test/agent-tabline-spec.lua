-- nvim --headless -u NONE --cmd "set rtp+=$HOME/dotfiles/nvim" -c "lua require('lib.test.agent-tabline-spec').run()" -c "qa!"

local M = {}

local function eq(actual, expected, label)
	if not vim.deep_equal(actual, expected) then
		error(string.format("%s: expected %s, got %s", label, vim.inspect(expected), vim.inspect(actual)))
	end
end

function M.run()
	vim.cmd("tabnew")
	local named_tab = vim.api.nvim_get_current_tabpage()
	vim.cmd("tabprevious")
	vim.api.nvim_tabpage_set_var(named_tab, "tab_name", "omni%chat")

	local tabline = require("lib.agent-tabline")
	vim.o.tabline = "%!v:lua.require'lualine'.tabline()"
	tabline.setup()
	eq(vim.o.tabline, "%!v:lua._tabline()", "agent tabline reclaims the option after plugin setup")
	eq(vim.o.showtabline, 2, "agent tabline remains visible")

	local rendered = tabline.render()
	if not rendered:find("2:omni%%chat", 1, true) then
		error("tabline did not render an escaped tab name: " .. rendered)
	end
	if not rendered:find("  1 ", 1, true) then
		error("an unnamed tab renders as its number: " .. rendered)
	end
	vim.api.nvim_eval_statusline(rendered, { use_tabline = true })

	local fill_hl = vim.api.nvim_get_hl(0, { name = "AgentTabFill", link = false })
	local inactive_hl = vim.api.nvim_get_hl(0, { name = "AgentTabInactive", link = false })
	if inactive_hl.bg == fill_hl.bg then
		error("inactive tab background is indistinguishable from tabline fill")
	end

	print("agent-tabline-spec: all checks passed")
end

return M
