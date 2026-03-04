#!/usr/bin/env python3
"""Enhanced Dashboard with Context Tracking and Alert System.

This enhanced version adds:
- Context percentage tracking with visual indicators
- Permission prompt detection and alerts
- Error state monitoring
- Real-time status with granular states
- Neovim-aware chrome filtering
"""

import os
import select
import signal
import sys
import termios
import time
import tty
from datetime import datetime
from pathlib import Path
from typing import List, Tuple, Optional

# Try to import enhanced version first, fall back to original
try:
    from agent_state_enhanced import (
        Agent, C, IDLE_ALERT_SECS, IDLE_WARN_SECS, SMART_ICONS,
        compute_idle, detect_status, enrich_pids, enrich_tmux, fmt_dur,
        get_preview, load_agents, parse_agents, print_summary,
        resolve_agents_file, tmux_cmd, vlen,
    )
except ImportError:
    from agent_state import (
        Agent, C, IDLE_ALERT_SECS, IDLE_WARN_SECS, SMART_ICONS,
        compute_idle, detect_status, enrich_pids, enrich_tmux, fmt_dur,
        get_preview, load_agents, parse_agents, print_summary,
        resolve_agents_file, tmux_cmd, vlen,
    )

REFRESH_SECS = 2.0
PREVIEW_LINES = 4


# ── TUI Helpers ───────────────────────────────────────────

def vpad(s: str, width: int) -> str:
    return s + " " * max(0, width - vlen(s))


def vtrunc(s: str, width: int) -> str:
    if vlen(s) <= width:
        return s
    out = []
    vis = 0
    i = 0
    while i < len(s) and vis < width - 1:
        if s[i] == "\033":
            try:
                j = s.index("m", i) + 1
            except ValueError:
                break
            out.append(s[i:j])
            i = j
        else:
            out.append(s[i])
            vis += 1
            i += 1
    out.append(f"…{C.RESET}")
    return "".join(out)


def term_size() -> Tuple[int, int]:
    try:
        return os.get_terminal_size()
    except OSError:
        return 80, 24


def draw_box(
    content: List[str], width: int, title: str = "",
    title_color: str = C.CYAN, border: str = C.GRAY,
) -> List[str]:
    inner = width - 2
    lines: List[str] = []

    if title:
        tvis_len = vlen(title)
        pad = inner - tvis_len - 4
        lines.append(
            f"{border}╭──{C.RESET} "
            f"{title_color}{C.BOLD}{title}{C.RESET} "
            f"{border}{'─' * max(0, pad)}╮{C.RESET}"
        )
    else:
        lines.append(f"{border}╭{'─' * inner}╮{C.RESET}")

    for line in content:
        pad = inner - vlen(line) - 1
        lines.append(
            f"{border}│{C.RESET} {line}{' ' * max(0, pad)}{border}│{C.RESET}"
        )

    lines.append(f"{border}╰{'─' * inner}╯{C.RESET}")
    return lines


def draw_context_bar(pct: Optional[int], width: int = 10) -> str:
    """Draw a visual context percentage bar."""
    if pct is None:
        return f"{C.GRAY}[?????]{C.RESET}"

    filled = int(width * pct / 100)
    empty = width - filled

    if pct > 85:
        color = C.RED
        bar = "█" * filled + "░" * empty
        return f"{color}[{bar}] {pct}%{C.RESET}"
    elif pct > 70:
        color = C.YELLOW
        bar = "█" * filled + "░" * empty
        return f"{color}[{bar}] {pct}%{C.RESET}"
    else:
        color = C.GREEN
        bar = "█" * filled + "░" * empty
        return f"{color}[{bar}] {pct}%{C.RESET}"


# ── Enhanced Dashboard ────────────────────────────────────

