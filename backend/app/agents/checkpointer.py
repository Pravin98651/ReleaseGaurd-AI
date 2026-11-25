"""Portable async SQLAlchemy LangGraph checkpointer (PostgreSQL + SQLite, zero pickle)."""

from __future__ import annotations

import asyncio
import base64
import json
import random
from collections import defaultdict
from collections.abc import AsyncIterator, Sequence
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import (
    WRITES_IDX_MAP,
    BaseCheckpointSaver,
    ChannelVersions,
    Checkpoint,
    CheckpointMetadata,
    CheckpointTuple,
    get_checkpoint_id,
    get_checkpoint_metadata,
)
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.database import get_session_factory, utcnow
from app.models.agents import LangGraphCheckpoint


def _encode_typed(serde: JsonPlusSerializer, value: Any) -> str:
    """Serialize any LangGraph checkpoint payload into a JSON-safe string without pickle."""
    serde_type, raw_bytes = serde.dumps_typed(value)
    if serde_type == "pickle":
        raise ValueError("Pickle serialization is prohibited in SqlAlchemyCheckpointSaver.")
    if serde_type == "json":
        envelope = {"__lg_serde__": serde_type, "data": raw_bytes.decode("utf-8")}
    else:
        envelope = {
            "__lg_serde__": serde_type,
            "data_b64": base64.b64encode(raw_bytes).decode("ascii"),
        }
    return json.dumps(envelope, ensure_ascii=True)


def _decode_typed(serde: JsonPlusSerializer, raw_json: str) -> Any:
    """Deserialize a JSON-safe LangGraph checkpoint envelope without pickle."""
    if not raw_json:
        return None
    envelope = json.loads(raw_json)
    if not isinstance(envelope, dict) or "__lg_serde__" not in envelope:
        return envelope
    serde_type = str(envelope["__lg_serde__"])
    if serde_type == "pickle":
        raise ValueError("Unsafe pickle checkpoint payload rejected.")
    if serde_type == "json":
        raw_bytes = str(envelope.get("data", "")).encode("utf-8")
    elif "data_b64" in envelope:
        raw_bytes = base64.b64decode(str(envelope["data_b64"]).encode("ascii"))
    else:
        raw_bytes = b""
    return serde.loads_typed((serde_type, raw_bytes))


def _encode_write_entry(
    serde: JsonPlusSerializer,
    *,
    task_id: str,
    write_idx: int,
    channel: str,
    value: Any,
    task_path: str,
) -> dict[str, Any]:
    serde_type, raw_bytes = serde.dumps_typed(value)
    if serde_type == "pickle":
        raise ValueError("Pickle serialization is prohibited for pending writes.")
    return {
        "task_id": task_id,
        "idx": write_idx,
        "channel": channel,
        "serde_type": serde_type,
        "data_b64": base64.b64encode(raw_bytes).decode("ascii"),
        "task_path": task_path,
    }


def _decode_write_entry(
    serde: JsonPlusSerializer,
    entry: dict[str, Any],
) -> tuple[str, str, Any]:
    serde_type = str(entry.get("serde_type", "null"))
    if serde_type == "pickle":
        raise ValueError("Unsafe pickle pending write rejected.")
    raw_bytes = base64.b64decode(str(entry.get("data_b64", "")).encode("ascii"))
    val = serde.loads_typed((serde_type, raw_bytes))
    return (str(entry["task_id"]), str(entry["channel"]), val)


