#!/usr/bin/env python3
"""Enhanced agent_state.py with tmux-orchestrator patterns integrated.

This is an enhanced version that adds:
- Real-time pane monitoring alongside transcript analysis
- Context percentage tracking
- Permission prompt detection
- Granular tool execution detection
- Better Neovim chrome filtering
"""

import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple

# Import the enhanced patterns
try:
    from enhanced_status_patterns import EnhancedStatusDetector, StatusResult
except ImportError:
    # Fallback if module not available yet
    EnhancedStatusDetector = None
    StatusResult = None

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


# Enhanced SMART_ICONS with more granular states
SMART_ICONS = {
    "thinking":   ("💭", "Thinking",         C.YELLOW),
    "processing": ("⚙️",  "Processing",       C.YELLOW),
    "tool":       ("🔧", "Running",           C.ORANGE),
    "writing":    ("✏️",  "Writing",           C.GREEN),
    "reading":    ("📖", "Reading",            C.CYAN),
    "searching":  ("🔍", "Searching",          C.BLUE),
    "waiting":    ("⚠️",  "Waiting for input", C.RED),
    "permission": ("🔐", "Permission needed",  C.RED),
    "complete":   ("✅", "Turn complete",       C.GREEN),
    "active":     ("⚡", "Active",             C.YELLOW),
    "stopped":    ("⏹️",  "Stopped",            C.GRAY),
    "idle":       ("💤", "Idle",                C.GRAY),
    "bg:running": ("🔵", "Background",         C.BLUE),
    "bg:done":    ("✅", "Background done",     C.GREEN),
    "stuck":      ("🔴", "Stuck",              C.RED),
    "error":      ("❌", "Error",              C.RED),
}

IDLE_STATUSES = ("waiting", "complete", "idle", "permission")


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
    # New fields for enhanced detection
    context_pct: Optional[int] = None
    is_permission_prompt: bool = False
    is_error: bool = False
    confidence: float = 1.0

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

    @property
    def needs_attention(self) -> bool:
        """Check if agent needs user attention."""
        return (
            self.is_permission_prompt
            or self.is_error
            or (self.context_pct and self.context_pct > 85)
            or self.idle_secs > IDLE_ALERT_SECS
        )


# ── Enhanced Detection with Pattern Library ──────────────

_enhanced_detector = None


def get_enhanced_detector():
    """Get or create the enhanced detector singleton."""
    global _enhanced_detector
    if _enhanced_detector is None and EnhancedStatusDetector:
        _enhanced_detector = EnhancedStatusDetector()
    return _enhanced_detector


def detect_enhanced_status(a: Agent) -> None:
    """Apply enhanced status detection to an agent."""
    if not a.is_live:
        a.smart_status = a.status_text or "stopped"
        return

    detector = get_enhanced_detector()
    if not detector or not a.pane_lines:
        # Fallback to original detection
        detect_status_original(a)
        return

    # Use enhanced detection
    result = detector.detect_comprehensive_status(a.pane_lines)

    # Map result to agent fields
    a.smart_status = result.state
    a.smart_detail = result.detail
    a.context_pct = result.context_pct
    a.is_permission_prompt = result.is_permission_prompt
    a.is_error = result.is_error
    a.confidence = result.confidence

    # Special handling for permission prompts
    if a.is_permission_prompt:
        a.smart_status = "permission"

    # Add context warning to detail if high
    if a.context_pct:
        if a.context_pct > 85:
            a.smart_detail = f"{a.smart_detail} [⚠️ {a.context_pct}% context]"
        elif a.context_pct > 70:
            a.smart_detail = f"{a.smart_detail} [{a.context_pct}% context]"


def detect_status_original(a: Agent) -> None:
    """Original status detection (fallback)."""
    if not a.is_live:
        a.smart_status = a.status_text or "stopped"
        return

    if a.status_text in ("stuck", "waiting"):
        a.smart_status = a.status_text
        _load_watcher_detail(a)
        return

    if not a.is_local:
        a.smart_status = a.status_text or "active"
        a.smart_detail = f"on {a.od} (unverified)"
        return

    if a.claude_state == "⚙":
        _detect_activity_original(a)
    elif a.claude_state == "!":
        a.smart_status = "waiting"
        _extract_detail(a)
    elif a.claude_state in ("✓", "~"):
        a.smart_status = "complete"
        _extract_detail(a)
    elif a.pane_lines:
        _detect_activity_original(a)
    else:
        a.smart_status = "active"


def _detect_activity_original(a: Agent) -> None:
    """Original activity detection."""
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
    """Extract detail from pane lines."""
    preview = get_preview(a.pane_lines, 1)
    if preview:
        a.smart_detail = preview[0][:60]


def _load_watcher_detail(a: Agent) -> None:
    """Load detail from watcher state file."""
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


# ── Chrome Filtering (Enhanced for Neovim) ───────────────

