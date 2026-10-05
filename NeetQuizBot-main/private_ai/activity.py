"""Activity-GC abstraction.

Phase 2 only defines the interface. Telegram topic creation/forwarding is wired
into the main bot in the integration phase.
"""

from __future__ import annotations

from typing import Protocol


class ActivitySink(Protocol):
    async def ensure_topic(self, user_id: int, topic_name: str) -> int:
        ...

    async def record(self, user_id: int, text: str, role: str) -> None:
        ...


class ActivityService:
    def __init__(self, db, sink: ActivitySink):
        self.db = db
        self.sink = sink

    async def ensure_user_topic(self, user_id: int, topic_name: str) -> int:
        existing = await self.db.get_ai_activity(user_id)
        if existing:
            return int(existing["topic_id"])

        topic_id = await self.sink.ensure_topic(user_id, topic_name)
        # Activity group ID is owned by the sink implementation; this phase does
        # not assume a hard-coded Telegram chat ID.
        raise RuntimeError(
            "Activity sink must persist the created topic mapping in Phase 3."
        )


__all__ = ["ActivityService", "ActivitySink"]
