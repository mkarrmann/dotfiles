"""Route each watch's session id to the backend that owns it.

The engine stays backend-neutral: it names a session and asks whether it is
live and whether a wake was accepted. Everything that is not an
``agenthome:`` id goes to Omnigent exactly as before this module existed.
"""

from __future__ import annotations

from .agenthome_client import is_agent_home
from .domain import DeliveryService, EventDeliveryResult, SessionService, SessionSnapshot


class RoutingSessionService:
    def __init__(self, omnigent: SessionService, agent_home: SessionService) -> None:
        self._omnigent = omnigent
        self._agent_home = agent_home

    async def get(self, session_id: str) -> SessionSnapshot:
        backend = self._agent_home if is_agent_home(session_id) else self._omnigent
        return await backend.get(session_id)


class RoutingDeliveryService:
    def __init__(self, omnigent: DeliveryService, agent_home: DeliveryService) -> None:
        self._omnigent = omnigent
        self._agent_home = agent_home

    def _backend(self, session_id: str) -> DeliveryService:
        return self._agent_home if is_agent_home(session_id) else self._omnigent

    async def delivery_receipt(
        self, session_id: str, delivery_id: str
    ) -> EventDeliveryResult | None:
        return await self._backend(session_id).delivery_receipt(session_id, delivery_id)

    async def deliver_message(
        self, session_id: str, delivery_id: str, content: str
    ) -> EventDeliveryResult:
        return await self._backend(session_id).deliver_message(session_id, delivery_id, content)
