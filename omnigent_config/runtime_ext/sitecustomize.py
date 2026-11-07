"""Install dotfiles-owned patches into Omnigent runner processes.

Three patches are installed, each into the module named:

- ``omnigent.runtime.filesystem_registry``: the Sapling changed-files backend
  (``omnigent_sapling_registry``).
- ``omnigent.runner.native.orchestration``: the claude-native ``tmux.json``
  self-heal (``omnigent_tmux_self_heal``).
- ``omnigent.harnesses.claude_native.bridge``: hook commands moved into script
  files so Claude Code cannot rewrite their ``${...}``
  (``omnigent_claude_hook_braces``).

Python imports ``sitecustomize`` automatically at interpreter start when it is
importable, which is the only injection point available here: the runner is
spawned by ``omnigent-host`` with a scrubbed environment and no
``PYTHONPATH``, and its package tree is the managed uv-tool install that
``uv tool install --upgrade omnigent`` replaces wholesale. Patching
site-packages directly would be reverted by the next upgrade; this directory is
source-controlled and merely has to stay on ``PYTHONPATH`` (see
``OMNIGENT_RUNNER_ENV_PASSTHROUGH`` in systemd/omnigent-host.service).

The patch is deferred rather than applied here. Importing ``omnigent`` from
inside ``sitecustomize`` would drag the package in before the interpreter has
finished starting, and would re-enter partially-initialized modules whenever
Omnigent is itself the program being launched. Instead a meta-path finder waits
for each target module to be imported through the normal machinery and patches
it afterwards. The host's runner zygote (``omnigent.runner._zygote``) imports
the runner graph once, at startup, and forks every runner from it. So a change
here reaches runners only after the host, and with it the zygote, restarts.

Fails open in every direction: any error leaves stock Omnigent behavior in
place, because a missing patch is a far better outcome than a runner that will
not boot. Set ``OMNIGENT_SAPLING_REGISTRY=0``, ``OMNIGENT_TMUX_SELF_HEAL=0`` or
``OMNIGENT_CLAUDE_HOOK_BRACES=0`` to disable one patch, or ``OMNIGENT_SAPLING_REGISTRY_DEBUG=1`` to report on
stderr what each patch did and any failure.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Callable
from types import ModuleType

_DEBUG_ENV = "OMNIGENT_SAPLING_REGISTRY_DEBUG"


def _debug(message: str) -> None:
    if os.environ.get(_DEBUG_ENV) == "1":
        print(f"[runtime-ext] {message}", file=sys.stderr)


def _apply_sapling_registry(module: ModuleType) -> None:
    """Swap the factory on a freshly imported filesystem_registry module."""
    from omnigent_sapling_registry import create_filesystem_registry

    module.create_filesystem_registry = create_filesystem_registry  # type: ignore[assignment]
    _debug(f"patched {module.__name__}.create_filesystem_registry")


def _apply_tmux_self_heal(module: ModuleType) -> None:
    """Wrap the native-terminal ensure so it re-advertises a lost tmux.json."""
    from omnigent_tmux_self_heal import install

    if install(module):
        _debug(f"patched {module.__name__}._ensure_native_terminal")
    else:
        _debug(f"{module.__name__} lacks a patched name; tmux self-heal not installed")


def _apply_claude_hook_braces(module: ModuleType) -> None:
    """Wrap build_hook_settings so Claude Code never sees ``${`` in a hook."""
    from omnigent_claude_hook_braces import install

    if install(module):
        _debug(f"patched {module.__name__}.build_hook_settings")
    else:
        _debug(f"{module.__name__} lacks a patched name; hook braces fix not installed")


# target module -> (env var that disables it when "0", patch function)
_PATCHES: dict[str, tuple[str, Callable[[ModuleType], None]]] = {
    "omnigent.runtime.filesystem_registry": (
        "OMNIGENT_SAPLING_REGISTRY",
        _apply_sapling_registry,
    ),
    "omnigent.runner.native.orchestration": (
        "OMNIGENT_TMUX_SELF_HEAL",
        _apply_tmux_self_heal,
    ),
    "omnigent.harnesses.claude_native.bridge": (
        "OMNIGENT_CLAUDE_HOOK_BRACES",
        _apply_claude_hook_braces,
    ),
}


def _apply(module: ModuleType, patch: Callable[[ModuleType], None]) -> None:
    try:
        patch(module)
    except Exception as exc:  # noqa: BLE001 -- must never break a runner
        _debug(f"patch of {module.__name__} failed, leaving upstream behavior: {exc!r}")


def _chain() -> None:
    """Run any other ``sitecustomize`` this one shadowed.

    Python imports exactly one ``sitecustomize``, and ours sits on
    ``PYTHONPATH`` ahead of everything else -- including, transitively, in every
    agent shell (``agent_shell_env`` strips only omnigent's own entry and
    preserves the rest). Nothing on this host ships one today, but a project
    venv or a future fbcode toolchain might, and silently disabling it from a
    dotfiles side-channel would be a genuinely awful bug to track down.

    ``PathFinder`` is used directly because ``find_spec`` would resolve
    ``sitecustomize`` from ``sys.modules`` -- which is this module, mid-import.
    """
    import importlib.util
    from importlib.machinery import PathFinder

    here = os.path.dirname(os.path.abspath(__file__))
    others = [p for p in sys.path if p and os.path.abspath(p) != here]
    spec = PathFinder.find_spec("sitecustomize", others)
    if spec is None or spec.loader is None:
        return
    # Executed for side effects only; sys.modules["sitecustomize"] stays ours.
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    _debug(f"chained to {spec.origin}")


def _install() -> None:
    try:
        _chain()
    except Exception as exc:  # noqa: BLE001 -- chaining is best-effort
        _debug(f"chaining failed: {exc!r}")

    patches: dict[str, Callable[[ModuleType], None]] = {}
    for target, (enable_env, patch) in _PATCHES.items():
        if os.environ.get(enable_env) == "0":
            _debug(f"{target} patch disabled via {enable_env}=0")
        else:
            patches[target] = patch
    if not patches:
        return

    import importlib.util
    from importlib.abc import Loader, MetaPathFinder
    from importlib.machinery import ModuleSpec

    class _PatchingLoader(Loader):
        """Delegate to the real loader, then patch the executed module."""

        def __init__(self, inner: Loader, patch: Callable[[ModuleType], None]) -> None:
            self._inner = inner
            self._patch = patch

        def create_module(self, spec: ModuleSpec) -> ModuleType | None:
            return self._inner.create_module(spec)

        def exec_module(self, module: ModuleType) -> None:
            self._inner.exec_module(module)
            _apply(module, self._patch)

    class _Finder(MetaPathFinder):
        def find_spec(
            self, fullname: str, path: object = None, target: object = None
        ) -> ModuleSpec | None:
            patch = patches.get(fullname)
            if patch is None:
                return None
            # Step aside while resolving, or find_spec would re-enter this
            # finder and recurse until the stack blows.
            sys.meta_path.remove(self)
            try:
                spec = importlib.util.find_spec(fullname)
            except Exception as exc:  # noqa: BLE001
                _debug(f"could not resolve {fullname}: {exc!r}")
                return None
            finally:
                sys.meta_path.insert(0, self)
            if spec is None or spec.loader is None:
                return None
            spec.loader = _PatchingLoader(spec.loader, patch)
            return spec

    for target in [t for t in patches if t in sys.modules]:
        # Already imported (an in-process import ordering we did not expect):
        # patch it directly rather than silently doing nothing.
        _apply(sys.modules[target], patches.pop(target))
    if not patches:
        return

    sys.meta_path.insert(0, _Finder())
    _debug(f"armed for {sorted(patches)}")


try:
    _install()
except Exception as exc:  # noqa: BLE001 -- never block interpreter startup
    _debug(f"install failed: {exc!r}")
