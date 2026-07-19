from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

SPACE_RE = re.compile(r"^spaces/[A-Za-z0-9_-]+$")
ACTOR_RE = re.compile(r"^(?:users/)?[A-Za-z0-9@._+-]+$")


def _validate_space(value: str) -> str:
    if not SPACE_RE.fullmatch(value):
        raise ValueError("must be an exact spaces/... resource name")
    return value


def _validate_unixname(value: str) -> str:
    normalized = value.removeprefix("@").strip()
    if not normalized or not re.fullmatch(r"[A-Za-z0-9._-]+", normalized):
        raise ValueError("must be a unixname, without an email domain")
    return normalized


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    omnigent_base_url: str = Field(
        default="http://127.0.0.1:6767",
        validation_alias=AliasChoices("OMNIGENT_BASE_URL", "OMNIGENT_URL"),
    )
    omnigent_auth_email: str | None = Field(default=None, validation_alias="OMNIGENT_AUTH_EMAIL")
    omnigent_auth_header_name: str = Field(
        default="X-Forwarded-Email",
        validation_alias="OMNIGENT_AUTH_HEADER_NAME",
    )
    omnigent_session_cookie: str | None = Field(
        default=None, validation_alias="OMNIGENT_SESSION_COOKIE"
    )
    omnigent_host_id: str | None = Field(default=None, validation_alias="OMNIGENT_GCHAT_HOST_ID")
    omnigent_host_scope: Literal["configured", "all"] = Field(
        default="configured", validation_alias="OMNIGENT_GCHAT_HOST_SCOPE"
    )
    omnigent_runner_launch_timeout_seconds: float = Field(
        default=60.0,
        ge=1.0,
        validation_alias="OMNIGENT_GCHAT_RUNNER_LAUNCH_TIMEOUT_SECONDS",
    )

    space_name: str = Field(validation_alias="OMNIGENT_GCHAT_SPACE")
    allowed_actor_id: str = Field(validation_alias="OMNIGENT_GCHAT_ALLOWED_ACTOR_ID")
    meta_bot_actor_id: str = Field(validation_alias="OMNIGENT_GCHAT_META_BOT_ACTOR_ID")
    mention_unixname: str = Field(validation_alias="OMNIGENT_GCHAT_MENTION_UNIXNAME")
    phase0_validated: bool = Field(
        default=False, validation_alias="OMNIGENT_GCHAT_PHASE0_VALIDATED"
    )

    discovery: Literal["label", "host-active"] = Field(
        default="label", validation_alias="OMNIGENT_GCHAT_DISCOVERY"
    )
    discovery_label: str = Field(
        default="omnigent.google_chat.enabled",
        validation_alias="OMNIGENT_GCHAT_LABEL",
    )
    session_lookback_hours: int = Field(
        default=24,
        ge=1,
        validation_alias="OMNIGENT_GCHAT_SESSION_LOOKBACK_HOURS",
    )
    discovery_interval_seconds: float = Field(
        default=15.0,
        ge=1.0,
        validation_alias="OMNIGENT_GCHAT_DISCOVERY_INTERVAL_SECONDS",
    )

    database_path: Path = Field(
        default=Path("~/.omnigent/google-chat.sqlite3"),
        validation_alias=AliasChoices("OMNIGENT_GCHAT_DATABASE", "OMNIGENT_GCHAT_DATABASE_PATH"),
    )
    mirror_mode: Literal["concise", "status-only"] = Field(
        default="concise", validation_alias="OMNIGENT_GCHAT_MIRROR_MODE"
    )
    mention_enabled: bool = Field(
        default=True,
        validation_alias="OMNIGENT_GCHAT_MENTION_ENABLED",
    )
    mention_on_completion: bool = Field(
        default=True,
        validation_alias="OMNIGENT_GCHAT_MENTION_ON_COMPLETION",
    )
    mention_on_root: bool = Field(default=True, validation_alias="OMNIGENT_GCHAT_MENTION_ON_ROOT")
    max_message_chars: int = Field(
        default=12_000,
        ge=100,
        le=30_000,
        validation_alias="OMNIGENT_GCHAT_MAX_MESSAGE_CHARS",
    )
    max_session_chars: int = Field(
        default=100_000,
        ge=100,
        validation_alias="OMNIGENT_GCHAT_MAX_SESSION_CHARS",
    )
    max_input_chars: int = Field(
        default=12_000,
        ge=1,
        validation_alias="OMNIGENT_GCHAT_MAX_INPUT_CHARS",
    )

    active_poll_seconds: float = Field(
        default=10.0,
        ge=1.0,
        validation_alias="OMNIGENT_GCHAT_ACTIVE_POLL_SECONDS",
    )
    idle_poll_seconds: float = Field(
        default=30.0,
        ge=1.0,
        validation_alias="OMNIGENT_GCHAT_IDLE_POLL_SECONDS",
    )
    poll_overlap_seconds: int = Field(
        default=120,
        ge=1,
        validation_alias="OMNIGENT_GCHAT_POLL_OVERLAP_SECONDS",
    )
    chat_timeout_seconds: float = Field(
        default=90.0,
        ge=1.0,
        validation_alias="OMNIGENT_GCHAT_CLI_TIMEOUT_SECONDS",
    )
    omnigent_timeout_seconds: float = Field(
        default=30.0,
        ge=1.0,
        validation_alias="OMNIGENT_GCHAT_OMNIGENT_TIMEOUT_SECONDS",
    )
    health_stale_seconds: float = Field(
        default=120.0,
        ge=10.0,
        validation_alias="OMNIGENT_GCHAT_HEALTH_STALE_SECONDS",
    )
    recent_active_seconds: float = Field(
        default=120.0,
        ge=0.0,
        validation_alias="OMNIGENT_GCHAT_RECENT_ACTIVE_SECONDS",
    )
    inbound_retention_days: int = Field(
        default=30,
        ge=1,
        validation_alias="OMNIGENT_GCHAT_INBOUND_RETENTION_DAYS",
    )

    meta_cli: Path = Field(default=Path("/usr/local/bin/meta"), validation_alias="META_CLI")
    log_level: str = Field(default="INFO", validation_alias="LOG_LEVEL")

    @field_validator("space_name")
    @classmethod
    def validate_space(cls, value: str) -> str:
        return _validate_space(value)

    @field_validator("allowed_actor_id", "meta_bot_actor_id")
    @classmethod
    def validate_actor(cls, value: str) -> str:
        if not ACTOR_RE.fullmatch(value):
            raise ValueError("must be an exact Google Chat actor identity")
        return value

    @field_validator("mention_unixname")
    @classmethod
    def validate_unixname(cls, value: str) -> str:
        return _validate_unixname(value)

    @field_validator("database_path")
    @classmethod
    def expand_database_path(cls, value: Path) -> Path:
        return value.expanduser().resolve()

    @field_validator("meta_cli")
    @classmethod
    def expand_meta_cli(cls, value: Path) -> Path:
        if value.is_absolute() or "/" in str(value):
            return value.expanduser().resolve()
        return value

    @model_validator(mode="after")
    def validate_relationships(self) -> Settings:
        if self.omnigent_host_scope == "configured" and not self.omnigent_host_id:
            raise ValueError(
                "OMNIGENT_GCHAT_HOST_ID is required when OMNIGENT_GCHAT_HOST_SCOPE=configured"
            )
        if self.allowed_actor_id == self.meta_bot_actor_id:
            raise ValueError("human and Meta Bot actor identities must be distinct")
        if self.idle_poll_seconds < self.active_poll_seconds:
            raise ValueError("idle poll interval must be at least the active interval")
        if self.max_session_chars < self.max_message_chars:
            raise ValueError("session output cap must be at least one message")
        return self

    def validate_daemon_gate(self) -> None:
        if not self.phase0_validated:
            raise ValueError(
                "OMNIGENT_GCHAT_PHASE0_VALIDATED must be true after completing the "
                "phone notification and identity checks"
            )
        if self.meta_cli.is_absolute() and not os.access(self.meta_cli, os.X_OK):
            raise ValueError(f"META_CLI is not executable: {self.meta_cli}")


def load_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]


class PhaseZeroSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    space_name: str = Field(validation_alias="OMNIGENT_GCHAT_SPACE")
    mention_unixname: str = Field(validation_alias="OMNIGENT_GCHAT_MENTION_UNIXNAME")
    meta_cli: Path = Field(default=Path("/usr/local/bin/meta"), validation_alias="META_CLI")
    chat_timeout_seconds: float = Field(
        default=90.0,
        ge=1.0,
        validation_alias="OMNIGENT_GCHAT_CLI_TIMEOUT_SECONDS",
    )

    _validate_space_field = field_validator("space_name")(_validate_space)
    _validate_unixname_field = field_validator("mention_unixname")(_validate_unixname)


def load_phase_zero_settings() -> PhaseZeroSettings:
    return PhaseZeroSettings()  # type: ignore[call-arg]
