-- Per-tabpage buffer isolation: each tab keeps its own buffer list, so buffer
-- pickers and :bnext only see the files opened in the current tab. Pairs well
-- with the agent-manager's tab-per-session model.
return {
	{
		"tiagovla/scope.nvim",
		config = function()
			require("scope").setup({})
		end,
	},
}
