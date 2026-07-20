-- Live smoke test. Run with the full config; creates one real Omnigent session.

local M = {}

local function find_new_chat(existing)
	for _, bufnr in ipairs(vim.api.nvim_list_bufs()) do
		if
			not existing[bufnr]
			and vim.api.nvim_buf_is_valid(bufnr)
			and vim.bo[bufnr].filetype == "codecompanion"
		then
			return bufnr
		end
	end
	return nil
end

local function count(values, wanted)
	local total = 0
	for _, value in ipairs(values) do
		if value == wanted then
			total = total + 1
		end
	end
	return total
end

function M.run()
	local existing = {}
	for _, bufnr in ipairs(vim.api.nvim_list_bufs()) do
		existing[bufnr] = true
	end

	local control_tab = vim.api.nvim_get_current_tabpage()
	vim.cmd("tabnew")
	local agent_tab = vim.api.nvim_get_current_tabpage()
	vim.t.tab_name = "omni-e2e"
	vim.cmd("CodeCompanionChat adapter=omnigent")

	local chat_bufnr
	assert(vim.wait(5000, function()
		chat_bufnr = find_new_chat(existing)
		return chat_bufnr ~= nil
	end, 50), "CodeCompanion chat did not open")

	assert(vim.b[chat_bufnr].cc_tab_owner == agent_tab, "chat was not assigned to its opening tab")
	local chat = assert(require("codecompanion").buf_get_chat(chat_bufnr), "chat object missing")
	local lifecycle = {}
	local group = vim.api.nvim_create_augroup("omnigent_tab_e2e", { clear = true })
	vim.api.nvim_create_autocmd("User", {
		group = group,
		pattern = "CodeCompanionOmnigentLifecycle",
		callback = function(args)
			if args.data and args.data.bufnr == chat_bufnr then
				lifecycle[#lifecycle + 1] = args.data.kind
			end
		end,
	})

	chat:add_message({ role = "user", content = "Reply with exactly: ok" })
	chat:submit()
	vim.api.nvim_set_current_tabpage(control_tab)

	local tab_state = require("lib.omnigent-tab-state")
	assert(vim.wait(120000, function()
		local current = tab_state.state(agent_tab)
		return current
			and current.phase == "idle"
			and current.unread
			and count(lifecycle, "turn_completed") == 1
	end, 100), "Omnigent turn did not complete with unread tab state: " .. vim.inspect(lifecycle))

	assert(count(lifecycle, "turn_started") == 1, "expected one turn_started: " .. vim.inspect(lifecycle))
	assert(count(lifecycle, "turn_completed") == 1, "expected one turn_completed: " .. vim.inspect(lifecycle))
	local marker, marker_kind = tab_state.marker(agent_tab)
	assert(marker == "✓" and marker_kind == "unread", "missing unread marker")
	assert(require("lib.agent-tabline").render():find("✓", 1, true), "tabline did not render unread marker")

	vim.api.nvim_set_current_tabpage(agent_tab)
	assert(tab_state.state(agent_tab).unread == false, "TabEnter did not acknowledge completion")
	assert(tab_state.marker(agent_tab) == nil, "acknowledged tab still has a marker")

	local session_id = chat.omnigent_session_id
	chat:close()
	assert(vim.wait(2000, function()
		return tab_state.state(agent_tab) == nil
	end, 50), "chat close did not clear tab state")

	vim.api.nvim_del_augroup_by_id(group)
	if vim.api.nvim_tabpage_is_valid(agent_tab) then
		vim.api.nvim_set_current_tabpage(agent_tab)
		vim.cmd("tabclose")
	end
	vim.api.nvim_set_current_tabpage(control_tab)
	print("omnigent-tab-e2e: passed (" .. tostring(session_id) .. ")")
end

return M
