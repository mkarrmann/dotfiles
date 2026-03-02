#!/usr/bin/env python3
"""Agent watcher — classifies idle sessions via LLM (claude -p --model haiku).

Standalone daemon with no tmux coupling. Reads only:
  - AGENTS.md (session list + idle times)
  - Transcript JSONL files (conversation context)
  - PID files (liveness + transcript path)

Single-instance guard via watcher.pid. Auto-exits after 10 min with no live sessions.
"""

import json
import logging
import os
import signal
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# ── Paths ─────────────────────────────────────────────────

STATE_DIR = Path.home() / ".claude" / "agent-manager"
PIDS_DIR = STATE_DIR / "pids"
LOG_DIR = STATE_DIR / "logs"
PID_FILE = STATE_DIR / "watcher.pid"
STATE_FILE = STATE_DIR / "watcher-state.json"

# ── Config ────────────────────────────────────────────────

POLL_INTERVAL = 30
INACTIVE_THRESHOLD = 150
IDLE_EXIT_THRESHOLD = 600  # 10 min with no live sessions → exit
TRANSCRIPT_TAIL_ENTRIES = 15
TRANSCRIPT_MAX_CHARS = 6000

# Cost tracking (Haiku pricing as of 2025)
HAIKU_INPUT_COST_PER_MTOK = 0.25
HAIKU_OUTPUT_COST_PER_MTOK = 1.25
CHARS_PER_TOKEN_ESTIMATE = 3.5

SKIP_STATUSES = {"waiting", "bg:running"}
CLASSIFIABLE_STATUSES = {"done", "stopped", "active", "interactive", "resumed"}

# ── Logging ───────────────────────────────────────────────

LOG_DIR.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    filename=str(LOG_DIR / "watcher.log"),
    format="%(asctime)s %(message)s",
    datefmt="%m-%d %H:%M:%S",
    level=logging.INFO,
)
log = logging.getLogger("watcher")


# ── AGENTS.md parsing (minimal, no tmux) ─────────────────


def resolve_agents_file() -> Path:
    if env := os.environ.get("CLAUDE_AGENTS_FILE"):
        return Path(env)
    gdrive = Path(f"/data/users/{os.environ.get('USER', 'nobody')}/gdrive/AGENTS.md")
    if gdrive.exists():
        return gdrive
    return Path.home() / ".claude" / "agents.md"


def parse_agents(path: Path) -> List[dict]:
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
        status_raw = parts[2]
        status_parts = status_raw.split(maxsplit=1)
        status_text = status_parts[1].strip() if len(status_parts) > 1 else ""
        agents.append({
            "name": parts[1],
            "status_raw": status_raw,
            "status_text": status_text,
            "session_id": parts[4],
            "updated": parts[7],
        })
    return agents


def compute_idle(agents: List[dict]) -> None:
    now = datetime.now()
    for a in agents:
        a["idle_secs"] = 0
        if not a["updated"]:
            continue
        try:
            ts = datetime.strptime(f"{now.year}-{a['updated']}", "%Y-%m-%d %H:%M")
            a["idle_secs"] = max(0, int((now - ts).total_seconds()))
        except ValueError:
            pass


# ── Transcript reading ────────────────────────────────────


def find_transcript_path(sid: str) -> Optional[Path]:
    tp_file = PIDS_DIR / f"{sid}.transcript"
    if tp_file.exists():
        try:
            tp = Path(tp_file.read_text().strip())
            if tp.exists():
                return tp
        except OSError:
            pass
    return None


def read_transcript_tail(path: Path) -> str:
    try:
        lines = path.read_text().strip().split("\n")
    except OSError:
        return "(transcript unreadable)"

    entries = []
    for line in lines[-TRANSCRIPT_TAIL_ENTRIES:]:
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue

        entry_type = d.get("type", "")
        if entry_type in ("summary", "progress"):
            continue

        role = d.get("role", "")
        if entry_type == "assistant":
            msg = d.get("message", {})
            content = msg.get("content", []) if isinstance(msg, dict) else []
            parts = []
            for block in (content if isinstance(content, list) else []):
                if isinstance(block, dict):
                    if block.get("type") == "text":
                        parts.append(block.get("text", "")[:300])
                    elif block.get("type") == "tool_use":
                        parts.append(f"[tool_use: {block.get('name', '?')}]")
                    elif block.get("type") == "tool_result":
                        parts.append(f"[tool_result: {str(block.get('content', ''))[:100]}]")
            entries.append(f"assistant: {' | '.join(parts)}")

        elif entry_type == "human":
            msg = d.get("message", {})
            content = msg.get("content", "") if isinstance(msg, dict) else ""
            if isinstance(content, list):
                text_parts = [
                    b.get("text", "")[:200]
                    for b in content
                    if isinstance(b, dict) and b.get("type") == "text"
                ]
                content = " ".join(text_parts)
            entries.append(f"user: {str(content)[:200]}")

    result = "\n".join(entries)
    if len(result) > TRANSCRIPT_MAX_CHARS:
        result = result[-TRANSCRIPT_MAX_CHARS:]
    return result if result else "(no transcript entries)"


