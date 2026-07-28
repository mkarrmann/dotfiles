-- nvim --headless -u NONE --cmd "set rtp+=$HOME/dotfiles/nvim" -c "lua require('lib.test.omnigent-tab-state-spec').run()" -c "qa!"

local M = {}

local function eq(actual, expected, label)
	if not vim.deep_equal(actual, expected) then
		error(string.format("%s: expected %s, got %s", label, vim.inspect(expected), vim.inspect(actual)))
	end
end

local function fire(data)
	vim.api.nvim_exec_autocmds("User", {
		pattern = "CodeCompanionOmnigentLifecycle",
		data = data,
	})
end

function M.run()
	local tab_state = require("lib.omnigent-tab-state")
	tab_state.setup()

	local first_tab = vim.api.nvim_get_current_tabpage()
	vim.cmd("tabnew")
	local agent_tab = vim.api.nvim_get_current_tabpage()
	local chat_buf = vim.api.nvim_create_buf(false, true)
	vim.b[chat_buf].cc_tab_owner = agent_tab
	vim.api.nvim_set_current_buf(chat_buf)
	vim.cmd("tabprevious")

	local base = { bufnr = chat_buf, session_id = "conv_test" }
	fire(vim.tbl_extend("force", base, { kind = "message_delta", response_id = "ignored" }))
	eq(tab_state.state(agent_tab), nil, "content updates do not create tab state")
	fire(vim.tbl_extend("force", base, {
		kind = "turn_started",
		response_id = "resp_1",
		active_response_id = "resp_1",
		status = "running",
		pending_elicitations = 0,
	}))
	eq(tab_state.state(agent_tab).phase, "running", "background turn starts running")
	eq({ tab_state.marker(agent_tab) }, { "⚙", "running" }, "running marker")

	fire(vim.tbl_extend("force", base, {
		kind = "elicitation",
		response_id = "resp_1",
		active_response_id = "resp_1",
		status = "running",
		pending_elicitations = 1,
	}))
	eq(tab_state.state(agent_tab).phase, "waiting", "elicitation takes precedence")
	eq({ tab_state.marker(agent_tab) }, { "!", "waiting" }, "waiting marker")

	vim.api.nvim_set_current_tabpage(agent_tab)
	eq(tab_state.state(agent_tab).phase, "waiting", "viewing does not clear waiting")
	fire(vim.tbl_extend("force", base, {
		kind = "elicitation_resolved",
		response_id = "resp_1",
		active_response_id = "resp_1",
		status = "running",
		pending_elicitations = 0,
	}))
	eq(tab_state.state(agent_tab).phase, "running", "resolved elicitation returns to running")

	fire(vim.tbl_extend("force", base, {
		kind = "turn_completed",
		response_id = "resp_1",
		status = "idle",
		pending_elicitations = 0,
	}))
	eq(tab_state.state(agent_tab).unread, false, "completion on current tab is already viewed")

	vim.api.nvim_set_current_tabpage(first_tab)
	fire(vim.tbl_extend("force", base, {
		kind = "turn_started",
		response_id = "resp_2",
		active_response_id = "resp_2",
		status = "running",
		pending_elicitations = 0,
	}))
	fire(vim.tbl_extend("force", base, {
		kind = "turn_completed",
		response_id = "resp_2",
		status = "idle",
		pending_elicitations = 0,
	}))
	eq(tab_state.state(agent_tab).unread, true, "background completion is unread")
	eq({ tab_state.marker(agent_tab) }, { "✓", "unread" }, "unread marker")

	vim.api.nvim_set_current_tabpage(agent_tab)
	eq(tab_state.state(agent_tab).unread, false, "TabEnter acknowledges completion")

	vim.api.nvim_set_current_tabpage(first_tab)
	fire(vim.tbl_extend("force", base, {
		kind = "turn_started",
		response_id = "resp_new",
		active_response_id = "resp_new",
		status = "running",
		pending_elicitations = 0,
	}))
	fire(vim.tbl_extend("force", base, {
		kind = "turn_completed",
		response_id = "resp_old",
		active_response_id = "resp_new",
		status = "running",
		pending_elicitations = 0,
	}))
	eq(tab_state.state(agent_tab).response_id, "resp_new", "stale completion is ignored")
	eq(tab_state.state(agent_tab).phase, "running", "stale completion keeps newer turn running")

	fire(vim.tbl_extend("force", base, {
		kind = "turn_cancelled",
		response_id = "resp_new",
		status = "idle",
		pending_elicitations = 0,
	}))
	eq(tab_state.state(agent_tab).phase, "idle", "cancellation returns to idle")
	eq(tab_state.state(agent_tab).unread, false, "cancellation is not unread")

	fire(vim.tbl_extend("force", base, {
		kind = "turn_started",
		response_id = "resp_failed",
		active_response_id = "resp_failed",
		status = "running",
		pending_elicitations = 0,
	}))
	fire(vim.tbl_extend("force", base, {
		kind = "stream_error",
		response_id = "resp_failed",
		active_response_id = "resp_failed",
		status = "running",
		pending_elicitations = 0,
	}))
	eq(tab_state.state(agent_tab).phase, "failed", "stream failure is terminal")
	eq({ tab_state.marker(agent_tab) }, { "×", "failed" }, "failed marker")

	vim.api.nvim_tabpage_set_var(agent_tab, "tab_name", "omni%chat")
	local tabline = require("lib.agent-tabline")
	vim.o.tabline = "%!v:lua.require'lualine'.tabline()"
	tabline.setup()
	eq(vim.o.tabline, "%!v:lua._tabline()", "agent tabline reclaims the option after plugin setup")
	eq(vim.o.showtabline, 2, "agent tabline remains visible")
	local rendered = tabline.render()
	if not rendered:find("2:omni%%chat", 1, true) then
		error("tabline did not render an escaped tab name: " .. rendered)
	end
	if not rendered:find("×", 1, true) or not rendered:find("", 1, true) then
		error("tabline did not render status and pointed separator: " .. rendered)
	end
	vim.api.nvim_eval_statusline(rendered, { use_tabline = true })
	local fill_hl = vim.api.nvim_get_hl(0, { name = "AgentTabFill", link = false })
	local inactive_hl = vim.api.nvim_get_hl(0, { name = "AgentTabInactive", link = false })
	local running_active_hl = vim.api.nvim_get_hl(0, { name = "AgentTabRunningActive", link = false })
	local active_hl = vim.api.nvim_get_hl(0, { name = "AgentTabActive", link = false })
	if inactive_hl.bg == fill_hl.bg then
		error("inactive tab background is indistinguishable from tabline fill")
	end
	if running_active_hl.fg == active_hl.bg then
		error("active running marker is indistinguishable from selected tab background")
	end

	vim.api.nvim_exec_autocmds("User", {
		pattern = "CodeCompanionChatClosed",
		data = { bufnr = chat_buf },
	})
	eq(tab_state.state(agent_tab), nil, "chat close clears tab state")

	vim.api.nvim_set_current_tabpage(agent_tab)
	vim.cmd("tabclose")
	local handled_closed_tab = pcall(fire, vim.tbl_extend("force", base, {
		kind = "turn_started",
		response_id = "resp_after_close",
		active_response_id = "resp_after_close",
	}))
	eq(handled_closed_tab, true, "events for a closed owner tab are ignored")

	-- Two-way tab-name <-> omnigent session-title integration.
	do
		local set_calls = {}
		local fake_session = {
			session_id = "conv_title",
			title = nil,
			set_config = function(self, key, value)
				set_calls[#set_calls + 1] = { key = key, value = value }
				self.title = self.title -- no-op; push_title owns the local update
				return true, nil -- pretend the PATCH succeeded
			end,
		}
		local title_buf = vim.api.nvim_create_buf(false, true)
		tab_state._set_chat_resolver(function(bufnr)
			if bufnr == title_buf then
				return { omnigent_session = fake_session }
			end
			return nil
		end)

		vim.cmd("tabnew")
		local title_tab = vim.api.nvim_get_current_tabpage()
		vim.api.nvim_tabpage_set_var(title_tab, "codecompanion_chat_bufnr", title_buf)

		-- tab -> session: a fresh session (no title) adopts the user-named tab.
		vim.api.nvim_tabpage_set_var(title_tab, "tab_name", "auth-work")
		tab_state.reconcile_title(title_tab, fake_session)
		vim.wait(200, function()
			return #set_calls > 0
		end)
		eq(set_calls, { { key = "title", value = "auth-work" } }, "fresh session adopts tab name as title")
		eq(fake_session.title, "auth-work", "push updates the local title on success")

		-- idempotent: no redundant PATCH when the title already matches the tab.
		tab_state.push_title(title_tab, fake_session)
		vim.wait(50)
		eq(#set_calls, 1, "no redundant PATCH when title already matches")

		-- session -> tab: a resumed session with a title overwrites the tab name.
		fake_session.title = "resumed-topic"
		tab_state.reconcile_title(title_tab, fake_session)
		local ok_tn, tn = pcall(vim.api.nvim_tabpage_get_var, title_tab, "tab_name")
		eq(ok_tn and tn, "resumed-topic", "durable title wins on resume (session -> tab)")

		vim.api.nvim_set_current_tabpage(title_tab)
		vim.cmd("tabclose")
		tab_state._set_chat_resolver(nil)
	end

	-- Background title refresh (session -> tab): a rename made elsewhere is
	-- pulled in on focus, throttled per session.
	do
		local get_calls = 0
		local fake_session = {
			session_id = "conv_refresh",
			title = "local-old",
			set_config = function()
				return true, nil
			end,
			client = {
				request_async = function(_, _, _, _, cb)
					get_calls = get_calls + 1
					cb({ title = "web-renamed" }, nil)
				end,
			},
		}
		local rbuf = vim.api.nvim_create_buf(false, true)
		tab_state._set_chat_resolver(function(bufnr)
			if bufnr == rbuf then
				return { omnigent_session = fake_session }
			end
			return nil
		end)

		vim.cmd("tabnew")
		local rtab = vim.api.nvim_get_current_tabpage()
		vim.api.nvim_tabpage_set_var(rtab, "codecompanion_chat_bufnr", rbuf)
		vim.api.nvim_tabpage_set_var(rtab, "tab_name", "local-old")

		tab_state.refresh_title(rtab)
		local ok_r, tn_r = pcall(vim.api.nvim_tabpage_get_var, rtab, "tab_name")
		eq(ok_r and tn_r, "web-renamed", "external title drift is adopted on refresh")
		eq(fake_session.title, "web-renamed", "refresh updates the cached title")
		eq(get_calls, 1, "refresh issued one GET")

		-- throttle: an immediate second refresh does not hit the server again.
		tab_state.refresh_title(rtab)
		eq(get_calls, 1, "throttle suppresses a rapid second poll")

		vim.api.nvim_set_current_tabpage(rtab)
		vim.cmd("tabclose")
		tab_state._set_chat_resolver(nil)
	end

	print("omnigent-tab-state-spec: all checks passed")
end

return M
