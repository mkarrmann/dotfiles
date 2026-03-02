local diff_session = require("lib.diff-session")

local M = {}

local SESSION_KEY = "claude_edit_diff"
local _term_win = nil
local _buf_counter = 0
local _hidden_pairs = nil
local _hidden_index = nil
local _disabled = false

local function find_terminal_win()
	for _, win in ipairs(vim.api.nvim_tabpage_list_wins(0)) do
		local cfg = vim.api.nvim_win_get_config(win)
		if cfg.relative == "" then
			local buf = vim.api.nvim_win_get_buf(win)
			if vim.bo[buf].buftype == "terminal" then
				local name = vim.api.nvim_buf_get_name(buf)
				if name:lower():find("claude") then
					return win
				end
			end
		end
	end
	return nil
end

local function get_session()
	return diff_session.sessions[SESSION_KEY]
end

local function update_winbar(session)
	local pair = session.pairs[session.index]
	local pos = string.format("[%d/%d]", session.index, #session.pairs)
	local display_name = vim.fn.fnamemodify(pair.file, ":.")
	vim.api.nvim_win_set_var(session.right_win, "custom_winbar_text",
		"%#Comment# before " .. pos .. " %* " .. display_name)
	vim.api.nvim_win_set_var(session.left_win, "custom_winbar_text",
		"%#DiagnosticOk# after " .. pos .. " %* " .. display_name)
	require("lualine").refresh()
end

local function setup_win_autocmd(session)
	local group = vim.api.nvim_create_augroup("claude_edit_diff", { clear = true })
	vim.api.nvim_create_autocmd("WinClosed", {
		group = group,
		callback = function(ev)
			local s = get_session()
			if not s then
				return true
			end
			local closed = tonumber(ev.match)
			if closed == s.left_win or closed == s.right_win then
				vim.schedule(function()
					diff_session.close(s)
				end)
				return true
			end
		end,
	})
end

local function ensure_session()
	local session = get_session()
	if session then
		return session
	end

	_term_win = find_terminal_win()
	if _term_win then
		vim.api.nvim_set_current_win(_term_win)
	end

	vim.cmd("belowright vsplit")
	local right_win = vim.api.nvim_get_current_win()
	vim.cmd("belowright vsplit")
	local left_win = vim.api.nvim_get_current_win()

	session = {
		pairs = {},
		index = 0,
		left_win = left_win,
		right_win = right_win,
		update_winbar = update_winbar,
		on_close = function()
			local s = get_session()
			if s then
				diff_session.cleanup(s)
			end
			if not _disabled then
				_hidden_pairs = nil
				_hidden_index = nil
			end
			vim.api.nvim_create_augroup("claude_edit_diff", { clear = true })
			for _, win in ipairs({ left_win, right_win }) do
				if vim.api.nvim_win_is_valid(win) then
					vim.api.nvim_win_call(win, function()
						vim.cmd("diffoff")
					end)
					pcall(vim.api.nvim_win_close, win, true)
				end
			end
			if _term_win and vim.api.nvim_win_is_valid(_term_win) then
				vim.api.nvim_set_current_win(_term_win)
			end
		end,
	}

	diff_session.register(SESSION_KEY, session)
	setup_win_autocmd(session)
	return session
end

local function make_pair(file_path, snapshot_path)
	local before_lines = {}
	if snapshot_path and snapshot_path ~= "" then
		local f = io.open(snapshot_path, "r")
		if f then
			local content = f:read("*a")
			f:close()
			before_lines = vim.split(content, "\n", { plain = true })
			os.remove(snapshot_path)
		end
	end

	local old_buf = vim.api.nvim_create_buf(false, true)
	vim.api.nvim_buf_set_lines(old_buf, 0, -1, false, before_lines)
	vim.bo[old_buf].modifiable = false
	vim.bo[old_buf].buftype = "nofile"
	vim.bo[old_buf].bufhidden = "hide"
	local ft = vim.filetype.match({ filename = file_path })
	if ft then
		vim.bo[old_buf].filetype = ft
	end
	_buf_counter = _buf_counter + 1
	vim.api.nvim_buf_set_name(old_buf, string.format("before://%s#%d", file_path, _buf_counter))

	local new_buf = vim.fn.bufadd(file_path)
	vim.fn.bufload(new_buf)
	vim.api.nvim_buf_call(new_buf, function()
		vim.cmd("silent! checktime")
		vim.cmd("silent! edit!")
	end)

	return {
		file = file_path,
		old_buf = old_buf,
		new_buf = new_buf,
		is_live = true,
	}
end

local function find_existing_pair(file_path)
	local session = get_session()
	if session then
		for _, pair in ipairs(session.pairs) do
			if pair.file == file_path then
				return pair, session
			end
		end
	end
	if _hidden_pairs then
		for _, pair in ipairs(_hidden_pairs) do
			if pair.file == file_path then
				return pair, nil
			end
		end
	end
	return nil, nil
end

local function cleanup_snapshot(snapshot_path)
	if snapshot_path and snapshot_path ~= "" then
		os.remove(snapshot_path)
	end
end

local function reload_live_buffer(file_path)
	local buf = vim.fn.bufnr(file_path)
	if buf ~= -1 then
		vim.api.nvim_buf_call(buf, function()
			vim.cmd("silent! checktime")
			vim.cmd("silent! edit!")
		end)
	end
end

function M.show(file_path, snapshot_path)
	vim.schedule(function()
		local ok, err = pcall(function()
			file_path = vim.fn.fnamemodify(file_path, ":p")

			local existing, session = find_existing_pair(file_path)
			if existing then
				cleanup_snapshot(snapshot_path)
				reload_live_buffer(file_path)
				if session and session.pairs[session.index] == existing then
					if vim.api.nvim_win_is_valid(session.left_win) then
						vim.api.nvim_win_call(session.left_win, function()
							vim.cmd("diffupdate")
						end)
					end
				end
				return
			end

			local pair = make_pair(file_path, snapshot_path)

			if _disabled then
				if not _hidden_pairs then
					_hidden_pairs = {}
				end
				table.insert(_hidden_pairs, pair)
				_hidden_index = #_hidden_pairs
				return
			end

			local prev_win = vim.api.nvim_get_current_win()
			session = ensure_session()
			table.insert(session.pairs, pair)
			diff_session.register(SESSION_KEY, session)
			diff_session.show_pair(session, #session.pairs)
			if vim.api.nvim_win_is_valid(prev_win) then
				vim.api.nvim_set_current_win(prev_win)
			end
		end)
		if not ok then
			vim.notify("claude-edit-diff error: " .. tostring(err), vim.log.levels.ERROR)
		end
	end)
	return true
end

function M.toggle()
	if not _disabled then
		_disabled = true
		local session = get_session()
		if session then
			_hidden_pairs = session.pairs
			_hidden_index = session.index
			vim.api.nvim_create_augroup("claude_edit_diff", { clear = true })
			diff_session.sessions[SESSION_KEY] = nil
			for _, win in ipairs({ session.left_win, session.right_win }) do
				if vim.api.nvim_win_is_valid(win) then
					vim.api.nvim_win_call(win, function()
						vim.cmd("diffoff")
					end)
					pcall(vim.api.nvim_win_del_var, win, "custom_winbar_text")
					pcall(vim.api.nvim_win_close, win, true)
				end
			end
			require("lualine").refresh()
			if _term_win and vim.api.nvim_win_is_valid(_term_win) then
				vim.api.nvim_set_current_win(_term_win)
			end
		end
		vim.notify("Diff viewer disabled", vim.log.levels.INFO)
	else
		_disabled = false
		if _hidden_pairs and #_hidden_pairs > 0 then
			local pairs = _hidden_pairs
			local idx = _hidden_index or #pairs
			_hidden_pairs = nil
			_hidden_index = nil
			local session = ensure_session()
			session.pairs = pairs
			diff_session.register(SESSION_KEY, session)
			diff_session.show_pair(session, idx)
		else
			_hidden_pairs = nil
			_hidden_index = nil
			vim.notify("Diff viewer enabled", vim.log.levels.INFO)
		end
	end
end

return M
