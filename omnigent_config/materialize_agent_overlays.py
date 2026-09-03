#!/usr/bin/env python3
"""Materialize packaged Omnigent agents with shared personal MCP tools."""

from __future__ import annotations

import argparse
import shutil
from importlib.resources import as_file, files
from pathlib import Path

import yaml

_PACKAGED_AGENTS = {"polly", "debby"}
_DIFF_WATCH = {
    "type": "mcp",
    "command": "omnigent-diff-watch-mcp",
    "tools": [
        "diff_watch_subscribe",
        "diff_watch_unsubscribe",
        "diff_watch_status",
    ],
}


def materialize(output_root: Path, names: list[str]) -> None:
    """Copy packaged agent bundles and add the shared diff-watch MCP server."""
    output_root.mkdir(parents=True, exist_ok=True)
    examples = files("omnigent").joinpath("resources", "examples")
    for name in names:
        if name not in _PACKAGED_AGENTS:
            raise ValueError(f"unsupported packaged agent: {name}")
        destination = output_root / name
        with as_file(examples.joinpath(name)) as source:
            shutil.copytree(source, destination)
        config_path = destination / "config.yaml"
        config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        tools = config.setdefault("tools", {})
        if not isinstance(tools, dict):
            raise TypeError(f"{name} tools config is not a mapping")
        tools["diff_watch"] = _DIFF_WATCH
        config_path.write_text(
            yaml.safe_dump(config, sort_keys=False, default_flow_style=False),
            encoding="utf-8",
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("output_root", type=Path)
    parser.add_argument("names", nargs="+", choices=sorted(_PACKAGED_AGENTS))
    args = parser.parse_args()
    materialize(args.output_root, args.names)


if __name__ == "__main__":
    main()
