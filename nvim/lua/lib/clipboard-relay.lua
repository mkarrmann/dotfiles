-- Clipboard relay for headless nvs sessions.
--
-- WHY THIS EXISTS:
-- Neovim runs as a headless server (nvim --headless --listen PORT) on devvms,
-- with a thin TUI client (nvim --server localhost:PORT --remote-ui) on the Mac
-- connected through ET tunnels. The headless server has no terminal, so:
--
--   1. OSC 52 clipboard sequences have nowhere to go (no TTY on the server).
--   2. The built-in OSC 52 provider writes to channel 2 (TUI) — nonexistent.
--   3. The --remote-ui RPC protocol has no clipboard channel (upstream gap).
--   4. The existing TextYankPost autocmd in autocmds.lua calls osc52.copy(),
--      which silently fails on headless servers.
--
-- HOW IT WORKS:
-- A reverse ET tunnel (-r 8765:8765) connects the devvm's localhost:8765 back
-- to the Mac, where nvs-clip-listen pipes incoming data to pbcopy.
--
-- On yank, this module sends the text to localhost:8765 via nc (async, never
-- blocks Neovim). If the tunnel is down, nc fails silently — the yank still
-- succeeds in the register.
--
-- For paste: "+p returns the last yanked content. For pasting from the Mac
-- clipboard, use Cmd+V (Ghostty sends it as bracketed paste).
--
-- COPY FLOW:
--   yank → vim.g.clipboard copy (for "+y) or TextYankPost autocmd (for y)
--     → vim.fn.jobstart nc → reverse ET tunnel → Mac nvs-clip-listen → pbcopy
--
-- LOADING (why --cmd + VimEnter):
-- This module must be loaded via --cmd (not -c) in bin/nvs. --cmd runs before
-- init.lua, so vim.g.clipboard is set before Neovim's provider detection.
-- Without this, Neovim sees no provider during init (no pbcopy/xclip on
-- headless) and emits "clipboard: No provider" to the user on first yank.
--
-- However, --cmd also means plugins load AFTER us, and any plugin that sets
-- vim.g.clipboard (e.g. an osc52 provider) would silently override our relay.
-- To prevent this, a VimEnter autocmd reasserts vim.g.clipboard after all
-- plugins have finished loading. This makes the relay survive both init-time
-- detection and plugin overrides.

local CLIP_PORT = tonumber(vim.env.NVS_CLIP_PORT) or 8765
local last_clip = {}
local last_regtype = "v"

local function send_to_clipboard(lines, regtype)
	last_clip = lines
	last_regtype = regtype or "v"
	pcall(function()
		local text = table.concat(lines, "\n")
		local job = vim.fn.jobstart(
			{ "nc", "-w", "1", "localhost", tostring(CLIP_PORT) },
			{ stdin = "pipe" }
		)
		if job > 0 then
			vim.fn.chansend(job, text)
			vim.fn.chanclose(job, "stdin")
		end
	end)
end

local function set_clipboard_provider()
	vim.g.clipboard = {
		name = "nvs-relay",
		copy = {
			["+"] = send_to_clipboard,
			["*"] = send_to_clipboard,
		},
		paste = {
			["+"] = function()
				return { last_clip, last_regtype }
			end,
			["*"] = function()
				return { last_clip, last_regtype }
			end,
		},
	}
end

-- Set provider immediately (--cmd runs before init.lua → prevents "No provider")
set_clipboard_provider()

-- Reassert after all plugins load (--cmd means plugins load after us and could
-- override vim.g.clipboard — this ensures our relay always wins)
vim.api.nvim_create_autocmd("VimEnter", {
	once = true,
	callback = set_clipboard_provider,
})

-- Mirror the TextYankPost pattern from autocmds.lua: copy every yank to the
-- Mac clipboard (not just "+y). Skip + and * registers to avoid double-send
-- since vim.g.clipboard already handles those.
local grp = vim.api.nvim_create_augroup("NvsClipboardRelay", { clear = true })
vim.api.nvim_create_autocmd("TextYankPost", {
	group = grp,
	callback = function()
		local e = vim.v.event
		if e.operator == "y" and e.regname ~= "+" and e.regname ~= "*" then
			send_to_clipboard(e.regcontents, e.regtype)
		end
	end,
})
