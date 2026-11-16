#!/usr/bin/env python3
"""orchest-open-workspaces against a simulated Orchest CLI and AeroSpace.

Run with: python3 -m unittest discover -s tests -p test_orchest_open_workspaces.py
"""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import textwrap
import time
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "bin-macos/orchest-open-workspaces"

# The CLI, run as `node bin/orchest.js ...`: workspaces in state.json; an open
# adds the workspace's window on its desktop workspace, as Orchest does.
NODE = '''
    import json, os, sys
    from pathlib import Path
    root = Path(os.environ["SIM"])
    args = sys.argv[2:]
    with open(root / "cli-calls", "a") as calls:
        calls.write(" ".join(args) + "\\n")
    state = json.loads((root / "state.json").read_text())
    if args == ["workspace", "list", "--json"]:
        print(json.dumps(state["workspaces"]))
    elif args[:2] == ["workspace", "open"]:
        ws = next(w for w in state["workspaces"] if w["id"] == args[2])
        state["windows"].append(dict(id=len(state["windows"]) + 100, ws=ws["desktopWorkspaceId"],
                                     title=f"{ws['label']} — Orchest [{ws['shortId']}]"))
    elif args[:2] == ["workspace", "create"]:
        slot, label = args[3], args[4]
        state["workspaces"].append(dict(id=f"new{slot}-0000", shortId=f"new{slot}", label=label,
                                        desktopWorkspaceId=slot))
    else:
        sys.exit(1)
    (root / "state.json").write_text(json.dumps(state))
'''

AEROSPACE = '''
    import json, os, sys
    from pathlib import Path
    root = Path(os.environ["SIM"])
    state = json.loads((root / "state.json").read_text())
    args = sys.argv[1:]
    if args[0] == "list-windows":
        for w in state["windows"]:
            print(f"{w['id']}|{w['ws']}|{w['title']}")
    elif args[0] == "move-node-to-workspace":
        wid, ws = int(args[2]), args[3]
        with open(root / "moves", "a") as moves:
            moves.write(f"{wid} {ws}\\n")
        for w in state["windows"]:
            if w["id"] == wid:
                w["ws"] = ws
        # The provider observes the move and persists the binding.
        for o in state["workspaces"]:
            if any(w["id"] == wid and f"[{o['shortId']}]" in w["title"] for w in state["windows"]):
                o["desktopWorkspaceId"] = ws
        (root / "state.json").write_text(json.dumps(state))
    else:
        sys.exit(1)
'''

CURL = '''
    import json
    print(json.dumps(dict(service="orchest", protocolVersion=1, shell="desktop", pid=123, ready=True)))
    print(200)
'''


def steady_state():
    workspaces, windows = [], []
    for slot in range(1, 9):
        short = f"{slot:08x}"
        workspaces.append(dict(id=f"{short}-aaaa", shortId=short, label=f"Workspace {slot}",
                               desktopWorkspaceId=str(slot)))
        windows.append(dict(id=slot, ws=str(slot), title=f"Workspace {slot} — Orchest [{short}]"))
    return dict(workspaces=workspaces, windows=windows)


class OpenWorkspacesTest(unittest.TestCase):
    def run_script(self, state, *args, stamp_age=None):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "bin").mkdir()
            (root / "home/dev/orchest/apps/cli/bin").mkdir(parents=True)
            (root / "home/dev/orchest/apps/cli/bin/orchest.js").touch()
            (root / "state.json").write_text(json.dumps(state))
            (root / "cli-calls").touch()
            (root / "moves").touch()
            for name, body in dict(node=NODE, aerospace=AEROSPACE, curl=CURL).items():
                executable = root / "bin" / name
                executable.write_text("#!/usr/bin/env python3\n" + textwrap.dedent(body).lstrip())
                executable.chmod(0o755)
            stamp = root / "stamp"
            if stamp_age is not None:
                stamp.touch()
                then = time.time() - stamp_age
                os.utime(stamp, (then, then))
            environment = dict(os.environ, SIM=str(root), HOME=str(root / "home"),
                               ORCHEST_ACTION_STAMP=str(stamp),
                               PATH=f"{root / 'bin'}:{os.environ['PATH']}")
            started = time.monotonic()
            result = subprocess.run(["bash", str(SCRIPT), *args], env=environment,
                                    capture_output=True, text=True, timeout=60)
            elapsed = time.monotonic() - started
            calls = (root / "cli-calls").read_text().splitlines()
            moves = (root / "moves").read_text().splitlines()
            return result, calls, moves, elapsed, json.loads((root / "state.json").read_text())

    def test_steady_state_open_lists_once_and_opens_nothing(self):
        result, calls, moves, _, _ = self.run_script(steady_state())
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(calls, ["workspace list --json"])
        self.assertEqual(moves, [])
        self.assertNotIn("opening", result.stdout)

    def test_steady_state_verify_lists_once(self):
        result, calls, moves, _, _ = self.run_script(steady_state(), "--verify-only")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(calls, ["workspace list --json"])
        self.assertEqual(moves, [])

    def test_verify_soon_after_a_request_looks_twice(self):
        result, calls, _, _, _ = self.run_script(steady_state(), "--verify-only", stamp_age=1)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(calls, ["workspace list --json"] * 2)

    def test_only_the_missing_window_is_opened(self):
        state = steady_state()
        state["windows"] = [w for w in state["windows"] if w["ws"] != "3"]
        result, calls, moves, _, after = self.run_script(state)
        self.assertEqual(result.returncode, 0, result.stderr)
        opens = [c for c in calls if c.startswith("workspace open")]
        self.assertEqual(opens, ["workspace open 00000003-aaaa"])
        self.assertIn(("3", "Workspace 3 — Orchest [00000003]"),
                      [(w["ws"], w["title"]) for w in after["windows"]])
        # Its open may still be in flight, so placement is confirmed twice.
        self.assertGreaterEqual(calls.count("workspace list --json"), 3)

    def test_a_misplaced_window_is_moved_back(self):
        state = steady_state()
        state["windows"][4]["ws"] = "1"
        result, calls, moves, _, _ = self.run_script(state)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(moves, ["5 5"])
        self.assertFalse([c for c in calls if c.startswith("workspace open")])

    def test_a_missing_workspace_is_created_and_opened(self):
        state = steady_state()
        state["workspaces"] = [w for w in state["workspaces"] if w["label"] != "Workspace 8"]
        state["windows"] = [w for w in state["windows"] if w["ws"] != "8"]
        result, calls, _, _, after = self.run_script(state)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("workspace create --desktop-workspace 8 Workspace 8", calls)
        self.assertIn("workspace open new8-0000", calls)
        self.assertIn("8", [w["ws"] for w in after["windows"] if "[new8]" in w["title"]])

    def test_verify_reports_a_misplaced_window_without_moving_it(self):
        state = steady_state()
        state["windows"][1]["ws"] = "Z"
        result, _, moves, _, _ = self.run_script(state, "--verify-only")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(moves, [])
        self.assertIn('"Workspace 2" is on workspace Z; expected 2', result.stderr)


if __name__ == "__main__":
    unittest.main()
