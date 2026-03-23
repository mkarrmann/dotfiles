#!/usr/bin/env python3
"""Agent Manager Dashboard — live TUI for monitoring Claude Code sessions.

Imports the shared agent_state module for data loading, status detection,
and chrome filtering. This file only contains the interactive TUI.
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
from typing import List, Tuple

from agent_state import (
    Agent, C, IDLE_ALERT_SECS, IDLE_WARN_SECS, SMART_ICONS,
    compute_idle, detect_status, enrich_pids, enrich_nvim, fmt_dur,
    get_preview, load_agents, parse_agents, print_summary,
    resolve_agents_file, _read_nvim_server, _nvim_expr, vlen,
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


# ── Dashboard ─────────────────────────────────────────────


class Dashboard:
    def __init__(self):
        self.agents_file = resolve_agents_file()
        self.agents: List[Agent] = []
        self.show_stopped = False
        self.selected = 0
        self.viewport_offset = 0
        self.card_ranges: List[Tuple[int, int]] = []
        self.rendered: List[str] = []
        self.hostname = os.uname().nodename.split(".")[0]
        self.running = True
        self.needs_resize = False
        self._orig_termios = None

    def refresh(self):
        self.agents = load_agents()
        vis = self._visible()
        if vis:
            self.selected = min(self.selected, len(vis) - 1)
        else:
            self.selected = 0

    def _visible(self) -> List[Agent]:
        return [
            a for a in self.agents
            if not a.is_stopped or self.show_stopped
        ]

    def _stopped_count(self) -> int:
        return sum(1 for a in self.agents if a.is_stopped)

    def render(self) -> List[str]:
        cols, _ = term_size()
        width = min(cols - 1, 90)
        inner = width - 4
        lines: List[str] = []
        self.card_ranges = []

        now_str = datetime.now().strftime("%H:%M:%S")
        counts: dict = {}
        for a in self.agents:
            e = a.status_emoji
            counts[e] = counts.get(e, 0) + 1
        summary = "  ".join(f"{e} {n}" for e, n in counts.items())
        lines.extend(draw_box(
            [vpad(summary, inner)], width,
            f"Agent Dashboard ── {self.hostname} ── {now_str}",
            C.CYAN, C.GRAY,
        ))
        lines.append("")

        visible = self._visible()
        local_live = [a for a in visible if not a.is_stopped and a.is_local]
        remote_live = [a for a in visible if not a.is_stopped and not a.is_local]
        stopped = [a for a in visible if a.is_stopped]
        sel_idx = 0

        if not self.agents:
            lines.append(
                f"  {C.DIM}No agents in {self.agents_file}{C.RESET}"
            )
            lines.append("")

        for a in local_live:
            start = len(lines)
            lines.extend(self._render_card(a, width, sel_idx == self.selected))
            lines.append("")
            self.card_ranges.append((start, len(lines)))
            sel_idx += 1

        if remote_live:
            remote_ods = sorted(set(a.od for a in remote_live))
            lines.append(
                f"  {C.MAGENTA}📡 Remote ({', '.join(remote_ods)}){C.RESET}"
            )
            lines.append("")
            for a in remote_live:
                start = len(lines)
                lines.extend(
                    self._render_card(a, width, sel_idx == self.selected)
                )
                lines.append("")
                self.card_ranges.append((start, len(lines)))
                sel_idx += 1

        sc = self._stopped_count()
        if sc > 0:
            toggle_hint = "[s] hide" if self.show_stopped else "[s] show"
            pad = max(0, inner - len(f"⏹️  Stopped ({sc})") - len(toggle_hint))
            lines.append(
                f"  {C.GRAY}⏹️  Stopped ({sc})"
                f"{' ' * pad}{toggle_hint}{C.RESET}"
            )
            lines.append("")

            if self.show_stopped:
                for a in stopped:
                    start = len(lines)
                    lines.extend(
                        self._render_card(a, width, sel_idx == self.selected)
                    )
                    lines.append("")
                    self.card_ranges.append((start, len(lines)))
                    sel_idx += 1

        lines.append(
            f"  {C.DIM}"
            f"q quit │ j/k scroll │ Enter go to window │ s stopped │ r refresh"
            f"{C.RESET}"
        )

        self.rendered = lines
        return lines

    def _render_card(
        self, a: Agent, width: int, selected: bool
    ) -> List[str]:
        inner = width - 4
        icon, label, color = SMART_ICONS.get(
            a.smart_status, ("·", a.status_raw, C.GRAY)
        )

        if a.smart_detail:
            status = f"{icon} {color}{label}: {a.smart_detail}{C.RESET}"
        else:
            status = f"{icon} {color}{label}{C.RESET}"

        idle_str = ""
        if a.idle_secs > 0 and a.is_live and a.smart_status in (
            "waiting", "complete", "idle"
        ):
            dur = fmt_dur(a.idle_secs)
            if a.idle_secs >= IDLE_ALERT_SECS:
                idle_str = f"{C.RED}{C.BOLD}{dur} ⚠{C.RESET}"
            elif a.idle_secs >= IDLE_WARN_SECS:
                idle_str = f"{C.ORANGE}{dur}{C.RESET}"
            else:
                idle_str = f"{C.DIM}{dur}{C.RESET}"

        if idle_str:
            gap = inner - vlen(status) - vlen(idle_str)
            line1 = status + " " * max(1, gap) + idle_str
        else:
            line1 = status

        content = [vtrunc(line1, inner)]

        if a.pid and not a.pid_alive and a.is_live:
            content.append(
                f"{C.RED}☠ PID {a.pid} dead — status may be stale{C.RESET}"
            )

        if a.pane_lines and a.is_live:
            content.append(f"{C.DIM}{'┄' * min(inner, 50)}{C.RESET}")
            preview = get_preview(a.pane_lines, PREVIEW_LINES)
            for pl in preview:
                content.append(
                    f"  {C.DIM}{vtrunc(pl, inner - 4)}{C.RESET}"
                )

        home = str(Path.home())
        dir_short = a.directory.replace(home, "~") if a.directory else ""
        title_parts = [a.status_emoji, a.name]
        if not a.is_local:
            title_parts.append(f"📡 {a.od}")
        elif a.od.startswith("nvim:tab-"):
            title_parts.append(a.od)
        if dir_short:
            title_parts.append(dir_short)
        title = " ── ".join(title_parts)

        if a.is_stopped:
            bc = tc = C.GRAY
        elif not a.is_local:
            bc = tc = C.MAGENTA
        elif a.smart_status == "waiting":
            bc = tc = C.RED
        elif a.smart_status in ("thinking", "processing", "tool", "active"):
            bc = tc = C.YELLOW
        elif a.smart_status == "complete":
            bc = tc = C.GREEN
        else:
            bc = tc = C.CYAN

        if selected:
            bc = C.WHITE + C.BOLD
            tc = C.WHITE + C.BOLD

        return draw_box(content, width, title, tc, bc)

    def _ensure_visible(self):
        _, rows = term_size()
        if not self.card_ranges or self.selected >= len(self.card_ranges):
            return
        start, end = self.card_ranges[self.selected]
        if start < self.viewport_offset + 3:
            self.viewport_offset = max(0, start - 3)
        if end > self.viewport_offset + rows - 2:
            self.viewport_offset = end - rows + 2

    def handle_key(self, key: str):
        vis = self._visible()
        mx = max(0, len(vis) - 1)

        if key == "q":
            self.running = False
        elif key == "s":
            self.show_stopped = not self.show_stopped
            vis = self._visible()
            self.selected = min(self.selected, max(0, len(vis) - 1))
        elif key in ("j", "B"):
            self.selected = min(self.selected + 1, mx)
        elif key in ("k", "A"):
            self.selected = max(self.selected - 1, 0)
        elif key == "G":
            self.selected = mx
        elif key == "g":
            self.selected = 0
        elif key in ("\r", "\n"):
            if 0 <= self.selected < len(vis):
                a = vis[self.selected]
                if a.is_local and a.od.startswith("nvim:tab-"):
                    try:
                        tab_handle = int(a.od[len("nvim:tab-"):])
                        server = _read_nvim_server()
                        if server:
                            _nvim_expr(server, f"execute('lua _G._claude_focus_tab_by_handle({tab_handle})')")
                    except ValueError:
                        pass

    def paint(self):
        _, rows = term_size()
        self._ensure_visible()
        visible = self.rendered[
            self.viewport_offset:self.viewport_offset + rows - 1
        ]

        buf = ["\033[H"]
        for line in visible:
            buf.append(line)
            buf.append("\033[K\n")
        buf.append("\033[J")
        sys.stdout.write("".join(buf))
        sys.stdout.flush()

    def run(self):
        sys.stdout.write("\033[?1049h\033[?25l")
        sys.stdout.flush()

        fd = sys.stdin.fileno()
        self._orig_termios = termios.tcgetattr(fd)
        tty.setcbreak(fd)

        def _quit(sig, frame):
            self.running = False

        def _resize(sig, frame):
            self.needs_resize = True

        signal.signal(signal.SIGINT, _quit)
        signal.signal(signal.SIGTERM, _quit)
        signal.signal(signal.SIGWINCH, _resize)

        try:
            while self.running:
                self.refresh()
                self.render()
                self.paint()

                deadline = time.time() + REFRESH_SECS
                while time.time() < deadline and self.running:
                    if self.needs_resize:
                        self.needs_resize = False
                        self.render()
                        self.paint()
                    remaining = deadline - time.time()
                    if remaining <= 0:
                        break
                    rlist, _, _ = select.select(
                        [sys.stdin], [], [], min(0.05, remaining)
                    )
                    if rlist:
                        key = sys.stdin.read(1)
                        if key == "\033":
                            if select.select([sys.stdin], [], [], 0.02)[0]:
                                k2 = sys.stdin.read(1)
                                if k2 == "[":
                                    if select.select(
                                        [sys.stdin], [], [], 0.02
                                    )[0]:
                                        key = sys.stdin.read(1)
                                    else:
                                        continue
                                else:
                                    continue
                            else:
                                continue
                        self.handle_key(key)
                        self.render()
                        self.paint()
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, self._orig_termios)
            sys.stdout.write("\033[?25h\033[?1049l")
            sys.stdout.flush()


if __name__ == "__main__":
    if "--summary" in sys.argv:
        scope = "all"
        if "--local" in sys.argv:
            scope = "local"
        elif "--remote" in sys.argv:
            scope = "remote"
        print_summary(scope)
    else:
        Dashboard().run()
