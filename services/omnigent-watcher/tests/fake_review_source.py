#!/usr/bin/env python3
"""Emit one sanitized review-source fixture through a real process boundary."""

from __future__ import annotations

import argparse
import re
from pathlib import Path

_FIXTURE_NAME = re.compile(r"^[a-z][a-z0-9_]*\.json$")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture-dir", type=Path, required=True)
    parser.add_argument("--fixture", required=True)
    args = parser.parse_args()
    if not _FIXTURE_NAME.fullmatch(args.fixture):
        parser.error("fixture must be a simple JSON filename")
    fixture_dir = args.fixture_dir.resolve(strict=True)
    fixture = (fixture_dir / args.fixture).resolve(strict=True)
    if fixture.parent != fixture_dir:
        parser.error("fixture must remain inside fixture directory")
    print(fixture.read_text(encoding="utf-8"), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
