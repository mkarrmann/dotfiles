"""Behavioural tests for the claude-native hook-brace fix.

Each scenario runs in a fresh interpreter with this directory on
``PYTHONPATH``, as ``omnigent-host`` launches runners. The real
``sitecustomize`` installs the patch, and the scenario calls upstream's
``augment_claude_args``, which writes ``claude-settings.json``. ``TMPDIR``
points into a scratch dir, so the bridge root
(``$TMPDIR/omnigent-<uid>/claude-native``) never touches the live one.

Claude Code is stood in for by ``claude_expand``, which reproduces the
substitution Claude Code 2.1.310 was measured to apply to hook commands before
``/bin/sh -c`` runs them: ``${NAME}`` becomes the variable's value, other
``${...}`` become empty unless they contain a ``:``, and ``$${...}`` is a
literal ``${...}``. Each hook command is expanded that way and then run by the
real ``/bin/sh``.

Run: ~/.local/share/uv/tools/omnigent/bin/python test_omnigent_claude_hook_braces.py
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from types import ModuleType

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from omnigent_claude_hook_braces import install  # noqa: E402

SCENARIO = r"""
import json, os, re, shlex, subprocess, sys, time

from omnigent.harnesses.claude_native import bridge


def claude_expand(command):
    def sub(m):
        escaped, body = m.group(1), m.group(2)
        if escaped or ":" in body:
            return "${" + body + "}" if escaped else m.group(0)
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", body):
            return os.environ.get(body, "")
        return ""
    return re.sub(r"(\$?)\$\{([^}]*)\}", sub, command)


bridge_dir = bridge.bridge_dir_for_bridge_id("bridge_label_id")
bridge._ensure_secure_dir(bridge_dir)
bridge.augment_claude_args(
    (), bridge_dir=bridge_dir, python_executable=sys.executable,
    ap_server_url="http://server.test",
)
settings = json.loads((bridge_dir / "claude-settings.json").read_text())
hooks = [
    (event, h["command"])
    for event, groups in settings["hooks"].items()
    for g in groups
    for h in g["hooks"]
    if "command" in h
]

unparsable = sorted({
    event for event, command in hooks
    if subprocess.run(["/bin/sh", "-n", "-c", claude_expand(command)],
                      capture_output=True).returncode != 0
})

# Open the policy allow window: the guard then answers a prompt in the shell
# with exit 0 and no output, the hooks' way of saying ALLOW.
(bridge_dir / "policy_allow_until").write_text(f"{int(time.time()) + 300} gen 0\n")
def script_text(command):
    argv = shlex.split(command)
    return open(argv[1]).read() if argv[:1] == ["/bin/sh"] and len(argv) == 2 else command


guard = next(
    c for e, c in hooks if e == "UserPromptSubmit" and "policy_allow_until" in script_text(c)
)
proc = subprocess.run(
    ["/bin/sh", "-c", claude_expand(guard)],
    input=json.dumps({"session_id": "s", "hook_event_name": "UserPromptSubmit", "prompt": "hi"}),
    capture_output=True, text=True, timeout=30,
)
print(json.dumps({
    "hook_count": len(hooks),
    "unparsable": unparsable,
    "guard_rc": proc.returncode,
    "guard_stdout": proc.stdout,
    "commands_with_braces": sum("${" in c for _, c in hooks),
}))
"""


def run_scenario(scratch: Path, *, env_overrides: dict[str, str] | None = None):
    tmpdir = Path(tempfile.mkdtemp(dir=scratch))
    env = {
        **{k: v for k, v in os.environ.items() if not k.startswith("OMNIGENT_")},
        "PYTHONPATH": str(HERE),
        "TMPDIR": str(tmpdir),
        **(env_overrides or {}),
    }
    proc = subprocess.run(
        [sys.executable, "-c", SCENARIO],
        capture_output=True,
        text=True,
        env=env,
        cwd=tmpdir,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"scenario crashed:\n{proc.stderr}")
    return json.loads(proc.stdout.strip().splitlines()[-1])


fails = []


def check(label, got, want):
    ok = got == want
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}")
    if not ok:
        print(f"         got={got!r}\n         want={want!r}")
        fails.append(label)


scratch = Path(tempfile.mkdtemp(prefix="hookbraces-"))
try:
    print("\n1. upstream (patch disabled): Claude Code's ${...} rewrite breaks the hooks")
    out = run_scenario(scratch, env_overrides={"OMNIGENT_CLAUDE_HOOK_BRACES": "0"})
    check(
        "policy guards no longer parse",
        {"PreToolUse", "PostToolUse", "UserPromptSubmit"} <= set(out["unparsable"]),
        True,
    )
    check("an open allow window does not answer the prompt", out["guard_rc"] == 0, False)

    print("\n2. patched: every hook survives the rewrite")
    out = run_scenario(scratch)
    check("hooks still registered", out["hook_count"] > 0, True)
    check("no command carries ${", out["commands_with_braces"], 0)
    check("every hook parses", out["unparsable"], [])
    check("an open allow window answers ALLOW", (out["guard_rc"], out["guard_stdout"]), (0, ""))

    print("\n3. upgrade guard: missing upstream names leave the module untouched")
    stripped = ModuleType("omnigent.harnesses.claude_native.bridge")
    stripped.build_hook_settings = sentinel = object()
    check("install reports not installed", install(stripped), False)
    check("build_hook_settings unchanged", stripped.build_hook_settings is sentinel, True)
finally:
    shutil.rmtree(scratch, ignore_errors=True)

print(
    f"\n{'ALL PASSED' if not fails else str(len(fails)) + ' FAILURE(S): ' + ', '.join(fails)}"
)
sys.exit(1 if fails else 0)