class EnhancedDashboard:
    def __init__(self):
        self.agents_file = resolve_agents_file()
        self.agents: List[Agent] = []
        self.selected_idx = 0
        self.start_time = time.time()
        self.error: Optional[str] = None
        self.show_stopped = False
        self.show_preview = True
        self.show_context = True
        self.last_refresh = 0
        self.alerts: List[str] = []

    def load(self) -> bool:
        try:
            self.agents = load_agents()
            self.detect_alerts()
            self.error = None
            return True
        except Exception as e:
            self.error = str(e)
            return False

    def detect_alerts(self):
        """Detect conditions that need user attention."""
        self.alerts = []
        for a in self.agents:
            if not a.is_live:
                continue

            # Permission prompts
            if hasattr(a, 'is_permission_prompt') and a.is_permission_prompt:
                self.alerts.append(f"🔐 {a.name}: Permission required")

            # Errors
            if hasattr(a, 'is_error') and a.is_error:
                self.alerts.append(f"❌ {a.name}: Error detected")

            # High context
            if hasattr(a, 'context_pct') and a.context_pct:
                if a.context_pct > 90:
                    self.alerts.append(f"🔴 {a.name}: Critical context ({a.context_pct}%)")
                elif a.context_pct > 85:
                    self.alerts.append(f"⚠️  {a.name}: High context ({a.context_pct}%)")

            # Long idle
            if a.idle_secs > IDLE_ALERT_SECS:
                self.alerts.append(f"💤 {a.name}: Idle {fmt_dur(a.idle_secs)}")

    def draw(self) -> List[str]:
        cols, rows = term_size()
        lines: List[str] = []

        # Header with alerts
        runtime = int(time.time() - self.start_time)
        header = f"Agent Dashboard | {len(self.agents)} agents | Runtime: {fmt_dur(runtime)}"

        if self.alerts:
            alert_str = f" | {C.RED}⚠️  {len(self.alerts)} alerts{C.RESET}"
            header += alert_str

        lines.append(f"{C.BOLD}{header}{C.RESET}")
        lines.append("─" * cols)

        # Alert section (if any)
        if self.alerts and len(lines) < rows - 5:
            lines.append(f"{C.RED}{C.BOLD}Alerts:{C.RESET}")
            for alert in self.alerts[:3]:  # Show max 3 alerts
                lines.append(f"  {alert}")
            if len(self.alerts) > 3:
                lines.append(f"  {C.DIM}... and {len(self.alerts) - 3} more{C.RESET}")
            lines.append("")

        # Main agent list
        visible_agents = [a for a in self.agents if a.is_live or self.show_stopped]

        if not visible_agents:
            lines.append("")
            lines.append(f"{C.DIM}No agents to display{C.RESET}")
        else:
            # Column headers
            if self.show_context:
                lines.append(
                    f"{'Name':<20} {'Status':<15} {'Context':<15} {'Idle':<10} {'Details'}"
                )
            else:
                lines.append(
                    f"{'Name':<20} {'Status':<15} {'Idle':<10} {'Details'}"
                )
            lines.append("─" * cols)

            # Agent rows
            for i, a in enumerate(visible_agents):
                if len(lines) >= rows - 5:
                    break

                is_selected = i == self.selected_idx
                prefix = "▶ " if is_selected else "  "

                # Get status icon and color
                icon, label, color = SMART_ICONS.get(
                    a.smart_status, ("?", a.smart_status, C.WHITE)
                )

                # Build status string with icon
                status_str = f"{icon} {color}{label}{C.RESET}"

                # Context bar (if enabled and available)
                context_str = ""
                if self.show_context:
                    if hasattr(a, 'context_pct') and a.context_pct is not None:
                        context_str = draw_context_bar(a.context_pct, 8)
                    else:
                        context_str = f"{C.GRAY}[unknown]{C.RESET}"

                # Idle time
                idle_str = ""
                if a.idle_secs > IDLE_WARN_SECS:
                    idle_str = f"{C.YELLOW}{fmt_dur(a.idle_secs)}{C.RESET}"
                elif a.idle_secs > 60:
                    idle_str = f"{C.DIM}{fmt_dur(a.idle_secs)}{C.RESET}"

                # Detail string
                detail_str = vtrunc(a.smart_detail, cols - 60) if a.smart_detail else ""

                # Build row
                if self.show_context:
                    row = (
                        f"{prefix}{a.name:<18} "
                        f"{vpad(status_str, 15)} "
                        f"{vpad(context_str, 15)} "
                        f"{idle_str:<10} "
                        f"{detail_str}"
                    )
                else:
                    row = (
                        f"{prefix}{a.name:<18} "
                        f"{vpad(status_str, 15)} "
                        f"{idle_str:<10} "
                        f"{detail_str}"
                    )

                lines.append(row.rstrip())

            # Preview section (if enabled and agent selected)
            if self.show_preview and visible_agents and self.selected_idx < len(visible_agents):
                selected = visible_agents[self.selected_idx]
                if selected.pane_lines:
                    lines.append("")
                    lines.append(f"{C.BOLD}Preview: {selected.name}{C.RESET}")
                    preview = get_preview(selected.pane_lines, PREVIEW_LINES)
                    for p_line in preview:
                        lines.append(f"  {C.DIM}{vtrunc(p_line, cols - 4)}{C.RESET}")

        # Footer
        while len(lines) < rows - 2:
            lines.append("")

        footer_parts = [
            f"{C.DIM}q:quit",
            "r:refresh",
            "↑↓:select",
            "Enter:focus",
            "s:stopped",
            "p:preview",
            "c:context",
        ]
        if self.alerts:
            footer_parts.append(f"{C.RED}!:alerts{C.RESET}")

        footer = " | ".join(footer_parts) + f"{C.RESET}"
        lines.append("─" * cols)
        lines.append(footer)

        return lines[:rows]

    def handle_key(self, key: str) -> bool:
        """Handle keyboard input. Returns False to quit."""
        visible = [a for a in self.agents if a.is_live or self.show_stopped]

        if key == 'q':
            return False
        elif key == 'r':
            self.load()
        elif key == '\x1b[A':  # Up arrow
            if self.selected_idx > 0:
                self.selected_idx -= 1
        elif key == '\x1b[B':  # Down arrow
            if self.selected_idx < len(visible) - 1:
                self.selected_idx += 1
        elif key == '\r':  # Enter
            if visible and self.selected_idx < len(visible):
                selected = visible[self.selected_idx]
                if selected.tmux_target:
                    # Focus the agent's tmux window
                    tmux_cmd("select-window", "-t", selected.tmux_target)
                    return False  # Exit dashboard
        elif key == 's':
            self.show_stopped = not self.show_stopped
            self.selected_idx = 0
        elif key == 'p':
            self.show_preview = not self.show_preview
        elif key == 'c':
            self.show_context = not self.show_context
        elif key == '!':
            # Show full alert details
            if self.alerts:
                self.show_alert_details()

        return True

    def show_alert_details(self):
        """Show detailed alert information."""
        print("\033[2J\033[H")  # Clear screen
        print(f"{C.RED}{C.BOLD}Alert Details{C.RESET}")
        print("─" * 60)
        for alert in self.alerts:
            print(f"  {alert}")
        print("─" * 60)
        print(f"{C.DIM}Press any key to return...{C.RESET}")
        self.get_key()

    def get_key(self) -> str:
        """Get a single keypress."""
        fd = sys.stdin.fileno()
        old_settings = termios.tcgetattr(fd)
        try:
            tty.setraw(sys.stdin.fileno())
            key = sys.stdin.read(1)
            if key == '\x1b':  # Escape sequence
                key += sys.stdin.read(2)
            return key
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)

    def run(self):
        """Run the interactive dashboard."""
        # Hide cursor
        print("\033[?25l", end="", flush=True)

        # Initial load
        self.load()

        try:
            while True:
                # Draw
                print("\033[2J\033[H", end="")  # Clear screen, move to top
                for line in self.draw():
                    print(line)
                sys.stdout.flush()

                # Wait for input or timeout
                rlist, _, _ = select.select([sys.stdin], [], [], REFRESH_SECS)

                if rlist:
                    # Handle input
                    key = self.get_key()
                    if not self.handle_key(key):
                        break
                else:
                    # Timeout - refresh
                    self.load()

        except KeyboardInterrupt:
            pass
        finally:
            # Show cursor
            print("\033[?25h", end="", flush=True)
            print()  # Final newline


def main():
    """Main entry point."""
    import argparse

    parser = argparse.ArgumentParser(description="Enhanced Agent Dashboard")
    parser.add_argument("--summary", action="store_true", help="Show summary and exit")
    args = parser.parse_args()

    if args.summary:
        agents = load_agents()
        print_summary(agents, show_all=True)
    else:
        dashboard = EnhancedDashboard()
        dashboard.run()


if __name__ == "__main__":
    main()