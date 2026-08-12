local M = {}

local STATE_VAR = "agent_status"
local setup_done = false

-- Background refresh of the durable session title (session -> tab). `title` never
-- streams over SSE, so a rename made elsewhere (web UI, the agent itself) is only
-- pulled in when a tab is focused. Throttled per session to keep rapid tab
-- cycling from spamming the local server.
local POLL_THROTTLE_MS = 3000
local last_poll = {}

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

-- Resolve a CodeCompanion chat from its buffer. Overridable for tests via
-- M._set_chat_resolver (the real path requires the plugin, which is absent in
-- headless unit runs).
local default_chat_resolver = function(bufnr)
	if not bufnr or not vim.api.nvim_buf_is_valid(bufnr) then
		return nil
	end
	local ok, codecompanion = pcall(require, "codecompanion")
	if not ok then
		return nil
	end
	local cok, chat = pcall(codecompanion.buf_get_chat, bufnr)
	return cok and chat or nil
end

local chat_resolver = default_chat_resolver

function M._set_chat_resolver(fn)
	chat_resolver = fn or default_chat_resolver
end

local function chat_for_tab(tab)
	if not valid_tab(tab) then
		return nil
	end
	local ok, bufnr = pcall(vim.api.nvim_tabpage_get_var, tab, "codecompanion_chat_bufnr")
	return ok and chat_resolver(bufnr) or nil
end

local function session_snapshot(bufnr)
	local chat = chat_resolver(bufnr)
	return chat and chat.omnigent_session or nil
end

local function tab_name(tab)
	local ok, name = pcall(vim.api.nvim_tabpage_get_var, tab, "tab_name")
	if ok and type(name) == "string" and name ~= "" then
		return name
	end
	return nil
end

-- Only omnigent chats carry a durable, PATCHable `title` (the session name);
-- other tab owners (claude terminals, diff tabs, plain edits) do not.
local function omnigent_session_for_tab(tab)
	local chat = chat_for_tab(tab)
	local session = chat and chat.omnigent_session
	if session and session.session_id and type(session.set_config_async) == "function" then
		return session
	end
	return nil
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

---Push the tab's display name up to the durable omnigent session `title`
---(tab -> session). No-op for tabs that don't own an omnigent chat. Deferred and
---best-effort: the PATCH is a blocking REST call, so it runs off the current
---keymap/autocmd callback.
---@param tab? integer
---@param session? table Pre-resolved omnigent session (else looked up from the tab).
function M.push_title(tab, session)
	tab = tab or vim.api.nvim_get_current_tabpage()
	session = session or omnigent_session_for_tab(tab)
	if not session or not session.session_id or type(session.set_config_async) ~= "function" then
		return
	end
	local name = tab_name(tab)
	if not name or session.title == name then
		return
	end
	vim.schedule(function()
		-- Re-check under the (possibly changed) latest state before the network call.
		if not valid_tab(tab) or tab_name(tab) ~= name or session.title == name then
			return
		end
		-- Asynchronous: this fires on every tab rename, and a rename must never
		-- stall on a PATCH.
		session:set_config_async("title", name, function(ok, err)
			if ok then
				session.title = name
			elseif err then
				vim.notify(
					"Omnigent: couldn't sync tab name to session title: " .. (err.message or "unknown error"),
					vim.log.levels.WARN
				)
			end
		end)
	end)
end

---Reconcile a tab's name with its omnigent session title on attach/restore.
---Precedence: a durable title (resumed/forked session) wins and is adopted as
---the tab name (session -> tab); otherwise a name the user gave the tab seeds the
---title (tab -> session). The equality checks make both directions idempotent so
---they can't ping-pong.
---@param tab? integer
---@param session? table Pre-resolved omnigent session (else looked up from the tab).
function M.reconcile_title(tab, session)
	tab = tab or vim.api.nvim_get_current_tabpage()
	session = session or omnigent_session_for_tab(tab)
	if not session or not session.session_id then
		return
	end
	local title = type(session.title) == "string" and session.title ~= "" and session.title or nil
	if title then
		if tab_name(tab) ~= title then
			pcall(vim.api.nvim_tabpage_set_var, tab, "tab_name", title)
			redraw()
		end
	elseif tab_name(tab) then
		M.push_title(tab, session)
	end
end

---Pull the durable session title from the server and adopt it if it drifted
---(session -> tab). Closes the gap that `title` isn't carried over SSE: a rename
---made in another client shows up on focus. Async (never blocks the UI) and
---best-effort; only a title that differs from our cached value is adopted, so it
---can't clobber an in-flight local rename or fire redundant writes.
---@param tab? integer
function M.refresh_title(tab)
	tab = tab or vim.api.nvim_get_current_tabpage()
	local session = omnigent_session_for_tab(tab)
	if not session then
		return
	end
	local client = session.client
	if not client or type(client.request_async) ~= "function" then
		return
	end
	local sid = session.session_id
	local now = (vim.uv or vim.loop).now()
	if last_poll[sid] and (now - last_poll[sid]) < POLL_THROTTLE_MS then
		return
	end
	last_poll[sid] = now
	client:request_async("get", "/v1/sessions/" .. sid, {}, function(snap, err)
		if err or type(snap) ~= "table" then
			return
		end
		local title = type(snap.title) == "string" and snap.title ~= "" and snap.title or nil
		if not title or title == session.title then
			return
		end
		session.title = title
		if valid_tab(tab) and tab_name(tab) ~= title then
			pcall(vim.api.nvim_tabpage_set_var, tab, "tab_name", title)
			redraw()
		end
	end)
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
	M.reconcile_title(tab, session)
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
			M.refresh_title(tab)
		end,
	})

	-- Coming back to nvim (e.g. after renaming a session in the web UI) refreshes
	-- the focused tab's title.
	vim.api.nvim_create_autocmd("FocusGained", {
		group = group,
		callback = function()
			M.refresh_title()
		end,
	})
end

return M
