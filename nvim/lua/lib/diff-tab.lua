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
