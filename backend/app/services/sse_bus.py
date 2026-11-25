"""In-process pub/sub SSE event bus with DB-backed replay for AgentTask.steps_log."""

from __future__ import annotations

import asyncio
import json
import uuid
from collections import defaultdict
from collections.abc import AsyncIterator
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import utcnow
from app.core.security import redact_dict
from app.models import AgentTask

TERMINAL_EVENTS = frozenset({"agent.completed", "task.failed", "task.cancelled"})


def format_sse(event: dict[str, Any]) -> str:
    """Format an event dict into a wire-compliant SSE frame."""
    seq = event.get("seq")
    event_type = str(event.get("event_type", "message"))
    data_str = json.dumps(event, default=str, ensure_ascii=True)
    lines: list[str] = []
    if seq is not None:
        lines.append(f"id: {seq}")
    lines.append(f"event: {event_type}")
    lines.append(f"data: {data_str}")
    return "\n".join(lines) + "\n\n"


class SSEBus:
    """In-process task event broadcaster paired with AgentTask.steps_log DB persistence."""

    def __init__(self) -> None:
        self._subscribers: dict[str, set[asyncio.Queue[dict[str, Any]]]] = defaultdict(set)
        self._seq_counters: dict[str, int] = defaultdict(int)
        self._locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    async def publish(
        self,
        task_id: uuid.UUID | str,
        event_type: str,
        payload: dict[str, Any] | None = None,
        *,
        session: AsyncSession | None = None,
        commit: bool = True,
    ) -> dict[str, Any]:
        """Persist event to AgentTask.steps_log (if session provided) and push to live queues."""
        task_key = str(task_id)
        async with self._locks[task_key]:
            task_obj: AgentTask | None = None
            if session is not None:
                task_uuid = task_id if isinstance(task_id, uuid.UUID) else uuid.UUID(task_key)
                task_obj = await session.get(AgentTask, task_uuid)
                if task_obj is not None and task_obj.steps_log:
                    max_db_seq = max(int(item.get("seq", 0)) for item in task_obj.steps_log)
                    self._seq_counters[task_key] = max(self._seq_counters[task_key], max_db_seq)

            self._seq_counters[task_key] += 1
            seq = self._seq_counters[task_key]
            safe_payload = redact_dict(payload or {})
            event_entry: dict[str, Any] = {
                "seq": seq,
                "event_type": event_type,
                "timestamp": utcnow().isoformat(),
                "payload": safe_payload,
            }

            if task_obj is not None:
                existing = list(task_obj.steps_log or [])
                existing.append(event_entry)
                task_obj.steps_log = existing
                task_obj.updated_at = utcnow()
                if commit:
                    await session.commit()
                else:
                    await session.flush()

            for queue in list(self._subscribers.get(task_key, ())):
                try:
                    queue.put_nowait(event_entry)
                except asyncio.QueueFull:
                    pass

            return event_entry

    async def get_replay_events(
        self,
        task_id: uuid.UUID | str,
        *,
        after_seq: int = 0,
        session: AsyncSession | None = None,
    ) -> list[dict[str, Any]]:
        """Read persisted steps_log events with seq > after_seq."""
        if session is None:
            return []
        task_uuid = task_id if isinstance(task_id, uuid.UUID) else uuid.UUID(str(task_id))
        task_obj = await session.get(AgentTask, task_uuid)
        if task_obj is None or not task_obj.steps_log:
            return []
        return [
            dict(item)
            for item in task_obj.steps_log
            if int(item.get("seq", 0)) > after_seq
        ]

    async def subscribe(
        self,
        task_id: uuid.UUID | str,
        *,
        after_seq: int = 0,
        session: AsyncSession | None = None,
        heartbeat_seconds: float = 15.0,
        stop_on_terminal: bool = True,
    ) -> AsyncIterator[dict[str, Any]]:
        """Subscribe to task stream, replaying persisted DB events > after_seq first."""
        task_key = str(task_id)
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=512)
        # Register subscriber BEFORE reading DB replay so no events fall through the gap
        self._subscribers[task_key].add(queue)
        last_seq = after_seq

        try:
            replayed = await self.get_replay_events(
                task_id, after_seq=after_seq, session=session
            )
            for item in replayed:
                seq = int(item.get("seq", 0))
                if seq > last_seq:
                    last_seq = seq
                    yield item
                    if stop_on_terminal and item.get("event_type") in TERMINAL_EVENTS:
                        return

            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=heartbeat_seconds)
                except TimeoutError:
                    yield {
                        "seq": None,
                        "event_type": "heartbeat",
                        "timestamp": utcnow().isoformat(),
                        "payload": {"timestamp": utcnow().isoformat()},
                    }
                    continue

                seq = int(event.get("seq") or 0)
                if seq <= last_seq:
                    continue
                last_seq = seq
                yield event
                if stop_on_terminal and event.get("event_type") in TERMINAL_EVENTS:
                    return
        finally:
            self._subscribers[task_key].discard(queue)
            if not self._subscribers[task_key]:
                self._subscribers.pop(task_key, None)


sse_bus = SSEBus()
