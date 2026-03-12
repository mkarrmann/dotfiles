require("hs.ipc")
hs.ipc.cliInstall("/opt/homebrew", true)

local spaces = require("hs.spaces")

hs.timer.doAfter(3, function()
	hs.task.new(os.getenv("HOME") .. "/dotfiles/bin/startup-windows", function(exitCode, stdOut, stdErr)
		if exitCode ~= 0 then
			hs.notify.show("startup-windows", "Failed (exit " .. exitCode .. ")", stdErr or "")
		end
	end):start()
end)

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

local workspaceApps = { ["Ghostty"] = true, ["VS Code @ Meta"] = true }

function ghosttySweepFocused()
	local win = hs.window.focusedWindow()
	if not win then return "empty" end
	local app = win:application()
	if not app then return "empty" end
	local name = app:name()
	if workspaceApps[name] then return name end
	if win:screen() ~= getGhosttyScreen() then return "other_screen" end
	hs.eventtap.keyStroke({"ctrl", "alt", "shift"}, "1")
	return "swept:" .. name
end

local throwSpaceKeys = {"1","2","3","4","5","6","7","8","9","0","-","="}

function ghosttyThrowToSpace(spaceIndex)
	if spaceIndex <= #throwSpaceKeys then
		hs.eventtap.keyStroke({"ctrl", "alt", "shift"}, throwSpaceKeys[spaceIndex])
		return "ok"
	end
	return "ERROR: Space " .. spaceIndex .. " out of range"
end

function ghosttyFocusAndThrow(windowId, spaceIndex)
	if spaceIndex > #throwSpaceKeys then
		return "ERROR: Space " .. spaceIndex .. " out of range"
	end
	local ok, result, raw = hs.osascript.applescript(
		'tell application "Ghostty"\n' ..
		'  set index of (first window whose id is "' .. windowId .. '") to 1\n' ..
		'  activate\n' ..
		'end tell'
	)
	if not ok then return "ERROR: focus failed: " .. tostring(raw) end
	hs.timer.usleep(1000000)
	hs.eventtap.keyStroke({"ctrl", "alt", "shift"}, throwSpaceKeys[spaceIndex])
	return "ok"
end

function ghosttyMoveWindowToSpace(windowId, spaceIndex)
	local screen = getGhosttyScreen()
	local userSpaces = userSpacesForScreen(screen)
	if not userSpaces or spaceIndex > #userSpaces then
		return "ERROR: Space " .. spaceIndex .. " out of range"
	end
	local ok, err = spaces.moveWindowToSpace(windowId, userSpaces[spaceIndex])
	if not ok then
		return "MOVE_FAILED: " .. tostring(err)
	end
	return "ok"
end

-- Desktop Focus bridge
dofile(os.getenv("HOME") .. "/dev/desktop-focus/providers/hammerspoon/init.lua")

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
