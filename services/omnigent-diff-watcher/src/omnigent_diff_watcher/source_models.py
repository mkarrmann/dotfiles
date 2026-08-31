"""Typed, privacy-safe contract between review sources and the watcher."""

from __future__ import annotations

import hashlib
from enum import StrEnum
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

SCHEMA_VERSION = 2
DIFF_ID_PATTERN = r"^D[1-9][0-9]*$"
FINGERPRINT_PATTERN = r"^sha256:[0-9a-f]{64}$"


def fingerprint(value: str) -> str:
    """Hash source content into the change-detection form used by every event."""
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class DiffLifecycle(StrEnum):
    ACTIVE = "active"
    COMMITTED = "committed"
    ABANDONED = "abandoned"
    REVERTED = "reverted"
    MISSING = "missing"

    @property
    def terminal(self) -> bool:
        return self is not DiffLifecycle.ACTIVE


class CIAggregateState(StrEnum):
    UNKNOWN = "unknown"
    PENDING = "pending"
    PASSED = "passed"
    FAILING = "failing"
    SKIPPED = "skipped"


class SourceErrorCategory(StrEnum):
    AUTH = "auth"
    TIMEOUT = "timeout"
    RATE_LIMIT = "rate_limit"
    UNAVAILABLE = "unavailable"
    MALFORMED = "malformed"
    MISSING = "missing"


class ReviewSourceError(RuntimeError):
    """Redacted top-level source error safe for scheduler logs.

    Part of the source contract rather than of any one implementation, so the
    watcher can handle a source that cannot be read without depending on a
    concrete source module.
    """

    def __init__(self, category: SourceErrorCategory) -> None:
        super().__init__(f"review source failed ({category.value})")
        self.category = category


class SourceFailure(_StrictModel):
    category: SourceErrorCategory
    retryable: bool
    summary: str = Field(min_length=1, max_length=160)


class ReviewComment(_StrictModel):
    external_id: str = Field(min_length=1, max_length=128)
    version_id: str = Field(min_length=1, max_length=128)
    updated_at: AwareDatetime
    content_fingerprint: str = Field(pattern=FINGERPRINT_PATTERN)


class CIFailure(_StrictModel):
    external_id: str = Field(min_length=1, max_length=256)
    fingerprint: str = Field(pattern=FINGERPRINT_PATTERN)


class AIReviewFinding(_StrictModel):
    """One unresolved finding from an automated reviewer.

    Distinct from ``ReviewComment`` because automated reviewers are filtered
    out of the human comment stream, and distinct from ``CIFailure`` because
    they are reported at warning severity and so never reach a failing CI
    aggregate.
    """

    external_id: str = Field(min_length=1, max_length=256)
    fingerprint: str = Field(pattern=FINGERPRINT_PATTERN)


class CommentsSnapshot(_StrictModel):
    status: Literal["ok", "error"]
    cursor: str | None = Field(default=None, max_length=512)
    items: tuple[ReviewComment, ...] = ()
    error: SourceFailure | None = None

    @model_validator(mode="after")
    def validate_status(self) -> CommentsSnapshot:
        if self.status == "ok":
            if not self.cursor:
                raise ValueError("successful comments snapshot requires a cursor")
            if self.error is not None:
                raise ValueError("successful comments snapshot cannot include an error")
        else:
            if self.cursor is not None or self.items:
                raise ValueError("failed comments snapshot cannot advance state")
            if self.error is None:
                raise ValueError("failed comments snapshot requires an error")
        ids = [item.external_id for item in self.items]
        if len(ids) != len(set(ids)):
            raise ValueError("comment external IDs must be unique")
        return self


class AIReviewSnapshot(_StrictModel):
    status: Literal["ok", "error"]
    cursor: str | None = Field(default=None, max_length=512)
    items: tuple[AIReviewFinding, ...] = ()
    error: SourceFailure | None = None

    @model_validator(mode="after")
    def validate_status(self) -> AIReviewSnapshot:
        if self.status == "ok":
            if not self.cursor:
                raise ValueError("successful AI review snapshot requires a cursor")
            if self.error is not None:
                raise ValueError("successful AI review snapshot cannot include an error")
        else:
            if self.cursor is not None or self.items:
                raise ValueError("failed AI review snapshot cannot advance state")
            if self.error is None:
                raise ValueError("failed AI review snapshot requires an error")
        ids = [item.external_id for item in self.items]
        if len(ids) != len(set(ids)):
            raise ValueError("AI review external IDs must be unique")
        return self


