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

local function win_move_wrap(dir, opposite)
	return function()
		local cur = vim.api.nvim_get_current_win()
		vim.cmd("wincmd " .. dir)
		if vim.api.nvim_get_current_win() == cur then
			vim.cmd("999wincmd " .. opposite)
		end
	end
end

vim.keymap.set("n", "<C-h>", win_move_wrap("h", "l"), { desc = "Go to Left Window (wrap)" })
vim.keymap.set("n", "<C-l>", win_move_wrap("l", "h"), { desc = "Go to Right Window (wrap)" })

vim.keymap.set("t", "<C-\\>", "<C-\\><C-n>", { desc = "Exit terminal mode" })
vim.keymap.set("t", "<C-h>", function()
	vim.cmd("stopinsert")
	win_move_wrap("h", "l")()
end, { desc = "Go to Left Window (wrap)" })
vim.keymap.set("t", "<C-j>", "<C-\\><C-n><C-w>j", { desc = "Go to Lower Window" })
vim.keymap.set("t", "<C-k>", "<C-\\><C-n><C-w>k", { desc = "Go to Upper Window" })
vim.keymap.set("t", "<C-l>", function()
	vim.cmd("stopinsert")
	win_move_wrap("l", "h")()
end, { desc = "Go to Right Window (wrap)" })

vim.keymap.set("n", "<leader>tt", "<cmd>terminal<cr>", { desc = "Terminal" })
vim.keymap.set("n", "<leader>tv", "<cmd>vsplit | terminal<cr>", { desc = "Terminal (vsplit)" })
vim.keymap.set("n", "<leader>th", "<cmd>split | terminal<cr>", { desc = "Terminal (hsplit)" })

vim.keymap.set("n", "<leader>lS", "<cmd>Lazy sync<cr>", { desc = "Lazy Sync" })

vim.keymap.set("n", "<C-w>+", "10<C-w>+", { desc = "Increase Window Height" })
vim.keymap.set("n", "<C-w>-", "10<C-w>-", { desc = "Decrease Window Height" })
vim.keymap.set("n", "<C-w>>", "10<C-w>>", { desc = "Increase Window Width" })
vim.keymap.set("n", "<C-w><", "10<C-w><", { desc = "Decrease Window Width" })

vim.keymap.set("n", "<leader><tab>n", "<cmd>tabnew<cr>", { desc = "New tab" })
vim.keymap.set("n", "<leader><tab>h", "<cmd>tabprevious<cr>", { desc = "Previous Tab" })
vim.keymap.set("n", "<leader><tab>l", "<cmd>tabnext<cr>", { desc = "Next Tab" })

vim.keymap.set("n", "<leader><tab>r", function()
	local tab = vim.api.nvim_get_current_tabpage()
	vim.ui.input({ prompt = "Tab name: " }, function(name)
		if name then
			vim.api.nvim_tabpage_set_var(tab, "tab_name", name)
			vim.cmd("redrawtabline")
			-- For an omnigent chat tab, mirror the name to the durable session title.
			require("lib.omnigent-tab-state").push_title(tab)
		end
	end)
end, { desc = "Rename tab" })

-- In diff mode, remap mouse scroll to Ctrl-E/Ctrl-Y which respect scrollbind,
-- so both diff panes scroll together.
vim.keymap.set("n", "<ScrollWheelUp>", function()
	if vim.wo.diff then
		vim.cmd("normal! 3\x19")
	else
		vim.cmd("normal! \x19")
	end
end)

vim.keymap.set("n", "<ScrollWheelDown>", function()
	if vim.wo.diff then
		vim.cmd("normal! 3\x05")
	else
		vim.cmd("normal! \x05")
	end
end)
