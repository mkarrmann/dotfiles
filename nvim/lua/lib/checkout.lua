-- Which paired workspace (~/checkoutN, or an ~/wt/<label> worktree) a path
-- belongs to, and the per-workspace resources that follow from it. Mirrors
-- _workspace_root / _checkout_suffix in ~/.localrc so editor tooling isolates
-- workspaces the same way the shell's Maven helpers do: ~/checkout1 is primary
-- and uses the defaults, every other workspace gets a "-<name>" suffix and its
-- own Maven local repository under $BUILD_ROOT.

local M = {}

local HOME = vim.env.HOME
local BUILD_ROOT = vim.env.BUILD_ROOT or ("/data/users/" .. (vim.env.USER or "") .. "/builds")
local PRIMARY = "checkout1"
-- Only these count as workspaces in ~/.localrc; anything else falls back to
-- the primary there, so it must here too or Maven would read one repo and
-- the shell build write another.
local CHECKOUTS = { checkout1 = true, checkout2 = true, checkout3 = true, checkout4 = true }

local function realpath(path)
	return vim.uv.fs_realpath(path) or vim.fs.normalize(path)
end

--- The workspace containing `path` ("checkoutN", or the label of an ~/wt/<label>
--- worktree), or nil when it is outside all of them.
---@param path string
---@return string|nil
function M.workspace(path)
	local rel = realpath(path):match("^" .. vim.pesc(HOME) .. "/(.+)$")
	if not rel then
		return nil
	end
	local top = rel:match("^[^/]+")
	if CHECKOUTS[top] then
		return top
	end
	return rel:match("^wt/([^/]+)")
end

--- Suffix that keeps per-tree state apart: "" for the primary workspace,
--- "-<workspace>" for the others, and a path hash for trees outside any
--- workspace so that two such trees never share state.
---@param path string
---@return string
function M.suffix(path)
	local ws = M.workspace(path)
	if ws == PRIMARY then
		return ""
	end
	return "-" .. (ws or vim.fn.sha256(realpath(path)):sub(1, 8))
end

--- Maven local repository for the workspace containing `path`, or nil for the
--- default (~/.m2/repository): the primary workspace and trees outside any
--- workspace, exactly as ~/.localrc's build helpers treat them.
---@param path string
---@return string|nil
function M.maven_repo(path)
	local ws = M.workspace(path)
	if not ws or ws == PRIMARY then
		return nil
	end
	return BUILD_ROOT .. "/m2-repo-" .. ws
end

--- `settings_xml` (a Maven settings.xml) with its localRepository set to `repo`.
--- Tools embedding Maven, such as jdtls, take the local repository only from a
--- settings file, not from -Dmaven.repo.local.
---@param settings_xml string|nil
---@param repo string
---@return string
function M.with_local_repository(settings_xml, repo)
	local element = "<localRepository>" .. repo .. "</localRepository>"
	if not settings_xml or not settings_xml:find("<settings[%s>]") then
		return "<settings>\n  " .. element .. "\n</settings>\n"
	end
	local replaced, n = settings_xml:gsub("<localRepository>.-</localRepository>", function()
		return element
	end, 1)
	if n > 0 then
		return replaced
	end
	return (settings_xml:gsub("(<settings[^>]*>)", function(open)
		return open .. "\n  " .. element
	end, 1))
end

return M
