"""Isolated checks for bin/mm-checkout: a stub agentcloudctl and workspace table, no network.

Run with: python3 -m unittest discover -s tests -p test_mm_checkout.py
"""

import json
import os
from pathlib import Path
import subprocess
import tempfile
import textwrap
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "bin/mm-checkout"
CCO = "devvm20365.cco0.facebook.com"
FTW = "devvm36111.ftw0.facebook.com"
NEW_ID = "11111111-2222-3333-4444-555555555555"

TABLE = {
    "workspaces": [
        {"workspace": "1", "kind": "local", "cwd": "/home/u"},
        {"workspace": "2", "kind": "devserver", "host": CCO, "checkout": "/home/u/checkout1", "nvsSession": "CCO-checkout1"},
        {"workspace": "4", "kind": "devserver", "host": CCO, "checkout": "/home/u/checkout2", "nvsSession": "CCO-checkout2"},
        {"workspace": "5", "kind": "devserver", "host": FTW, "checkout": "/home/u/checkout2", "nvsSession": "FTW-checkout2"},
    ]
}


def row(sid, host, workspace, running=False, awaiting=False, root=None):
    return {
        "session_id": sid,
        "title": f"thread {sid[:4]}",
        "harness": "claude_code",
        "workspace": workspace,
        "attached_nodes": [host],
        "running": running,
        "awaiting_person": awaiting,
        "tree": {"root": root or sid},
    }


ROWS = [
    row("aaaa0000-0000-0000-0000-000000000000", CCO, "/home/u/checkout1/fbsource", running=True),
    row("bbbb0000-0000-0000-0000-000000000000", FTW, "/home/u/checkout2/fbsource"),
    # Same folder name on the other host: must not count toward CCO-checkout2.
    row("cccc0000-0000-0000-0000-000000000000", FTW, "/home/u/checkout2/fbsource/fbcode"),
    # A child session: its tree root is another session, so it is not listed.
    row("dddd0000-0000-0000-0000-000000000000", CCO, "/home/u/checkout2/fbsource", running=True, root="aaaa0000-0000-0000-0000-000000000000"),
    # Archived, and a child marked only by `parent`: neither is listed.
    {**row("eeee0000-0000-0000-0000-000000000000", CCO, "/home/u/checkout2/fbsource", running=True), "archived_at_unix_ms": 1},
    {**row("ffff0000-0000-0000-0000-000000000000", CCO, "/home/u/checkout2/fbsource", running=True), "parent": "aaaa0000-0000-0000-0000-000000000000"},
]

STUB = textwrap.dedent(
    """\
    #!/usr/bin/env python3
    import json, os, sys
    args = sys.argv[1:]
    with open(os.environ["STUB_LOG"], "a") as f:
        f.write(json.dumps(args) + "\\n")
    print("note: dialing prod", file=sys.stderr)
    if args[0] == "list":
        print(open(os.environ["STUB_ROWS"]).read())
    elif args[0] == "create":
        print(os.environ["STUB_NEW_ID"])
    elif args[:2] == ["node", "exec"]:
        host = args[args.index("--node") + 1]
        script = args[-1]
        dirty = json.loads(os.environ["STUB_DIRTY"]).get(host + ":" + script.split()[1].split("/")[3], 0)
        print("abc123 some commit")
        print("__DIRTY__", dirty)
    """
)


class MmCheckoutTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        ctl = self.tmp / "agentcloudctl"
        ctl.write_text(STUB)
        ctl.chmod(0o755)
        table = self.tmp / "workspaces"
        table.write_text(f"#!/bin/sh\ncat <<'EOF'\n{json.dumps(TABLE)}\nEOF\n")
        table.chmod(0o755)
        (self.tmp / "rows.json").write_text(json.dumps(ROWS))
        self.log = self.tmp / "calls.jsonl"
        self.env = {
            **os.environ,
            "AGENTCLOUDCTL": str(ctl),
            "MM_CHECKOUT_WORKSPACES": str(table),
            "STUB_LOG": str(self.log),
            "STUB_ROWS": str(self.tmp / "rows.json"),
            "STUB_NEW_ID": NEW_ID,
            "STUB_DIRTY": json.dumps({}),
        }

    def run_script(self, *args, dirty=None, check=True):
        env = dict(self.env)
        if dirty is not None:
            env["STUB_DIRTY"] = json.dumps(dirty)
        return subprocess.run([str(SCRIPT), *args], env=env, capture_output=True, text=True, check=check)

    def calls(self):
        return [json.loads(l) for l in self.log.read_text().splitlines()] if self.log.exists() else []

    def test_list_attributes_threads_by_host_and_folder(self):
        out = json.loads(self.run_script("list", "--json", "--no-vcs").stdout)
        by_place = {r["place"]: r for r in out}
        self.assertEqual([t["session_id"][:4] for t in by_place["CCO-checkout1"]["threads"]], ["aaaa"])
        self.assertTrue(by_place["CCO-checkout1"]["busy"])
        self.assertEqual(by_place["CCO-checkout2"]["threads"], [])
        self.assertEqual(sorted(t["session_id"][:4] for t in by_place["FTW-checkout2"]["threads"]), ["bbbb", "cccc"])
        self.assertFalse(by_place["FTW-checkout2"]["busy"])

    def test_new_starts_the_thread_in_the_named_checkout(self):
        out = json.loads(self.run_script("new", "ftw:2", "--title", "T", "--prompt", "do it", "--json").stdout)
        self.assertEqual(out["session_id"], NEW_ID)
        self.assertEqual(out["url"], f"https://agentcloud.internalmeta.com/{NEW_ID}")
        create = next(c for c in self.calls() if c[0] == "create")
        opts = dict(zip(create[1::2], create[2::2]))
        self.assertEqual(opts["--node-id"], FTW)
        self.assertEqual(opts["--workspace"], "/home/u/checkout2/fbsource")
        self.assertEqual(opts["--startup-folder"], "project")
        self.assertEqual(opts["--prompt"], "do it")

    def test_harness_accepts_the_session_row_spelling(self):
        self.run_script("new", "cco:2", "--harness", "claude_code")
        create = next(c for c in self.calls() if c[0] == "create")
        self.assertEqual(create[create.index("--harness") + 1], "claude-code")

    def test_place_names_accept_nvs_and_short_forms(self):
        for name in ("CCO-checkout2", "cco:2", "cco2"):
            out = json.loads(self.run_script("new", name, "--json").stdout)
            self.assertEqual(out["place"], "CCO-checkout2")

    def test_auto_skips_busy_and_dirty_checkouts(self):
        # checkout1 on CCO is busy (a running thread); CCO checkout2 has uncommitted changes.
        out = json.loads(self.run_script("new", "auto", "--json", dirty={f"{CCO}:checkout2": 3}).stdout)
        self.assertEqual(out["place"], "FTW-checkout2")

    def test_auto_with_no_free_checkout_fails_and_creates_nothing(self):
        proc = self.run_script("new", "auto", "--host", "cco", dirty={f"{CCO}:checkout2": 1}, check=False)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("no free checkout on cco", proc.stderr)
        self.assertFalse(any(c[0] == "create" for c in self.calls()))

    def test_unknown_place_is_refused(self):
        proc = self.run_script("new", "cco:9", check=False)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("unknown place", proc.stderr)


if __name__ == "__main__":
    unittest.main()
