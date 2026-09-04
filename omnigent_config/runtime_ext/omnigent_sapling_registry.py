"""Sapling (``sl``) backend for Omnigent's changed-files panel.

Omnigent resolves a workspace to a change tracker in
``omnigent.runtime.filesystem_registry.create_filesystem_registry``, which
looks for a ``.git`` entry at or above the workspace root and otherwise falls
back to ``AgentEditFilesystemRegistry`` -- a registry that observes nothing but
Omnigent's own ``sys_os_write`` / ``sys_os_edit`` tool calls. Two consequences
for an fbsource checkout:

1. fbsource is Sapling (``.hg``), so the git probe never matches. Nothing in
   upstream Omnigent mentions sapling, hg, eden, or sl.
2. A native harness (claude-native / codex-native) edits through its OWN
   Edit/Write tools, which never reach Omnigent's PUT/PATCH filesystem
   handlers, so the fallback registry records nothing at all. The panel is
   empty rather than merely incomplete.

This module supplies the missing backend plus a workspace-root fix, and is
injected over the upstream factory by the sibling ``sitecustomize`` module. It
lives here rather than in a patched site-packages tree so an
``uv tool install --upgrade omnigent`` cannot silently revert it.

Design notes:

* Sapling needs no ``--untracked-files=all`` equivalent -- ``sl status``
  already expands a brand-new untracked directory into its individual files,
  which is the behavior the git backend has to ask for explicitly.
* No ``core.untrackedCache`` tuning. That is a workaround for git crawling a
  large worktree; EdenFS answers status from its journal (measured 0.34s on a
  clean fbsource), so there is nothing to cache.
* ``sl diff`` has no ``--numstat``. Line counts are derived from a unified
  ``sl diff --git`` instead. Counts are optional in the record contract
  (untracked and binary files carry ``None`` upstream too), so a failure here
  degrades to "no counts" rather than to "no changes".

MULTI-REPO: upstream assumes one workspace == one repo root and only ever
walks UP. A paired workspace (``~/checkoutN`` holding ``fbsource`` and
``configerator`` as siblings) has its repo roots one level DOWN, so even a
correct Sapling backend would never activate. ``create_filesystem_registry``
below also scans one level down and unions the results, which additionally
keeps cross-repo work visible in a single session -- record paths stay
workspace-relative, so they read as ``fbsource/...`` / ``configerator/...``.
"""

from __future__ import annotations

import json
import logging
import subprocess
import time
from pathlib import Path
from typing import Any

# HACK: the upstream module exposes its filters and path helpers only as
# private names. Re-deriving them here would let this backend drift from the
# git one (different ephemeral patterns, different skip dirs, different
# traversal-escape rules), which is worse than the coupling. Every name below
# is read-only; if an upgrade removes one, the sitecustomize guard catches the
# ImportError and leaves upstream behavior untouched.
from omnigent.runtime.filesystem_registry import (
    _SKIP_DIRS,
    AgentEditFilesystemRegistry,
    FilesystemRegistry,
    GitFilesystemRegistry,
    GitStatusUnavailable,
    _git_timeout_seconds,
    _is_ephemeral,
    _normalize_path,
)

_logger = logging.getLogger(__name__)

_SL = "sl"

# `sl status` codes -> the record vocabulary ("created"/"modified"/"deleted").
# Mirrors _parse_git_porcelain_line: untracked and added both read as created,
# and both flavors of gone (R = explicitly removed, ! = missing from disk) read
# as deleted. "C" (clean) and "I" (ignored) are dropped by omission.
_STATUS_TO_OPERATION: dict[str, str] = {
    "M": "modified",
    "A": "created",
    "?": "created",
    "R": "deleted",
    "!": "deleted",
}

# Cap on the unified diff parsed for line counts. A very large working tree
# would otherwise turn a panel refresh into a multi-megabyte read; counts are a
# nicety, so past this we return none and still render the file list.
_MAX_DIFF_BYTES = 8 * 1024 * 1024


