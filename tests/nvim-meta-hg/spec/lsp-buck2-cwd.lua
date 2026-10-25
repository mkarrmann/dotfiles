-- buck2@meta starts in the Buck project it serves, not in nvim's cwd: an nvs
-- session runs in the workspace root, which isn't a Buck project, and
-- `buck2 lsp` exits there. A fake buck2 records where it was started and
-- exits like the real one outside a Buck project.
local H = dofile(os.getenv("NVIM_META_HG_LIB"))

H.run(function()
	local workspace = H.tmp .. "/checkout"
	local project = workspace .. "/fbsource"
	vim.fn.mkdir(project .. "/pkg", "p")
	H.write(project .. "/.buckconfig", "")
	H.write(project .. "/pkg/BUCK", 'python_library(name = "pkg")\n')

	local started_in = H.tmp .. "/buck2-cwd"
	local bin = H.tmp .. "/bin"
	vim.fn.mkdir(bin, "p")
	H.write(bin .. "/buck2", table.concat({
		"#!/bin/sh",
		'pwd -P > "' .. started_in .. '"',
		"[ -f .buckconfig ] || { echo 'Error: not in a Buck project' >&2; exit 1; }",
		"",
	}, "\n"))
	vim.uv.fs_chmod(bin .. "/buck2", 493)
	vim.env.PATH = bin .. ":" .. vim.env.PATH

	vim.cmd.cd(workspace)
	vim.cmd.edit(project .. "/pkg/BUCK")
	H.eq(vim.bo.filetype, "starlark", "BUCK is starlark, which buck2@meta serves")
	H.check(vim.wait(5000, function()
		return vim.uv.fs_stat(started_in) ~= nil
	end, 50), "buck2 started")
	H.eq(vim.trim(H.read(started_in)), vim.uv.fs_realpath(project), "buck2 started in the project, not the workspace root")
	vim.wait(500)
	H.check(not vim.iter(H.notifications):any(function(n)
		return tostring(n.msg):find("buck2@meta quit", 1, true) ~= nil
	end), "no exit error: " .. vim.inspect(H.notifications))
end)
