local M = {}

local Manager = {}
Manager.__index = Manager

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

function Manager:add_file(session_id, file_path, opts) end

function Manager:refresh_after(session_id, file_path, lines) end

function Manager:new_turn(session_id) end

function Manager:cleanup(session_id) end

function Manager:toggle() end

function Manager:debug() end

return M
