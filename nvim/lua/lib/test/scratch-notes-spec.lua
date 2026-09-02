-- nvim --headless -u NONE --cmd "set rtp+=$HOME/dotfiles/nvim" -c "lua require('lib.test.scratch-notes-spec').run()" -c "qa!"
local M = {}

local function assert_equal(actual, expected, message)
	if actual ~= expected then
		error(string.format("%s: expected %s, got %s", message, vim.inspect(expected), vim.inspect(actual)))
	end
end

function M.run()
	local root = vim.fn.tempname()
	vim.fn.mkdir(root .. "/Pad", "p")
	vim.fn.mkdir(root .. "/Archive/Pad", "p")
	vim.g.obsidian_vault = root
	package.loaded["lib.scratch-notes"] = nil
	local notes = require("lib.scratch-notes")

	local plain = root .. "/Pad/plain.md"
	vim.fn.writefile({ "# Plain", "", "body" }, plain)
	local note_id = assert(notes.ensure_note_id(plain))
	assert(note_id:match("^note_[0-9a-f]+$"), "generated note ID has expected shape")
	assert_equal(#note_id, 37, "generated note ID length")
	assert_equal(notes.read_note_id(plain), note_id, "reads generated note ID")
	assert_equal(notes.ensure_note_id(plain), note_id, "preserves existing note ID")

	local archived = root .. "/Archive/Pad/renamed.md"
	assert_equal(vim.fn.rename(plain, archived), 0, "moves fixture into archive")
	assert_equal(notes.find_note_by_id(note_id), archived, "finds ID after rename/archive")
	assert_equal(notes.relative_note_path(archived), "Archive/Pad/renamed.md", "uses vault-relative paths")
	local outside_path, outside_error = notes.relative_note_path(root .. "/../outside.md")
	assert_equal(outside_path, nil, "rejects note paths outside the vault")
	assert(outside_error:match("outside the configured vault"), outside_error)
	local outside_root = vim.fn.tempname()
	vim.fn.mkdir(outside_root, "p")
	local outside_note = outside_root .. "/outside.md"
	local outside_id = "note_33333333333333333333333333333333"
	vim.fn.writefile({ "---", "orchest_note_id: " .. outside_id, "---", "", "# Outside" }, outside_note)
	local symlink = root .. "/Pad/outside-link.md"
	assert_equal(vim.uv.fs_symlink(outside_note, symlink), true, "creates outside-vault symlink")
	local symlink_path, symlink_error = notes.relative_note_path(symlink)
	assert_equal(symlink_path, nil, "rejects symlinks that escape the vault")
	assert(symlink_error:match("outside the configured vault"), symlink_error)
	local escaped_note, escaped_error = notes.find_note_by_id(outside_id)
	assert_equal(escaped_note, nil, "does not open a note through an outside-vault symlink")
	assert(escaped_error:match("No note found"), escaped_error)

	local duplicate = root .. "/Pad/duplicate.md"
	vim.fn.writefile(vim.fn.readfile(archived), duplicate)
	local found, duplicate_error = notes.find_note_by_id(note_id)
	assert_equal(found, nil, "duplicate ID fails closed")
	assert(duplicate_error:match("Multiple notes"), duplicate_error)

	local malformed = root .. "/Pad/malformed.md"
	vim.fn.writefile({ "---", "orchest_note_id: nope", "---", "# Bad" }, malformed)
	local malformed_id, malformed_error = notes.read_note_id(malformed)
	assert_equal(malformed_id, nil, "malformed ID is rejected")
	assert(malformed_error:match("Malformed orchest_note_id"), malformed_error)

	local original_system = vim.system
	local original_input = vim.ui.input
	local original_notify = vim.notify
	local calls = {}
	local responses = {
		{ ok = true, kind = "create", token = "token-1", taskId = "task-1", taskTitle = "Task One" },
		{ ok = true, noteId = "unused", taskId = "task-1", taskTitle = "Task One" },
	}
	vim.env.ORCHEST_INTEGRATION_URL = "http://127.0.0.1:13100"
	vim.env.NVS_SESSION_NAME = "CCO-checkout1"
	vim.system = function(argv, _, callback)
		table.insert(calls, argv)
		local response = table.remove(responses, 1)
		callback({ code = 0, stdout = vim.json.encode(response), stderr = "" })
		return {}
	end
	vim.ui.input = function(_, callback)
		callback("Task One")
	end
	vim.notify = function() end
	local opened = nil
	notes.open_in_panel = function(file)
		opened = file
	end

	notes.open_for_active_task()
	assert(vim.wait(1000, function() return #calls == 2 end), "prepare/commit flow timed out")
	local prepare_payload = vim.json.decode(calls[1][13])
	assert_equal(prepare_payload.editorContext.sessionName, "CCO-checkout1", "sends NVS session verbatim")
	local commit_payload = vim.json.decode(calls[2][13])
	assert_equal(commit_payload.token, "token-1", "commits opaque prepare token")
	assert_equal(commit_payload.relativePath, "Pad/task-one.md", "commits relative path")
	assert(commit_payload.noteId:match("^note_[0-9a-f]+$"), "commits stable note ID")
	assert_equal(opened, root .. "/Pad/task-one.md", "opens the attached note")
	assert_equal(notes.read_note_id(opened), commit_payload.noteId, "writes committed ID into frontmatter")

	vim.system = original_system
	vim.ui.input = original_input
	vim.notify = original_notify
	vim.env.ORCHEST_INTEGRATION_URL = nil
	vim.env.NVS_SESSION_NAME = nil

	vim.fn.delete(root, "rf")
	vim.fn.delete(outside_root, "rf")
	vim.g.obsidian_vault = nil
	print("scratch-notes-spec: ok")
end

return M
