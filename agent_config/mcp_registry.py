"""Shared MCP registry loader and per-harness serializers.

Canonical MCP definitions live in ``agent_config/plugins/custom-mcps/mcps/*.json``.
Both ``agent_config/sync-mcps`` (native CLI configs) and
``omnigent_config/materialize_agent_overlays.py`` (Omnigent agent bundles)
read through this module so MCP definitions have one parser and one filter.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
from typing import Any, Mapping

RETIRED_MCPS: frozenset[str] = frozenset({"diff_watch"})

_PORTABLE_BIN_PREFIXES: tuple[str, ...] = ("~/bin/", "~/dotfiles/bin/")


@dataclass(frozen=True)
class McpServerSpec:
    """One managed MCP server definition from ``mcps/*.json``."""

    name: str
    raw_command: str
    command: str
    args: tuple[str, ...] = ()
    env: tuple[tuple[str, str], ...] = ()
    description: str | None = None
    profiles: frozenset[str] = frozenset({"work"})
    agents: frozenset[str] | None = None
    transport: str = "stdio"

    @property
    def env_dict(self) -> dict[str, str]:
        return dict(self.env)

    @property
    def portable_command(self) -> str:
        """Return a host-independent command name for cross-host Omnigent bundles.

        Bundles are registered on the Linux hub and executed on any connected
        host (including macOS where ``$HOME`` differs), so ``~/bin/<name>`` and
        ``~/dotfiles/bin/<name>`` collapse to ``<name>`` and resolve via PATH.
        """
        for prefix in _PORTABLE_BIN_PREFIXES:
            if self.raw_command.startswith(prefix):
                candidate = self.raw_command[len(prefix):]
                if candidate and "/" not in candidate:
                    return candidate
        return self.raw_command

    def applies_to_profile(self, profile: str) -> bool:
        return profile in self.profiles

    def applies_to_agent(self, agent: str) -> bool:
        return self.agents is None or agent in self.agents

    def to_claude_dict(self) -> dict[str, Any]:
        entry: dict[str, Any] = {"type": self.transport, "command": self.command}
        if self.args:
            entry["args"] = list(self.args)
        if self.env:
            entry["env"] = self.env_dict
        return entry

    def to_codex_dict(self) -> dict[str, Any]:
        entry: dict[str, Any] = {"command": self.command}
        if self.args:
            entry["args"] = list(self.args)
        if self.env:
            entry["env"] = self.env_dict
        return entry

    def to_metacode_dict(self) -> dict[str, Any]:
        entry: dict[str, Any] = {
            "type": "local",
            "command": [self.command, *self.args],
        }
        if self.env:
            entry["environment"] = self.env_dict
        return entry

    def to_pi_dict(self, existing: Mapping[str, Any] | None = None) -> dict[str, Any]:
        """Serialize for Pi ``mcp.json``, preserving user exposure/enabled overrides."""
        entry: dict[str, Any] = {"command": self.command}
        if self.args:
            entry["args"] = list(self.args)
        if self.env:
            entry["env"] = self.env_dict
        if self.description:
            entry["description"] = self.description
        if isinstance(existing, Mapping):
            for key in ("enabled", "exposure", "toolExposure", "timeout"):
                if key in existing:
                    entry[key] = existing[key]
        return entry

    def to_muse_dict(self, existing: Mapping[str, Any] | None = None) -> dict[str, Any]:
        """Serialize for Muse ``settings.json`` ``mcpServers``, preserving enabled state."""
        entry: dict[str, Any] = {"command": self.command}
        if self.args:
            entry["args"] = list(self.args)
        if self.env:
            entry["env"] = self.env_dict
        if isinstance(existing, Mapping):
            for key in ("enabled", "mode", "framing", "startup_timeout_sec", "tool_timeout_sec"):
                if key in existing:
                    entry[key] = existing[key]
        return entry

    def to_omnigent_yaml(self, *, tools: list[str] | None = None) -> dict[str, Any]:
        """Serialize as an inline ``tools.<name>`` entry for Omnigent ``config.yaml``."""
        entry: dict[str, Any] = {
            "type": "mcp",
            "command": self.portable_command,
        }
        if self.args:
            entry["args"] = list(self.args)
        if self.env:
            entry["env"] = self.env_dict
        if tools is not None:
            entry["tools"] = list(tools)
        if self.description:
            entry["description"] = self.description
        return entry


def load_mcps(mcp_dir: Path) -> dict[str, McpServerSpec]:
    """Load all canonical MCP specs from ``mcp_dir/*.json``."""
    out: dict[str, McpServerSpec] = {}
    for path in sorted(mcp_dir.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        agents_raw = data.get("agents")
        agents = frozenset(str(a) for a in agents_raw) if agents_raw is not None else None
        profiles = frozenset(str(p) for p in data.get("profiles", ["work"]))
        file_description = data.get("description")
        for name, raw in data.get("mcpServers", {}).items():
            raw_command = str(raw["command"])
            expanded_command = os.path.expanduser(raw_command)
            raw_args = raw.get("args") or []
            args = tuple(str(a) for a in raw_args)
            # Drop env entries left as unresolved ${VAR} refs: no agent expands
            # them uniformly, and child processes inherit the spawning environment.
            env = tuple(
                sorted(
                    (str(k), str(v))
                    for k, v in (raw.get("env") or {}).items()
                    if not (isinstance(v, str) and v.startswith("${") and v.endswith("}"))
                )
            )
            description = raw.get("description") or file_description
            out[str(name)] = McpServerSpec(
                name=str(name),
                raw_command=raw_command,
                command=expanded_command,
                args=args,
                env=env,
                description=str(description) if description else None,
                profiles=profiles,
                agents=agents,
                transport=str(raw.get("type", "stdio")),
            )
    return out


def for_profile(
    mcps: Mapping[str, McpServerSpec], profile: str
) -> dict[str, McpServerSpec]:
    return {name: spec for name, spec in mcps.items() if spec.applies_to_profile(profile)}


def for_agent(
    mcps: Mapping[str, McpServerSpec], agent: str
) -> dict[str, McpServerSpec]:
    return {name: spec for name, spec in mcps.items() if spec.applies_to_agent(agent)}


def resolve_pi_agent_dir(
    home: Path | None = None,
    env: Mapping[str, str] | None = None,
) -> Path:
    """Resolve the canonical Pi agent directory, ignoring ephemeral Omnigent homes."""
    resolved_home = home or Path.home()
    source_env = os.environ if env is None else env
    configured_raw = source_env.get("PI_CODING_AGENT_DIR")
    if not configured_raw:
        return resolved_home / ".pi" / "agent"
    configured = Path(os.path.expanduser(configured_raw))
    tmpdir = Path(source_env.get("TMPDIR") or "/tmp")
    ephemeral_prefixes = (
        resolved_home / ".omnigent" / "pi-native",
        resolved_home / ".pi",
        Path("/tmp"),
        tmpdir,
    )
    if configured.name.startswith("omnigent_pi_") and any(
        configured.parent == prefix for prefix in ephemeral_prefixes
    ):
        return resolved_home / ".pi" / "agent"
    if str(configured).startswith(str(resolved_home / ".omnigent" / "pi-native") + os.sep):
        return resolved_home / ".pi" / "agent"
    return configured


def resolve_muse_settings_path(
    home: Path | None = None,
    env: Mapping[str, str] | None = None,
) -> Path:
    resolved_home = home or Path.home()
    source_env = os.environ if env is None else env
    xdg_config = Path(source_env.get("XDG_CONFIG_HOME") or resolved_home / ".config")
    return xdg_config / "muse" / "settings.json"
