from __future__ import annotations

from omnigent_watcher.domain import EventDeliveryResult, EventDeliveryStatus, SessionSnapshot
from omnigent_watcher.session_routing import RoutingDeliveryService, RoutingSessionService


class Recorder:
    def __init__(self, name: str) -> None:
        self.name = name
        self.seen: list[str] = []

    async def get(self, session_id: str) -> SessionSnapshot:
        self.seen.append(session_id)
        return SessionSnapshot(session_id=session_id, labels={"backend": self.name})

    async def delivery_receipt(
        self, session_id: str, delivery_id: str
    ) -> EventDeliveryResult | None:
        self.seen.append(session_id)
        return None

    async def deliver_message(
        self, session_id: str, delivery_id: str, content: str
    ) -> EventDeliveryResult:
        self.seen.append(session_id)
        return EventDeliveryResult(EventDeliveryStatus.ACCEPTED)


async def test_only_agent_home_ids_leave_the_omnigent_path() -> None:
    omnigent, agent_home = Recorder("omnigent"), Recorder("agenthome")
    sessions = RoutingSessionService(omnigent, agent_home)
    delivery = RoutingDeliveryService(omnigent, agent_home)

    for session_id in ("conv_abc12345", "ah_rrO5GPFnu14SVsq9ExhBFJ"):
        await sessions.get(session_id)
        await delivery.delivery_receipt(session_id, "b1")
        await delivery.deliver_message(session_id, "b1", "wake")
    await sessions.get("agenthome:ah_rrO5GPFnu14SVsq9ExhBFJ")
    await delivery.delivery_receipt("agenthome:ah_rrO5GPFnu14SVsq9ExhBFJ", "b1")
    await delivery.deliver_message("agenthome:ah_rrO5GPFnu14SVsq9ExhBFJ", "b1", "wake")

    assert set(omnigent.seen) == {"conv_abc12345", "ah_rrO5GPFnu14SVsq9ExhBFJ"}
    assert agent_home.seen == ["agenthome:ah_rrO5GPFnu14SVsq9ExhBFJ"] * 3
