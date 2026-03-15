#!/usr/bin/env python3
"""Agent state — shared core for agent tracking, status detection, and display.

Used by dashboard.py (TUI), the snacks dashboard summary (--summary),
and available for any other consumer (statusline, scripts, etc.).
"""

import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import List, Optional


# ── Constants ─────────────────────────────────────────────

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
    "stuck":      ("🔴", "Stuck",              C.RED),
}

IDLE_STATUSES = ("waiting", "complete", "idle")


# ── Helpers ───────────────────────────────────────────────


def vlen(s: str) -> int:
    return len(_ANSI_RE.sub("", s))


def fmt_dur(secs: int) -> str:
    if secs < 60:
        return f"{secs}s"
    if secs < 3600:
        return f"{secs // 60}m"
    h = secs // 3600
    m = (secs % 3600) // 60
    return f"{h}h{m}m"


def tmux_cmd(*args: str) -> Optional[str]:
    try:
        r = subprocess.run(
            ["tmux", *args], capture_output=True, text=True, timeout=2
        )
        return r.stdout.strip() if r.returncode == 0 else None
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None


# ── Data Model ────────────────────────────────────────────


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
            "active", "waiting", "done", "interactive", "resumed", "stuck"
        )

    @property
    def is_stopped(self) -> bool:
        return self.status_text in ("stopped", "idle") or self.status_text == "bg:done"


# ── Data Loading ──────────────────────────────────────────


def _read_vault_root() -> str:
    conf = Path.home() / ".claude" / "obsidian-vault.conf"
    if conf.exists():
        try:
            for line in conf.read_text().splitlines():
                line = line.strip()
                if line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                if key.strip() == "OBSIDIAN_VAULT":
                    val = val.strip().strip('"').strip("'")
                    if val.startswith("${OBSIDIAN_VAULT:-") and val.endswith("}"):
                        env_default = val[len("${OBSIDIAN_VAULT:-"):-1]
                        val = os.environ.get("OBSIDIAN_VAULT", env_default)
                    val = val.replace("$HOME", str(Path.home()))
                    return val
        except OSError:
            pass
    return os.environ.get("OBSIDIAN_VAULT", str(Path.home() / "obsidian"))


def _hostname() -> str:
    return os.uname().nodename.split(".")[0]


def _resolve_agents_dir() -> Path:
    if env := os.environ.get("CLAUDE_AGENTS_FILE"):
        return Path(env).parent
    vault = _read_vault_root()
    d = Path(vault)
    try:
        if d.is_dir():
            return d
    except OSError:
        pass
    return Path.home() / ".claude"


def resolve_local_agents_file() -> Path:
    if env := os.environ.get("CLAUDE_AGENTS_FILE"):
        return Path(env)
    return _resolve_agents_dir() / f"AGENTS-{_hostname()}.md"


def resolve_all_agents_files() -> List[Path]:
    if env := os.environ.get("CLAUDE_AGENTS_FILE"):
        p = Path(env)
        return [p] if p.exists() else []
    d = _resolve_agents_dir()
    files = sorted(d.glob("AGENTS-*.md"))
    legacy = d / "AGENTS.md"
    if legacy.exists():
        files.append(legacy)
    return files


resolve_agents_file = resolve_local_agents_file


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


def parse_all_agents() -> List[Agent]:
    all_agents = []
    for path in resolve_all_agents_files():
        all_agents.extend(parse_agents(path))
    return all_agents


def load_agents() -> List[Agent]:
    agents = parse_all_agents()
    enrich_pids(agents)
    enrich_tmux(agents)
    for a in agents:
        detect_status(a)
    compute_idle(agents)
    return agents


# ── Chrome Filtering ──────────────────────────────────────


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


def is_chrome(line: str) -> bool:
    return bool(_CHROME_RE.search(line))


_NVIM_LINENUM_RE = re.compile(r"^\s*\d{1,5}\s{2,}\S")


def clean_line(line: str) -> str:
    s = line.strip()
    s = re.sub(r"^\d+\s+\d+\s+", "", s)
    if "│" in s:
        parts = [p.strip() for p in s.split("│") if p.strip()]
        if not parts:
            return ""
        non_code = [p for p in parts if not _NVIM_LINENUM_RE.match(p)]
        if non_code:
            return max(non_code, key=len)
        return max(parts, key=len)
    if _NVIM_LINENUM_RE.match(s):
        return ""
    s = re.sub(r"^\d{1,5}\s{2,}", "", s)
    return s


