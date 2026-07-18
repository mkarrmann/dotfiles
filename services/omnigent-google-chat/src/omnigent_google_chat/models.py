from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any


class MappingState(StrEnum):
    ACTIVE = "active"
    DETACHED = "detached"
    ARCHIVED = "archived"
    ERROR = "error"


class InboundState(StrEnum):
    CLAIMED = "claimed"
    DISPATCHING = "dispatching"
    SUBMITTED = "submitted"
    AMBIGUOUS = "ambiguous"
    REJECTED = "rejected"


class OutboundState(StrEnum):
    PENDING = "pending"
    SENT = "sent"
    FAILED = "failed"
    SUPPRESSED = "suppressed"


@dataclass(frozen=True, slots=True)
class SessionSummary:
    id: str
    title: str
    status: str
    labels: dict[str, Any] = field(default_factory=dict)
    host_id: str | None = None
    workspace: str | None = None
    runner_id: str | None = None
    runner_online: bool | None = None
    host_online: bool | None = None
    archived: bool = False
    updated_at: int = 0
    pending_elicitations_count: int = 0
    permission_level: int | None = None

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> SessionSummary:
        session_id = payload.get("id")
        if not isinstance(session_id, str) or not session_id:
            raise ValueError("session payload is missing id")
        title = payload.get("title")
        status = payload.get("status")
        labels = payload.get("labels")
        return cls(
            id=session_id,
            title=title if isinstance(title, str) and title else session_id,
            status=status if isinstance(status, str) else "unknown",
            labels=dict(labels) if isinstance(labels, dict) else {},
            host_id=_optional_str(payload.get("host_id")),
            workspace=_optional_str(payload.get("workspace")),
            runner_id=_optional_str(payload.get("runner_id")),
            runner_online=_optional_bool(payload.get("runner_online")),
            host_online=_optional_bool(payload.get("host_online")),
            archived=payload.get("archived") is True,
            updated_at=_int_or_zero(payload.get("updated_at")),
            pending_elicitations_count=_int_or_zero(
                payload.get("pending_elicitations_count")
                if "pending_elicitations_count" in payload
                else len(payload.get("pending_elicitations", []))
                if isinstance(payload.get("pending_elicitations"), list)
                else 0
            ),
            permission_level=(
                int(payload["permission_level"])
                if isinstance(payload.get("permission_level"), int)
                else None
            ),
        )


@dataclass(frozen=True, slots=True)
class SessionThread:
    omnigent_session_id: str
    space_name: str
    thread_name: str
    root_message_name: str
    title: str
    last_item_position: str | None
    state: MappingState
    mirrored_chars: int = 0


@dataclass(frozen=True, slots=True)
class GoogleChatMessage:
    name: str
    space_name: str
    thread_name: str
    actor_id: str
    actor_type: str
    create_time: str
    text: str
    has_attachments: bool
    raw: dict[str, Any] = field(repr=False, compare=False)

    @property
    def ordering_key(self) -> tuple[str, str]:
        return (self.create_time, self.name)


@dataclass(frozen=True, slots=True)
class GoogleChatPage:
    messages: list[GoogleChatMessage]
    next_page_token: str | None = None


@dataclass(frozen=True, slots=True)
class SentMessage:
    name: str
    thread_name: str
    actor_id: str | None = None
    actor_type: str | None = None


@dataclass(frozen=True, slots=True)
class ItemPage:
    items: list[dict[str, Any]]
    last_id: str | None
    has_more: bool


@dataclass(frozen=True, slots=True)
class ClaimResult:
    claimed: bool
    changed_content: bool = False
    state: InboundState | None = None


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _optional_str(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _optional_bool(value: object) -> bool | None:
    return value if isinstance(value, bool) else None


def _int_or_zero(value: object) -> int:
    return int(value) if isinstance(value, (int, float)) else 0
