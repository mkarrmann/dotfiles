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
			vim.keymap.set("n", "<leader>on", function() pad().open_for_active_task() end, { desc = "Open task note" })
			vim.keymap.set("n", "<leader>ot", function() pad().focus_linked_task() end, { desc = "Focus linked Orchest task" })
			vim.keymap.set("n", "<leader>oU", function() pad().unlink_current() end, { desc = "Unlink pad from Orchest" })
			vim.keymap.set("n", "<leader>os", function() pad().sync_current() end, { desc = "Sync pad path to Orchest" })
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

			vim.api.nvim_create_user_command("PadTask", function()
				pad().focus_linked_task()
			end, { desc = "Focus the Orchest task linked to this note" })

			vim.api.nvim_create_user_command("PadUnlink", function()
				pad().unlink_current()
			end, { desc = "Unlink the current pad note from Orchest" })

			vim.api.nvim_create_user_command("PadSync", function()
				pad().sync_current()
			end, { desc = "Refresh the current pad note path in Orchest" })
		end,
	},
}
