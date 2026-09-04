"""Integration tests for the Sapling changed-files backend.

Drives real ``sl`` and ``git`` repositories rather than mocking the
subprocess layer: the whole point of this backend is that it reads a
working tree the way the VCS actually reports it, so a stubbed
``sl status`` would assert only that the stub matches itself.

Run: ~/.local/share/uv/tools/omnigent/bin/python test_omnigent_sapling_registry.py
"""

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

if shutil.which("sl") is None:
    print("SKIP: sl not on PATH")
    sys.exit(0)
from omnigent_sapling_registry import (
    create_filesystem_registry,
)


def sh(cmd, cwd):
    return subprocess.run(
        cmd,
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "HGUSER": "t <t@t>"},
    )


def make_sl_repo(root: Path):
    root.mkdir(parents=True, exist_ok=True)
    sh(["sl", "init", "--git", "."], root)
    (root / "sub").mkdir(exist_ok=True)
    (root / "tracked.txt").write_text("one\ntwo\n")
    (root / "sub" / "nested.txt").write_text("x\n")
    (root / "gone.txt").write_text("del\n")
    sh(["sl", "add", "tracked.txt", "sub/nested.txt", "gone.txt"], root)
    sh(["sl", "commit", "-m", "base"], root)
    # dirty it
    (root / "tracked.txt").write_text("one\ntwo\nthree\nfour\n")  # +2
    (root / "added.txt").write_text("new\n")
    sh(["sl", "add", "added.txt"], root)
    (root / "gone.txt").unlink()
    (root / "untracked.txt").write_text("junk\n")
    (root / "brandnew").mkdir(exist_ok=True)
    (root / "brandnew" / "deep.txt").write_text("d\n")
    (root / "editor.txt~").write_text("backup\n")  # ephemeral
    (root / "node_modules").mkdir(exist_ok=True)
    (root / "node_modules" / "junk.js").write_text("x\n")  # skip dir


fails = []


def check(label, got, want):
    ok = got == want
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}")
    if not ok:
        print(f"         got={got!r}\n         want={want!r}")
        fails.append(label)


tmp = Path(tempfile.mkdtemp(prefix="slreg-"))
try:
    # ---------- 1. single Sapling repo, watch_path == repo root ----------
    print("\n1. single Sapling repo (watch_path == repo root)")
    repo = tmp / "solo"
    make_sl_repo(repo)
    reg = create_filesystem_registry(repo)
    check(
        "picks SaplingFilesystemRegistry",
        type(reg).__name__,
        "SaplingFilesystemRegistry",
    )
    recs = {r["path"]: r for r in reg.list_changed_files("c", limit=100)}
    check("modified detected", recs["tracked.txt"]["status"], "modified")
    check("added detected", recs["added.txt"]["status"], "created")
    check("deleted detected", recs["gone.txt"]["status"], "deleted")
    check("untracked -> created", recs["untracked.txt"]["status"], "created")
    check("new dir expanded to file", recs["brandnew/deep.txt"]["status"], "created")
    check("ephemeral ~ filtered", "editor.txt~" in recs, False)
    check("node_modules filtered", "node_modules/junk.js" in recs, False)
    check(
        "line counts (+2/-0)",
        (recs["tracked.txt"]["lines_added"], recs["tracked.txt"]["lines_removed"]),
        (2, 0),
    )
    check("deleted has no bytes", recs["gone.txt"]["bytes"], None)
    check(
        "record keys",
        sorted(recs["added.txt"]),
        ["bytes", "lines_added", "lines_removed", "modified_at", "path", "status"],
    )
    check("limit respected", len(reg.list_changed_files("c", limit=2)), 2)

    # ---------- 2. single-file + baseline ----------
    print("\n2. get_changed_file / get_baseline")
    check(
        "single file lookup",
        reg.get_changed_file("s", "tracked.txt")["status"],
        "modified",
    )
    check("clean file -> None", reg.get_changed_file("s", "sub/nested.txt"), None)
    check("baseline is pre-edit content", reg.get_baseline("tracked.txt"), "one\ntwo\n")
    check("baseline of new file -> None", reg.get_baseline("added.txt"), None)
    check("escape attempt -> None", reg.get_changed_file("s", "../../etc/passwd"), None)

    # ---------- 3. multi-repo workspace (the ~/checkoutN shape) ----------
    print("\n3. multi-repo workspace (sibling repos one level down)")
    ws = tmp / "workspace"
    ws.mkdir()
    make_sl_repo(ws / "fbsource")
    make_sl_repo(ws / "configerator")
    (ws / "home").mkdir()  # non-repo sibling, must be ignored
    mreg = create_filesystem_registry(ws)
    check(
        "picks MultiRepoFilesystemRegistry",
        type(mreg).__name__,
        "MultiRepoFilesystemRegistry",
    )
    check("child count (non-repo sibling ignored)", len(mreg.children), 2)
    paths = {r["path"] for r in mreg.list_changed_files("c", limit=100)}
    check("fbsource path prefixed", "fbsource/tracked.txt" in paths, True)
    check("configerator path prefixed", "configerator/added.txt" in paths, True)
    check("union size (5 visible changes x 2 repos)", len(paths), 10)
    check(
        "routed single lookup",
        mreg.get_changed_file("s", "fbsource/tracked.txt")["status"],
        "modified",
    )
    check(
        "routed baseline", mreg.get_baseline("configerator/tracked.txt"), "one\ntwo\n"
    )
    check("unknown path -> None", mreg.get_changed_file("s", "home/nope.txt"), None)

    # ---------- 4. git workspace must be untouched ----------
    print("\n4. git repo still uses upstream backend")
    g = tmp / "gitrepo"
    g.mkdir()
    for c in (
        ["git", "init", "-q"],
        ["git", "config", "user.email", "t@t"],
        ["git", "config", "user.name", "t"],
    ):
        sh(c, g)
    (g / "a.txt").write_text("1\n")
    sh(["git", "add", "-A"], g)
    sh(["git", "commit", "-qm", "b"], g)
    (g / "a.txt").write_text("1\n2\n")
    check(
        "git -> GitFilesystemRegistry",
        type(create_filesystem_registry(g)).__name__,
        "GitFilesystemRegistry",
    )
    check(
        "git changes still listed",
        create_filesystem_registry(g).list_changed_files("c", limit=10)[0]["path"],
        "a.txt",
    )

    # ---------- 5. no repo at all -> upstream fallback ----------
    print("\n5. non-repo workspace falls back to upstream")
    plain = tmp / "plain"
    plain.mkdir()
    check(
        "fallback preserved",
        type(create_filesystem_registry(plain)).__name__,
        "AgentEditFilesystemRegistry",
    )
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print(
    f"\n{'ALL PASSED' if not fails else str(len(fails)) + ' FAILURE(S): ' + ', '.join(fails)}"
)
sys.exit(1 if fails else 0)
