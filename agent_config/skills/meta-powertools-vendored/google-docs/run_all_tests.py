#!/usr/bin/env python3
# (c) Meta Platforms, Inc. and affiliates. Confidential and proprietary.
from __future__ import annotations

"""
Run all Google API integration tests from a sandbox environment.

Usage:
    python3 run_all_tests.py
    python3 run_all_tests.py --sandbox-host interngraph.12345.od.facebook.com
"""

import argparse
import os
import subprocess
import sys
from pathlib import Path


def get_sandbox_host() -> str | None:
    """Get the sandbox host from ONDEMAND_HOSTNAME environment variable."""
    hostname = os.environ.get("ONDEMAND_HOSTNAME")
    if not hostname:
        return None
    # Replace fbinfra.net with facebook.com
    hostname = hostname.replace("fbinfra.net", "facebook.com")
    return f"interngraph.{hostname}"


def run_test(test_dir: Path, sandbox_host: str) -> bool:
    """Run a single test file and return True if it passed."""
    test_file = test_dir / "tests.py"
    if not test_file.exists():
        print(f"SKIP: {test_file} not found")
        return True

    print(f"\n{'=' * 60}")
    print(f"Running: {test_dir.name}")
    print(f"{'=' * 60}")

    result = subprocess.run(
        ["python3", str(test_file), "--sandbox-host", sandbox_host],
        cwd=str(test_dir),
    )
    return result.returncode == 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Run all Google API integration tests")
    parser.add_argument(
        "--sandbox-host",
        help="Sandbox host (defaults to ONDEMAND_HOSTNAME with fbinfra.net -> facebook.com)",
    )
    args = parser.parse_args()

    sandbox_host = args.sandbox_host or get_sandbox_host()
    if not sandbox_host:
        print("ERROR: No sandbox host provided and ONDEMAND_HOSTNAME not set")
        print(
            "Usage: python3 run_all_tests.py --sandbox-host interngraph.12345.od.facebook.com"
        )
        return 1

    print(f"Using sandbox host: {sandbox_host}")

    base_dir = Path(__file__).parent
    skills_dir = base_dir.parent
    test_dirs = [
        base_dir,  # google-docs (current directory)
        skills_dir / "google-sheets",
        skills_dir / "google-slides-presentation",
    ]

    results = {}
    for test_dir in test_dirs:
        results[test_dir.name] = run_test(test_dir, sandbox_host)

    print(f"\n{'=' * 60}")
    print("SUMMARY")
    print(f"{'=' * 60}")
    all_passed = True
    for name, passed in results.items():
        status = "PASSED" if passed else "FAILED"
        print(f"  {name}: {status}")
        if not passed:
            all_passed = False

    print(f"{'=' * 60}")
    if all_passed:
        print("ALL TESTS PASSED")
        return 0
    else:
        print("SOME TESTS FAILED")
        return 1


if __name__ == "__main__":
    sys.exit(main())
