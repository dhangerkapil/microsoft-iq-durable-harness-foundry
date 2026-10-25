from __future__ import annotations

import asyncio
import logging
import os
from contextlib import AsyncExitStack
from copy import deepcopy
from typing import Any, Literal

from agent_framework import Content
from agent_framework.exceptions import ToolExecutionException
from agent_framework_foundry_hosting import FoundryToolbox
from azure.core.credentials_async import AsyncTokenCredential

from .execution import tool_output_failed
from .progress import SourceSearchProgress

logger = logging.getLogger(__name__)


def own_session_prefixes() -> tuple[str, ...]:
    # Slow sources get their own toolbox session so parallel calls really run in parallel.
    raw = os.environ.get("ADVISOR_OWN_SESSION_PREFIXES", "fabric_portfolio___,work-iq___,workiq___")
    return tuple(prefix.strip() for prefix in raw.split(",") if prefix.strip())


class SessionUnavailable(Exception):
    """A separate toolbox session could not be opened; the tool itself was not called."""


class ParallelWorkIQToolbox(FoundryToolbox):
    connect_attempts = 3
    connect_backoff_seconds = 2.0

    def __init__(
        self, credential: AsyncTokenCredential, *, url: str, progress: SourceSearchProgress | None = None,
        timeout: float | None = None, allowed_tools: list[str] | None = None,
    ) -> None:
        options = {} if timeout is None else {"timeout": timeout}
        # allowed_tools=[] keeps the connection (and its consent check) but exposes no functions.
        extra = {} if allowed_tools is None else {"allowed_tools": allowed_tools}
        super().__init__(credential, url=url, load_prompts=False, **options, **extra)
        self.retrieval_credential = credential
        self.retrieval_url = url
        self.progress = progress
        self.retrieval_options = options

    async def connect(self, *, reset: bool = False) -> None:
        # Connection resolution can fail transiently (seen on the first request after an agent
        # rollout); a failed tools/list otherwise fails the whole request before any tool runs.
        for attempt in range(1, self.connect_attempts + 1):
            try:
                await super().connect(reset=reset or attempt > 1)
                return
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                message = str(exc)
                transient = "Error resolving connection" in message and "consent" not in message.lower()
                if attempt == self.connect_attempts or not transient:
                    raise
                logger.warning("Toolbox connection resolution failed (attempt %d); retrying.", attempt)
                await asyncio.sleep(self.connect_backoff_seconds * attempt)

    async def call_in_own_session(self, tool_name: str, arguments: dict[str, Any]) -> str | list[Content]:
        # One toolbox session runs its tool calls one at a time behind a caller-header lock, so a slow
        # source (the Fabric data agent) gets its own session and runs concurrently with Work IQ.
        async with AsyncExitStack() as stack:
            try:
                source = await stack.enter_async_context(FoundryToolbox(
                    self.retrieval_credential, url=self.retrieval_url, load_prompts=False, **self.retrieval_options,
                ))
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                raise SessionUnavailable(str(exc)[:300]) from exc
            function = next((tool for tool in source.functions if tool.name == tool_name), None)
            if function is None:
                raise SessionUnavailable(f"{tool_name} is unavailable in a separate toolbox session.")
            return await function.invoke(arguments=arguments)

    async def call_tool(self, tool_name: str, **kwargs: Any) -> str | list[Content]:
        capabilities = kwargs.get("capabilities")
        if not (
            tool_name == "workiq___retrieve"
            and isinstance(capabilities, list) and len(capabilities) == 2
            and all(isinstance(capability, dict) for capability in capabilities)
            and {capability.get("name") for capability in capabilities} == {"OneDriveAndSharePoint", "Email"}
        ):
            if tool_name.startswith(own_session_prefixes()):
                try:
                    return await self.call_in_own_session(tool_name, dict(kwargs))
                except SessionUnavailable:
                    # Only a session that never reached the tool falls back; a tool error or a policy denial
                    # is returned as is and never retried.
                    logger.warning("Separate session for %s unavailable; using the shared toolbox session.",
                                   tool_name, exc_info=True)
            return await super().call_tool(tool_name, **kwargs)

        async def search(capability: dict[str, Any]) -> list[Content]:
            source_name = capability["name"]
            if source_name not in ("Email", "OneDriveAndSharePoint"):
                raise ValueError("Unsupported parallel search source.")
            search = self.progress.start(source_name) if self.progress else None
            status: Literal["completed", "failed", "cancelled"] = "failed"
            arguments = deepcopy(kwargs)
            arguments["capabilities"] = [deepcopy(capability)]
            try:
                # Separate SDK sessions retain caller-header isolation without serializing the searches.
                async with FoundryToolbox(
                    self.retrieval_credential, url=self.retrieval_url, load_prompts=False, **self.retrieval_options,
                ) as source:
                    function = next((tool for tool in source.functions if tool.name == tool_name), None)
                    if function is None:
                        raise ToolExecutionException(
                            "Work IQ retrieval is unavailable for this source search.",
                        )
                    result = await function.invoke(arguments=arguments)
                if not isinstance(result, list) or any(
                    not isinstance(content, Content) for content in result
                ):
                    raise ToolExecutionException("Work IQ returned unsupported retrieval content.")
                status = "failed" if tool_output_failed(result, skill=False) else "completed"
            except asyncio.CancelledError:
                status = "cancelled"
                raise
            finally:
                if self.progress:
                    self.progress.finish(search, status)
            return [
                Content.from_text(
                    f"Search source: {capability['name']}. Citation IDs belong only to this source section.",
                ),
                *result,
            ]

        tasks = [asyncio.create_task(search(capability)) for capability in capabilities]
        try:
            results = await asyncio.gather(*tasks)
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        return [content for result in results for content in result]
