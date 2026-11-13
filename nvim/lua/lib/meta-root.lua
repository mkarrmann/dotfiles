-- HACK: meta.nvim finds a repo root by walking up from a file until
-- glob(dir .. "/" .. marker) matches. /mnt/gvfs reports every name it is asked
-- about as a directory, so from a third-party2 header (e.g. a go-to-definition
-- into libgcc) the walk "finds" /mnt/gvfs/third-party2/libgcc/.arcconfig, and
-- arc.read_config then dies with E17 calling readfile() on it. Every marker
-- meta.nvim looks for (.arcconfig, .buckconfig) is a file, so require one.
local M = {}

function M.setup()
	local util = require("meta.util")
	function util.find_root_file_path(path, file, count)
		count = count or 100
		if count <= 0 or path == "/" or path == "" then
			return nil
		end
		if vim.fn.filereadable(path .. "/" .. file) == 1 then
			return path
		end
		return util.find_root_file_path(vim.fn.fnamemodify(path, ":h"), file, count - 1)
	end
end

return M
