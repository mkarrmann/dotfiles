from __future__ import annotations

import argparse
import asyncio

from omnigent_google_chat.app import run
from omnigent_google_chat.config import load_phase_zero_settings, load_settings
from omnigent_google_chat.phase_zero import run_phase_zero


def main() -> None:
    parser = argparse.ArgumentParser(description="Omnigent Google Chat mobile bridge")
    parser.add_argument(
        "command",
        choices=("run", "phase-zero"),
        nargs="?",
        default="run",
    )
    args = parser.parse_args()
    if args.command == "phase-zero":
        asyncio.run(run_phase_zero(load_phase_zero_settings()))
    else:
        asyncio.run(run(load_settings()))


if __name__ == "__main__":
    main()
