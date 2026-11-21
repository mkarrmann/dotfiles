#!/usr/bin/env python3
"""Materialize Omnigent agent bundles with shared personal overlays.

Applies one canonical set of personal defaults across both source-controlled
agent specs (``omnigent_config/agents/<name>``) and packaged upstream agents
(``polly``, ``debby``):

- ``watch`` MCP server (portable ``omnigent-watch-mcp`` command + tools).
- ``blast_radius`` guardrail for any spec that declares ``os_env:``.
- Managed MCP servers from ``agent_config/plugins/custom-mcps/mcps/*.json`` for
  headless harnesses that disable native CLI MCP loading (e.g. ``harness: pi``).
"""

from __future__ import annotations

import argparse
import copy
from importlib.resources import as_file, files
from pathlib import Path
import shutil
import sys
from typing import Any, Mapping

import yaml

_DOTFILES_ROOT = Path(__file__).resolve().parents[1]
_AGENT_CONFIG_DIR = _DOTFILES_ROOT / "agent_config"
if str(_AGENT_CONFIG_DIR) not in sys.path:
    sys.path.insert(0, str(_AGENT_CONFIG_DIR))

from mcp_registry import McpServerSpec, for_agent, for_profile, load_mcps

_PACKAGED_AGENTS: frozenset[str] = frozenset({"polly", "debby"})

# Headless harnesses that disable the underlying CLI's native MCP tools
# (e.g. PiExecutor launches `pi --no-tools --tools <bridged>,read`), mapped to
# their agent key in mcps/*.json `agents` allowlists.
_BRIDGED_MCP_HARNESSES: dict[str, str] = {
    "pi": "pi",
}

_WATCH_MCP: dict[str, Any] = {
    "type": "mcp",
    "command": "omnigent-watch-mcp",
    "tools": [
        "diff_subscribe",
        "diff_unsubscribe",
        "diff_status",
        "subscribe",
        "unsubscribe",
        "status",
    ],
}

_BLAST_RADIUS_POLICY: dict[str, Any] = {
    "type": "function",
    "on": ["tool_call"],
    "function": {
        "path": "omnigent.inner.nessie.policies.blast_radius",
        "arguments": {"gate_pushes": False},
    },
}


def _harness_name(config: Mapping[str, Any]) -> str | None:
    executor = config.get("executor")
    if not isinstance(executor, Mapping):
        return None
    inner = executor.get("config")
    if not isinstance(inner, Mapping):
        return None
    harness = inner.get("harness")
    return str(harness) if isinstance(harness, str) and harness else None


def apply_shared_overlays(
    config: Mapping[str, Any],
    *,
    profile: str,
    mcps: Mapping[str, McpServerSpec],
) -> dict[str, Any]:
    """Return a copy of *config* with shared personal MCPs and guardrails applied."""
    result: dict[str, Any] = copy.deepcopy(dict(config))

    tools = result.setdefault("tools", {})
    if not isinstance(tools, dict):
        raise TypeError(f"{result.get('name', '<agent>')} tools config is not a mapping")
    tools.setdefault("watch", copy.deepcopy(_WATCH_MCP))

    if "os_env" in result:
        guardrails = result.setdefault("guardrails", {})
        if not isinstance(guardrails, dict):
            raise TypeError(f"{result.get('name', '<agent>')} guardrails config is not a mapping")
        policies = guardrails.setdefault("policies", {})
        if not isinstance(policies, dict):
            raise TypeError(f"{result.get('name', '<agent>')} guardrails.policies is not a mapping")
        policies.setdefault("blast_radius", copy.deepcopy(_BLAST_RADIUS_POLICY))

    harness = _harness_name(result)
    agent_key = _BRIDGED_MCP_HARNESSES.get(harness or "")
    if agent_key is not None:
        selected = for_agent(for_profile(mcps, profile), agent_key)
        for name, spec in selected.items():
            if name in tools:
                continue
            tools[name] = spec.to_omnigent_yaml()

    return result


def _overlay_bundle_config(
    bundle_dir: Path,
    *,
    profile: str,
    mcps: Mapping[str, McpServerSpec],
) -> None:
    config_path = bundle_dir / "config.yaml"
    config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    if not isinstance(config, dict):
        raise TypeError(f"{config_path} root is not a mapping")
    overlaid = apply_shared_overlays(config, profile=profile, mcps=mcps)
    config_path.write_text(
        yaml.safe_dump(overlaid, sort_keys=False, default_flow_style=False),
        encoding="utf-8",
    )


def materialize(
    output_root: Path,
    packaged_names: list[str] | None = None,
    *,
    tracked_dir: Path | None = None,
    tracked_names: list[str] | None = None,
    profile: str = "work",
    mcp_dir: Path | None = None,
) -> None:
    """Copy tracked and packaged agent bundles into *output_root* with shared overlays."""
    output_root.mkdir(parents=True, exist_ok=True)
    resolved_mcp_dir = mcp_dir or (_AGENT_CONFIG_DIR / "plugins" / "custom-mcps" / "mcps")
    mcps = load_mcps(resolved_mcp_dir) if resolved_mcp_dir.is_dir() else {}

    for name in tracked_names or []:
        if tracked_dir is None:
            raise ValueError("--tracked-dir is required when --tracked is given")
        source = tracked_dir / name
        if not source.is_dir():
            raise FileNotFoundError(f"tracked agent spec not found: {source}")
        destination = output_root / name
        shutil.copytree(source, destination)
        _overlay_bundle_config(destination, profile=profile, mcps=mcps)

    if packaged_names:
        examples = files("omnigent").joinpath("resources", "examples")
        for name in packaged_names:
            if name not in _PACKAGED_AGENTS:
                raise ValueError(f"unsupported packaged agent: {name}")
            destination = output_root / name
            with as_file(examples.joinpath(name)) as source:
                shutil.copytree(source, destination)
            _overlay_bundle_config(destination, profile=profile, mcps=mcps)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("output_root", type=Path)
    parser.add_argument(
        "names",
        nargs="*",
        choices=sorted(_PACKAGED_AGENTS),
        help="Packaged upstream agent names (legacy positional form).",
    )
    parser.add_argument(
        "--packaged",
        nargs="*",
        default=[],
        choices=sorted(_PACKAGED_AGENTS),
        help="Packaged upstream agent names to overlay.",
    )
    parser.add_argument(
        "--tracked-dir",
        type=Path,
        default=None,
        help="Directory holding source-controlled agent specs.",
    )
    parser.add_argument(
        "--tracked",
        nargs="*",
        default=[],
        help="Source-controlled agent spec names under --tracked-dir.",
    )
    parser.add_argument(
        "--profile",
        default="work",
        choices=("work", "desktop"),
        help="Dotfiles profile controlling which managed MCPs apply.",
    )
    parser.add_argument(
        "--mcp-dir",
        type=Path,
        default=None,
        help="Override canonical mcps/*.json directory (for tests).",
    )
    args = parser.parse_args()
    packaged = list(dict.fromkeys([*args.names, *args.packaged]))
    if not packaged and not args.tracked:
        parser.error("at least one packaged or --tracked agent name is required")
    materialize(
        args.output_root,
        packaged,
        tracked_dir=args.tracked_dir,
        tracked_names=args.tracked,
        profile=args.profile,
        mcp_dir=args.mcp_dir,
    )


if __name__ == "__main__":
    main()
