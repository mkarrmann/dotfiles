local function pad()
	return require("lib.scratch-notes")
end

return {
	{
		dir = vim.fn.stdpath("config"),
		name = "obsidian-pad",
		lazy = false,
		init = function()
			vim.keymap.set("n", "<leader>oo", function() pad().toggle() end, { desc = "Toggle pad panel" })
			vim.keymap.set("n", "<leader>on", function() pad().open() end, { desc = "New pad note" })
			vim.keymap.set("n", "<leader>of", function() pad().find() end, { desc = "Find pad notes" })
			vim.keymap.set("n", "<leader>oa", function() pad().archive_current() end, { desc = "Archive current pad" })
			vim.keymap.set("n", "<leader>oA", function() pad().archive_bulk() end, { desc = "Bulk archive pads" })
			vim.keymap.set("n", "<leader>ou", function() pad().unarchive() end, { desc = "Unarchive pad notes" })

			vim.api.nvim_create_user_command("Pad", function(opts)
				pad().open(opts.args ~= "" and opts.args or nil)
			end, { nargs = "?", desc = "Open/create a pad note" })

			vim.api.nvim_create_user_command("PadArchive", function()
				pad().archive_current()
			end, { desc = "Archive current pad note" })
		end,
	},
}
