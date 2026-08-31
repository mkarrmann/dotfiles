"""Read-only Phabricator review and aggregate CI source adapter."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from datetime import UTC, datetime

from .source_command import (
    SourceCommandError,
    SourceCommandErrorCategory,
    run_json_command,
)
from .source_models import (
    AIReviewFinding,
    AIReviewSnapshot,
    CIAggregateState,
    CIFailure,
    CISnapshot,
    CommentsSnapshot,
    DiffLifecycle,
    DiffSnapshot,
    ReviewComment,
    ReviewSourceError,
    SourceCursor,
    SourceErrorCategory,
    SourceFailure,
)
from .source_models import (
    fingerprint as _fingerprint,
)

__all__ = ["PhabricatorReviewSource", "ReviewSourceError"]

_DIFF_ID = re.compile(r"^D[1-9][0-9]*$")
_SAFE_ENV_NAMES = (
    "PATH",
    "HOME",
    "USER",
    "USERNAME",
    "LOGNAME",
    "TMPDIR",
    "LANG",
    "LC_ALL",
    "HTTPS_PROXY",
    "HTTP_PROXY",
    "NO_PROXY",
    "SSL_CERT_FILE",
    "KRB5CCNAME",
    "X509_USER_PROXY",
)
# `reviews` is grouped server-side because automated reviewers report at
# WARNING, where they would otherwise sit behind hundreds of coverage warnings
# in a flat, paginated signal list.
_CI_QUERY = """query ($version_id: ID!) {
  signalview_signals(phabricator_version_fbid: $version_id) {
    all: signals(filters: {}) { count }
    failed: signals(filters: {status: [FAILED]}) {
      count
      nodes { name status slp_functional_type }
    }
    pending: signals(filters: {status: [PENDING]}) { count }
    reviews: signals_by_functional_type_or_provider(statuses: [FAILED, WARNING]) {
      nodes {
        group_data {
          ... on XFBSignalviewFunctionalTypeGroupData { functional_type }
        }
        signals(first: 50) {
          nodes {
            ... on SignalviewSignal { name status slp_functional_type }
          }
        }
      }
    }
  }
}"""

_REVIEW_INSIGHTS_GROUP = "REVIEW_INSIGHTS"
_ARCTIC_ACTIONABLE_INSIGHT = "spotlight"
_ARCTIC_UNRESOLVED = "unresolved"

JsonRunner = Callable[[Sequence[str]], Awaitable[object]]


class PhabricatorReviewSource:
    """Normalize fixed, read-only jf/meta command results into DiffSnapshot."""

    def __init__(
        self,
        *,
        env: Mapping[str, str] | None = None,
        runner: JsonRunner | None = None,
    ) -> None:
        self._env = dict(env) if env is not None else bounded_source_environment()
        self._runner = runner

    async def _run(self, argv: Sequence[str]) -> object:
        if self._runner is not None:
            return await self._runner(tuple(argv))
        return await run_json_command(tuple(argv), env=self._env)

    async def snapshot(
        self,
        diff_id: str,
        previous: SourceCursor | None,
    ) -> DiffSnapshot:
        del previous
        if not _DIFF_ID.fullmatch(diff_id):
            raise ValueError("diff ID must match D<number>")
        observed_at = datetime.now(UTC)
        try:
            properties_raw = await self._run(("jf", "diff-properties", diff_id))
            properties = _object(properties_raw, "diff properties")
            metadata = _parse_metadata(diff_id, properties, observed_at)
        except SourceCommandError as exc:
            if exc.category is SourceCommandErrorCategory.EXIT:
                raise ReviewSourceError(SourceErrorCategory.UNAVAILABLE) from exc
            raise ReviewSourceError(_source_error_category(exc)) from exc
        except (KeyError, TypeError, ValueError) as exc:
            raise ReviewSourceError(SourceErrorCategory.MALFORMED) from exc

        if metadata.lifecycle is DiffLifecycle.MISSING:
            failure = SourceFailure(
                category=SourceErrorCategory.MISSING,
                retryable=False,
                summary="diff was not found",
            )
            return DiffSnapshot(
                schema_version=2,
                diff_id=diff_id,
                lifecycle=DiffLifecycle.MISSING,
                last_activity_at=metadata.last_activity_at,
                observed_at=observed_at,
                comments=CommentsSnapshot(status="error", error=failure),
                ci=CISnapshot(status="error", error=failure),
                ai_reviews=AIReviewSnapshot(status="error", error=failure),
            )

        if metadata.lifecycle.terminal:
            return DiffSnapshot(
                schema_version=2,
                diff_id=diff_id,
                lifecycle=metadata.lifecycle,
                author_id=metadata.author_id,
                latest_version_id=metadata.latest_version_id,
                last_activity_at=metadata.last_activity_at,
                observed_at=observed_at,
                comments=CommentsSnapshot(
                    status="ok",
                    cursor=_fingerprint("terminal-comments"),
                ),
                ci=CISnapshot(
                    status="ok",
                    cursor=_fingerprint("terminal-ci"),
                    aggregate=CIAggregateState.SKIPPED,
                ),
                ai_reviews=AIReviewSnapshot(
                    status="ok",
                    cursor=_fingerprint("terminal-ai-reviews"),
                ),
            )

        comments_task = asyncio.create_task(
            self._run(
                (
                    "meta",
                    "phabricator.diff",
                    "comments",
                    f"--number={diff_id}",
                    "--output=json",
                    "--no-color",
                    "--latest-version",
                    "--skip-author",
                    "--unresolved-only",
                    "--no-suggestions",
                )
            )
        )
        ci_task = asyncio.create_task(
            self._run(
                (
                    "jf",
                    "graphql",
                    "--query",
                    _CI_QUERY,
                    "--variables",
                    json.dumps({"version_id": metadata.latest_version_id}),
                )
            )
        )
        arctic_task = asyncio.create_task(
            self._run(
                (
                    "meta",
                    "phabricator.diff",
                    "arctic",
                    f"--number={diff_id}",
                    f"--insight-type={_ARCTIC_ACTIONABLE_INSIGHT}",
                    "--output=json",
                )
            )
        )
        comments_result, ci_result, arctic_result = await asyncio.gather(
            comments_task,
            ci_task,
            arctic_task,
            return_exceptions=True,
        )
        comments = _parse_comments_result(
            comments_result,
            author_id=metadata.author_id or "",
            version_id=metadata.latest_version_id or "",
        )
        version_id = metadata.latest_version_id or ""
        ci, signal_reviews = _parse_ci_result(ci_result, version_id)
        ai_reviews = _merge_ai_reviews(
            signal_reviews,
            _parse_arctic_result(arctic_result),
            version_id,
        )
        return DiffSnapshot(
            schema_version=2,
            diff_id=diff_id,
            lifecycle=metadata.lifecycle,
            author_id=metadata.author_id,
            latest_version_id=metadata.latest_version_id,
            last_activity_at=metadata.last_activity_at,
            observed_at=observed_at,
            comments=comments,
            ci=ci,
            ai_reviews=ai_reviews,
        )


class _Metadata:
    def __init__(
        self,
        lifecycle: DiffLifecycle,
        author_id: str | None,
        latest_version_id: str | None,
        last_activity_at: datetime,
    ) -> None:
        self.lifecycle = lifecycle
        self.author_id = author_id
        self.latest_version_id = latest_version_id
        self.last_activity_at = last_activity_at


def bounded_source_environment(
    source: Mapping[str, str] | None = None,
) -> dict[str, str]:
    source = os.environ if source is None else source
    return {name: source[name] for name in _SAFE_ENV_NAMES if source.get(name)}


def _object(value: object, name: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise TypeError(f"{name} must be an object")
    if not all(isinstance(key, str) for key in value):
        raise TypeError(f"{name} keys must be strings")
    return {str(key): item for key, item in value.items()}


def _parse_metadata(
    diff_id: str,
    payload: dict[str, object],
    observed_at: datetime,
) -> _Metadata:
    if payload.get("diff") is None and payload.get("not_found") is True:
        return _Metadata(DiffLifecycle.MISSING, None, None, observed_at)
    nested = payload.get("diff")
    root = _object(nested, "diff") if isinstance(nested, dict) else payload
    status = _string_value(root, "status", "status_name", "diff_status").lower()
    if any(token in status for token in ("commit", "landed", "closed")):
        lifecycle = DiffLifecycle.COMMITTED
    elif "abandon" in status:
        lifecycle = DiffLifecycle.ABANDONED
    elif "revert" in status:
        lifecycle = DiffLifecycle.REVERTED
    elif status in {"missing", "not_found", "not found"}:
        return _Metadata(DiffLifecycle.MISSING, None, None, observed_at)
    else:
        lifecycle = DiffLifecycle.ACTIVE

    author = root.get("author")
    author_id = _identity(author) or _optional_string(root.get("author_id"))
    version = _latest_version(root)
    if lifecycle is not DiffLifecycle.MISSING and (author_id is None or version is None):
        raise ValueError(f"{diff_id} metadata omitted author or latest version")
    activity_value = next(
        (
            root[key]
            for key in (
                "date_modified",
                "updated_at",
                "last_activity_at",
                "modified_time",
                "created_time",
            )
            if root.get(key) is not None
        ),
        observed_at,
    )
    activity = _datetime(activity_value)
    if activity > observed_at:
        activity = observed_at
    return _Metadata(lifecycle, author_id, version, activity)


def _latest_version(root: dict[str, object]) -> str | None:
    published = root.get("latest_phabricator_version")
    draft = root.get("latest_draft_phabricator_version")
    published_number = _int_field(published, "number")
    draft_number = _int_field(draft, "number")
    selected = draft if draft_number > published_number else published
    if isinstance(selected, dict):
        return _optional_string(selected.get("id") or selected.get("fbid"))
    return _optional_string(root.get("latest_version_id"))


def _parse_comments_result(
    result: object,
    *,
    author_id: str,
    version_id: str,
) -> CommentsSnapshot:
    if isinstance(result, BaseException):
        return CommentsSnapshot(status="error", error=_component_failure(result))
    try:
        raw_items: list[object]
        if isinstance(result, list):
            raw_items = list(result)
        else:
            payload = _object(result, "comments")
            raw_items = []
            for key in ("comments", "data", "results"):
                if key not in payload:
                    continue
                value = payload[key]
                if not isinstance(value, list):
                    raise TypeError(f"{key} must be a list")
                raw_items = list(value)
                break
        comments: list[ReviewComment] = []
        for raw in raw_items:
            if not isinstance(raw, dict):
                continue
            item = _object(raw, "comment")
            if not _comment_actionable(item, author_id):
                continue
            external_id = _optional_string(
                item.get("id") or item.get("comment_id") or item.get("fbid")
            )
            content = _optional_string(
                item.get("content") or item.get("message") or item.get("text") or item.get("body")
            )
            if external_id is None or content is None:
                continue
            item_version = (
                _optional_string(item.get("version_id") or item.get("phabricator_version_id"))
                or version_id
            )
            if item_version != version_id:
                continue
            comments.append(
                ReviewComment(
                    external_id=external_id,
                    version_id=item_version,
                    updated_at=_datetime(
                        item.get("updated_at")
                        or item.get("date_modified")
                        or item.get("created_at")
                        or item.get("created")
                    ),
                    content_fingerprint=_fingerprint(" ".join(content.split())),
                )
            )
        comments.sort(key=lambda item: (item.updated_at, item.external_id))
        cursor = _fingerprint(
            json.dumps(
                [(item.external_id, item.content_fingerprint) for item in comments],
                separators=(",", ":"),
            )
        )
        return CommentsSnapshot(status="ok", cursor=cursor, items=tuple(comments))
    except (TypeError, ValueError, KeyError):
        return CommentsSnapshot(
            status="error",
            error=SourceFailure(
                category=SourceErrorCategory.MALFORMED,
                retryable=True,
                summary="review comments response was malformed",
            ),
        )


def _comment_actionable(item: dict[str, object], author_id: str) -> bool:
    if item.get("resolved") is True or item.get("is_resolved") is True:
        return False
    status = _optional_string(item.get("status"))
    if status and status.lower() in {"resolved", "deleted", "draft"}:
        return False
    if any(item.get(key) is True for key in ("draft", "is_draft", "deleted", "is_deleted")):
        return False
    if any(item.get(key) is True for key in ("automated", "is_automated", "is_signal")):
        return False
    kind = _optional_string(item.get("type") or item.get("kind") or item.get("source"))
    if kind and any(token in kind.lower() for token in ("signal", "bot", "automated")):
        return False
    comment_author = _identity(item.get("author")) or _optional_string(item.get("author_id"))
    return comment_author is not None and comment_author != author_id


def _parse_ci_result(
    result: object,
    version_id: str,
) -> tuple[CISnapshot, tuple[AIReviewFinding, ...] | None]:
    """Return the CI snapshot plus any automated-review findings it carried.

    The findings ride along on the CI response rather than being fetched
    separately because they are signals on the same version. ``None`` means the
    response could not be read, which the caller must distinguish from "read
    fine, no findings".
    """
    if isinstance(result, BaseException):
        return CISnapshot(status="error", error=_component_failure(result)), None
    try:
        payload = _object(result, "CI")
        nested = payload.get("data")
        if isinstance(nested, dict):
            payload = _object(nested, "CI data")
        signals = _object(payload.get("signalview_signals"), "signalview_signals")
        total = _count(signals.get("all"))
        pending = _count(signals.get("pending"))
        failed_box = _object(signals.get("failed"), "failed signals")
        raw_failures = failed_box.get("nodes", [])
        if not isinstance(raw_failures, list):
            raise TypeError("failed signal nodes must be a list")
        # Failures are surfaced as soon as they are known. Waiting for the run
        # to settle would hide them for as long as the diff's slowest signal,
        # which on a wide test selection is most of the run.
        failures: tuple[CIFailure, ...] = ()
        if _count(failed_box) > 0:
            normalized: dict[str, CIFailure] = {}
            for raw in raw_failures:
                identity = _signal_identity(raw)
                external_id = "signal:" + hashlib.sha256(identity.encode()).hexdigest()[:32]
                normalized[external_id] = CIFailure(
                    external_id=external_id,
                    fingerprint=_fingerprint(f"{version_id}:{identity}"),
                )
            if not normalized:
                normalized["signal:aggregate"] = CIFailure(
                    external_id="signal:aggregate",
                    fingerprint=_fingerprint(f"{version_id}:failed:{_count(failed_box)}"),
                )
            failures = tuple(normalized[key] for key in sorted(normalized))
        if pending > 0:
            aggregate = CIAggregateState.PENDING
        elif failures:
            aggregate = CIAggregateState.FAILING
        elif total > 0:
            aggregate = CIAggregateState.PASSED
        else:
            aggregate = CIAggregateState.UNKNOWN
        # Scoped narrowly: a reviewer feed this adapter cannot read degrades to
        # "unknown reviews", never to a CI snapshot that hides failures.
        try:
            reviews = _parse_review_signals(signals.get("reviews"), version_id)
        except (TypeError, ValueError, KeyError):
            reviews = None
        cursor = _fingerprint(
            json.dumps(
                [aggregate.value, version_id, [item.fingerprint for item in failures]],
                separators=(",", ":"),
            )
        )
        return (
            CISnapshot(
                status="ok",
                cursor=cursor,
                aggregate=aggregate,
                failures=failures,
            ),
            reviews,
        )
    except (TypeError, ValueError, KeyError):
        return (
            CISnapshot(
                status="error",
                error=SourceFailure(
                    category=SourceErrorCategory.MALFORMED,
                    retryable=True,
                    summary="CI response was malformed",
                ),
            ),
            None,
        )


def _parse_review_signals(
    value: object,
    version_id: str,
) -> tuple[AIReviewFinding, ...] | None:
    """Pull automated-reviewer signals out of the grouped signal response.

    Only FAILED and WARNING statuses are requested, so a reviewer that ran and
    found nothing (INFO or PASSED) contributes no findings.
    """
    if value is None:
        return None
    groups = _object(value, "review signal groups").get("nodes", [])
    if not isinstance(groups, list):
        raise TypeError("review signal groups must be a list")
    findings: dict[str, AIReviewFinding] = {}
    for group in groups:
        item = _object(group, "review signal group")
        group_data = item.get("group_data")
        functional_type = (
            _optional_string(_object(group_data, "group data").get("functional_type"))
            if isinstance(group_data, dict)
            else None
        )
        if functional_type != _REVIEW_INSIGHTS_GROUP:
            continue
        nodes = _object(item.get("signals"), "review signals").get("nodes", [])
        if not isinstance(nodes, list):
            raise TypeError("review signal nodes must be a list")
        for raw in nodes:
            signal = _object(raw, "review signal")
            name = _optional_string(signal.get("name"))
            if name is None:
                continue
            status = _optional_string(signal.get("status")) or ""
            external_id = "review:" + hashlib.sha256(name.encode()).hexdigest()[:32]
            findings[external_id] = AIReviewFinding(
                external_id=external_id,
                fingerprint=_fingerprint(f"{version_id}:{name}:{status}"),
            )
    return tuple(findings[key] for key in sorted(findings))


def _parse_arctic_result(result: object) -> tuple[AIReviewFinding, ...] | None:
    """Normalize Arctic spotlight findings, dropping ones already dealt with.

    Arctic reports per diff rather than per version and carries no stable
    identifier, so identity is derived from the finding's own location and
    title.
    """
    if isinstance(result, BaseException):
        return None
    if not isinstance(result, list):
        return None
    findings: dict[str, AIReviewFinding] = {}
    for raw in result:
        if not isinstance(raw, dict):
            return None
        item = {str(key): value for key, value in raw.items()}
        resolution = (_optional_string(item.get("resolution")) or "").lower()
        if resolution and resolution != _ARCTIC_UNRESOLVED:
            continue
        title = _optional_string(item.get("title"))
        if title is None:
            continue
        identity = "\0".join(
            (
                _optional_string(item.get("insight_type")) or "",
                title,
                _optional_string(item.get("file")) or "",
                _optional_string(item.get("lines")) or "",
            )
        )
        external_id = "arctic:" + hashlib.sha256(identity.encode()).hexdigest()[:32]
        findings[external_id] = AIReviewFinding(
            external_id=external_id,
            fingerprint=_fingerprint(
                "\0".join(
                    (
                        identity,
                        _optional_string(item.get("severity")) or "",
                        _optional_string(item.get("details")) or "",
                    )
                )
            ),
        )
    return tuple(findings[key] for key in sorted(findings))


def _merge_ai_reviews(
    signal_reviews: tuple[AIReviewFinding, ...] | None,
    arctic_reviews: tuple[AIReviewFinding, ...] | None,
    version_id: str,
) -> AIReviewSnapshot:
    """Combine the two reviewer feeds, holding state if either could not be read.

    Advancing on a partial read would mark the unread feed's findings resolved,
    so a single failing feed suppresses the component exactly as a failing
    comment or CI read does.
    """
    if signal_reviews is None or arctic_reviews is None:
        return AIReviewSnapshot(
            status="error",
            error=SourceFailure(
                category=SourceErrorCategory.MALFORMED,
                retryable=True,
                summary="automated review response was malformed",
            ),
        )
    items = tuple(sorted(signal_reviews + arctic_reviews, key=lambda item: item.external_id))
    return AIReviewSnapshot(
        status="ok",
        cursor=_fingerprint(
            json.dumps(
                [version_id, [item.fingerprint for item in items]],
                separators=(",", ":"),
            )
        ),
        items=items,
    )


def _component_failure(error: BaseException) -> SourceFailure:
    category = (
        _source_error_category(error)
        if isinstance(error, SourceCommandError)
        else SourceErrorCategory.UNAVAILABLE
    )
    return SourceFailure(
        category=category,
        retryable=category is not SourceErrorCategory.AUTH,
        summary=f"review source component failed ({category.value})",
    )


def _source_error_category(error: SourceCommandError) -> SourceErrorCategory:
    return {
        SourceCommandErrorCategory.AUTH: SourceErrorCategory.AUTH,
        SourceCommandErrorCategory.RATE_LIMIT: SourceErrorCategory.RATE_LIMIT,
        SourceCommandErrorCategory.TIMEOUT: SourceErrorCategory.TIMEOUT,
        SourceCommandErrorCategory.OUTPUT_LIMIT: SourceErrorCategory.MALFORMED,
        SourceCommandErrorCategory.EXIT: SourceErrorCategory.UNAVAILABLE,
        SourceCommandErrorCategory.MALFORMED: SourceErrorCategory.MALFORMED,
    }[error.category]


def _signal_identity(value: object) -> str:
    item = _object(value, "signal")
    fields = (
        _optional_string(item.get("name")) or "unknown",
        _optional_string(item.get("backend")) or "",
        _optional_string(item.get("slp_functional_type")) or "",
    )
    return "\0".join(fields)


def _count(value: object) -> int:
    if not isinstance(value, dict):
        raise TypeError("signal count must be an object")
    count = value.get("count")
    if not isinstance(count, int) or isinstance(count, bool) or count < 0:
        raise TypeError("signal count must be a non-negative integer")
    return count


def _identity(value: object) -> str | None:
    if isinstance(value, str):
        return value or None
    if isinstance(value, dict):
        for key in ("id", "fbid", "username", "name"):
            identity = _optional_string(value.get(key))
            if identity:
                return identity
    return None


def _string_value(payload: dict[str, object], *keys: str) -> str:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, dict):
            nested = _object(value, key)
            value = nested.get("name") or nested.get("value")
        result = _optional_string(value)
        if result is not None:
            return result
    return "active"


def _optional_string(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _int_field(value: object, key: str) -> int:
    if not isinstance(value, dict):
        return 0
    raw = value.get(key)
    if isinstance(raw, int) and not isinstance(raw, bool):
        return raw
    if isinstance(raw, str) and raw.isdigit():
        return int(raw)
    return 0


def _datetime(value: object) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return datetime.fromtimestamp(value, tz=UTC)
    if isinstance(value, str):
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    raise TypeError("timestamp is missing or malformed")