# ── LLM classification ───────────────────────────────────

CLASSIFICATION_PROMPT = """You are monitoring a Claude Code session. Based on the conversation transcript below, classify the session state as one of:
- WORKING: actively making progress on the task
- STUCK: not making progress — looping, hitting repeated errors, or hung
- CRASHED: terminated unexpectedly mid-task
- WAITING: legitimately waiting for user input
- DONE: finished its task successfully

Respond with ONLY the classification word and a short reason (max 15 words).
Example: "DONE — completed all requested file changes"
Example: "STUCK — hitting the same permission error in a loop"

Session status: {status}
Idle time: {idle_secs}s

Recent conversation:
{transcript}"""


def classify_session(agent: dict, transcript: str) -> Tuple[str, str, dict]:
    """Returns (verdict, raw_output, usage_stats)."""
    prompt = CLASSIFICATION_PROMPT.format(
        status=agent["status_raw"],
        idle_secs=agent["idle_secs"],
        transcript=transcript,
    )

    input_chars = len(prompt)
    try:
        result = subprocess.run(
            ["claude", "-p", "--model", "haiku", prompt],
            capture_output=True,
            text=True,
            timeout=60,
            env={**os.environ, "CLAUDECODE": ""},  # unset to allow nested invocation
        )
        output = result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        log.error("classify failed for %s: %s", agent["session_id"][:8], e)
        return "ERROR", str(e), {}

    output_chars = len(output)
    input_tokens_est = int(input_chars / CHARS_PER_TOKEN_ESTIMATE)
    output_tokens_est = int(output_chars / CHARS_PER_TOKEN_ESTIMATE)
    cost_est = (
        input_tokens_est * HAIKU_INPUT_COST_PER_MTOK / 1_000_000
        + output_tokens_est * HAIKU_OUTPUT_COST_PER_MTOK / 1_000_000
    )

    usage = {
        "input_tokens_est": input_tokens_est,
        "output_tokens_est": output_tokens_est,
        "cost_est": cost_est,
    }

    verdict = output.split()[0].upper().rstrip("—:-") if output else "ERROR"
    if verdict not in ("WORKING", "STUCK", "CRASHED", "WAITING", "DONE"):
        verdict = "ERROR"
    return verdict, output, usage


# ── AGENTS.md update ──────────────────────────────────────


def update_agents_md(sid: str, new_status: str) -> None:
    agents_file = resolve_agents_file()
    lock_dir = agents_file.parent / ".agents.lock.d"

    # Acquire lock (same protocol as agent-tracker.sh)
    for i in range(10):
        try:
            lock_dir.mkdir()
            break
        except FileExistsError:
            lock_age = time.time() - lock_dir.stat().st_mtime
            if lock_age > 30:
                lock_dir.rmdir()
                continue
            time.sleep(0.5)
    else:
        log.error("could not acquire lock to update %s", sid[:8])
        return

    try:
        ts = datetime.now().strftime("%m-%d %H:%M")
        lines = agents_file.read_text().split("\n")
        new_lines = []
        updated = False
        for line in lines:
            if f"| {sid} |" in line:
                parts = line.split("|")
                if len(parts) >= 9:
                    current_status = parts[3].strip()
                    # Only update if session is still idle — don't overwrite
                    # if it went active between our check and this write
                    if current_status in ("⚡ active", "🔵 bg:running"):
                        log.info("skip update %s — status is now %s", sid[:8], current_status)
                    else:
                        parts[3] = f" {new_status} "
                        parts[8] = f" {ts} "
                        line = "|".join(parts)
                        updated = True
            new_lines.append(line)
        if updated:
            agents_file.write_text("\n".join(new_lines))
            log.info("updated %s → %s", sid[:8], new_status)
    except OSError as e:
        log.error("failed to update agents.md: %s", e)
    finally:
        try:
            lock_dir.rmdir()
        except OSError:
            pass


def notify_stuck(agent: dict) -> None:
    """Ring the terminal bell on the agent's tmux window and set @claude_state."""
    # The description field stores the tmux session:window context (e.g. "main:3")
    target = agent.get("description", "").strip()
    if not target or ":" not in target:
        return
    try:
        # Find the pane TTY and ring the bell
        result = subprocess.run(
            ["tmux", "display-message", "-t", target, "-p", "#{pane_tty}"],
            capture_output=True, text=True, timeout=2,
        )
        tty = result.stdout.strip()
        if tty:
            with open(tty, "w") as f:
                f.write("\a")
        # Set @claude_state to "!" so the window gets flagged
        subprocess.run(
            ["tmux", "set-option", "-wq", "-t", target, "@claude_state", "!"],
            capture_output=True, timeout=2,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass


# ── Watcher state ─────────────────────────────────────────


def load_state() -> Dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def save_state(state: Dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2))