def get_preview(pane_lines: List[str], count: int) -> List[str]:
    raw = "\n".join(pane_lines[-10:])
    is_nvim = bool(re.search(r"NORMAL\s+term:|TERMINAL\s+term:|INSERT\s+.*term:", raw))
    cleaned = []
    for line in pane_lines:
        if is_chrome(line):
            continue
        s = clean_line(line)
        if not s or is_chrome(s):
            continue
        if is_nvim and re.match(r"^\d{1,5}\s+\S", s):
            continue
        cleaned.append(s)
    if len(cleaned) > count + 2:
        cleaned = cleaned[:-2]
    return cleaned[-count:]


# ── Smart Status Detection ────────────────────────────────

_watcher_state_cache: Optional[dict] = None


def _load_watcher_detail(a: Agent) -> None:
    global _watcher_state_cache
    if _watcher_state_cache is None:
        state_file = Path.home() / ".claude" / "agent-manager" / "watcher-state.json"
        try:
            import json
            _watcher_state_cache = json.loads(state_file.read_text()) if state_file.exists() else {}
        except (OSError, ValueError):
            _watcher_state_cache = {}
    entry = _watcher_state_cache.get(a.session_id, {})
    if entry.get("detail"):
        a.smart_detail = entry["detail"]


def detect_status(a: Agent) -> None:
    if not a.is_live:
        a.smart_status = a.status_text or "stopped"
        return

    # Watcher-classified sessions: use detail from watcher state
    if a.status_text in ("stuck", "waiting"):
        a.smart_status = a.status_text
        _load_watcher_detail(a)
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


def _extract_detail(a: Agent) -> None:
    if not a.pane_lines:
        return
    for line in reversed(a.pane_lines[:-3]):
        if is_chrome(line):
            continue
        s = clean_line(line)
        if not s or len(s) < 6 or is_chrome(s):
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


# ── Summary Output ────────────────────────────────────────


def _print_agent_card(a: Agent, show_od: bool = False):
    icon, label, color = SMART_ICONS.get(
        a.smart_status, ("·", a.status_raw, C.GRAY)
    )

    name_line = f"  {icon}  {C.BOLD}{a.name}{C.RESET}"
    if show_od:
        name_line += f"  {C.DIM}({a.od}){C.RESET}"
    print(name_line)

    parts = []
    if a.tmux_target:
        parts.append(f"{C.DIM}{a.tmux_target}{C.RESET}")
    parts.append(f"{color}{label}{C.RESET}")
    if a.smart_status in IDLE_STATUSES:
        if a.idle_secs >= IDLE_ALERT_SECS:
            parts.append(f"{C.RED}{fmt_dur(a.idle_secs)} ⚠{C.RESET}")
        elif a.idle_secs >= IDLE_WARN_SECS:
            parts.append(f"{C.ORANGE}{fmt_dur(a.idle_secs)}{C.RESET}")
    print(f"     {' · '.join(parts)}")

    if a.smart_detail:
        detail = a.smart_detail[:50]
        print(f"     {C.DIM}{detail}{C.RESET}")

    print()


def print_summary(scope: str = "all"):
    agents = load_agents()

    local_live = [a for a in agents if a.is_local and a.is_live]
    local_stopped = [a for a in agents if a.is_local and a.is_stopped]
    remote_live = [a for a in agents if not a.is_local and a.is_live]
    remote_stopped = [a for a in agents if not a.is_local and a.is_stopped]

    if scope in ("all", "local"):
        hostname = os.uname().nodename.split(".")[0]
        print(f"  {C.BOLD}{C.CYAN}⚡ Local Agents{C.RESET}  {C.DIM}{hostname}{C.RESET}")
        print()
        if not local_live:
            print(f"  {C.DIM}No active sessions{C.RESET}")
            print()
        else:
            for a in local_live:
                _print_agent_card(a)
        if local_stopped:
            print(f"  {C.DIM}+ {len(local_stopped)} stopped{C.RESET}")
            print()

    if scope in ("all", "remote"):
        print(f"  {C.BOLD}{C.MAGENTA}📡 Remote Agents{C.RESET}")
        print()
        if not remote_live:
            print(f"  {C.DIM}No remote sessions{C.RESET}")
            print()
        else:
            for a in remote_live:
                _print_agent_card(a, show_od=True)
        if remote_stopped:
            print(f"  {C.DIM}+ {len(remote_stopped)} stopped{C.RESET}")
            print()


if __name__ == "__main__":
    scope = "all"
    if "--local" in sys.argv:
        scope = "local"
    elif "--remote" in sys.argv:
        scope = "remote"
    print_summary(scope)
