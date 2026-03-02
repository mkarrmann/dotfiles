#!/usr/bin/env python3
"""Agent Manager Dashboard — live TUI for monitoring Claude Code sessions.

Reads from AGENTS.md, PID files, and tmux pane output to provide:
- Per-agent cards with live pane preview
- Smart status detection (thinking, running tool, writing, etc.)
- Idle time alerts for potentially stuck agents
"""

import os
import re
import select
import signal
import subprocess
import sys
import termios
import time
import tty
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple

REFRESH_SECS = 2.0
PREVIEW_LINES = 4
IDLE_WARN_SECS = 600
IDLE_ALERT_SECS = 1800
SPINNER = set("⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏")
PIDS_DIR = Path.home() / ".claude" / "agent-manager" / "pids"
_ANSI_RE = re.compile(r"\033\[[^m]*m")


class C:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    RED = "\033[91m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    BLUE = "\033[94m"
    MAGENTA = "\033[95m"
    CYAN = "\033[96m"
    WHITE = "\033[97m"
    GRAY = "\033[90m"
    ORANGE = "\033[38;5;208m"


SMART_ICONS = {
    "thinking":   ("💭", "Thinking",         C.YELLOW),
    "processing": ("⚙️",  "Processing",       C.YELLOW),
    "tool":       ("🔧", "Running",           C.ORANGE),
    "writing":    ("✏️",  "Writing",           C.GREEN),
    "reading":    ("📖", "Reading",            C.CYAN),
    "searching":  ("🔍", "Searching",          C.BLUE),
    "waiting":    ("⚠️",  "Waiting for input", C.RED),
    "complete":   ("✅", "Turn complete",       C.GREEN),
    "active":     ("⚡", "Active",             C.YELLOW),
    "stopped":    ("⏹️",  "Stopped",            C.GRAY),
    "idle":       ("💤", "Idle",                C.GRAY),
    "bg:running": ("🔵", "Background",         C.BLUE),
    "bg:done":    ("✅", "Background done",     C.GREEN),
}


# ── Helpers ────────────────────────────────────────────────


def vlen(s: str) -> int:
    return len(_ANSI_RE.sub("", s))


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


def fmt_dur(secs: int) -> str:
    if secs < 60:
        return f"{secs}s"
    if secs < 3600:
        return f"{secs // 60}m"
    h = secs // 3600
    m = (secs % 3600) // 60
    return f"{h}h{m}m"


def term_size() -> Tuple[int, int]:
    try:
        return os.get_terminal_size()
    except OSError:
        return 80, 24


def tmux_cmd(*args: str) -> Optional[str]:
    try:
        r = subprocess.run(
            ["tmux", *args], capture_output=True, text=True, timeout=2
        )
        return r.stdout.strip() if r.returncode == 0 else None
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None


def draw_box(
    content: List[str], width: int, title: str = "",
    title_color: str = C.CYAN, border: str = C.GRAY,
) -> List[str]:
    inner = width - 2
    lines: List[str] = []

    if title:
        tvis_len = len(_ANSI_RE.sub("", title))
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


# ── Data Model ─────────────────────────────────────────────


@dataclass
class Agent:
    name: str
    status_raw: str
    od: str
    session_id: str
    description: str
    started: str
    updated: str
    directory: str
    pid: Optional[int] = None
    pid_alive: bool = False
    is_local: bool = False
    tmux_target: Optional[str] = None
    claude_state: Optional[str] = None
    smart_status: str = ""
    smart_detail: str = ""
    pane_lines: List[str] = field(default_factory=list)
    idle_secs: int = 0

    @property
    def status_text(self) -> str:
        parts = self.status_raw.split(maxsplit=1)
        return parts[1].strip() if len(parts) > 1 else ""

    @property
    def status_emoji(self) -> str:
        return self.status_raw.split()[0] if self.status_raw.strip() else "·"

    @property
    def is_live(self) -> bool:
        return self.status_text in (
            "active", "waiting", "done", "interactive", "resumed"
        )

    @property
    def is_stopped(self) -> bool:
        return self.status_text in ("stopped", "idle") or self.status_text == "bg:done"


# ── Data Loading ───────────────────────────────────────────


def resolve_agents_file() -> Path:
    if env := os.environ.get("CLAUDE_AGENTS_FILE"):
        return Path(env)
    gdrive = Path(
        f"/data/users/{os.environ.get('USER', 'nobody')}/gdrive/AGENTS.md"
    )
    if gdrive.exists():
        return gdrive
    return Path.home() / ".claude" / "agents.md"


def parse_agents(path: Path) -> List[Agent]:
    if not path.exists():
        return []
    try:
        text = path.read_text()
    except OSError:
        return []
    rows = text.strip().split("\n")
    agents = []
    for row in rows[4:]:
        parts = [p.strip() for p in row.split("|")]
        if len(parts) < 9 or not parts[1].strip():
            continue
        agents.append(Agent(
            name=parts[1], status_raw=parts[2], od=parts[3],
            session_id=parts[4], description=parts[5],
            started=parts[6], updated=parts[7],
            directory=parts[8] if len(parts) > 8 else "",
        ))
    return agents


def enrich_pids(agents: List[Agent]) -> None:
    hostname = os.uname().nodename.split(".")[0]
    for a in agents:
        a.is_local = hostname in a.od or a.od in hostname
        if not a.session_id:
            continue
        pf = PIDS_DIR / a.session_id
        if pf.exists():
            try:
                a.pid = int(pf.read_text().strip())
                if a.is_local:
                    a.pid_alive = Path(f"/proc/{a.pid}").exists()
            except (ValueError, OSError):
                pass


def enrich_tmux(agents: List[Agent]) -> None:
    raw = tmux_cmd(
        "list-windows", "-a", "-F",
        "#{session_name}:#{window_index} #{window_name}"
    )
    if not raw:
        return
    windows = {}
    for line in raw.split("\n"):
        parts = line.split(" ", 1)
        if len(parts) == 2:
            windows[parts[1].rstrip("-")] = parts[0]

    for a in agents:
        if not a.is_local:
            continue

        for wname, target in windows.items():
            if a.name and (a.name in wname or wname in a.name):
                a.tmux_target = target
                break

        if not a.tmux_target and a.description and ":" in a.description:
            desc = a.description.strip()
            if any(c.isdigit() for c in desc):
                a.tmux_target = desc

        if not a.tmux_target:
            continue

        st = tmux_cmd(
            "show-options", "-wqv", "-t", a.tmux_target, "@claude_state"
        )
        if st:
            a.claude_state = st

        if a.is_live:
            raw_pane = tmux_cmd(
                "capture-pane", "-t", a.tmux_target, "-p", "-S", "-30"
            )
            if raw_pane:
                a.pane_lines = [l for l in raw_pane.split("\n") if l.strip()]


# ── Smart Status Detection ─────────────────────────────────


def detect_status(a: Agent) -> None:
    if not a.is_live:
        a.smart_status = a.status_text or "stopped"
        return

    if not a.is_local:
        a.smart_status = a.status_text or "active"
        a.smart_detail = f"on {a.od} (unverified)"
        return

    if a.claude_state == "⚙":
        _detect_activity(a)
    elif a.claude_state == "!":
        a.smart_status = "waiting"
        _extract_detail(a)
    elif a.claude_state in ("✓", "~"):
        a.smart_status = "complete"
        _extract_detail(a)
    elif a.pane_lines:
        _detect_activity(a)
    else:
        a.smart_status = "active"


def _detect_activity(a: Agent) -> None:
    if not a.pane_lines:
        a.smart_status = "active"
        return

    chunk = "\n".join(a.pane_lines[-12:])[-800:]

    m = re.search(r"(?:Running|Executing)[:\s]+(.+?)(?:\n|$)", chunk)
    if m:
        a.smart_status = "tool"
        a.smart_detail = m.group(1).strip()[:60]
        return

    if "esc to interrupt" in chunk.lower():
        a.smart_status = "processing"
        for line in reversed(a.pane_lines[-8:]):
            s = line.strip()
            if s and "esc to interrupt" not in s.lower() and len(s) > 5:
                a.smart_detail = s[:60]
                break
        return

    if any(c in SPINNER for c in chunk[-200:]) or "Thinking" in chunk[-300:]:
        a.smart_status = "thinking"
        return

    m = re.search(r"(?:Writ|Edit|Creat)\w*\s+(\S+)", chunk)
    if m:
        a.smart_status = "writing"
        a.smart_detail = m.group(1)[:60]
        return

    m = re.search(r"Read(?:ing)?\s+(\S+)", chunk)
    if m:
        a.smart_status = "reading"
        a.smart_detail = m.group(1)[:60]
        return

    m = re.search(r"(?:Grep|Glob|Search)\w*\s+(.*?)(?:\n|$)", chunk)
    if m:
        a.smart_status = "searching"
        a.smart_detail = m.group(1).strip()[:60]
        return

    a.smart_status = "active"


_CHROME_RE = re.compile(
    r"TERMINAL\s+term:"         # neovim terminal statusline
    r"|NORMAL\s+term:"          # neovim normal mode statusline
    r"|INSERT\s+.*term:"        # neovim insert mode statusline
    r"|-- INSERT --"            # vim insert mode indicator
    r"|-- NORMAL --"            # vim normal mode indicator
    r"|⏵⏵\s*accept"            # claude accept edits hint
    r"|shift\+tab to cycle"     # claude mode hint
    r"|📁\s+\w+.*🤖"           # claude statusline
    r"|Bot \d+:\d+"             # neovim statusline right side
    r"|^\s*\d+\s+\d+\s*$"      # bare neovim line number pairs
    r"|^\s*\d{1,5}\s*$"        # bare single line numbers
    r"|^[─━═]{5,}$"            # horizontal rules
    r"|▏"                       # neovim indent guides
    r"|^\s*$"                   # blank lines
)


def _is_chrome(line: str) -> bool:
    return bool(_CHROME_RE.search(line))


_NVIM_LINENUM_RE = re.compile(r"^\s*\d{1,5}\s{2,}\S")


def _clean_line(line: str) -> str:
    s = line.strip()
    # Strip leading neovim line number pairs (e.g., "78  46 ")
    s = re.sub(r"^\d+\s+\d+\s+", "", s)
    # If there's a neovim split gutter, pick the terminal-like side
    if "│" in s:
        parts = [p.strip() for p in s.split("│") if p.strip()]
        if not parts:
            return ""
        # Prefer the side that doesn't look like numbered editor content
        non_code = [p for p in parts if not _NVIM_LINENUM_RE.match(p)]
        if non_code:
            return max(non_code, key=len)
        return max(parts, key=len)
    # If line itself looks like a neovim numbered editor line, skip it
    if _NVIM_LINENUM_RE.match(s):
        return ""
    # Strip leading single neovim line number (e.g., "96  content")
    s = re.sub(r"^\d{1,5}\s{2,}", "", s)
    return s


def _get_preview(pane_lines: List[str], count: int) -> List[str]:
    raw = "\n".join(pane_lines[-10:])
    is_nvim = bool(re.search(r"NORMAL\s+term:|TERMINAL\s+term:|INSERT\s+.*term:", raw))
    cleaned = []
    for line in pane_lines:
        if _is_chrome(line):
            continue
        s = _clean_line(line)
        if not s or _is_chrome(s):
            continue
        if is_nvim and re.match(r"^\d{1,5}\s+\S", s):
            continue
        cleaned.append(s)
    if len(cleaned) > count + 2:
        cleaned = cleaned[:-2]
    return cleaned[-count:]


def _extract_detail(a: Agent) -> None:
    if not a.pane_lines:
        return
    for line in reversed(a.pane_lines[:-3]):
        if _is_chrome(line):
            continue
        s = _clean_line(line)
        if not s or len(s) < 6 or _is_chrome(s):
            continue
        a.smart_detail = s[:60]
        break


def compute_idle(agents: List[Agent]) -> None:
    now = datetime.now()
    for a in agents:
        if not a.updated:
            continue
        try:
            ts = datetime.strptime(f"{now.year}-{a.updated}", "%Y-%m-%d %H:%M")
            a.idle_secs = max(0, int((now - ts).total_seconds()))
        except ValueError:
            pass


# ── Dashboard ──────────────────────────────────────────────


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
        self.agents = parse_agents(self.agents_file)
        enrich_pids(self.agents)
        enrich_tmux(self.agents)
        for a in self.agents:
            detect_status(a)
        compute_idle(self.agents)
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
        if a.idle_secs > 0 and a.is_live:
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
            preview = _get_preview(a.pane_lines, PREVIEW_LINES)
            for pl in preview:
                content.append(
                    f"  {C.DIM}{vtrunc(pl, inner - 4)}{C.RESET}"
                )

        home = str(Path.home())
        dir_short = a.directory.replace(home, "~") if a.directory else ""
        title_parts = [a.status_emoji, a.name]
        if not a.is_local:
            title_parts.append(f"📡 {a.od}")
        if a.tmux_target:
            title_parts.append(a.tmux_target)
        elif a.description and ":" in a.description:
            title_parts.append(a.description)
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
                if a.is_local and a.tmux_target:
                    tmux_cmd("select-window", "-t", a.tmux_target)

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
    Dashboard().run()
