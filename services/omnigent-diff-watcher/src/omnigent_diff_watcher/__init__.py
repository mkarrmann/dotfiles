"""Standalone sidecar for opt-in Phabricator diff notifications."""

from .source_models import DiffSnapshot, SourceCursor

__all__ = ["DiffSnapshot", "SourceCursor"]
