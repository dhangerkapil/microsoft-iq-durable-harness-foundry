from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime
from typing import Any

from agent_framework import HistoryProvider, Message
from agent_framework._sessions import filter_new_messages
from agent_framework_azure_cosmos import CosmosHistoryProvider

from .advisor import PROCESS
from .naming import store_name

logger = logging.getLogger(__name__)
HISTORY_STORE_NAME = store_name("conversations")
CHUNK_BYTES = 700_000  # state-store items hold at most 1 MB of JSON


class StateStoreHistoryProvider(HistoryProvider):
    """Conversation history in the Foundry durable state store (user-isolated store, chunked items).

    Each save appends one or more chunk items and rewrites a small index item, so history survives
    container restarts and idle eviction without any database the agent must reach over the network.
    """

    def __init__(self, store_factory: Callable[[], Awaitable[Any]], *, scope: Sequence[str]) -> None:
        super().__init__("advisor_history")
        if not all(scope):
            raise ValueError("State-store history requires a complete trusted caller scope.")
        self._store_factory = store_factory
        self._scope = list(scope)
        self._cache: dict[str, tuple[list[Message], int]] = {}

    def key(self, session_id: str | None) -> str:
        if not session_id:
            raise ValueError("A conversation session ID is required for history.")
        identity = json.dumps([*self._scope, session_id], separators=(",", ":"))
        return "h-" + hashlib.sha256(identity.encode()).hexdigest()[:48]

    async def _load(self, key: str) -> tuple[list[Message], int]:
        if key in self._cache:
            return self._cache[key]
        store = await self._store_factory()
        index = await store.get_item(f"{key}--index")
        chunks = int(index.value.get("chunks", 0)) if index is not None else 0
        items = await asyncio.gather(*(store.get_item(f"{key}--{n:04d}") for n in range(1, chunks + 1)))
        messages: list[Message] = []
        for item in items:
            for payload in (item.value.get("messages", []) if item is not None else []):
                try:
                    messages.append(Message.from_dict(payload))
                except ValueError:
                    logger.warning("Skipping an unreadable history message.")
        self._cache[key] = (messages, chunks)
        return messages, chunks

    async def get_messages(
        self, session_id: str | None, *, state: dict[str, Any] | None = None, **kwargs: Any,
    ) -> list[Message]:
        messages, _ = await self._load(self.key(session_id))
        return list(messages)

    async def save_messages(
        self, session_id: str | None, messages: Sequence[Message],
        *, state: dict[str, Any] | None = None, **kwargs: Any,
    ) -> None:
        if not messages:
            return
        key = self.key(session_id)
        existing, chunks = await self._load(key)
        new_messages = filter_new_messages(existing, messages)
        if not new_messages:
            return
        store = await self._store_factory()
        batch: list[dict] = []
        size = 0
        for message in new_messages:
            payload = message.to_dict()
            payload_size = len(json.dumps(payload, default=str))
            if batch and size + payload_size > CHUNK_BYTES:
                chunks += 1
                await store.set_item(f"{key}--{chunks:04d}", {"messages": batch}, tags={"kind": "history"})
                batch, size = [], 0
            batch.append(payload)
            size += payload_size
        if batch:
            chunks += 1
            await store.set_item(f"{key}--{chunks:04d}", {"messages": batch}, tags={"kind": "history"})
        await store.set_item(f"{key}--index", {
            "chunks": chunks, "updated_at": datetime.now(UTC).isoformat(), "written_by": PROCESS["id"],
        }, tags={"kind": "history-index"})
        self._cache[key] = ([*existing, *new_messages], chunks)


class ScopedCosmosHistoryProvider(HistoryProvider):
    def __init__(
        self, provider: CosmosHistoryProvider, *, tenant_id: str,
        project_endpoint: str, user_id: str, platform_session_id: str,
    ) -> None:
        super().__init__("workplace_history")
        if not all((tenant_id, project_endpoint, user_id, platform_session_id)):
            raise ValueError("Cosmos history requires a complete trusted caller scope.")
        self._provider = provider
        self._scope = [tenant_id, project_endpoint, user_id, platform_session_id]

    def partition(self, session_id: str | None) -> str:
        if not session_id:
            raise ValueError("A conversation session ID is required for Cosmos history.")
        identity = json.dumps([*self._scope, session_id], separators=(",", ":"))
        return hashlib.sha256(identity.encode()).hexdigest()

    async def get_messages(
        self, session_id: str | None, *, state: dict[str, Any] | None = None, **kwargs: Any,
    ) -> list[Message]:
        return await self._provider.get_messages(self.partition(session_id))

    async def save_messages(
        self, session_id: str | None, messages: Sequence[Message],
        *, state: dict[str, Any] | None = None, **kwargs: Any,
    ) -> None:
        await self._provider.save_messages(self.partition(session_id), messages)
