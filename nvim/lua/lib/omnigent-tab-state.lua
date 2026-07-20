local M = {}

local STATE_VAR = "agent_status"
local setup_done = false

local terminal_kinds = {
	turn_completed = true,
	turn_failed = true,
	turn_cancelled = true,
	interrupted = true,
	stream_error = true,
	error = true,
}

local status_kinds = vim.tbl_extend("force", terminal_kinds, {
	turn_started = true,
	elicitation = true,
	elicitation_resolved = true,
	status = true,
})

local function valid_tab(tab)
	return tab and vim.api.nvim_tabpage_is_valid(tab)
end

local function tab_for_chat(bufnr)
	if not bufnr or not vim.api.nvim_buf_is_valid(bufnr) then
		return nil
	end
	local ok, tab = pcall(function()
		return vim.b[bufnr].cc_tab_owner
	end)
	return ok and valid_tab(tab) and tab or nil
end

local function redraw()
	pcall(vim.cmd, "redrawtabline")
end

local function read_state(tab)
	if not valid_tab(tab) then
		return nil
	end
	local ok, state = pcall(vim.api.nvim_tabpage_get_var, tab, STATE_VAR)
	return ok and type(state) == "table" and state or nil
end

local function write_state(tab, state)
	if not valid_tab(tab) then
		return
	end
	vim.api.nvim_tabpage_set_var(tab, STATE_VAR, state)
	redraw()
end

local function viewed(tab)
	return tab == vim.api.nvim_get_current_tabpage()
end

local function initial_state(session_id)
	return {
		phase = "idle",
		unread = false,
		session_id = session_id,
		response_id = nil,
	}
end

local function session_snapshot(bufnr)
	local ok, codecompanion = pcall(require, "codecompanion")
	if not ok then
		return nil
	end
	local chat = codecompanion.buf_get_chat(bufnr)
	return chat and chat.omnigent_session or nil
end

function M.state(tab)
	return read_state(tab or vim.api.nvim_get_current_tabpage())
end

function M.clear(tab)
	if not valid_tab(tab) then
		return
	end
	pcall(vim.api.nvim_tabpage_del_var, tab, STATE_VAR)
	redraw()
end

function M.attach(data)
	data = data or {}
	local tab = tab_for_chat(data.bufnr)
	if not tab then
		return
	end
	local state = initial_state(data.session_id)
	local session = session_snapshot(data.bufnr)
	if session then
		state.session_id = session.session_id or state.session_id
		local pending = vim.tbl_count(session.pending_elicitations or {})
		if pending > 0 then
			state.phase = "waiting"
		elseif session.reducer and session.reducer.current_response_id then
			state.phase = "running"
			state.response_id = session.reducer.current_response_id
		elseif session.status == "running" then
			state.phase = "running"
		elseif session.status == "failed" then
			state.phase = "failed"
		end
	end
	write_state(tab, state)
end

function M.handle_lifecycle(data)
	data = data or {}
	if not status_kinds[data.kind] then
		return
	end
	local tab = tab_for_chat(data.bufnr)
	if not tab or not data.kind then
		return
	end

	local state = read_state(tab) or initial_state(data.session_id)
	local previous = vim.deepcopy(state)
	if state.session_id and data.session_id and state.session_id ~= data.session_id then
		if data.kind ~= "turn_started" then
			return
		end
		state = initial_state(data.session_id)
	end
	state.session_id = data.session_id or state.session_id

	if
		terminal_kinds[data.kind]
		and data.response_id
		and data.active_response_id
		and data.response_id ~= data.active_response_id
	then
		return
	end

	local kind = data.kind
	if kind == "turn_started" then
		state.phase = "running"
		state.unread = false
		state.response_id = data.response_id or data.active_response_id
	elseif kind == "elicitation" then
		state.phase = "waiting"
		state.response_id = data.response_id or data.active_response_id or state.response_id
	elseif kind == "elicitation_resolved" then
		if (data.pending_elicitations or 0) > 0 then
			state.phase = "waiting"
		elseif data.active_response_id then
			state.phase = "running"
			state.response_id = data.active_response_id
		else
			state.phase = "idle"
			state.response_id = nil
		end
	elseif kind == "turn_completed" then
		state.phase = "idle"
		state.unread = not viewed(tab)
		state.response_id = nil
	elseif kind == "turn_failed" or kind == "error" or kind == "stream_error" then
		state.phase = "failed"
		state.unread = not viewed(tab)
		state.response_id = nil
	elseif kind == "interrupted" or kind == "turn_cancelled" then
		state.phase = "idle"
		state.unread = false
		state.response_id = nil
	elseif kind == "status" then
		if (data.pending_elicitations or 0) > 0 then
			state.phase = "waiting"
		elseif data.status == "running" then
			state.phase = "running"
		elseif data.status == "failed" then
			state.phase = "failed"
		elseif data.status == "idle" and state.phase == "running" then
			state.phase = "idle"
			state.response_id = nil
		end
	end

	if not vim.deep_equal(previous, state) then
		write_state(tab, state)
	end
end

function M.marker(tab)
	local state = read_state(tab)
	if not state then
		return nil
	end
	if state.phase == "running" then
		return "⚙", "running"
	elseif state.phase == "waiting" then
		return "!", "waiting"
	elseif state.phase == "failed" and state.unread then
		return "×", "failed"
	elseif state.phase == "idle" and state.unread then
		return "✓", "unread"
	end
	return nil
end

function M.setup()
	if setup_done then
		return
	end
	setup_done = true
	local group = vim.api.nvim_create_augroup("omnigent_tab_state", { clear = true })

	vim.api.nvim_create_autocmd("User", {
		group = group,
		pattern = "CodeCompanionOmnigentLifecycle",
		callback = function(args)
			M.handle_lifecycle(args.data)
		end,
	})

	vim.api.nvim_create_autocmd("User", {
		group = group,
		pattern = { "CodeCompanionOmnigentSessionReady", "CodeCompanionOmnigentChatRestored" },
		callback = function(args)
			M.attach(args.data)
		end,
	})

	vim.api.nvim_create_autocmd("User", {
		group = group,
		pattern = "CodeCompanionChatClosed",
		callback = function(args)
			local tab = tab_for_chat(args.data and args.data.bufnr)
			if tab then
				M.clear(tab)
			end
		end,
	})

	vim.api.nvim_create_autocmd("TabEnter", {
		group = group,
		callback = function()
			local tab = vim.api.nvim_get_current_tabpage()
			local state = read_state(tab)
			if state and state.unread then
				state.unread = false
				write_state(tab, state)
			end
		end,
	})
end

return M
