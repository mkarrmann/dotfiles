#Requires AutoHotkey v2.0
#SingleInstance Force ; replace old instance immediately

^j::Send "{Down}"        ; Rebinds Ctrl + J to Down arrow
^k::Send "{Up}"          ; Rebinds Ctrl + K to Up arrow
^\::SetCapsLockState !GetKeyState("CapsLock", "T")   ; Rebinds Ctrl + \ to Caps Lock
!-::—            ; Rebinds Alt + - to an em dash (—)
