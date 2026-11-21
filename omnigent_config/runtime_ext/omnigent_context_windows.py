"""Context windows for models Omnigent's lookup does not know.

Omnigent sizes a session's context indicator with
``omnigent.llms.context_window.get_model_context_window``: an env override,
the ``[1m]`` marker, the shared MLflow model catalog, litellm, and finally a
128K default. For the headless ``pi`` harness the server does this itself
(``_context_labels_from_turn_usage`` in ``server/routes/_sessions/orchestration.py``)
instead of asking Pi, and the catalog does not list Gemini 4 yet, so a
``google/gemini-4-neon`` session shows its context against 128,000 while Pi
runs it with a 1,048,576-token window (Pi's own model registration, read with
``pi --mode rpc`` ``set_model`` on 2026-11-21). Only the indicator is wrong:
Pi compacts on its own window, and T3 Code takes the window from
omnigent-opencode's catalog.

This patch wraps ``find_model_context_window``, which
``get_model_context_window`` and the native forwarders call, so that a model
upstream cannot resolve gets its window from ``WINDOWS`` instead of ``None``
(and so instead of the 128K default). It never overrides a window upstream
does resolve, so it goes inert on its own once the catalog lists these models.

Installed by ``sitecustomize`` when ``omnigent.llms.context_window`` is
imported, in the server (``systemd/omnigent-server.service`` puts this
directory on its ``PYTHONPATH``) and in runners. Set
``OMNIGENT_CONTEXT_WINDOWS=0`` to disable it.

REMOVE when upstream resolves these models, which this command shows (``None``
means still needed; it runs without this directory on ``PYTHONPATH``, so the
patch is not applied, and ``tail`` drops litellm's chatter)::

    env -u PYTHONPATH ~/.local/share/uv/tools/omnigent/bin/python -c \\
      'from omnigent.llms.context_window import find_model_context_window as f; \\
       print(f("google/gemini-4-neon"), f("google/gemini-4-carbon"))' | tail -1

That happens when Omnigent's model catalog lists Gemini 4, or when its pi
executor reports the window Pi itself states. Then delete this file, its
entry in ``sitecustomize._PATCHES``, its test, and the ``runtime_ext`` entry
and the three runner-patch ``=0`` lines in ``systemd/omnigent-server.service``.
"""

from __future__ import annotations

import functools
from collections.abc import Callable
from types import ModuleType
from typing import Final

# Model id (without a provider prefix) -> context window in tokens.
WINDOWS: Final[dict[str, int]] = {
    "gemini-4-neon": 1_048_576,
    "gemini-4-carbon": 1_048_576,
}

_PATCHED_MARKER = "_omnigent_context_windows"
_REQUIRED_NAMES = ("find_model_context_window", "get_model_context_window")


def known_window(model: str) -> int | None:
    """``WINDOWS``'s entry for ``model``, e.g. ``"google/gemini-4-neon"`` or ``"gemini-4-neon"``."""
    return WINDOWS.get(model.rsplit("/", 1)[-1].lower())


def install(context_window: ModuleType) -> bool:
    """Wrap ``context_window.find_model_context_window`` to fall back on ``WINDOWS``.

    ``get_model_context_window`` reads the module global at call time, so
    replacing the attribute reaches it as well as direct importers.

    :param context_window: The freshly imported ``omnigent.llms.context_window``.
    :returns: ``True`` if the wrapper is in place; ``False`` if upstream no
        longer has a name this relies on, leaving the module untouched.
    """
    if any(not hasattr(context_window, name) for name in _REQUIRED_NAMES):
        return False
    original: Callable[[str], int | None] = context_window.find_model_context_window
    if getattr(original, _PATCHED_MARKER, False):
        return True

    @functools.wraps(original)
    def find_model_context_window(model: str) -> int | None:
        window = original(model)
        if window is not None:
            return window
        return known_window(model) if isinstance(model, str) else None

    setattr(find_model_context_window, _PATCHED_MARKER, True)
    context_window.find_model_context_window = find_model_context_window
    return True
