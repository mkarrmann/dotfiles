local vim = vim

if vim.g.vscode then
	-- See https://github.com/vscode-neovim/vscode-neovim/issues/1902#issuecomment-2073831492

	-- Allows you distinguish whether an operator is pending using VSCode context keys

	vim.api.nvim_create_autocmd({ "VimEnter", "ModeChanged" }, {
		pattern = '*',
		callback = function()
			local fullMode = vim.api.nvim_eval('mode(1)')
			vim.fn["VSCodeCall"]('setContext', 'neovim.fullMode', fullMode)
		end,
	})

	-- See https://github.com/vscode-neovim/vscode-neovim/issues/1902#issuecomment-2151329542
	-- Actually, now just let vscode totally handle this
	-- vim.api.nvim_set_keymap(
	--         'n',
	--         '<C-space>',
	--         [[<Cmd>lua require('vscode').call('vspacecode.space')<CR>]],
	--         { noremap = true, silent = true }
	-- )

	-- Reverse of what extension sets, but what I'm used to
	vim.api.nvim_set_keymap('n', 'gD', "<Cmd>lua require('vscode').call('editor.action.revealDefinitionAside')<CR>",
		{ noremap = true, silent = true })

	vim.api.nvim_set_keymap('n', '<C-w>gd', "<Cmd>lua require('vscode').call('editor.action.peekDefinition')<CR>",
		{ noremap = true, silent = true })
end

vim.keymap.set("t", "<C-Space>", "<C-\\><C-n>", { desc = "Exit terminal mode" })
vim.keymap.set("t", "<C-h>", "<C-\\><C-n><C-w>h", { desc = "Go to Left Window" })
vim.keymap.set("t", "<C-j>", "<C-\\><C-n><C-w>j", { desc = "Go to Lower Window" })
vim.keymap.set("t", "<C-k>", "<C-\\><C-n><C-w>k", { desc = "Go to Upper Window" })
vim.keymap.set("t", "<C-l>", "<C-\\><C-n><C-w>l", { desc = "Go to Right Window" })

vim.keymap.set("n", "<leader>tt", "<cmd>terminal<cr>", { desc = "Terminal" })
vim.keymap.set("n", "<leader>tv", "<cmd>vsplit | terminal<cr>", { desc = "Terminal (vsplit)" })
vim.keymap.set("n", "<leader>th", "<cmd>split | terminal<cr>", { desc = "Terminal (hsplit)" })

vim.keymap.set("n", "<leader>lS", "<cmd>Lazy sync<cr>", { desc = "Lazy Sync" })

vim.keymap.set("n", "<C-w>+", "10<C-w>+", { desc = "Increase Window Height" })
vim.keymap.set("n", "<C-w>-", "10<C-w>-", { desc = "Decrease Window Height" })
vim.keymap.set("n", "<C-w>>", "10<C-w>>", { desc = "Increase Window Width" })
vim.keymap.set("n", "<C-w><", "10<C-w><", { desc = "Decrease Window Width" })

-- Unified send: dispatches to Claude Code or Codex based on which terminal is visible.
local function _find_active_agent()
	for _, win in ipairs(vim.api.nvim_tabpage_list_wins(0)) do
		local buf = vim.api.nvim_win_get_buf(win)
		if vim.bo[buf].buftype == "terminal" then
			local name = vim.api.nvim_buf_get_name(buf)
			if name:find("claude") then
				return "claude"
			elseif name:find("codex") then
				return "codex"
			end
		end
	end
	return nil
end

local function agent_send_selection()
	local agent = _find_active_agent()
	if agent == "claude" then
		vim.cmd("ClaudeCodeSend")
	elseif agent == "codex" then
		vim.cmd("'<,'>CodexSendSelection")
	else
		vim.notify("No Claude or Codex terminal visible", vim.log.levels.WARN)
	end
end

local function agent_send_path()
	local agent = _find_active_agent()
	if agent == "claude" then
		vim.cmd("ClaudeCodeSend")
	elseif agent == "codex" then
		vim.cmd("CodexSendPath")
	else
		vim.notify("No Claude or Codex terminal visible", vim.log.levels.WARN)
	end
end

vim.keymap.set("v", "<leader>aS", agent_send_selection, { desc = "Send selection to agent" })
vim.keymap.set("n", "<leader>af", agent_send_path, { desc = "Add file to agent" })
vim.keymap.set("n", "<leader>aS", agent_send_path, {
	desc = "Add file to agent (file explorer)",
	-- Works from any buffer; file explorer plugins set ft-specific overrides if needed
})
