-- Clipboard relay for headless nvs sessions.
-- Sends yanked text to a Mac clipboard listener (reverse-tunneled via ET)
-- using netcat. Paste returns the last yanked content; for pasting from the
-- Mac clipboard, use Cmd+V (Ghostty sends it as bracketed paste).

local CLIP_PORT = 8765
local last_clip = {}

local function send_to_clipboard(lines)
	last_clip = lines
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

vim.g.clipboard = {
	name = "nvs-relay",
	copy = {
		["+"] = send_to_clipboard,
		["*"] = send_to_clipboard,
	},
	paste = {
		["+"] = function()
			return last_clip
		end,
		["*"] = function()
			return last_clip
		end,
	},
}
