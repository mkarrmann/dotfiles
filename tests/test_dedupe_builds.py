"""Checks for bin/dedupe-builds against real files on a btrfs temp dir.

Run with: python3 -m unittest discover -s tests -p test_dedupe_builds.py
"""

import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "bin/dedupe-builds"
MIB = 1 << 20


def is_btrfs(path: str) -> bool:
    out = subprocess.run(["stat", "-f", "-c", "%T", path], capture_output=True, text=True)
    return out.stdout.strip() == "btrfs"


def shared(path: Path) -> bool:
    out = subprocess.run(["filefrag", "-v", str(path)], capture_output=True, text=True, check=True).stdout
    return "shared" in out


@unittest.skipUnless(is_btrfs(tempfile.gettempdir()), "needs btrfs")
class DedupeBuildsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "builds"
        self.state = Path(self.tmp.name) / "state"
        self.root.mkdir()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def write(self, rel: str, data: bytes) -> Path:
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        return p

    def run_tool(self, *extra: str) -> dict:
        out = subprocess.run(
            [str(SCRIPT), str(self.root), "--state-dir", str(self.state), "--min-size", str(MIB), "--min-age", "0", "--json", *extra],
            capture_output=True, text=True, check=True,
        )
        return json.loads(out.stdout)

    def test_identical_files_share_extents_and_keep_contents_and_mtime(self) -> None:
        data = os.urandom(8 * MIB)
        a = self.write("a/plugin.zip", data)
        b = self.write("b/plugin.zip", data)
        c = self.write("m2/plugin-0.1.zip", data)
        mtimes = [p.stat().st_mtime_ns for p in (a, b, c)]

        s = self.run_tool()

        self.assertEqual(s["deduped_files"], 2)
        self.assertEqual(s["bytes_shared"], 16 * MIB)
        for p in (a, b, c):
            self.assertTrue(shared(p), p)
            self.assertEqual(p.read_bytes(), data)
        self.assertEqual([p.stat().st_mtime_ns for p in (a, b, c)], mtimes)

    def test_files_that_differ_stay_unshared(self) -> None:
        a = self.write("a.jar", os.urandom(4 * MIB))
        b = self.write("b.jar", os.urandom(4 * MIB))

        s = self.run_tool()

        self.assertEqual(s["bytes_shared"], 0)
        self.assertFalse(shared(a))
        self.assertFalse(shared(b))

    def test_same_samples_but_different_middle_shares_only_identical_ranges(self) -> None:
        size = 64 * MIB
        base = bytearray(os.urandom(size))
        other = bytearray(base)
        other[20 * MIB] ^= 0xFF
        a = self.write("a.zip", bytes(base))
        b = self.write("b.zip", bytes(other))

        s = self.run_tool()

        self.assertEqual(s["differing_files"], 1)
        self.assertEqual(s["bytes_shared"], size - 16 * MIB)
        self.assertEqual(a.read_bytes(), bytes(base))
        self.assertEqual(b.read_bytes(), bytes(other))

    def test_rerun_skips_files_already_shared_and_picks_up_new_copies(self) -> None:
        data = os.urandom(4 * MIB)
        self.write("a.jar", data)
        self.write("b.jar", data)
        self.run_tool()

        again = self.run_tool()
        self.assertEqual(again["deduped_files"], 0)
        self.assertEqual(again["already_shared"], 1)
        self.assertEqual(again["hashed"], 0)

        c = self.write("c.jar", data)
        third = self.run_tool()
        self.assertEqual(third["deduped_files"], 1)
        self.assertEqual(third["already_shared"], 1)
        self.assertTrue(shared(c))

    def test_rewritten_file_is_shared_again(self) -> None:
        data = os.urandom(4 * MIB)
        self.write("a.jar", data)
        b = self.write("b.jar", data)
        self.run_tool()

        b.unlink()
        b = self.write("b.jar", data)
        self.assertFalse(shared(b))

        s = self.run_tool()
        self.assertEqual(s["deduped_files"], 1)
        self.assertTrue(shared(b))

    def test_dry_run_changes_nothing(self) -> None:
        data = os.urandom(4 * MIB)
        a = self.write("a.jar", data)
        b = self.write("b.jar", data)

        s = self.run_tool("--dry-run")

        self.assertEqual(s["bytes_possible"], 4 * MIB)
        self.assertEqual(s["bytes_shared"], 0)
        self.assertFalse(shared(a))
        self.assertFalse(shared(b))

    def test_recently_modified_and_small_files_are_left_alone(self) -> None:
        data = os.urandom(4 * MIB)
        self.write("a.jar", data)
        b = self.write("b.jar", data)
        small = os.urandom(MIB // 2)
        s1 = self.write("s1.txt", small)
        self.write("s2.txt", small)

        out = subprocess.run(
            [str(SCRIPT), str(self.root), "--state-dir", str(self.state), "--min-size", str(MIB), "--min-age", "3600", "--json"],
            capture_output=True, text=True, check=True,
        )
        s = json.loads(out.stdout)

        self.assertEqual(s["files"], 0)
        self.assertFalse(shared(b))
        self.assertFalse(shared(s1))

    def test_hardlinks_are_one_file(self) -> None:
        data = os.urandom(4 * MIB)
        a = self.write("a.jar", data)
        os.link(a, self.root / "a-link.jar")

        s = self.run_tool()

        self.assertEqual(s["files"], 1)
        self.assertEqual(s["groups"], 0)


if __name__ == "__main__":
    unittest.main()