def find_repo_root(path: Path) -> tuple[Path, str] | None:
    """Walk up for the nearest repo root, returning ``(root, kind)``.

    :param path: Starting directory.
    :returns: ``(root, "git")`` / ``(root, "sapling")``, or ``None``.
    """
    current = path.resolve()
    while True:
        if (current / ".git").is_dir() or (current / ".git").is_file():
            return current, "git"
        # `.hg` is a symlink into the Eden client dir on an Eden checkout, so
        # test existence rather than is_dir().
        if (current / ".hg").exists():
            return current, "sapling"
        parent = current.parent
        if parent == current:
            return None
        current = parent


class SaplingFilesystemRegistry(FilesystemRegistry):
    """Changed-files registry backed by ``sl status`` / ``sl diff``.

    Mirrors :class:`GitFilesystemRegistry`: it reports the whole working tree
    relative to the current commit, so edits are visible no matter which tool
    made them -- including a native harness writing through its own file tools.

    :param watch_path: Workspace root the panel is rooted at. Record paths are
        relative to this, which is what makes a repo one level down render as
        ``fbsource/...``.
    :param repo_root: The Sapling repository root (the directory holding
        ``.hg``). May be ``watch_path`` itself or a descendant.
    """

    def __init__(self, watch_path: Path, repo_root: Path) -> None:
        super().__init__(watch_path)
        self._repo_root = repo_root

    @property
    def repo_root(self) -> Path:
        """The Sapling repository root this registry reads."""
        return self._repo_root

    def _run_sl(
        self, args: list[str], *, what: str, warn_on_exit: bool = True
    ) -> bytes:
        """Run ``sl`` in the repo root and return stdout.

        Failure modes match the git backend deliberately: a read that could not
        run raises :class:`GitStatusUnavailable` so the UI shows an error state,
        instead of an empty list that looks identical to "no changes".

        :param args: Arguments after ``sl``, e.g. ``["status", "-Tjson"]``.
        :param what: Caller label used in log lines and error text.
        :param warn_on_exit: Log a warning on a non-zero exit. ``False`` for
            callers where a non-zero exit is an expected answer rather than a
            fault -- ``sl cat`` on a path absent from the working parent is
            simply how "this file is new" is reported, and warning on it would
            emit a line per untracked file on every panel refresh.
        :returns: Raw stdout bytes.
        :raises GitStatusUnavailable: On timeout, spawn error, or non-zero exit.
        """
        argv = [_SL, *args, "--pager=never"]
        started = time.monotonic()
        try:
            result = subprocess.run(
                argv,
                cwd=str(self._repo_root),
                capture_output=True,
                timeout=_git_timeout_seconds(),
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            elapsed = time.monotonic() - started
            _logger.warning(
                "SaplingFilesystemRegistry.%s: %r in %s timed out after %.2fs",
                what,
                argv,
                self._repo_root,
                elapsed,
            )
            raise GitStatusUnavailable(
                f"sl {what} timed out after {elapsed:.1f}s"
            ) from exc
        except OSError as exc:
            elapsed = time.monotonic() - started
            _logger.warning(
                "SaplingFilesystemRegistry.%s: %r in %s could not run after %.2fs: %s",
                what,
                argv,
                self._repo_root,
                elapsed,
                exc,
            )
            raise GitStatusUnavailable(f"sl {what} could not run: {exc}") from exc

        if result.returncode != 0:
            stderr = result.stderr.decode("utf-8", errors="replace").strip()
            if warn_on_exit:
                _logger.warning(
                    "SaplingFilesystemRegistry.%s: %r in %s exited %d: %s",
                    what,
                    argv,
                    self._repo_root,
                    result.returncode,
                    stderr,
                )
            raise GitStatusUnavailable(
                f"sl {what} exited {result.returncode}"
                + (f": {stderr}" if stderr else "")
            )
        return result.stdout

    def _repo_to_rel(self, repo_path: str) -> str | None:
        """Convert a repo-root-relative path to a workspace-relative one."""
        try:
            return str((self._repo_root / repo_path).relative_to(self._cwd))
        except ValueError:
            return None

    def _rel_to_repo(self, rel_path: str) -> str | None:
        """Convert a workspace-relative path to a repo-root-relative one."""
        try:
            return (self._cwd / rel_path).relative_to(self._repo_root).as_posix()
        except ValueError:
            return None

    def _make_record(
        self,
        rel_path: str,
        operation: str,
        line_counts: tuple[int | None, int | None] = (None, None),
    ) -> dict[str, Any]:
        """Build a file-record dict for *rel_path*.

        Duplicated from ``GitFilesystemRegistry`` rather than imported: it is a
        method on that class, not a shared helper, and subclassing the git
        backend to borrow it would inherit its git-specific ``start()``.

        :param rel_path: Path relative to the workspace root.
        :param operation: ``"created"``, ``"modified"``, or ``"deleted"``.
        :param line_counts: ``(added, removed)``, each ``None`` when unknown.
        :returns: Record with ``path``, ``status``, ``bytes``, ``modified_at``,
            ``lines_added``, ``lines_removed``.
        """
        bytes_: int | None = None
        modified_at: int | None = None
        if operation != "deleted":
            try:
                st = (self._cwd / rel_path).stat()
                bytes_ = st.st_size
                modified_at = int(st.st_mtime)
            except OSError:
                pass
        added, removed = line_counts
        return {
            "path": rel_path,
            "status": operation,
            "bytes": bytes_,
            "modified_at": modified_at,
            "lines_added": added,
            "lines_removed": removed,
        }

    def _parse_status(self, payload: bytes) -> list[tuple[str, str]]:
        """Parse ``sl status -Tjson`` into ``(repo_path, operation)`` pairs."""
        try:
            entries = json.loads(payload.decode("utf-8", errors="replace") or "[]")
        except (ValueError, TypeError):
            _logger.warning("SaplingFilesystemRegistry: unparseable sl status JSON")
            return []
        if not isinstance(entries, list):
            return []
        pairs: list[tuple[str, str]] = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            repo_path = entry.get("path")
            operation = _STATUS_TO_OPERATION.get(str(entry.get("status")))
            if isinstance(repo_path, str) and repo_path and operation is not None:
                pairs.append((repo_path, operation))
        return pairs

    def list_changed_files(
        self, conversation_id: str, *, limit: int
    ) -> list[dict[str, Any]]:
        """Return every uncommitted change in the working tree, newest first.

        :param conversation_id: Ignored; ``sl status`` always reflects the
            current tree relative to the working parent.
        :param limit: Maximum number of records to return.
        :returns: File-record dicts, newest first.
        """
        pairs = self._parse_status(self._run_sl(["status", "-Tjson"], what="status"))
        counts = self._line_counts()
        records: list[dict[str, Any]] = []
        for repo_path, operation in pairs:
            rel_path = self._repo_to_rel(repo_path)
            if rel_path is None or _is_ephemeral(rel_path):
                continue
            parts = Path(rel_path).parts
            if any(part in _SKIP_DIRS for part in parts):
                continue
            records.append(
                self._make_record(
                    rel_path, operation, counts.get(rel_path, (None, None))
                )
            )
        records.sort(key=lambda r: (r["modified_at"] or 0, r["path"]), reverse=True)
        return records[:limit]

    def get_changed_file(self, session_id: str, path: str) -> dict[str, Any] | None:
        """Return the change record for a single *path*, or ``None``."""
        norm = _normalize_path(path, self._cwd)
        if norm is None or _is_ephemeral(norm):
            return None
        repo_path = self._rel_to_repo(norm)
        if repo_path is None:
            return None
        for found_path, operation in self._parse_status(
            self._run_sl(["status", "-Tjson", "--", repo_path], what="status")
        ):
            if found_path == repo_path:
                return self._make_record(norm, operation)
        return None

    def get_baseline(self, path: str) -> str | None:
        """Return committed content via ``sl cat -r . <path>``.

        :param path: Path relative to the workspace root.
        :returns: Content at the working parent, or ``None`` for a new or
            untracked file (``sl cat`` exits non-zero) or on subprocess failure.
        """
        norm = _normalize_path(path, self._cwd)
        if norm is None:
            return None
        repo_path = self._rel_to_repo(norm)
        if repo_path is None:
            return None
        try:
            payload = self._run_sl(
                ["cat", "-r", ".", repo_path], what="cat", warn_on_exit=False
            )
        except GitStatusUnavailable:
            # A path absent from the working parent is the normal "new file"
            # case, not an error worth surfacing -- match the git backend,
            # which returns None when `git show HEAD:<path>` fails.
            return None
        return payload.decode("utf-8", errors="replace")

    def _line_counts(self) -> dict[str, tuple[int | None, int | None]]:
        """Return per-file ``(added, removed)`` parsed from ``sl diff --git``.

        Sapling has no ``--numstat``, and ``--stat``'s histogram is lossy for
        wide files, so counts come from the unified diff. Any failure yields an
        empty mapping and the records simply carry no counts.
        """
        try:
            payload = self._run_sl(["diff", "--git"], what="diff")
        except GitStatusUnavailable:
            return {}
        if len(payload) > _MAX_DIFF_BYTES:
            _logger.info(
                "SaplingFilesystemRegistry: diff of %d bytes exceeds cap; omitting line counts",
                len(payload),
            )
            return {}

        counts: dict[str, tuple[int | None, int | None]] = {}
        current: str | None = None
        added = removed = 0
        binary = False

        def flush() -> None:
            if current is None:
                return
            rel = self._repo_to_rel(current)
            if rel is not None:
                counts[rel] = (None, None) if binary else (added, removed)

        for raw in payload.decode("utf-8", errors="replace").splitlines():
            if raw.startswith("diff --git "):
                flush()
                added = removed = 0
                binary = False
                # "diff --git a/<path> b/<path>" -- take the destination so a
                # rename is attributed to where the file now lives.
                _, _, rest = raw.partition(" b/")
                current = rest or None
            elif current is None:
                continue
            elif raw.startswith(("GIT binary patch", "Binary file")):
                binary = True
            elif raw.startswith("+") and not raw.startswith("+++"):
                added += 1
            elif raw.startswith("-") and not raw.startswith("---"):
                removed += 1
        flush()
        return counts


class MultiRepoFilesystemRegistry(FilesystemRegistry):
    """Union of several repo-backed registries sharing one workspace root.

    Exists because a paired workspace holds sibling repos rather than being one
    itself. Each child already emits workspace-relative paths, so the union
    needs no prefixing -- only merge, re-sort, and routing by which child owns
    a path.

    :param watch_path: The shared workspace root.
    :param children: Per-repo registries, each rooted at *watch_path*.
    """

    def __init__(self, watch_path: Path, children: list[FilesystemRegistry]) -> None:
        super().__init__(watch_path)
        self._children = children

    @property
    def children(self) -> list[FilesystemRegistry]:
        """The per-repo registries this instance fans out to."""
        return self._children

    def start(self) -> None:
        """Start every child registry. Idempotent."""
        for child in self._children:
            child.start()

    def stop(self) -> None:
        """Stop every child registry. Idempotent."""
        for child in self._children:
            child.stop()

    def _owner(self, rel_path: str) -> FilesystemRegistry | None:
        """Return the child registry whose repo contains *rel_path*."""
        target = (self._cwd / rel_path).resolve()
        for child in self._children:
            root = getattr(child, "repo_root", None) or getattr(
                child, "_git_root", None
            )
            if root is None:
                continue
            try:
                target.relative_to(root)
            except ValueError:
                continue
            return child
        return None

    def list_changed_files(
        self, conversation_id: str, *, limit: int
    ) -> list[dict[str, Any]]:
        """Merge every child's changes, newest first.

        A child that cannot be read is skipped rather than failing the whole
        panel: one unreadable repo in a paired workspace should not hide the
        changes in its sibling.
        """
        records: list[dict[str, Any]] = []
        for child in self._children:
            try:
                records.extend(child.list_changed_files(conversation_id, limit=limit))
            except GitStatusUnavailable as exc:
                _logger.warning("MultiRepoFilesystemRegistry: skipping a repo: %s", exc)
        records.sort(key=lambda r: (r["modified_at"] or 0, r["path"]), reverse=True)
        return records[:limit]

    def get_changed_file(self, session_id: str, path: str) -> dict[str, Any] | None:
        """Route to the child owning *path*."""
        norm = _normalize_path(path, self._cwd)
        if norm is None:
            return None
        child = self._owner(norm)
        return None if child is None else child.get_changed_file(session_id, norm)

    def get_baseline(self, path: str) -> str | None:
        """Route to the child owning *path*."""
        norm = _normalize_path(path, self._cwd)
        if norm is None:
            return None
        child = self._owner(norm)
        return None if child is None else child.get_baseline(norm)

    def record_change(self, path: str, operation: str, session_id: str) -> None:
        """Forward a tool-call edit to the owning child, if any."""
        norm = _normalize_path(path, self._cwd)
        if norm is None:
            return
        child = self._owner(norm)
        if child is not None:
            child.record_change(norm, operation, session_id)

    def unregister_conversation(self, conversation_id: str) -> None:
        """Drop per-session state in every child."""
        for child in self._children:
            child.unregister_conversation(conversation_id)


def _registry_for(watch_path: Path, root: Path, kind: str) -> FilesystemRegistry:
    """Build the registry matching *kind* for one repo root."""
    if kind == "git":
        return GitFilesystemRegistry(watch_path, root)
    return SaplingFilesystemRegistry(watch_path, root)


def _child_repos(watch_path: Path) -> list[tuple[Path, str]]:
    """Return repo roots exactly one level below *watch_path*, sorted by name."""
    found: list[tuple[Path, str]] = []
    try:
        entries = sorted(watch_path.iterdir())
    except OSError:
        return found
    for entry in entries:
        if not entry.is_dir() or entry.name in _SKIP_DIRS or entry.name.startswith("."):
            continue
        if (entry / ".git").is_dir() or (entry / ".git").is_file():
            found.append((entry, "git"))
        elif (entry / ".hg").exists():
            found.append((entry, "sapling"))
    return found


def create_filesystem_registry(watch_path: Path) -> FilesystemRegistry:
    """Drop-in replacement for the upstream factory, with Sapling + multi-repo.

    Resolution order:

    1. A repo root at or above *watch_path* -- git or Sapling. This keeps every
       single-repo workspace (including plain git ones) on exactly the upstream
       behavior.
    2. Otherwise, repo roots one level below. One match behaves like case 1
       rooted at the parent; several are unioned.
    3. Otherwise, upstream's tool-call-only fallback, unchanged.

    :param watch_path: The workspace root to track.
    :returns: A ready-to-use :class:`FilesystemRegistry`.
    """
    resolved = watch_path.resolve()

    found = find_repo_root(resolved)
    if found is not None:
        root, kind = found
        return _registry_for(resolved, root, kind)

    children = _child_repos(resolved)
    if children:
        _logger.info(
            "filesystem registry: %d repo(s) below %s: %s",
            len(children),
            resolved,
            ", ".join(f"{root.name}({kind})" for root, kind in children),
        )
        registries = [_registry_for(resolved, root, kind) for root, kind in children]
        if len(registries) == 1:
            return registries[0]
        return MultiRepoFilesystemRegistry(resolved, registries)

    return AgentEditFilesystemRegistry(resolved)