class SqlAlchemyCheckpointSaver(BaseCheckpointSaver[str]):
    """LangGraph BaseCheckpointSaver backed by the `langgraph_checkpoints` SQLAlchemy table."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
    ) -> None:
        super().__init__(serde=JsonPlusSerializer(pickle_fallback=False))
        self._session_factory = session_factory or get_session_factory()
        self._locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    def get_next_version(self, current: str | None, channel: None) -> str:
        """Generate monotonically increasing string channel version IDs."""
        if current is None:
            current_v = 0
        elif isinstance(current, int):
            current_v = current
        else:
            current_v = int(str(current).split(".")[0])
        next_v = current_v + 1
        next_h = random.random()
        return f"{next_v:032}.{next_h:016}"

    def _row_to_tuple(self, row: LangGraphCheckpoint) -> CheckpointTuple:
        checkpoint: Checkpoint = _decode_typed(self.serde, row.checkpoint_json)
        metadata: CheckpointMetadata = _decode_typed(self.serde, row.metadata_json) or {}

        raw_writes: list[dict[str, Any]] = (
            json.loads(row.pending_writes_json) if row.pending_writes_json else []
        )
        raw_writes.sort(key=lambda w: (str(w.get("task_path", "")), str(w["task_id"]), int(w["idx"])))
        pending_writes = [_decode_write_entry(self.serde, w) for w in raw_writes]

        parent_config: RunnableConfig | None = None
        if row.parent_checkpoint_id:
            parent_config = {
                "configurable": {
                    "thread_id": row.thread_id,
                    "checkpoint_ns": row.checkpoint_ns,
                    "checkpoint_id": row.parent_checkpoint_id,
                }
            }

        return CheckpointTuple(
            config={
                "configurable": {
                    "thread_id": row.thread_id,
                    "checkpoint_ns": row.checkpoint_ns,
                    "checkpoint_id": row.checkpoint_id,
                }
            },
            checkpoint=checkpoint,
            metadata=metadata,
            parent_config=parent_config,
            pending_writes=pending_writes,
        )

    async def aget_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
        """Load either a specific checkpoint_id or the latest checkpoint for a thread."""
        configurable = config.get("configurable", {})
        thread_id = str(configurable["thread_id"])
        checkpoint_ns = str(configurable.get("checkpoint_ns", ""))
        checkpoint_id = get_checkpoint_id(config)

        async with self._locks[thread_id], self._session_factory() as session:
            if checkpoint_id:
                row = await session.get(
                    LangGraphCheckpoint, (thread_id, checkpoint_ns, checkpoint_id)
                )
            else:
                stmt = (
                    select(LangGraphCheckpoint)
                    .where(
                        LangGraphCheckpoint.thread_id == thread_id,
                        LangGraphCheckpoint.checkpoint_ns == checkpoint_ns,
                    )
                    .order_by(LangGraphCheckpoint.checkpoint_id.desc())
                    .limit(1)
                )
                row = (await session.execute(stmt)).scalar_one_or_none()

            if row is None:
                return None
            return self._row_to_tuple(row)

    async def alist(
        self,
        config: RunnableConfig | None,
        *,
        filter: dict[str, Any] | None = None,
        before: RunnableConfig | None = None,
        limit: int | None = None,
    ) -> AsyncIterator[CheckpointTuple]:
        """Iterate checkpoints in reverse chronological order matching filters."""
        stmt = select(LangGraphCheckpoint)
        if config is not None:
            configurable = config.get("configurable", {})
            if "thread_id" in configurable:
                stmt = stmt.where(
                    LangGraphCheckpoint.thread_id == str(configurable["thread_id"])
                )
            if "checkpoint_ns" in configurable and configurable["checkpoint_ns"] is not None:
                stmt = stmt.where(
                    LangGraphCheckpoint.checkpoint_ns == str(configurable["checkpoint_ns"])
                )
            if cfg_ckpt_id := get_checkpoint_id(config):
                stmt = stmt.where(LangGraphCheckpoint.checkpoint_id == cfg_ckpt_id)

        if before is not None and (before_id := get_checkpoint_id(before)):
            stmt = stmt.where(LangGraphCheckpoint.checkpoint_id < before_id)

        stmt = stmt.order_by(LangGraphCheckpoint.checkpoint_id.desc())

        async with self._session_factory() as session:
            rows = (await session.execute(stmt)).scalars().all()

        emitted = 0
        for row in rows:
            ckpt_tuple = self._row_to_tuple(row)
            if filter:
                meta = ckpt_tuple.metadata or {}
                if not all(meta.get(k) == v for k, v in filter.items()):
                    continue
            yield ckpt_tuple
            emitted += 1
            if limit is not None and emitted >= limit:
                break

    async def aput(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        """Persist a JSON-safe LangGraph checkpoint into `langgraph_checkpoints`."""
        del new_versions  # channel_values are stored directly inside checkpoint_json
        configurable = config["configurable"]
        thread_id = str(configurable["thread_id"])
        checkpoint_ns = str(configurable.get("checkpoint_ns", ""))
        checkpoint_id = str(checkpoint["id"])
        parent_checkpoint_id = configurable.get("checkpoint_id")

        ckpt_json = _encode_typed(self.serde, dict(checkpoint))
        meta_json = _encode_typed(self.serde, get_checkpoint_metadata(config, metadata))

        async with self._locks[thread_id], self._session_factory() as session:
            existing = await session.get(
                LangGraphCheckpoint, (thread_id, checkpoint_ns, checkpoint_id)
            )
            if existing is not None:
                existing.parent_checkpoint_id = (
                    str(parent_checkpoint_id) if parent_checkpoint_id else None
                )
                existing.checkpoint_json = ckpt_json
                existing.metadata_json = meta_json
            else:
                row = LangGraphCheckpoint(
                    thread_id=thread_id,
                    checkpoint_ns=checkpoint_ns,
                    checkpoint_id=checkpoint_id,
                    parent_checkpoint_id=(
                        str(parent_checkpoint_id) if parent_checkpoint_id else None
                    ),
                    checkpoint_json=ckpt_json,
                    metadata_json=meta_json,
                    pending_writes_json="[]",
                    created_at=utcnow(),
                )
                session.add(row)
            await session.commit()

        return {
            "configurable": {
                "thread_id": thread_id,
                "checkpoint_ns": checkpoint_ns,
                "checkpoint_id": checkpoint_id,
            }
        }

    async def aput_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        """Persist intermediate channel writes/interrupts on a checkpoint."""
        configurable = config["configurable"]
        thread_id = str(configurable["thread_id"])
        checkpoint_ns = str(configurable.get("checkpoint_ns", ""))
        checkpoint_id = str(configurable["checkpoint_id"])

        async with self._locks[thread_id], self._session_factory() as session:
            row = await session.get(
                LangGraphCheckpoint, (thread_id, checkpoint_ns, checkpoint_id)
            )
            if row is None:
                row = LangGraphCheckpoint(
                    thread_id=thread_id,
                    checkpoint_ns=checkpoint_ns,
                    checkpoint_id=checkpoint_id,
                    parent_checkpoint_id=None,
                    checkpoint_json=_encode_typed(self.serde, {}),
                    metadata_json=_encode_typed(self.serde, {}),
                    pending_writes_json="[]",
                    created_at=utcnow(),
                )
                session.add(row)

            existing_writes: list[dict[str, Any]] = (
                json.loads(row.pending_writes_json) if row.pending_writes_json else []
            )
            existing_keys = {(str(w["task_id"]), int(w["idx"])) for w in existing_writes}

            for idx, (channel, value) in enumerate(writes):
                write_idx = WRITES_IDX_MAP.get(channel, idx)
                key = (task_id, write_idx)
                if write_idx >= 0 and key in existing_keys:
                    continue
                if write_idx < 0:
                    existing_writes = [
                        w
                        for w in existing_writes
                        if not (str(w["task_id"]) == task_id and int(w["idx"]) == write_idx)
                    ]
                existing_writes.append(
                    _encode_write_entry(
                        self.serde,
                        task_id=task_id,
                        write_idx=write_idx,
                        channel=channel,
                        value=value,
                        task_path=task_path,
                    )
                )
                existing_keys.add(key)

            row.pending_writes_json = json.dumps(existing_writes, ensure_ascii=True)
            await session.commit()

    async def adelete_thread(self, thread_id: str) -> None:
        """Delete all checkpoints associated with a thread_id."""
        async with self._locks[thread_id], self._session_factory() as session:
            await session.execute(
                delete(LangGraphCheckpoint).where(LangGraphCheckpoint.thread_id == thread_id)
            )
            await session.commit()
