"""Keep Claude Code from rewriting ``${...}`` in claude-native hook commands.

Claude Code 2.1.310 treats ``${...}`` in a hook command as an environment
variable reference and substitutes it before handing the command to
``/bin/sh -c``. Names that are not valid variable names become empty strings
(stderr: ``Invalid environment variable name evaluates to an empty string``);
only braces containing a ``:`` survive, and ``$${...}`` is the escape.
Omnigent's hook commands are inline shell scripts full of parameter
expansions, so they reach ``sh`` mangled:

- The policy guard (``omnigent.native.policy_allow_cache._GUARD_SCRIPT``) on
  ``UserPromptSubmit``, ``PreToolUse`` and ``PostToolUse`` no longer parses
  (``case  in '}'*)``), so every prompt and tool call is blocked.
- The event forwarder's ``sent=${out##*$nl}; out=${out%$nl*}`` becomes
  ``sent=; out=``, so every relay response is silently dropped.

This patch wraps ``bridge.build_hook_settings``, whose only caller,
``augment_claude_args``, writes the result to ``claude-settings.json``. Every
hook command containing ``${`` is written to
``<bridge_dir>/hook-scripts/<sha256[:16]>.sh`` and replaced by
``/bin/sh <script>``, which Claude Code passes through untouched on any
version. Stdin, stdout and the exit status are unchanged.

Installed by ``sitecustomize`` when ``omnigent.harnesses.claude_native.bridge``
is imported. Remove once upstream stops inlining ``${`` in hook commands
(still inlined on omnigent main ``f726587bb``, 2026-11-07). If an upgrade
removes a name this patch relies on, :func:`install` returns ``False`` and
leaves the module untouched.
"""

from __future__ import annotations

import functools
import hashlib
import shlex
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import Any

_SCRIPT_DIR = "hook-scripts"
_PATCHED_MARKER = "_omnigent_claude_hook_braces"
_REQUIRED_NAMES = ("build_hook_settings", "_write_private_file")


def externalize(
    bridge_dir: Path,
    settings: dict[str, Any],
    write_private_file: Callable[[Path, str], None],
) -> None:
    """Move every hook command containing ``${`` into a script file, in place.

    :param bridge_dir: The session's bridge dir, e.g.
        ``Path("/tmp/omnigent-501/claude-native/abc")``.
    :param settings: Claude Code settings as built by ``build_hook_settings``.
    :param write_private_file: Upstream's owner-only atomic writer, which also
        validates that the script dir sits under an allowed bridge root.
    """
    hooks = settings.get("hooks")
    if not isinstance(hooks, dict):
        return
    for groups in hooks.values():
        for group in groups if isinstance(groups, list) else ():
            for hook in group.get("hooks", ()) if isinstance(group, dict) else ():
                command = hook.get("command") if isinstance(hook, dict) else None
                if not isinstance(command, str) or "${" not in command:
                    continue
                digest = hashlib.sha256(command.encode()).hexdigest()[:16]
                script = bridge_dir / _SCRIPT_DIR / f"{digest}.sh"
                write_private_file(script, command)
                hook["command"] = f"/bin/sh {shlex.quote(str(script))}"


def install(bridge: ModuleType) -> bool:
    """Wrap ``bridge.build_hook_settings`` so its hook commands carry no ``${``.

    :param bridge: The freshly imported
        ``omnigent.harnesses.claude_native.bridge`` module.
    :returns: ``True`` if the wrapper is in place. ``False`` if upstream no
        longer has a name the patch depends on; the module is then left
        untouched.
    """
    if any(not hasattr(bridge, name) for name in _REQUIRED_NAMES):
        return False
    original: Callable[..., dict[str, Any]] = bridge.build_hook_settings
    if getattr(original, _PATCHED_MARKER, False):
        return True

    @functools.wraps(original)
    def build_hook_settings(bridge_dir: Path, *args: Any, **kwargs: Any) -> dict[str, Any]:
        settings = original(bridge_dir, *args, **kwargs)
        externalize(Path(bridge_dir), settings, bridge._write_private_file)
        return settings

    setattr(build_hook_settings, _PATCHED_MARKER, True)
    bridge.build_hook_settings = build_hook_settings
    return True
