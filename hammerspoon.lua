require("hs.ipc")
hs.ipc.cliInstall("/opt/homebrew", true)

local spaces = require("hs.spaces")

-- Auto-launch disabled during development. Run ghostty-workspaces manually.
-- hs.timer.doAfter(3, function()
-- 	hs.task.new(os.getenv("HOME") .. "/dotfiles/bin/ghostty-workspaces", function(exitCode, stdOut, stdErr)
-- 		if exitCode ~= 0 then
-- 			hs.notify.show("ghostty-workspaces", "Failed (exit " .. exitCode .. ")", stdErr or "")
-- 		end
-- 	end):start()
-- end)

local function userSpacesForScreen(screen)
	local all = spaces.spacesForScreen(screen)
	if not all then return nil end
	local result = {}
	for _, sid in ipairs(all) do
		if spaces.spaceType(sid) == "user" then
			result[#result + 1] = sid
		end
	end
	return result
end

ghosttyScreenId = nil

function ghosttyInitScreen()
	local screen = hs.screen.mainScreen()
	ghosttyScreenId = screen:id()
	return "ok"
end

local function getGhosttyScreen()
	if ghosttyScreenId then
		local screen = hs.screen.find(ghosttyScreenId)
		if screen then return screen end
	end
	return hs.screen.mainScreen()
end

function ghosttyEnsureSpaces(count)
	local screen = getGhosttyScreen()
	local userSpaces = userSpacesForScreen(screen)
	if not userSpaces then return -1 end

	local needed = count - #userSpaces
	if needed <= 0 then return 0 end

	for i = 1, needed do
		local closeMC = (i == needed)
		local ok, err = spaces.addSpaceToScreen(screen, closeMC)
		if not ok then
			return -1
		end
	end

	return needed
end

function ghosttyGotoSpace(spaceIndex)
	local screen = getGhosttyScreen()
	local userSpaces = userSpacesForScreen(screen)
	if not userSpaces then return "ERROR: Failed to get spaces" end
	if spaceIndex > #userSpaces then
		return "ERROR: Space " .. spaceIndex .. " does not exist (" .. #userSpaces .. " user spaces)"
	end

	spaces.gotoSpace(userSpaces[spaceIndex])
	return "ok"
end

function ghosttySweepFocused()
	local win = hs.window.focusedWindow()
	if not win then return "empty" end
	local app = win:application()
	if not app then return "empty" end
	if app:name() == "Ghostty" then return "ghostty" end
	if win:screen() ~= getGhosttyScreen() then return "other_screen" end
	local appName = app:name()
	hs.eventtap.keyStroke({"ctrl", "alt", "shift"}, "1")
	return "swept:" .. appName
end

function ghosttyThrowToSpace(spaceIndex)
	hs.eventtap.keyStroke({"ctrl", "alt", "shift"}, tostring(spaceIndex))
	return "ok"
end

function ghosttyWindowExists(title)
	local ghosttyApp = hs.application.find("Ghostty")
	if not ghosttyApp then return false end
	local ok, windows = pcall(function() return ghosttyApp:allWindows() end)
	if not ok or not windows then return false end
	for _, win in ipairs(windows) do
		if win:title() == title then return true end
	end
	return false
end

-- Screen navigation

function getNextScreen()
	local screens = hs.screen.allScreens()
	for i, s in ipairs(screens) do
		if s == hs.mouse.getCurrentScreen() then
			if i == #screens then
				return screens[1]
			else
				return screens[i + 1]
			end
		end
	end
	return screens[#screens]
end

function getWindowUnderMouse()
	local my_pos = hs.geometry.new(hs.mouse.absolutePosition())
	local my_screen = hs.mouse.getCurrentScreen()

	return hs.fnutils.find(
		-- Ideally use orderedWindows, but that seems to return empty list
		hs.window.allWindows(),
		function(w)
			return my_screen == w:screen() and my_pos:inside(w:frame())
		end
	)
end

function getPrevScreen()
	local screens = hs.screen.allScreens()
	for i, s in ipairs(screens) do
		if s == hs.mouse.getCurrentScreen() then
			if i == 1 then
				return screens[#screens]
			else
				return screens[i - 1]
			end
		end
	end
	return 1
end

hs.hotkey.bind(
	{"ctrl"},
	"k",
	function()
		local nextScreen = getNextScreen()
		local point = {}
		point.x = nextScreen:frame().w / 2
		point.y = nextScreen:frame().h / 2
		hs.mouse.setRelativePosition(point, nextScreen)
		local w = getWindowUnderMouse()
		w:focus()
	end
)

hs.hotkey.bind(
	{"ctrl"},
	"j",
	function()
		local prevScreen = getPrevScreen()
		local point = {}
		point.x = prevScreen:frame().w / 2
		point.y = prevScreen:frame().h / 2
		hs.mouse.setRelativePosition(point, prevScreen)
		-- There's a bug where this sometimes returns nil. Therefore, nothing
		-- is focused.
		local w = getWindowUnderMouse()
		w:focus()
	end
)
