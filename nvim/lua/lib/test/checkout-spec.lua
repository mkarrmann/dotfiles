-- Headless test for lib.checkout.
-- nvim --headless -u NONE --cmd "set rtp+=$HOME/dotfiles/nvim" -c "lua require('lib.test.checkout-spec').run()" -c "qa!"

local M = {}

local function assert_eq(actual, expected, label)
	if actual ~= expected then
		error(string.format("%s: expected %s, got %s", label, vim.inspect(expected), vim.inspect(actual)))
	end
end

function M.run()
	local checkout = require("lib.checkout")
	local home = vim.env.HOME
	local build_root = vim.env.BUILD_ROOT or ("/data/users/" .. vim.env.USER .. "/builds")
	local trunk = "/fbsource/fbcode/github/presto-trunk"

	assert_eq(checkout.workspace(home .. "/checkout1" .. trunk), "checkout1", "checkout1 workspace")
	assert_eq(checkout.suffix(home .. "/checkout1" .. trunk), "", "primary suffix")
	assert_eq(checkout.maven_repo(home .. "/checkout1" .. trunk), nil, "primary maven repo")

	assert_eq(checkout.workspace(home .. "/checkout2" .. trunk), "checkout2", "checkout2 workspace")
	assert_eq(checkout.suffix(home .. "/checkout2" .. trunk), "-checkout2", "checkout2 suffix")
	assert_eq(
		checkout.maven_repo(home .. "/checkout2" .. trunk),
		build_root .. "/m2-repo-checkout2",
		"checkout2 maven repo"
	)

	assert_eq(checkout.workspace(home .. "/wt/feature" .. trunk), "feature", "worktree workspace")
	assert_eq(
		checkout.maven_repo(home .. "/wt/feature" .. trunk),
		build_root .. "/m2-repo-feature",
		"worktree maven repo"
	)

	-- ~/.localrc treats anything else as the primary for Maven; jdtls state still
	-- gets its own directory so unrelated trees never share a workspace.
	for _, path in ipairs({ "/tmp/elsewhere/presto-trunk", home .. "/checkout5" .. trunk }) do
		assert_eq(checkout.workspace(path), nil, path .. " workspace")
		assert_eq(checkout.maven_repo(path), nil, path .. " maven repo")
		assert_eq(#checkout.suffix(path), 9, path .. " suffix is a path hash")
	end
	assert(checkout.suffix("/tmp/a") ~= checkout.suffix("/tmp/b"), "distinct trees get distinct suffixes")

	local header =
		'<?xml version="1.0"?>\n<settings xmlns="http://maven.apache.org/SETTINGS/1.0.0">\n  <mirrors/>\n</settings>'
	assert_eq(
		checkout.with_local_repository(header, "/r"),
		'<?xml version="1.0"?>\n<settings xmlns="http://maven.apache.org/SETTINGS/1.0.0">\n  <localRepository>/r</localRepository>\n  <mirrors/>\n</settings>',
		"inserts after the settings element"
	)
	assert_eq(
		checkout.with_local_repository("<settings><localRepository>/old</localRepository></settings>", "/r"),
		"<settings><localRepository>/r</localRepository></settings>",
		"replaces an existing localRepository"
	)
	assert_eq(
		checkout.with_local_repository(nil, "/r"),
		"<settings>\n  <localRepository>/r</localRepository>\n</settings>\n",
		"no base settings"
	)

	print("checkout-spec: OK")
end

return M
