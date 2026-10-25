"""A bound on stalled model calls.

The service occasionally holds a model request for minutes and then answers normally: on 2026-10-21 two team-lead calls
took 290 s and 217 s against a 3.4 s median, which turned a one-minute review pack into ten. A call that hasn't answered
within the bound is abandoned and sent once more. Streaming calls pass through untouched (the lead streams to the app,
and a half-streamed answer can't be resent).
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Awaitable, Callable

from agent_framework import ChatContext, ChatMiddleware

logger = logging.getLogger(__name__)
DEFAULT_STALL_SECONDS = 120.0


def stall_seconds() -> float:
    try:
        return max(10.0, float(os.environ.get("TEAM_MODEL_STALL_SECONDS", DEFAULT_STALL_SECONDS)))
    except ValueError:
        return DEFAULT_STALL_SECONDS


class ModelStallGuard(ChatMiddleware):
    def __init__(self, name: str, seconds: float | None = None) -> None:
        self.name = name
        self.seconds = seconds if seconds is not None else stall_seconds()

    async def process(self, context: ChatContext, call_next: Callable[[], Awaitable[None]]) -> None:
        if context.stream:
            await call_next()
            return
        try:
            await asyncio.wait_for(call_next(), timeout=self.seconds)
        except TimeoutError:
            logger.warning("%s: no model answer within %.0f s; sending the call once more.", self.name, self.seconds)
            await asyncio.wait_for(call_next(), timeout=self.seconds)
