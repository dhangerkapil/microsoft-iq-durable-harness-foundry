"""Add-on O2: Microsoft Purview data security on the lead's turns (agent-framework-purview). TEAM_PURVIEW=on.

Each prompt and each final answer is evaluated against the tenant's Purview policies for AI apps (DLP, audit) as the
signed-in user's activity. The agent's own identity calls Microsoft Graph with application permissions
(ProtectionScopes.Compute.All, Content.Process.All, ContentActivity.Write; Terraform's enable_purview grants them), and
every input message carries the user's Entra object ID from the platform. Purview errors are logged, never shown, so
Purview can't take the agent down. scripts/Set-PurviewDlpForAgent.ps1 creates the DLP policy the demo uses: a prompt
with a U.S. Social Security number is blocked.

A blocked prompt never reaches the agents: PurviewGate answers it with the policy message, as the assistant, in the
shape the caller asked for (the hosted agent streams, and Purview's own middleware ends a streamed run with an error
instead). The middleware evaluates the whole answer before releasing it, so with Purview on the app shows the answer
when it is complete rather than streaming it.
"""

from __future__ import annotations

import logging
import os
import re
from collections.abc import Awaitable, Callable

from agent_framework import AgentContext, AgentMiddleware, AgentResponse, Message, MiddlewareTermination, ResponseStream
from azure.ai.agentserver.core import get_request_context

from .solo import flag

logger = logging.getLogger(__name__)
GUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
APP_NAME = "Investment Advisor Team"
BLOCKED_PROMPT = ("**Blocked by Microsoft Purview.** Your organization's data loss prevention (DLP) policy doesn't allow "
                  "this prompt, because it contains sensitive information. No agent saw it.")
BLOCKED_RESPONSE = ("**Withheld by Microsoft Purview.** Your organization's data loss prevention (DLP) policy doesn't "
                    "allow this answer.")


class AdvisorUserStamp(AgentMiddleware):
    """Marks the run's input messages with the signed-in advisor's Entra object ID, which Purview needs when the agent
    calls Graph as itself. Without a valid ID, Purview skips the evaluation (logged)."""

    async def process(self, context: AgentContext, call_next: Callable[[], Awaitable[None]]) -> None:
        user = (get_request_context().user_id or "").strip()
        if GUID.match(user):
            for message in context.messages:
                message.additional_properties["user_id"] = user
        elif user:
            logger.info("Purview: the platform user ID is not an object ID; this turn is not evaluated.")
        await call_next()


class PurviewGate(AgentMiddleware):
    """Runs Purview's policy middleware. When it blocks the prompt (it ends the run before the agents start), answers
    with the policy message as the assistant: a stream for a streamed run, a response otherwise. A termination raised
    after the run started belongs to another middleware and is passed on."""

    def __init__(self, inner: AgentMiddleware) -> None:
        self.inner = inner

    async def process(self, context: AgentContext, call_next: Callable[[], Awaitable[None]]) -> None:
        started = False

        async def run() -> None:
            nonlocal started
            started = True
            await call_next()

        try:
            await self.inner.process(context, run)
        except MiddlewareTermination:
            if started:
                raise
            logger.warning("Purview: a DLP policy blocked the prompt; the agents did not run.")
            answer = AgentResponse(messages=[Message(role="assistant", contents=[BLOCKED_PROMPT])])
            context.result = as_stream(answer) if getattr(context, "stream", False) else answer


def as_stream(answer: AgentResponse) -> ResponseStream:
    """A finished response as a stream, for a caller that asked to stream."""

    async def updates():
        for update in AgentResponse.to_updates(answer):
            yield update

    return ResponseStream(updates(), finalizer=lambda _: answer)


def purview_middleware(credential) -> list:
    if not flag("TEAM_PURVIEW", False):
        return []
    from agent_framework_purview import PurviewPolicyMiddleware, PurviewSettings

    settings = PurviewSettings(app_name=APP_NAME, tenant_id=os.environ.get("AZURE_TENANT_ID"),
                               ignore_exceptions=True, ignore_payment_required=True,
                               blocked_prompt_message=BLOCKED_PROMPT, blocked_response_message=BLOCKED_RESPONSE)
    return [AdvisorUserStamp(), PurviewGate(PurviewPolicyMiddleware(credential, settings))]
