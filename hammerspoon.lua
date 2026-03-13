require("hs.ipc")
hs.ipc.cliInstall("/opt/homebrew", true)

-- Desktop Focus bridge
dofile(os.getenv("HOME") .. "/dev/orchest/providers/hammerspoon/init.lua")
