local M = {}

local Manager = {}
Manager.__index = Manager

--- Helpers ---

local function update_scratch_buf(buf, lines)
	vim.bo[buf].modifiable = true
	vim.api.nvim_buf_set_lines(buf, 0, -1, false, lines)
	vim.bo[buf].modifiable = false
end

local function delete_buf(buf)
	if buf and vim.api.nvim_buf_is_valid(buf) then
		vim.api.nvim_buf_delete(buf, { force = true })
	end
end

local function get_file_list(state)
	return state.mode == "turn" and state.turn_files or state.files
end

local function get_before_buf(state, file_path)
	local data = state.file_data[file_path]
	if not data then
		return nil
	end
	return state.mode == "turn" and data.turn_buf or data.session_buf
end

--- Winbar ---

local function update_winbar(state)
	if not state.diff_tab then
		return
	end
	if not state.left_win or not vim.api.nvim_win_is_valid(state.left_win) then
		return
	end
	if not state.right_win or not vim.api.nvim_win_is_valid(state.right_win) then
		return
	end

	local file_list = get_file_list(state)
	local mode_label = state.mode

	if #file_list == 0 then
		vim.api.nvim_win_set_var(
			state.left_win,
			"custom_winbar_text",
			"%#Comment# [" .. mode_label .. "] no changes %*"
		)
		vim.api.nvim_win_set_var(
			state.right_win,
			"custom_winbar_text",
			"%#Comment# [" .. mode_label .. "] no changes %*"
		)
	else
		local idx = math.min(state.index, #file_list)
		local file_path = file_list[idx]
		local display = vim.fn.fnamemodify(file_path, ":.")
		local pos = string.format("[%d/%d]", idx, #file_list)
		vim.api.nvim_win_set_var(
			state.left_win,
			"custom_winbar_text",
			"%#DiagnosticOk# [" .. mode_label .. "] after " .. pos .. " %* " .. display
		)
		vim.api.nvim_win_set_var(
			state.right_win,
			"custom_winbar_text",
			"%#Comment# [" .. mode_label .. "] before " .. pos .. " %* " .. display
		)
	end

	pcall(function()
		require("lualine").refresh()
	end)
end

--- Display ---

local function show_pair(state, index)
	local file_list = get_file_list(state)
	if #file_list == 0 then
		update_winbar(state)
		return
	end

	index = math.max(1, math.min(index, #file_list))
	state.index = index

	local file_path = file_list[index]
	local data = state.file_data[file_path]
	if not data then
		return
	end

	local before_buf = get_before_buf(state, file_path)
	local after_buf = data.after_buf
	if not before_buf or not vim.api.nvim_buf_is_valid(before_buf) then
		return
	end
	if not after_buf or not vim.api.nvim_buf_is_valid(after_buf) then
		return
	end
	if not vim.api.nvim_win_is_valid(state.left_win) or not vim.api.nvim_win_is_valid(state.right_win) then
		return
	end

	local cur_left = vim.api.nvim_win_get_buf(state.left_win)
	local cur_right = vim.api.nvim_win_get_buf(state.right_win)

	if cur_left == after_buf and cur_right == before_buf then
		vim.api.nvim_win_call(state.left_win, function()
			vim.cmd("diffupdate")
		end)
		update_winbar(state)
		return
	end

	vim.api.nvim_win_call(state.left_win, function()
		vim.cmd("diffoff")
	end)
	vim.api.nvim_win_call(state.right_win, function()
		vim.cmd("diffoff")
	end)

	vim.api.nvim_win_set_buf(state.left_win, after_buf)
	vim.api.nvim_win_set_buf(state.right_win, before_buf)

	vim.api.nvim_win_call(state.left_win, function()
		vim.cmd("diffthis")
	end)
	vim.api.nvim_win_call(state.right_win, function()
		vim.cmd("diffthis")
	end)

	for _, win in ipairs({ state.left_win, state.right_win }) do
		vim.wo[win].scrollbind = true
		vim.wo[win].foldenable = false
		vim.wo[win].relativenumber = true
	end

	update_winbar(state)

	vim.api.nvim_win_call(state.left_win, function()
		vim.cmd("syncbind")
		vim.cmd("normal! gg")
	end)
	vim.api.nvim_win_call(state.right_win, function()
		vim.cmd("normal! gg")
	end)
end

--- Manager ---

function M.new(opts)
	opts = opts or {}
	local self = setmetatable({}, Manager)
	self.opts = {
		name = opts.name,
		tab_var = opts.tab_var,
		diff_tab_var = opts.diff_tab_var or (opts.name .. "_session"),
	}
	self._sessions = {}
	self._counter = 0
	return self
end

function Manager:make_scratch_buf(lines, file_path, label)
	self._counter = self._counter + 1
	local buf = vim.api.nvim_create_buf(false, true)
	vim.api.nvim_buf_set_lines(buf, 0, -1, false, lines or {})
	vim.bo[buf].modifiable = false
	vim.bo[buf].buftype = "nofile"
	vim.bo[buf].bufhidden = "hide"
	local ft = vim.filetype.match({ filename = file_path })
	if ft then
		vim.bo[buf].filetype = ft
	end
	pcall(vim.api.nvim_buf_set_name, buf, string.format("%s://%s#%d", label, file_path, self._counter))
	return buf
end

function Manager:get_state(session_id)
	if not session_id then
		return nil
	end
	if not self._sessions[session_id] then
		self._sessions[session_id] = {
			mode = "turn",
			diff_tab = nil,
			work_tab = nil,
			left_win = nil,
			right_win = nil,
			index = 1,
			files = {},
			turn_files = {},
			file_data = {},
		}
	end
	return self._sessions[session_id]
end

function Manager:add_file(session_id, file_path, opts) end

function Manager:refresh_after(session_id, file_path, lines) end

function Manager:new_turn(session_id) end

function Manager:cleanup(session_id) end

function Manager:toggle() end

function Manager:debug() end

return M
