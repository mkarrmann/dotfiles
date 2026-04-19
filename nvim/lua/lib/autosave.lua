-- Per-buffer autosave + autoreload. Default-on for files inside a
-- source-controlled repo (.git/.sl/.hg, walking upward). Toggle with
-- :AutoSaveToggle. State is exposed via M.status() for the winbar.

local M = {}

local function in_repo(path)
	if not path or path == "" then
		return false
	end
	-- No type filter: .git can be a file (worktrees) as well as a directory.
	return #vim.fs.find({ ".git", ".sl", ".hg" }, { path = path, upward = true }) > 0
end

local function detect_default(buf)
	if vim.bo[buf].buftype ~= "" then
		return false
	end
	local name = vim.api.nvim_buf_get_name(buf)
	if name == "" then
		return false
	end
	return in_repo(name)
end

function M.is_enabled(buf)
	buf = buf or 0
	return vim.b[buf].autosave_enabled == true
end

function M.toggle(buf)
	buf = buf or vim.api.nvim_get_current_buf()
	vim.b[buf].autosave_enabled = not M.is_enabled(buf)
	vim.notify("autosave " .. (M.is_enabled(buf) and "ON" or "OFF") .. " for this buffer")
	vim.cmd("redrawstatus")
end

-- Winbar indicator. Subtle filled dot when autosave is on; empty otherwise.
function M.status()
	return M.is_enabled() and "●" or ""
end

function M.setup()
	vim.o.autoread = true

	local group = vim.api.nvim_create_augroup("autosave_and_autoread", { clear = true })

	vim.api.nvim_create_autocmd({ "BufReadPost", "BufNewFile" }, {
		group = group,
		callback = function(args)
			if vim.b[args.buf].autosave_enabled == nil then
				vim.b[args.buf].autosave_enabled = detect_default(args.buf)
			end
		end,
	})

	vim.api.nvim_create_autocmd({ "InsertLeave", "TextChanged" }, {
		group = group,
		callback = function(args)
			if vim.b[args.buf].autosave_enabled and vim.bo[args.buf].modified then
				vim.cmd("silent! update")
			end
		end,
	})

	vim.api.nvim_create_autocmd({ "FocusGained", "BufEnter", "CursorHold", "CursorHoldI" }, {
		group = group,
		callback = function(args)
			if vim.fn.mode() == "c" then
				return
			end
			if vim.b[args.buf].autosave_enabled then
				vim.cmd("checktime " .. args.buf)
			end
		end,
	})

	vim.api.nvim_create_user_command("AutoSaveToggle", function()
		M.toggle()
	end, {})
end

return M
