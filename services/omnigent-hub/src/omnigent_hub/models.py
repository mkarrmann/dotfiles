from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal


class ValidationError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class Topology:
    primary_fqdn: str
    standby_fqdn: str
    port: int

    @property
    def hubs(self) -> tuple[str, str]:
        return (self.primary_fqdn, self.standby_fqdn)

    def validate_hub(self, fqdn: str) -> None:
        if fqdn not in self.hubs:
            raise ValidationError(f"{fqdn!r} is not a configured Omnigent hub")


@dataclass(frozen=True, slots=True)
class ActiveHubRecord:
    format_version: int
    epoch: int
    state: Literal["active", "transition"]
    active_hub: str | None
    activation_id: str | None
    restored_generation: str | None
    updated_at: str
    updated_by: str
    source_hub: str | None = None
    target_hub: str | None = None
    transition_id: str | None = None

    @classmethod
    def from_dict(cls, value: Any, topology: Topology) -> ActiveHubRecord:
        if not isinstance(value, dict):
            raise ValidationError("active-hub record must be a JSON object")
        if value.get("format_version") != 1:
            raise ValidationError("unsupported active-hub format_version")
        epoch = value.get("epoch")
        if not isinstance(epoch, int) or isinstance(epoch, bool) or epoch < 1:
            raise ValidationError("active-hub epoch must be a positive integer")
        state = value.get("state", "active" if value.get("active_hub") else None)
        if state not in ("active", "transition"):
            raise ValidationError("active-hub state must be active or transition")

        active_hub = _optional_string(value, "active_hub")
        activation_id = _optional_string(value, "activation_id")
        restored_generation = _optional_string(value, "restored_generation")
        source_hub = _optional_string(value, "source_hub")
        target_hub = _optional_string(value, "target_hub")
        transition_id = _optional_string(value, "transition_id")
        updated_at = _required_string(value, "updated_at")
        updated_by = _required_string(value, "updated_by")

        if state == "active":
            if active_hub is None or activation_id is None:
                raise ValidationError("active record requires active_hub and activation_id")
            topology.validate_hub(active_hub)
            if any(part is not None for part in (source_hub, target_hub, transition_id)):
                raise ValidationError("active record cannot contain transition fields")
        else:
            if active_hub is not None or activation_id is not None:
                raise ValidationError("transition record cannot name an active hub")
            if source_hub is None or target_hub is None or transition_id is None:
                raise ValidationError(
                    "transition record requires source_hub, target_hub, and transition_id"
                )
            topology.validate_hub(source_hub)
            topology.validate_hub(target_hub)
            if source_hub == target_hub:
                raise ValidationError("transition source and target must differ")

        return cls(
            format_version=1,
            epoch=epoch,
            state=state,
            active_hub=active_hub,
            activation_id=activation_id,
            restored_generation=restored_generation,
            updated_at=updated_at,
            updated_by=updated_by,
            source_hub=source_hub,
            target_hub=target_hub,
            transition_id=transition_id,
        )

    def to_dict(self) -> dict[str, Any]:
        value: dict[str, Any] = {
            "format_version": self.format_version,
            "epoch": self.epoch,
            "state": self.state,
            "active_hub": self.active_hub,
            "activation_id": self.activation_id,
            "restored_generation": self.restored_generation,
            "updated_at": self.updated_at,
            "updated_by": self.updated_by,
        }
        if self.state == "transition":
            value.update(
                {
                    "source_hub": self.source_hub,
                    "target_hub": self.target_hub,
                    "transition_id": self.transition_id,
                }
            )
        return value


def _optional_string(value: dict[str, Any], key: str) -> str | None:
    item = value.get(key)
    if item is None:
        return None
    if not isinstance(item, str) or not item.strip():
        raise ValidationError(f"{key} must be a non-empty string or null")
    return item


def _required_string(value: dict[str, Any], key: str) -> str:
    item = _optional_string(value, key)
    if item is None:
        raise ValidationError(f"{key} is required")
    return item
