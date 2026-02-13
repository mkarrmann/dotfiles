return {
	{ "christoomey/vim-tmux-navigator", lazy = false },
	{ "mbbill/undotree" },
	{
		"xiyaowong/virtcolumn.nvim",
		event = "VeryLazy",
	},
	{
		"amitds1997/remote-nvim.nvim",
		cond = not vim.g.vscode,
		dependencies = { "nvim-lua/plenary.nvim", "MunifTanjim/nui.nvim" },
		opts = {},
	},
}
