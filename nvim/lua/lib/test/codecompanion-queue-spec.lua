-- Headless smoke test for the per-entry CodeCompanion queue.
-- Run with:
--   nvim --headless -u NONE \
--     --cmd "set rtp+=$HOME/dotfiles/nvim" \
--     -c "lua require('lib.test.codecompanion-queue-spec').run()" \
--     -c "qa!"
--
-- Covers the entry buffers/windows, the hold rule (an entry being edited
-- pauses the queue at that point, while entries ahead of it keep flowing),
-- commit, and drop.

local M = {}

local function eq(got, want, label)
	if got ~= want then
		error(string.format("%s: expected %s, got %s", label, vim.inspect(want), vim.inspect(got)))
	end
end

local function truthy(got, label)
	if not got then
		error(string.format("%s: expected truthy, got %s", label, vim.inspect(got)))
	end
end

-- Entry windows top-to-bottom. nvim_tabpage_list_wins order is not guaranteed
-- to match the visual stack, so sort by screen row.
local function entry_stack(tab)
	local ws = {}
	for _, w in ipairs(vim.api.nvim_tabpage_list_wins(tab)) do
		local b = vim.api.nvim_win_get_buf(w)
		if vim.bo[b].filetype == "codecompanion_queue_entry" then
			ws[#ws + 1] = { win = w, buf = b, row = vim.api.nvim_win_get_position(w)[1] }
		end
	end
	table.sort(ws, function(a, b)
		return a.row < b.row
	end)
	return ws
end

local function press(buf, lhs)
	local want = vim.api.nvim_replace_termcodes(lhs, true, false, true)
	for _, m in ipairs(vim.api.nvim_buf_get_keymap(buf, "n")) do
		local got = vim.api.nvim_replace_termcodes(m.lhs, true, false, true)
		if (m.lhs == lhs or got == want) and m.callback then
			m.callback()
			return
		end
	end
	error("no keymap " .. lhs .. " in buffer " .. buf)
end

-- Headless nvim has no UI, so neither feedkeys nor nvim_input reaches the main
-- loop where TextChanged is evaluated. Write the text and fire the event the
-- way real typing would -- which is the wiring under test.
local function edit_entry(buf, text)
	vim.api.nvim_buf_set_lines(buf, 0, -1, false, vim.split(text, "\n"))
	vim.api.nvim_exec_autocmds("TextChanged", { buffer = buf })
end

function M.run()
	-- The split stack does not fit in the default headless 80x24.
	vim.o.lines = 60
	vim.o.columns = 200

	local submitted = {}
	local fake_chat

	package.loaded["codecompanion"] = {
		buf_get_chat = function(_)
			return fake_chat
		end,
	}
	-- Stubbing the root module hides the real submodules from `require`, so the
	-- ones the queue reaches for have to be stubbed too.
	package.loaded["codecompanion.providers.completion"] = {
		slash_commands = function()
			return {}
		end,
	}
	package.loaded["codecompanion.triggers"] = { mappings = { acp_slash_commands = "\\" } }

	local q = require("lib.codecompanion-queue")

	local tab = vim.api.nvim_get_current_tabpage()
	local chat_buf = vim.api.nvim_create_buf(false, true)
	vim.b[chat_buf].cc_tab_owner = tab
	vim.api.nvim_win_set_buf(vim.api.nvim_get_current_win(), chat_buf)
	vim.api.nvim_buf_set_lines(chat_buf, 0, -1, false, { "## Me", "" })

	fake_chat = {
		bufnr = chat_buf,
		adapter = { type = "omnigent", name = "omnigent" },
		submit = function()
			local lines = vim.api.nvim_buf_get_lines(chat_buf, 0, -1, false)
			submitted[#submitted + 1] = lines[#lines]
			-- Emulate ready_for_input writing a fresh trailing `## Me`.
			vim.api.nvim_buf_set_lines(chat_buf, -1, -1, false, { "", "## Me", "" })
		end,
	}

	q.on_chat_opened(chat_buf)
	local input_buf = q.bufnr()
	truthy(input_buf, "input box created")

	-- Busy chat, so sends queue instead of going out immediately.
	q.on_request_started(chat_buf, 1)
	for _, msg in ipairs({ "first", "second", "third" }) do
		vim.api.nvim_buf_set_lines(input_buf, 0, -1, false, { msg })
		press(input_buf, "<C-S>")
	end

	local stack = entry_stack(tab)
	eq(#stack, 3, "one window per queued entry")
	eq(vim.bo[stack[1].buf].modifiable, true, "entry buffers are editable")
	truthy(
		vim.api.nvim_buf_get_name(stack[1].buf):match("cc%-queue://%d+"),
		"entry buffer is named cc-queue://<id>"
	)
	eq(vim.api.nvim_buf_get_lines(stack[1].buf, 0, -1, false)[1], "first", "entry holds its text")
	truthy(stack[1].row < stack[2].row and stack[2].row < stack[3].row, "stack is in flush order")
	truthy(vim.wo[stack[1].win].winbar:match("»"), "queued entry labelled in winbar")

	-- Yellow means "this goes out"; the input box holds the one thing that does
	-- not, so it must stay plain however full the queue is.
	truthy(vim.wo[stack[1].win].winhighlight:match("CCQueuedNormal"), "queued entry is tinted")
	eq(vim.wo[vim.fn.bufwinid(input_buf)].winhighlight, "", "input box is not tinted by a queue")

	-- Edit #2: it and #3 go held, #1 keeps flowing.
	edit_entry(stack[2].buf, "second EDITED")
	truthy(vim.wo[stack[2].win].winbar:match("✎"), "edited entry is marked editing")
	truthy(vim.wo[stack[3].win].winbar:match("⏸"), "entry queued after the edit is held")
	truthy(vim.wo[stack[1].win].winbar:match("»"), "entry queued before the edit still flows")

	truthy(vim.wo[stack[2].win].winhighlight:match("CCHeldNormal"), "an edited entry is not tinted queued")
	truthy(vim.wo[stack[3].win].winhighlight:match("CCHeldNormal"), "a held entry is not tinted queued")
	truthy(vim.wo[stack[1].win].winhighlight:match("CCQueuedNormal"), "the flowing entry stays tinted")

	-- Turn ends: #1 is clean and ahead of the hold point, so it flushes.
	q.on_request_finished(chat_buf, 1, "success")
	q.on_chat_done(chat_buf)
	eq(submitted[#submitted], "first", "clean head flushes past a later edit")

	-- Next turn: the head is now the entry being edited, so the queue pauses.
	local before = #submitted
	q.on_chat_done(chat_buf)
	eq(#submitted, before, "an edited head pauses the queue")

	-- Commit releases the hold and, with the chat idle, resumes the flush.
	press(stack[2].buf, "<C-S>")
	eq(submitted[#submitted], "second EDITED", "commit sends the edited text")

	eq(#entry_stack(tab), 1, "flushed entries are torn down")
	eq(vim.api.nvim_buf_is_valid(stack[1].buf), false, "flushed entry buffer is wiped")

	-- Drop the survivor.
	q.on_request_started(chat_buf, 2)
	local last = entry_stack(tab)[1]
	press(last.buf, "<C-D>")
	eq(#entry_stack(tab), 0, "<C-d> drops the entry")
	eq(submitted[#submitted], "second EDITED", "dropping does not send")

	-- Hiding the chat closes the entry windows but must not lose the messages
	-- or any uncommitted edit in them.
	for _, msg in ipairs({ "alpha", "beta" }) do
		vim.api.nvim_buf_set_lines(input_buf, 0, -1, false, { msg })
		press(input_buf, "<C-S>")
	end
	edit_entry(entry_stack(tab)[2].buf, "beta EDITED")

	q.on_chat_hidden(chat_buf)
	eq(#entry_stack(tab), 0, "hiding the chat closes the entry windows")

	q.on_chat_opened(chat_buf)
	local restored = entry_stack(tab)
	eq(#restored, 2, "re-opening restores a window per queued entry")
	eq(
		vim.api.nvim_buf_get_lines(restored[2].buf, 0, -1, false)[1],
		"beta EDITED",
		"an uncommitted edit survives hide/re-open"
	)
	truthy(vim.wo[restored[2].win].winbar:match("✎"), "the restored entry is still held")

	print("codecompanion-queue-spec: all checks passed")
end

return M
