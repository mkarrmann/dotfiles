return {
	{ "christoomey/vim-tmux-navigator", lazy = false },
	{ "mbbill/undotree" },
	{
		"lewis6991/satellite.nvim",
		config = function(_, opts)
			require("lib.satellite-diff")
			require("satellite").setup(opts)
		end,
	},
	{
		"xiyaowong/virtcolumn.nvim",
		event = "VeryLazy",
	},
	{
		"amitds1997/remote-nvim.nvim",
		cond = not vim.g.vscode,
		dependencies = { "nvim-lua/plenary.nvim", "MunifTanjim/nui.nvim" },
		opts = {
			client_callback = function(port, workspace_config)
				if vim.env.TMUX then
					local cmd = ("tmux new-window -n 'Remote: %s' 'nvim --server localhost:%s --remote-ui'")
						:format(workspace_config.host, port)
					vim.fn.jobstart(cmd, { detach = true })
				else
					require("remote-nvim.ui").float_term(
						("nvim --server localhost:%s --remote-ui"):format(port),
						function(exit_code)
							if exit_code ~= 0 then
								vim.notify(
									("Remote UI exited with code %d"):format(exit_code),
									vim.log.levels.ERROR
								)
							end
						end
					)
				end
			end,
		},
	},
}
