"""Isolated checks for bin/prune-stale-builds: a fake home, build root and /proc.

Run with: python3 -m unittest discover -s tests -p test_prune_stale_builds.py
"""

from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "bin/prune-stale-builds"
LAUNCHER = "org.codehaus.plexus.classworlds.launcher.Launcher"

POM = """<?xml version="1.0" encoding="UTF-8"?>
<project xmlns="http://maven.apache.org/POM/4.0.0">
  <parent><groupId>com.facebook.airlift</groupId><version>9.9</version></parent>
  <artifactId>presto-root</artifactId>
  <version>{version}</version>
</project>
"""


class PruneStaleBuildsTest(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        base = Path(tmp.name)
        self.home = base / "home"
        self.builds = base / "builds"
        self.proc = base / "proc"
        self.proc.mkdir(parents=True)
        self.builds.mkdir()

    def workspace(self, name: str, version: str = "0.301-SNAPSHOT") -> None:
        github = self.home / name / "fbsource/fbcode/github"
        for repo in ("presto-trunk", "presto-facebook-trunk"):
            (github / repo).mkdir(parents=True)
            (github / repo / "pom.xml").write_text(POM.format(version=version))

    def touch(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x" * 10)
        return path

    def maven_process(self, pid: int, *args: str) -> None:
        (self.proc / str(pid)).mkdir()
        argv = ["java", "-classpath", "plexus.jar", LAUNCHER, "install", *args]
        (self.proc / str(pid) / "cmdline").write_bytes(b"\0".join(a.encode() for a in argv) + b"\0")

    def run_script(self, *args: str) -> str:
        result = subprocess.run(
            [str(SCRIPT), "--home", str(self.home), "--build-root", str(self.builds), "--proc-root", str(self.proc), *args],
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout

    def test_deletes_only_older_snapshots_in_repo(self) -> None:
        self.workspace("checkout2")
        repo = self.builds / "m2-repo-checkout2/com/facebook/presto"
        old = self.touch(repo / "presto-main/0.299-SNAPSHOT/presto-main-0.299-SNAPSHOT.jar").parent
        nested_old = self.touch(repo / "router/presto-router-plugin/0.300-SNAPSHOT/a.jar").parent
        current = self.touch(repo / "presto-main/0.301-SNAPSHOT/presto-main-0.301-SNAPSHOT.jar").parent
        newer = self.touch(repo / "presto-main/0.302-SNAPSHOT/presto-main-0.302-SNAPSHOT.jar").parent
        release = self.touch(repo / "presto-main/0.174/presto-main-0.174.jar").parent
        other_group = self.touch(self.builds / "m2-repo-checkout2/com/facebook/airlift/x/0.200-SNAPSHOT/x.jar").parent

        self.run_script("--yes")

        self.assertFalse(old.exists())
        self.assertFalse(nested_old.exists())
        for kept in (current, newer, release, other_group):
            self.assertTrue(kept.exists(), kept)

    def test_deletes_older_files_and_dirs_in_build_roots(self) -> None:
        self.workspace("checkout1")
        module = self.builds / "presto-facebook-trunk/com.facebook.presto:presto-tpch"
        old_jar = self.touch(module / "presto-tpch-0.300-SNAPSHOT.jar")
        old_plugin = self.touch(module / "presto-tpch-0.299-SNAPSHOT/aopalliance-1.0.jar").parent
        current_jar = self.touch(module / "presto-tpch-0.301-SNAPSHOT.jar")
        classes = self.touch(module / "classes/Foo.class")

        self.run_script("--yes")

        self.assertFalse(old_jar.exists())
        self.assertFalse(old_plugin.exists())
        self.assertTrue(current_jar.exists())
        self.assertTrue(classes.exists())

    def test_checkout1_uses_home_m2(self) -> None:
        self.workspace("checkout1")
        old = self.touch(self.home / ".m2/repository/com/facebook/presto/presto-spi/0.298-SNAPSHOT/a.jar").parent

        self.run_script("--yes")

        self.assertFalse(old.exists())

    def test_reports_without_deleting_by_default(self) -> None:
        self.workspace("checkout2")
        old = self.touch(self.builds / "m2-repo-checkout2/com/facebook/presto/presto-spi/0.298-SNAPSHOT/a.jar")

        out = self.run_script()

        self.assertTrue(old.exists())
        self.assertIn("rerun with --yes", out)

    def test_skips_workspace_with_running_build(self) -> None:
        self.workspace("checkout2")
        self.workspace("checkout3")
        busy = self.touch(self.builds / "m2-repo-checkout3/com/facebook/presto/presto-spi/0.298-SNAPSHOT/a.jar")
        idle = self.touch(self.builds / "m2-repo-checkout2/com/facebook/presto/presto-spi/0.298-SNAPSHOT/a.jar")
        self.maven_process(4242, f"-Dmaven.repo.local={self.builds}/m2-repo-checkout3")

        out = self.run_script("--yes")

        self.assertTrue(busy.exists())
        self.assertFalse(idle.exists())
        self.assertIn("skip checkout3", out)

    def test_build_without_repo_flag_marks_checkout1_busy(self) -> None:
        self.workspace("checkout1")
        busy = self.touch(self.home / ".m2/repository/com/facebook/presto/presto-spi/0.298-SNAPSHOT/a.jar")
        self.maven_process(4243, "-DskipTests")

        self.run_script("--yes")

        self.assertTrue(busy.exists())

    def test_lists_orphans_without_deleting(self) -> None:
        self.workspace("checkout1")
        orphan = self.touch(self.builds / "m2-repo-gone/com/facebook/presto/x/0.200-SNAPSHOT/a.jar")
        unrelated = self.touch(self.builds / "presto-local-dev-checkout9/run/x")

        out = self.run_script("--yes")

        self.assertTrue(orphan.exists())
        self.assertIn(str(self.builds / "m2-repo-gone"), out)
        self.assertNotIn("presto-local-dev", out)
        self.assertTrue(unrelated.exists())

    def test_skips_workspace_without_readable_version(self) -> None:
        (self.home / "checkout2/fbsource").mkdir(parents=True)
        kept = self.touch(self.builds / "m2-repo-checkout2/com/facebook/presto/presto-spi/0.298-SNAPSHOT/a.jar")

        out = self.run_script("--yes")

        self.assertTrue(kept.exists())
        self.assertIn("skip", out)


if __name__ == "__main__":
    unittest.main()
