#!/usr/bin/env python3
"""Exercise the launch/readiness phase with isolated paths and mocked commands."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import textwrap
import time
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "bin-macos/orchest-open-workspaces"


def health(ready=True, **overrides):
    body = dict(service="orchest", protocolVersion=1, shell="desktop", pid=123, ready=ready)
    body.update(overrides)
    return dict(status=200 if ready else 503, body=json.dumps(body))


class StartupTest(unittest.TestCase):
    def run_startup(self, responses, *args, launch_exit=None):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "bin").mkdir()
            (root / "apps/desktop/out/main").mkdir(parents=True)
            (root / "apps/desktop/out/main/index.js").touch()
            # Run the real launch phase, stopping before any workspace mutation.
            script = SCRIPT.read_text().split("workspace_json='[]'", 1)[0]
            script = script.replace('${HOME}/dev/orchest', '${ORCHEST_TEST_DIR}')
            script = script.replace('deadline=$((SECONDS + 20))', 'deadline=$((SECONDS + 1))')
            script += '\ntouch "$ORCHEST_TEST_DIR/ready"\n'
            script += 'if [[ -n "$launched_pid" ]]; then wait "$launched_pid"; fi\n'
            (root / "launch").write_text(script)
            (root / "responses").write_text(json.dumps(responses))
            mocks = {
                "curl": '''
                    import json, os, sys, time
                    from pathlib import Path
                    root = Path(os.environ["ORCHEST_TEST_DIR"])
                    args = sys.argv[1:]
                    assert args[args.index("--noproxy") + 1] == "*"
                    assert args[args.index("--connect-timeout") + 1] == "1"
                    assert args[args.index("--max-time") + 1] == "2"
                    assert args[-1] == "http://127.0.0.1:3100/health"
                    counter = root / "count"
                    count = int(counter.read_text()) if counter.exists() else 0
                    counter.write_text(str(count + 1))
                    responses = json.loads((root / "responses").read_text())
                    result = responses[min(count, len(responses) - 1)]
                    if count and os.environ.get("ORCHEST_TEST_LAUNCH_EXIT"):
                        time.sleep(0.1)
                    print(result.get("body", ""))
                    print(result.get("status", "000"))
                    sys.exit(result.get("exit", 0))
                ''',
                "pnpm": '''
                    import os, sys, time
                    from pathlib import Path
                    root = Path(os.environ["ORCHEST_TEST_DIR"])
                    (root / "launched").write_text(" ".join(sys.argv[1:]))
                    try:
                        if os.environ.get("ORCHEST_TEST_LAUNCH_EXIT"):
                            print("fixture startup failure", flush=True)
                            sys.exit(int(os.environ["ORCHEST_TEST_LAUNCH_EXIT"]))
                        deadline = time.monotonic() + 5
                        while not (root / "ready").exists() and not (root / "done").exists():
                            if time.monotonic() > deadline:
                                sys.exit(99)
                            time.sleep(0.01)
                    finally:
                        (root / "child-finished").touch()
                ''',
                "aerospace": 'raise AssertionError("Workspace operations must not run")',
            }
            for name, body in mocks.items():
                executable = root / "bin" / name
                executable.write_text("#!/usr/bin/env python3\n" + textwrap.dedent(body).lstrip())
                executable.chmod(0o755)
            environment = dict(os.environ, ORCHEST_TEST_DIR=str(root),
                               ORCHEST_LOG=str(root / "app.log"),
                               PATH=f"{root / 'bin'}:{os.environ['PATH']}")
            if launch_exit is not None:
                environment["ORCHEST_TEST_LAUNCH_EXIT"] = str(launch_exit)
            try:
                result = subprocess.run(["bash", str(root / "launch"), *args],
                                        env=environment, capture_output=True, text=True, timeout=8)
                launched = (root / "launched").read_text() if (root / "launched").exists() else None
                calls = int((root / "count").read_text())
                log = (root / "app.log").read_text() if (root / "app.log").exists() else ""
                return result, launched, calls, log
            finally:
                (root / "done").touch()
                # Only the mock created by this test can be running; let it exit itself.
                for _ in range(100):
                    if not (root / "launched").exists() or (root / "child-finished").exists():
                        break
                    time.sleep(0.01)

    def test_ready_instance_is_reused(self):
        result, launched, calls, _ = self.run_startup([health()])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIsNone(launched)
        self.assertEqual(calls, 1)

    def test_initializing_instance_is_awaited(self):
        result, launched, calls, _ = self.run_startup([health(False), health()])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIsNone(launched)
        self.assertEqual(calls, 2)

    def test_absent_instance_launches_both_modes(self):
        for mode, command in [("--dev", "dev"), ("--prod", "exec electron .")]:
            with self.subTest(mode=mode):
                result, launched, _, _ = self.run_startup([dict(exit=7), health()], mode)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(launched, command)

    def test_absent_verify_never_launches(self):
        result, launched, _, _ = self.run_startup([dict(exit=7)], "--verify-only")
        self.assertEqual(result.returncode, 1)
        self.assertIsNone(launched)
        self.assertIn("is not running", result.stderr)

    def test_incompatible_listeners_never_launch(self):
        responses = [dict(status=200, body="Orchest CLI Server"),
                     dict(status=404, body="Not Found"),
                     health(service="other"), health(protocolVersion=2), health(shell="web"),
                     health(pid=0), health(pid=1.5), health(pid="123"),
                     dict(status=200, body=health(False)["body"]),
                     dict(status=503, body=health()["body"]),
                     dict(status=200, body=health()["body"] + '\n' + health()["body"])]
        for response in responses:
            with self.subTest(response=response):
                result, launched, _, _ = self.run_startup([response])
                self.assertEqual(result.returncode, 1)
                self.assertIsNone(launched)
                self.assertIn("rebuild and explicitly restart", result.stderr)

    def test_probe_timeout_does_not_launch(self):
        result, launched, _, _ = self.run_startup([dict(exit=28)])
        self.assertEqual(result.returncode, 1)
        self.assertIsNone(launched)
        self.assertIn("curl exit 28", result.stderr)

    def test_early_exit_reports_status_and_log(self):
        result, launched, calls, log = self.run_startup([dict(exit=7)], launch_exit=23)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(launched, "dev")
        self.assertLessEqual(calls, 3)
        self.assertIn("status 23", result.stderr)
        self.assertIn("app.log", result.stderr)
        self.assertIn("fixture startup failure", log)

    def test_existing_instance_readiness_timeout(self):
        result, launched, _, _ = self.run_startup([health(False)])
        self.assertEqual(result.returncode, 1)
        self.assertIsNone(launched)
        self.assertIn("did not become ready", result.stderr)


if __name__ == "__main__":
    unittest.main()
