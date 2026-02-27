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

			require("lazy").load({ plugins = { "claudecode.nvim" } })
			require("claudecode.terminal").open({}, "-- " .. prompt)
		end,
	})
end