# ── Single instance ───────────────────────────────────────


def check_single_instance() -> bool:
    if PID_FILE.exists():
        try:
            old_pid = int(PID_FILE.read_text().strip())
            if Path(f"/proc/{old_pid}").exists():
                return False
        except (ValueError, OSError):
            pass
    PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    PID_FILE.write_text(str(os.getpid()))
    return True


def cleanup_pid_file(*_):
    try:
        PID_FILE.unlink(missing_ok=True)
    except OSError:
        pass


# ── Main loop ─────────────────────────────────────────────


def main():
    if not check_single_instance():
        log.info("another watcher is running, exiting")
        sys.exit(0)

    signal.signal(signal.SIGTERM, lambda *_: (cleanup_pid_file(), sys.exit(0)))
    signal.signal(signal.SIGINT, lambda *_: (cleanup_pid_file(), sys.exit(0)))

    import atexit
    atexit.register(cleanup_pid_file)

    log.info("watcher started (pid=%d)", os.getpid())

    no_live_since: Optional[float] = None

    while True:
        try:
            agents_file = resolve_agents_file()
            agents = parse_agents(agents_file)
            compute_idle(agents)
            state = load_state()

            active_sids = {a["session_id"] for a in agents}
            live_agents = [
                a for a in agents
                if a["status_text"] not in ("stopped", "idle", "bg:done", "stuck", "crashed")
            ]

            # Auto-exit if no live sessions for IDLE_EXIT_THRESHOLD
            if live_agents:
                no_live_since = None
            else:
                if no_live_since is None:
                    no_live_since = time.time()
                elif time.time() - no_live_since > IDLE_EXIT_THRESHOLD:
                    log.info("no live sessions for %ds, exiting", IDLE_EXIT_THRESHOLD)
                    break

            # Clear classifications for sessions that went active again
            cleared = [
                sid for sid in list(state.keys())
                if sid != "_usage" and isinstance(state[sid], dict)
                and state[sid].get("classified")
                and any(
                    a["session_id"] == sid and a["status_text"] == "active"
                    for a in agents
                )
            ]
            for sid in cleared:
                log.info("clearing classification for %s (went active)", sid[:8])
                del state[sid]

            # Prune state entries for sessions no longer in AGENTS.md
            pruned = [
                sid for sid in state
                if sid not in active_sids and sid != "_usage"
            ]
            for sid in pruned:
                del state[sid]

            if cleared or pruned:
                save_state(state)

            # Classify idle sessions
            for agent in agents:
                sid = agent["session_id"]
                if not sid:
                    continue
                if agent["idle_secs"] < INACTIVE_THRESHOLD:
                    continue
                if sid in state and state[sid].get("classified"):
                    continue
                if agent["status_text"] in SKIP_STATUSES:
                    continue
                if agent["status_text"] not in CLASSIFIABLE_STATUSES:
                    continue

                # Mark as in-flight BEFORE the LLM call so a crash won't re-classify
                state[sid] = {
                    "classified": True,
                    "verdict": "PENDING",
                    "timestamp": time.time(),
                }
                save_state(state)

                transcript_path = find_transcript_path(sid)
                if not transcript_path:
                    log.info("skip %s (%s): no transcript", sid[:8], agent["name"])
                    state[sid]["verdict"] = "UNKNOWN"
                    state[sid]["reason"] = "no transcript path"
                    save_state(state)
                    continue

                transcript = read_transcript_tail(transcript_path)
                log.info(
                    "classifying %s (%s) idle=%ds status=%s",
                    sid[:8], agent["name"], agent["idle_secs"], agent["status_text"],
                )

                verdict, raw, usage = classify_session(agent, transcript)
                log.info("verdict for %s: %s (%s)", sid[:8], verdict, raw[:80])

                # Track cumulative usage
                totals = state.get("_usage", {
                    "total_classifications": 0,
                    "total_input_tokens_est": 0,
                    "total_output_tokens_est": 0,
                    "total_cost_est": 0.0,
                })
                totals["total_classifications"] += 1
                totals["total_input_tokens_est"] += usage.get("input_tokens_est", 0)
                totals["total_output_tokens_est"] += usage.get("output_tokens_est", 0)
                totals["total_cost_est"] += usage.get("cost_est", 0.0)
                state["_usage"] = totals

                state[sid] = {
                    "classified": True,
                    "verdict": verdict,
                    "reason": raw,
                    "timestamp": time.time(),
                    "usage": usage,
                }
                save_state(state)

                if verdict == "STUCK":
                    update_agents_md(sid, "🔴 stuck")
                    notify_stuck(agent)

        except Exception:
            log.exception("watcher loop error")

        time.sleep(POLL_INTERVAL)

    cleanup_pid_file()
    log.info("watcher exiting")


if __name__ == "__main__":
    main()