class CISnapshot(_StrictModel):
    status: Literal["ok", "error"]
    cursor: str | None = Field(default=None, max_length=512)
    aggregate: CIAggregateState = CIAggregateState.UNKNOWN
    failures: tuple[CIFailure, ...] = ()
    error: SourceFailure | None = None

    @property
    def green(self) -> bool:
        """True once the run finished for this version with nothing failing.

        Deliberately narrower than "finished": a run that settles red already
        reports itself through ``failures``, so treating that as a completion
        event too would wake a session twice for one outcome. ``SKIPPED`` is
        excluded because it marks a diff whose lifecycle ended rather than a
        run that reached a verdict.
        """
        return self.status == "ok" and self.aggregate is CIAggregateState.PASSED

    @model_validator(mode="after")
    def validate_status(self) -> CISnapshot:
        if self.status == "ok":
            if not self.cursor:
                raise ValueError("successful CI snapshot requires a cursor")
            if self.error is not None:
                raise ValueError("successful CI snapshot cannot include an error")
            if self.aggregate is CIAggregateState.FAILING and not self.failures:
                raise ValueError("failing CI snapshot requires stable failure identities")
            # Failures are reported as soon as they are known rather than held
            # until the whole run settles, so a still-pending aggregate may
            # carry them. A run that reached a verdict without failing cannot.
            if (
                self.aggregate
                in {
                    CIAggregateState.PASSED,
                    CIAggregateState.SKIPPED,
                    CIAggregateState.UNKNOWN,
                }
                and self.failures
            ):
                raise ValueError("only failing or pending CI snapshots may contain failures")
        else:
            if self.cursor is not None or self.failures:
                raise ValueError("failed CI snapshot cannot advance state")
            if self.aggregate is not CIAggregateState.UNKNOWN:
                raise ValueError("failed CI snapshot must use unknown aggregate state")
            if self.error is None:
                raise ValueError("failed CI snapshot requires an error")
        ids = [failure.external_id for failure in self.failures]
        if len(ids) != len(set(ids)):
            raise ValueError("CI failure external IDs must be unique")
        return self


class SourceCursor(_StrictModel):
    latest_version_id: str | None = Field(default=None, max_length=128)
    comments: str | None = Field(default=None, max_length=512)
    ci: str | None = Field(default=None, max_length=512)
    ai_reviews: str | None = Field(default=None, max_length=512)


class DiffSnapshot(_StrictModel):
    schema_version: Literal[2]
    diff_id: str = Field(pattern=DIFF_ID_PATTERN)
    lifecycle: DiffLifecycle
    author_id: str | None = Field(default=None, min_length=1, max_length=128)
    latest_version_id: str | None = Field(default=None, min_length=1, max_length=128)
    last_activity_at: AwareDatetime
    observed_at: AwareDatetime
    comments: CommentsSnapshot
    ci: CISnapshot
    ai_reviews: AIReviewSnapshot

    @model_validator(mode="after")
    def validate_lifecycle(self) -> DiffSnapshot:
        if self.lifecycle is DiffLifecycle.MISSING:
            if self.author_id is not None or self.latest_version_id is not None:
                raise ValueError("missing diff cannot include author or version identity")
        elif self.author_id is None or self.latest_version_id is None:
            raise ValueError("known diff requires author and latest version identity")
        if self.last_activity_at > self.observed_at:
            raise ValueError("last activity cannot be later than observation time")
        return self

    def cursor(self, previous: SourceCursor | None = None) -> SourceCursor:
        """Advance only source components that succeeded in this snapshot."""
        previous = previous or SourceCursor()
        return SourceCursor(
            latest_version_id=self.latest_version_id or previous.latest_version_id,
            comments=(self.comments.cursor if self.comments.status == "ok" else previous.comments),
            ci=self.ci.cursor if self.ci.status == "ok" else previous.ci,
            ai_reviews=(
                self.ai_reviews.cursor if self.ai_reviews.status == "ok" else previous.ai_reviews
            ),
        )
