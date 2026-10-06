"""Bus d'événements en mémoire → flux SSE (`GET /api/events`) + notifications.

Types d'événements publiés :
  player_registered, player_linked, draw_done, challenge_started, challenge_finished,
  challenge_reset, live_start, live_end, match_recorded, rank_changed, poll_done.
Chaque événement : {"id": int, "type": str, "ts": iso-utc, "data": dict}.
"""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from typing import Any


class EventBus:
    def __init__(self, history: int = 200):
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()
        self._history: deque[dict[str, Any]] = deque(maxlen=history)
        self._counter = 0

    def publish(self, type: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
        """Publie un événement (appel depuis une coroutine ou le thread de la boucle)."""
        self._counter += 1
        event = {
            "id": self._counter,
            "type": type,
            "ts": datetime.now(timezone.utc).isoformat(),
            "data": data or {},
        }
        self._history.append(event)
        for queue in list(self._subscribers):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                # Abonné trop lent : on le laisse rattraper via l'historique
                pass
        return event

    def recent(self, limit: int = 50, types: set[str] | None = None) -> list[dict[str, Any]]:
        items = [e for e in self._history if types is None or e["type"] in types]
        return items[-limit:]

    async def subscribe(self, since_id: int | None = None) -> AsyncIterator[dict[str, Any]]:
        """Itérateur asynchrone des événements (rejoue l'historique après `since_id`)."""
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=500)
        self._subscribers.add(queue)
        try:
            if since_id is not None:
                for event in list(self._history):
                    if event["id"] > since_id:
                        yield event
            while True:
                yield await queue.get()
        finally:
            self._subscribers.discard(queue)

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)


bus = EventBus()
