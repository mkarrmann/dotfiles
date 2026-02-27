local lazypath = vim.fn.stdpath("data") .. "/lazy/lazy.nvim"
if not (vim.uv or vim.loop).fs_stat(lazypath) then
	local lazyrepo = "https://github.com/folke/lazy.nvim.git"
	local out = vim.fn.system({ "git", "clone", "--filter=blob:none", "--branch=stable", lazyrepo, lazypath })
	if vim.v.shell_error ~= 0 then
		vim.api.nvim_echo({
			{ "Failed to clone lazy.nvim:\n", "ErrorMsg" },
			{ out, "WarningMsg" },
			{ "\nPress any key to exit...", "MoreMsg" },
		}, true, {})
		vim.fn.getchar()
		vim.cmd([[quit]])
		return
	end
end
vim.opt.rtp:prepend(lazypath)

require("config.lazy")

-- Auto-submit a prompt to Claude Code when CLAUDE_AUTO_PROMPT is set.
-- See prompt_new_window() in plugins/claude-agent-manager.lua.
local _auto_prompt = vim.env.CLAUDE_AUTO_PROMPT
if _auto_prompt then
	vim.api.nvim_create_autocmd("User", {
		pattern = "VeryLazy",
		once = true,
		callback = function()
			local f = io.open(_auto_prompt, "r")
			if not f then
				return
			end
			local prompt = f:read("*a")
			f:close()
			os.remove(_auto_prompt)
			if not prompt or prompt == "" then
				return
			end

			-- Close the snacks dashboard buffer before opening the terminal.
			-- The dashboard registers a WinResized autocmd that references its
			-- window; if we just :only to kill that window, the autocmd fires
			-- on the now-invalid window id and errors.  Wiping the buffer first
			-- lets the dashboard's BufWipeout handler clean up its augroup.
			for _, buf in ipairs(vim.api.nvim_list_bufs()) do
				if vim.bo[buf].filetype == "snacks_dashboard" then
					vim.api.nvim_buf_delete(buf, { force = true })
				end
			end

			require("lazy").load({ plugins = { "claudecode.nvim" } })
			require("claudecode.terminal").open({}, "-- " .. prompt)
			vim.cmd("only")
		end,
	})
end
