#!/usr/bin/env python3
"""
Fix for agent-watcher.py to prevent overwriting stopped sessions.

This patch prevents the watcher from overwriting sessions that are already
marked as stopped, preventing the "stopped + waiting" contradiction.
"""

import sys
from pathlib import Path

def apply_fix():
    """Apply the fix to agent-watcher.py"""

    watcher_path = Path.home() / ".claude" / "agent-manager" / "agent-watcher.py"
    if not watcher_path.exists():
        watcher_path = Path.home() / "dotfiles" / "claude_config" / "agent-manager" / "agent-watcher.py"

    if not watcher_path.exists():
        print(f"Error: Could not find agent-watcher.py")
        return False

    content = watcher_path.read_text()

    # Fix 1: Remove "stopped" from CLASSIFIABLE_STATUSES
    old_line = 'CLASSIFIABLE_STATUSES = {"done", "stopped", "active", "interactive", "resumed"}'
    new_line = 'CLASSIFIABLE_STATUSES = {"done", "active", "interactive", "resumed"}  # Removed "stopped" to prevent overwriting'

    if old_line in content:
        content = content.replace(old_line, new_line)
        print("✅ Fixed: Removed 'stopped' from CLASSIFIABLE_STATUSES")
    else:
        print("⚠️  CLASSIFIABLE_STATUSES line not found or already fixed")

    # Fix 2: Also prevent overwriting stopped status in update_agents_md
    old_check = 'if current_status in ("⚡ active", "🔵 bg:running"):'
    new_check = 'if current_status in ("⚡ active", "🔵 bg:running", "⏹️ stopped"):'

    if old_check in content:
        content = content.replace(old_check, new_check)
        print("✅ Fixed: Added 'stopped' to status protection in update_agents_md")
    else:
        print("⚠️  update_agents_md check not found or already fixed")

    # Write back
    watcher_path.write_text(content)
    print(f"\n✅ Fixes applied to {watcher_path}")
    return True


def verify_fix():
    """Verify the fix was applied correctly"""

    watcher_path = Path.home() / ".claude" / "agent-manager" / "agent-watcher.py"
    if not watcher_path.exists():
        watcher_path = Path.home() / "dotfiles" / "claude_config" / "agent-manager" / "agent-watcher.py"

    if not watcher_path.exists():
        print("Error: Could not find agent-watcher.py to verify")
        return False

    content = watcher_path.read_text()

    # Check both fixes are in place
    has_classifiable_fix = 'CLASSIFIABLE_STATUSES = {"done", "active", "interactive", "resumed"}' in content
    has_update_fix = '"⏹️ stopped"' in content and 'if current_status in' in content

    if has_classifiable_fix and has_update_fix:
        print("✅ Both fixes verified successfully")
        return True
    else:
        if not has_classifiable_fix:
            print("❌ CLASSIFIABLE_STATUSES fix not applied")
        if not has_update_fix:
            print("❌ update_agents_md fix not applied")
        return False


def clean_agents_md():
    """Clean up any sessions with conflicting statuses"""
    from pathlib import Path
    import re

    agents_file = Path.home() / ".claude" / "agents.md"
    if not agents_file.exists():
        gdrive = Path(f"/data/users/{Path.home().name}/gdrive/AGENTS.md")
        if gdrive.exists():
            agents_file = gdrive

    if not agents_file.exists():
        print("Could not find AGENTS.md")
        return

    content = agents_file.read_text()
    lines = content.split('\n')

    fixed_count = 0
    new_lines = []

    for line in lines:
        # Look for lines with "❓ waiting" status
        if '❓ waiting' in line:
            parts = line.split('|')
            if len(parts) >= 9:
                # Replace with stopped status
                parts[2] = ' ⏹️ stopped '
                line = '|'.join(parts)
                fixed_count += 1
        new_lines.append(line)

    if fixed_count > 0:
        agents_file.write_text('\n'.join(new_lines))
        print(f"✅ Fixed {fixed_count} sessions with 'waiting' status back to 'stopped'")
    else:
        print("✅ No sessions with conflicting status found")


def restart_watcher():
    """Kill the current watcher so it restarts with the fix"""
    import subprocess

    # Find watcher PID
    pid_file = Path.home() / ".claude" / "agent-manager" / "watcher.pid"
    if pid_file.exists():
        try:
            pid = int(pid_file.read_text().strip())
            subprocess.run(["kill", str(pid)], capture_output=True)
            print(f"✅ Killed watcher process (PID {pid})")
            print("   It will restart automatically on next agent activity")
        except (ValueError, subprocess.CalledProcessError):
            print("⚠️  Could not kill watcher process")
    else:
        print("ℹ️  No watcher process running")


def main():
    print("Agent Watcher Fix")
    print("=" * 50)
    print("\nThis will fix the 'stopped + waiting' status bug\n")

    # Apply the fix
    if not apply_fix():
        return 1

    print("\nVerifying fix...")
    if not verify_fix():
        return 1

    print("\nCleaning up AGENTS.md...")
    clean_agents_md()

    print("\nRestarting watcher...")
    restart_watcher()

    print("\n" + "=" * 50)
    print("✅ Fix complete!")
    print("\nThe watcher will no longer overwrite 'stopped' sessions.")
    print("Any new sessions marked as stopped will stay stopped.")

    return 0


if __name__ == "__main__":
    sys.exit(main())