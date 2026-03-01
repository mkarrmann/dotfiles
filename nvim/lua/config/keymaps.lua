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

vim.keymap.set("t", "<C-\\>", "<C-\\><C-n>", { desc = "Exit terminal mode" })
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

-- Run a command and send its output to the Claude Code terminal.
local _active_run_job = nil

local function _clean_cmd(text)
	text = text:match("^%s*(.-)%s*$") or ""
	text = text:gsub("^```%w*\n", ""):gsub("\n```%s*$", "")
	text = text:gsub("^`(.+)`$", "%1")
	text = text:gsub("^[%$#%%>]+%s+", "")
	return text
end

local function _find_claude_term()
	for _, win in ipairs(vim.api.nvim_tabpage_list_wins(0)) do
		local buf = vim.api.nvim_win_get_buf(win)
		if vim.bo[buf].buftype == "terminal" then
			local name = vim.api.nvim_buf_get_name(buf)
			if name:find("claude") then
				local chan = vim.bo[buf].channel
				if chan and chan > 0 then
					return { buf = buf, win = win, chan = chan }
				end
			end
		end
	end
	return nil
end

local function _run_cmd_and_send(cmd)
	local claude = _find_claude_term()
	if not claude then
		vim.notify("No Claude Code terminal found in this tab", vim.log.levels.ERROR)
		return
	end
	if _active_run_job then
		vim.notify("A command is already running — <leader>aX to cancel", vim.log.levels.WARN)
		return
	end

	vim.notify("Running: " .. cmd:sub(1, 80) .. (#cmd > 80 and "…" or ""))

	local output = {}
	_active_run_job = vim.fn.jobstart({ vim.o.shell, "-c", cmd }, {
		stdout_buffered = true,
		stderr_buffered = true,
		on_stdout = function(_, data)
			if data then
				vim.list_extend(output, data)
			end
		end,
		on_stderr = function(_, data)
			if data then
				vim.list_extend(output, data)
			end
		end,
		on_exit = function(_, exit_code)
			_active_run_job = nil
			vim.schedule(function()
				while #output > 0 and output[#output] == "" do
					table.remove(output)
				end

				local total = #output
				local max_lines = 200
				if total > max_lines then
					local head_n = math.floor(max_lines * 0.7)
					local tail_n = max_lines - head_n
					local head = { unpack(output, 1, head_n) }
					local tail = { unpack(output, total - tail_n + 1) }
					output = head
					output[#output + 1] = ("... (%d lines omitted) ..."):format(total - head_n - tail_n)
					for _, l in ipairs(tail) do
						output[#output + 1] = l
					end
				end

				local body = table.concat(output, "\n")
				local msg = ("I ran `%s` (exit code %d). Combined stdout+stderr:\n```\n%s\n```"):format(cmd, exit_code, body)

				vim.api.nvim_chan_send(claude.chan, "\x1b[200~" .. msg .. "\x1b[201~")

				local win = claude.win
				if vim.api.nvim_win_is_valid(win) then
					pcall(vim.api.nvim_set_current_win, win)
					pcall(vim.cmd, "startinsert")
				end

				local status = exit_code == 0 and "ok" or "exit " .. exit_code
				vim.notify(("Sent %d lines to Claude (%s)"):format(total, status))
			end)
		end,
	})
end

vim.keymap.set("v", "<leader>ax", function()
	local save_reg = vim.fn.getreg("z")
	local save_type = vim.fn.getregtype("z")
	vim.cmd('noautocmd normal! gv"zy')
	local text = vim.fn.getreg("z")
	vim.fn.setreg("z", save_reg, save_type)

	local cmd = _clean_cmd(text)
	if cmd == "" then
		vim.notify("No command to run", vim.log.levels.WARN)
		return
	end
	_run_cmd_and_send(cmd)
end, { desc = "Run selection, send output to Claude" })

vim.keymap.set("n", "<leader>ax", function()
	vim.ui.input({ prompt = "Run and send to Claude: " }, function(cmd)
		if cmd and cmd ~= "" then
			_run_cmd_and_send(cmd)
		end
	end)
end, { desc = "Run command, send output to Claude" })

vim.keymap.set("n", "<leader>aX", function()
	if _active_run_job then
		vim.fn.jobstop(_active_run_job)
		_active_run_job = nil
		vim.notify("Cancelled running command")
	else
		vim.notify("No command running", vim.log.levels.WARN)
	end
end, { desc = "Cancel running command" })
