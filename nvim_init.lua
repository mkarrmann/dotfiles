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
			vim.cmd("ClaudeCodeOpen")

			local needle = prompt:sub(1, math.min(#prompt, 20))
			local attempts = 0
			local sent_text = false
			local function poll()
				attempts = attempts + 1
				for _, buf in ipairs(vim.api.nvim_list_bufs()) do
					if vim.bo[buf].buftype == "terminal" and vim.bo[buf].channel > 0 then
						local chan = vim.bo[buf].channel
						if not sent_text then
							vim.api.nvim_chan_send(chan, prompt)
							sent_text = true
						end
						local lines = vim.api.nvim_buf_get_lines(buf, 0, -1, false)
						for _, line in ipairs(lines) do
							if line:find(needle, 1, true) then
								vim.api.nvim_chan_send(chan, "\r")
								return
							end
						end
					end
				end
				if attempts < 150 then
					vim.defer_fn(poll, 200)
				end
			end
			vim.defer_fn(poll, 200)
		end,
	})
end
