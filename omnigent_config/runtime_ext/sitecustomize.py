"""Install the Sapling changed-files backend into an Omnigent runner.

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
for ``omnigent.runtime.filesystem_registry`` to be imported through the normal
machinery and rewrites its factory afterwards.

Fails open in every direction: any error leaves stock Omnigent behavior in
place, because an empty Files panel is a far better outcome than a runner that
will not boot. Set ``OMNIGENT_SAPLING_REGISTRY=0`` to disable, or
``OMNIGENT_SAPLING_REGISTRY_DEBUG=1`` to surface patch failures on stderr.
"""

from __future__ import annotations

import os
import sys
from types import ModuleType

_TARGET = "omnigent.runtime.filesystem_registry"
_ENABLE_ENV = "OMNIGENT_SAPLING_REGISTRY"
_DEBUG_ENV = "OMNIGENT_SAPLING_REGISTRY_DEBUG"


def _debug(message: str) -> None:
    if os.environ.get(_DEBUG_ENV) == "1":
        print(f"[sapling-registry] {message}", file=sys.stderr)


def _apply(module: ModuleType) -> None:
    """Swap the factory on a freshly imported filesystem_registry module."""
    from omnigent_sapling_registry import create_filesystem_registry

    module.create_filesystem_registry = create_filesystem_registry  # type: ignore[assignment]
    _debug(f"patched {_TARGET}.create_filesystem_registry")


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

    if os.environ.get(_ENABLE_ENV) == "0":
        _debug(f"disabled via {_ENABLE_ENV}=0")
        return

    import importlib.util
    from importlib.abc import Loader, MetaPathFinder
    from importlib.machinery import ModuleSpec

    class _PatchingLoader(Loader):
        """Delegate to the real loader, then patch the executed module."""

        def __init__(self, inner: Loader) -> None:
            self._inner = inner

        def create_module(self, spec: ModuleSpec) -> ModuleType | None:
            return self._inner.create_module(spec)

        def exec_module(self, module: ModuleType) -> None:
            self._inner.exec_module(module)
            try:
                _apply(module)
            except Exception as exc:  # noqa: BLE001 -- must never break a runner
                _debug(f"patch failed, leaving upstream behavior: {exc!r}")

    class _Finder(MetaPathFinder):
        def find_spec(
            self, fullname: str, path: object = None, target: object = None
        ) -> ModuleSpec | None:
            if fullname != _TARGET:
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
            spec.loader = _PatchingLoader(spec.loader)
            return spec

    if _TARGET in sys.modules:
        # Already imported (an in-process import ordering we did not expect):
        # patch it directly rather than silently doing nothing.
        try:
            _apply(sys.modules[_TARGET])
        except Exception as exc:  # noqa: BLE001
            _debug(f"late patch failed: {exc!r}")
        return

    sys.meta_path.insert(0, _Finder())
    _debug("armed")


try:
    _install()
except Exception as exc:  # noqa: BLE001 -- never block interpreter startup
    _debug(f"install failed: {exc!r}")
