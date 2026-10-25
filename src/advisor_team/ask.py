from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable

from agent_framework import Content, FunctionInvocationContext, FunctionMiddleware
from agent_framework.exceptions import ToolExecutionException
from pydantic import BaseModel

from .execution import tool_output_failed

logger = logging.getLogger(__name__)
ASK_STATE_KEY = "workiq_ask_context"
DEFAULT_AGENT_ID = "bizchat-as-gpt-scenario"
# Service-side failures worth one more try in a new conversation (seen live: "The dependency rejected the request",
# errorCode InternalError). A policy, permission or consent denial is never retried.
SERVICE_FAILURES = ("dependency rejected", "internalerror", "could not be completed")
DENIALS = ("polic", "not allowed", "not permitted", "disabled", "blocked", "consent", "forbidden", "unauthorized",
           "access denied")


def service_failure(text: str) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in SERVICE_FAILURES) and not any(marker in lowered for marker in DENIALS)


def result_text(result: object) -> str:
    if isinstance(result, str):
        return result
    if isinstance(result, list):
        return "\n".join(item.text or "" for item in result if isinstance(item, Content))
    return ""


def valid_identifier(value: object) -> bool:
    return (
        isinstance(value, str) and 0 < len(value) <= 512
        and not any(character.isspace() or ord(character) < 32 for character in value)
    )


def conversation_id_from_result(result: object) -> str:
    if isinstance(result, str):
        texts = [result]
    elif isinstance(result, list) and all(isinstance(item, Content) for item in result):
        texts = [item.text for item in result if item.type == "text" and item.text is not None]
    else:
        raise ToolExecutionException("Work IQ Ask returned unsupported conversation metadata.")
    identifiers: set[str] = set()
    for text in texts:
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and "conversationId" in payload:
            identifier = payload["conversationId"]
            if not valid_identifier(identifier):
                raise ToolExecutionException("Work IQ Ask returned an invalid conversation ID.")
            identifiers.add(identifier)
    if len(identifiers) != 1:
        raise ToolExecutionException("Work IQ Ask did not return one unambiguous conversation ID.")
    return identifiers.pop()


class AskSessionContext(FunctionMiddleware):
    """Work IQ Ask continuity: the runtime, never the model, threads each agent's Work IQ conversation through the
    session. The session's first agent keeps its conversation under ASK_STATE_KEY; any other agent the advisor asks
    (for example a Copilot agent found with list_agents) gets its own conversation under its own key."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()

    async def process(
        self, context: FunctionInvocationContext, call_next: Callable[[], Awaitable[None]],
    ) -> None:
        if context.function.name != "workiq___ask":
            await call_next()
            return
        if context.session is None:
            raise ToolExecutionException("Work IQ Ask requires an active conversation session.")
        async with self._lock:
            arguments = (
                context.arguments.model_dump()
                if isinstance(context.arguments, BaseModel) else dict(context.arguments)
            )
            state = context.session.state
            first = state.get(ASK_STATE_KEY)
            if ASK_STATE_KEY in state and not (isinstance(first, dict) and valid_identifier(first.get("agent_id"))):
                raise ToolExecutionException(
                    "Work IQ Ask continuity is unavailable for this session; start a new session.",
                )
            agent_id = arguments.get("agentId")
            if agent_id is None:
                agent_id = first["agent_id"] if first else DEFAULT_AGENT_ID
            if not valid_identifier(agent_id):
                raise ToolExecutionException("Work IQ Ask requires a valid agent ID.")
            key = ASK_STATE_KEY if not first or first["agent_id"] == agent_id else f"{ASK_STATE_KEY}:{agent_id}"
            saved = state.get(key)
            if key in state and (
                not isinstance(saved, dict) or set(saved) != {"agent_id", "conversation_id"}
                or saved.get("agent_id") != agent_id
                or not valid_identifier(saved.get("conversation_id"))
            ):
                raise ToolExecutionException(
                    "Work IQ Ask continuity is unavailable for this session; start a new session.",
                )
            # Only the runtime's saved Work IQ ID may continue this session, never a model-supplied ID.
            arguments["conversationId"] = saved["conversation_id"] if saved else None
            arguments["agentId"] = agent_id
            context.arguments = arguments
            try:
                await call_next()
                retry = tool_output_failed(context.result, skill=False) and service_failure(result_text(context.result))
            except Exception as error:
                if not service_failure(str(error)):
                    raise
                retry = True
            if retry:
                # The runtime, not the model, starts the new conversation; it replaces the saved one if it succeeds.
                logger.warning("Work IQ Ask failed on the service side; asking once more in a new conversation.")
                arguments["conversationId"] = None
                context.arguments = arguments
                saved = None
                await call_next()
            if tool_output_failed(context.result, skill=False):
                logger.warning("Work IQ Ask failed; existing conversation context was retained.")
                return
            if not saved:
                # A successful response without usable metadata must not silently start another thread.
                state[key] = {"agent_id": agent_id, "conversation_id": None}
            conversation_id = conversation_id_from_result(context.result)
            if saved and conversation_id != saved["conversation_id"]:
                raise ToolExecutionException(
                    "Work IQ Ask returned a different conversation ID; existing context was retained.",
                )
            state[key] = {"agent_id": agent_id, "conversation_id": conversation_id}
            logger.info("Work IQ Ask conversation context %s for agent %s.", "reused" if saved else "established",
                        agent_id[:80])
