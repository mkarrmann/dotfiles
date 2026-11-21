"""Behavioural tests for the context-window fallback.

Each scenario runs in a fresh interpreter with this directory on
``PYTHONPATH``, as the Omnigent server and runners start, so the real
``sitecustomize`` installs the patch. The shared model catalog is switched off
(``OMNIGENT_DISABLE_CATALOG_LOOKUP=1``) and litellm reads its bundled map, so
no scenario touches the network and "upstream does not know the model" holds
whatever the live catalog lists today.

Run: ~/.local/share/uv/tools/omnigent/bin/python test_omnigent_context_windows.py
"""

import json
import os
import subprocess
import sys
from pathlib import Path
from types import ModuleType

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from omnigent_context_windows import install  # noqa: E402

SCENARIO = r"""
import json
from omnigent.llms.context_window import find_model_context_window as find, get_model_context_window as get
print(json.dumps({
    "find_neon": find("google/gemini-4-neon"),
    "get_neon": get("google/gemini-4-neon"),
    "get_carbon_bare": get("gemini-4-carbon"),
    "get_unknown": get("google/gemini-9-unknown"),
}))
"""

SERVER_FLAGS = {"OMNIGENT_SAPLING_REGISTRY": "0", "OMNIGENT_TMUX_SELF_HEAL": "0", "OMNIGENT_CLAUDE_HOOK_BRACES": "0"}


def run_scenario(env_overrides: dict[str, str] | None = None) -> tuple[dict, str]:
    env = {
        k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "AP_CONTEXT_WINDOW_OVERRIDE", *SERVER_FLAGS)
    }
    env.update(
        PYTHONPATH=str(HERE),
        OMNIGENT_DISABLE_CATALOG_LOOKUP="1",
        LITELLM_LOCAL_MODEL_COST_MAP="True",
        OMNIGENT_SAPLING_REGISTRY_DEBUG="1",
    )
    env.update(env_overrides or {})
    proc = subprocess.run(
        [sys.executable, "-P", "-c", SCENARIO], env=env, capture_output=True, text=True, timeout=120, check=False
    )
    if proc.returncode != 0:
        raise SystemExit(f"scenario failed:\n{proc.stderr}")
    return json.loads(proc.stdout.strip().splitlines()[-1]), proc.stderr


failures = 0


def check(label, got, want):
    global failures
    ok = got == want
    failures += not ok
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}")
    if not ok:
        print(f"         got={got!r}\n         want={want!r}")


print("\n1. upstream (patch disabled): Gemini 4 falls to the 128K default")
got, _ = run_scenario({"OMNIGENT_CONTEXT_WINDOWS": "0"})
check("find_model_context_window(gemini-4-neon) is None", got["find_neon"], None)
check("get_model_context_window(gemini-4-neon) is 128K", got["get_neon"], 128_000)

print("\n2. patched: Gemini 4 gets Pi's window; other unknown models keep the default")
got, _ = run_scenario()
check("find_model_context_window(google/gemini-4-neon)", got["find_neon"], 1_048_576)
check("get_model_context_window(google/gemini-4-neon)", got["get_neon"], 1_048_576)
check("get_model_context_window(gemini-4-carbon), no provider prefix", got["get_carbon_bare"], 1_048_576)
check("an unknown model not in the table keeps 128K", got["get_unknown"], 128_000)

print("\n3. a window upstream resolves wins over the table")
got, _ = run_scenario({"AP_CONTEXT_WINDOW_OVERRIDE": "5000"})
check("upstream's override beats the table", got["get_neon"], 5000)

print("\n4. the server's environment installs only this patch")
got, stderr = run_scenario(SERVER_FLAGS)
check("patched under the server's flags", got["get_neon"], 1_048_576)
armed = next((line for line in stderr.splitlines() if "armed for" in line), "")
check("only omnigent.llms.context_window is armed", armed.endswith("armed for ['omnigent.llms.context_window']"), True)

print("\n5. upgrade guard: missing upstream names leave the module untouched")
bare = ModuleType("omnigent.llms.context_window")
bare.find_model_context_window = lambda model: None  # type: ignore[attr-defined]
check("install() refuses a module without get_model_context_window", install(bare), False)
check("find_model_context_window left as it was", bare.find_model_context_window("gemini-4-neon"), None)

print(f"\n{'all passed' if not failures else f'{failures} failed'}")
sys.exit(1 if failures else 0)