_CHROME_RE = re.compile(
    r"TERMINAL\s+term:"         # neovim terminal statusline
    r"|NORMAL\s+term:"          # neovim normal mode statusline
    r"|INSERT\s+.*term:"        # neovim insert mode statusline
    r"|VISUAL\s+.*term:"        # neovim visual mode
    r"|-- INSERT --"            # vim insert mode indicator
    r"|-- NORMAL --"            # vim normal mode indicator
    r"|-- VISUAL --"            # vim visual mode
    r"|-- REPLACE --"           # vim replace mode
    r"|⏵⏵\s*accept"            # claude accept edits hint
    r"|shift\+tab to cycle"     # claude mode hint
    r"|📁\s+\w+.*🤖"           # claude statusline
    r"|Bot \d+:\d+"             # neovim statusline right side
    r"|^\s*\d+\s+\d+\s*$"      # bare neovim line number pairs
    r"|^\s*\d{1,5}\s*$"        # bare single line numbers
    r"|^[─━═]{5,}$"            # horizontal rules
    r"|▏"                       # neovim indent guides
    r"|~$"                      # vim empty line indicator
    r"|^\[No Name\]"           # vim buffer name
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
    is_nvim = bool(re.search(r"NORMAL\s+term:|TERMINAL\s+term:|INSERT\s+.*term:|VISUAL\s+.*term:", raw))
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


# ── Data Loading ──────────────────────────────────────────

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from agent_state import (
    resolve_local_agents_file, resolve_all_agents_files, resolve_agents_file,
)


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


def compute_idle(agents: List[Agent]) -> None:
    now = datetime.now()
    for a in agents:
        a.idle_secs = 0
        if not a.updated:
            continue
        try:
            ts = datetime.strptime(f"{now.year}-{a.updated}", "%Y-%m-%d %H:%M")
            a.idle_secs = max(0, int((now - ts).total_seconds()))
        except ValueError:
            pass


def detect_status(a: Agent) -> None:
    """Main status detection entry point - uses enhanced detection if available."""
    if EnhancedStatusDetector:
        detect_enhanced_status(a)
    else:
        detect_status_original(a)


def load_agents() -> List[Agent]:
    all_agents = []
    for path in resolve_all_agents_files():
        all_agents.extend(parse_agents(path))
    enrich_pids(all_agents)
    enrich_tmux(all_agents)
    for a in all_agents:
        detect_status(a)
    compute_idle(all_agents)
    return all_agents


# ── Summary Printing ──────────────────────────────────────

def print_summary(agents: List[Agent], show_all: bool = False) -> None:
    """Print agent summary with enhanced status information."""
    if not agents:
        print("No agents found")
        return

    active = [a for a in agents if a.is_live]
    stopped = [a for a in agents if a.is_stopped]

    print(f"\n{'─' * 60}")
    print(f"Active: {len(active)} | Stopped: {len(stopped)} | Total: {len(agents)}")
    print(f"{'─' * 60}\n")

    # Active agents
    if active:
        print(f"{C.BOLD}Active Agents:{C.RESET}")
        for a in active:
            icon, label, color = SMART_ICONS.get(
                a.smart_status, ("?", a.smart_status, C.WHITE)
            )
            idle_str = f" ({fmt_dur(a.idle_secs)} idle)" if a.idle_secs > IDLE_WARN_SECS else ""

            # Add context percentage if available
            ctx_str = ""
            if a.context_pct:
                if a.context_pct > 85:
                    ctx_str = f" {C.RED}[{a.context_pct}%]{C.RESET}"
                elif a.context_pct > 70:
                    ctx_str = f" {C.YELLOW}[{a.context_pct}%]{C.RESET}"
                else:
                    ctx_str = f" {C.GRAY}[{a.context_pct}%]{C.RESET}"

            # Add permission indicator
            perm_str = ""
            if a.is_permission_prompt:
                perm_str = f" {C.RED}🔐{C.RESET}"

            # Add error indicator
            err_str = ""
            if a.is_error:
                err_str = f" {C.RED}❌{C.RESET}"

            print(
                f"  {icon} {C.BOLD}{a.name}{C.RESET} "
                f"{color}{label}{C.RESET}{ctx_str}{perm_str}{err_str}{idle_str}"
            )
            if a.smart_detail:
                print(f"     {C.DIM}{a.smart_detail}{C.RESET}")

    # Stopped agents (if showing all)
    if show_all and stopped:
        print(f"\n{C.BOLD}Stopped Agents:{C.RESET}")
        for a in stopped:
            print(f"  {C.DIM}• {a.name}{C.RESET}")

    # Alerts section
    alerts = [a for a in agents if a.needs_attention]
    if alerts:
        print(f"\n{C.BOLD}{C.RED}⚠️  Alerts:{C.RESET}")
        for a in alerts:
            if a.is_permission_prompt:
                print(f"  {C.RED}• {a.name}: Permission required{C.RESET}")
            if a.is_error:
                print(f"  {C.RED}• {a.name}: Error detected{C.RESET}")
            if a.context_pct and a.context_pct > 85:
                print(f"  {C.YELLOW}• {a.name}: High context usage ({a.context_pct}%){C.RESET}")
            if a.idle_secs > IDLE_ALERT_SECS:
                print(f"  {C.YELLOW}• {a.name}: Idle for {fmt_dur(a.idle_secs)}{C.RESET}")


_watcher_state_cache = None


# Export main functions
__all__ = [
    'Agent', 'C', 'SMART_ICONS',
    'load_agents', 'parse_agents', 'detect_status',
    'print_summary', 'get_preview',
    'fmt_dur', 'vlen', 'tmux_cmd',
    'resolve_agents_file', 'resolve_local_agents_file', 'resolve_all_agents_files',
]
