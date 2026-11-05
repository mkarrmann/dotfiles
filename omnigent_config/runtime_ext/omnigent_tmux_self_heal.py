"""Re-advertise a live Claude terminal's tmux target when ``tmux.json`` is lost.

A claude-native session delivers every injected message (web-UI input,
sub-agent wake notices) by reading ``<bridge_dir>/tmux.json`` and running
``tmux send-keys`` against the socket it names. Only the runner writes that
file, and only once: when it launches the terminal
(``orchestration._publish_tmux_target_for_bridge``). When a terminal already
exists, ``orchestration._ensure_native_terminal`` returns that terminal without
touching the bridge dir. So if ``tmux.json`` disappears while the pane is
alive, the session stays broken until its runner dies. Every delivery then
polls for 30 s and fails with ``TmuxSessionNotAdvertised``
(``bridge._wait_for_tmux_info``).

The file does disappear. On this devserver something outside Omnigent deletes
every regular file under ``/tmp``, keeping directories and sockets. This
happened on 2026-11-02 at 22:05:18 and on 2026-11-03 at 17:58:56. It removes
``tmux.json``, ``bridge.json`` and ``owner.pid`` from every bridge dir under
``/tmp/omnigent-<uid>/claude-native/``, but the tmux servers survive.

This patch wraps ``_ensure_native_terminal``. The server calls it before it
forwards each turn into a claude-native session. After the call returns for the
``claude`` terminal, the patch compares ``tmux.json`` with the running
instance in the runner's terminal registry. That registry is the source of
truth. If the file is missing or names a different socket or target, the patch
rewrites it through upstream's own ``_publish_tmux_target_for_bridge``. It does
this while holding the same per-session lock that launches hold. When the file
is already correct, the patch changes nothing.

Installed by ``sitecustomize`` when ``omnigent.runner.native.orchestration`` is
imported. If an upstream upgrade removes or renames any private name this patch
relies on, :func:`install` returns ``False`` and leaves the module untouched.
The runner then keeps upstream behaviour.
"""

from __future__ import annotations

import asyncio
import functools
import json
import logging
from collections.abc import Awaitable, Callable, MutableMapping
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from omnigent.runner.native.orchestration import NativeLaunchContext

_logger = logging.getLogger(__name__)

_CLAUDE_TERMINAL = "claude"
_SESSION_KEY = "main"
_TMUX_FILE = "tmux.json"
_PATCHED_MARKER = "_omnigent_tmux_self_heal"
_REQUIRED_NAMES = (
    "_ensure_native_terminal",
    "_publish_tmux_target_for_bridge",
    "_claude_native_bridge_id_for_session",
)

_EnsureFn = Callable[..., Awaitable[Any]]


def _advertised_target(bridge_dir: Path) -> tuple[str, str] | None:
    """Return ``(socket_path, tmux_target)`` from ``tmux.json``, or ``None``."""
    try:
        payload = json.loads((bridge_dir / _TMUX_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    socket_path = payload.get("socket_path")
    tmux_target = payload.get("tmux_target")
    if isinstance(socket_path, str) and isinstance(tmux_target, str):
        return socket_path, tmux_target
    return None


async def _heal(
    orchestration: ModuleType,
    ctx: NativeLaunchContext,
    ensure_locks: MutableMapping[str, asyncio.Lock],
) -> None:
    """Rewrite ``tmux.json`` if it does not match the live Claude terminal."""
    if ctx.server_client is None:
        return
    terminal_registry = ctx.resource_registry.terminal_registry
    if terminal_registry is None:
        return
    from omnigent.harnesses.claude_native.bridge import bridge_dir_for_bridge_id

    bridge_id = await orchestration._claude_native_bridge_id_for_session(
        server_client=ctx.server_client, session_id=ctx.session_id
    )
    bridge_dir = bridge_dir_for_bridge_id(bridge_id)
    async with ensure_locks.setdefault(ctx.session_id, asyncio.Lock()):
        # A labels lookup failure falls back to bridge_id == session_id. Only
        # repair a dir that already exists, so a wrong guess cannot create a
        # stray one (it would carry no owner.pid and never be pruned).
        if not bridge_dir.is_dir():
            return
        instance = terminal_registry.get(ctx.session_id, _CLAUDE_TERMINAL, _SESSION_KEY)
        if instance is None or not instance.running:
            return
        live = (str(instance.socket_path), str(instance.tmux_target))
        advertised = _advertised_target(bridge_dir)
        if advertised == live:
            return
        orchestration._publish_tmux_target_for_bridge(
            resource_registry=ctx.resource_registry,
            session_id=ctx.session_id,
            bridge_id=bridge_id,
            terminal_name=_CLAUDE_TERMINAL,
            session_key=_SESSION_KEY,
        )
    _logger.warning(
        "tmux self-heal: re-advertised Claude tmux target session=%s bridge_dir=%s "
        "previous=%s live=%s",
        ctx.session_id,
        bridge_dir,
        advertised,
        live,
        extra={"session_id": ctx.session_id},
    )


def install(orchestration: ModuleType) -> bool:
    """Wrap ``orchestration._ensure_native_terminal`` with the tmux self-heal.

    :param orchestration: The freshly imported
        ``omnigent.runner.native.orchestration`` module.
    :returns: ``True`` if the wrapper is in place. ``False`` if upstream no
        longer has a name the patch depends on; the module is then left
        untouched.
    """
    if any(not hasattr(orchestration, name) for name in _REQUIRED_NAMES):
        return False
    original: _EnsureFn = orchestration._ensure_native_terminal
    if getattr(original, _PATCHED_MARKER, False):
        return True

    @functools.wraps(original)
    async def _ensure_native_terminal(
        terminal_name: str,
        ctx: NativeLaunchContext,
        *,
        ensure_locks: MutableMapping[str, asyncio.Lock],
        **kwargs: Any,
    ) -> Any:
        response = await original(terminal_name, ctx, ensure_locks=ensure_locks, **kwargs)
        if (
            terminal_name == _CLAUDE_TERMINAL
            and response is not None
            and getattr(response, "status_code", 200) < 400
        ):
            try:
                await _heal(orchestration, ctx, ensure_locks)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 -- the ensure already succeeded
                _logger.warning(
                    "tmux self-heal failed; leaving tmux.json as is session=%s",
                    ctx.session_id,
                    exc_info=True,
                    extra={"session_id": ctx.session_id},
                )
        return response

    setattr(_ensure_native_terminal, _PATCHED_MARKER, True)
    orchestration._ensure_native_terminal = _ensure_native_terminal
    return True
