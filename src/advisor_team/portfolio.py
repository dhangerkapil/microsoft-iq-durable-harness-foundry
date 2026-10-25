"""Optional Fabric IQ portfolio data. Work IQ is the primary source; this tool connects to the Fabric-only
toolbox only when the advisor asks for live portfolio data, so a paused Fabric capacity can never block
a Work IQ request."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Annotated

from agent_framework import tool
from agent_framework_foundry_hosting import FoundryToolbox
from azure.core.credentials_async import AsyncTokenCredential

from .advisor import NO_RETRY_MARKERS, FaultSwitch, looks_failed, result_text, tracer, unavailable
from .consent import first_consent_link

logger = logging.getLogger(__name__)
FABRIC_TOOL_PREFIX = "fabric_portfolio___"
PORTFOLIO_TOOL = "query_portfolio_data"
RETRY_DELAY_SECONDS = 2.0
NOT_TRANSIENT = ("capacitynotactive", "capacity is paused", *NO_RETRY_MARKERS)


def transient_toolbox_failure(error: BaseException) -> bool:
    """The Fabric toolbox couldn't start its session (tools/list failed) for a reason other than consent, a paused
    capacity or a permission: worth one more try."""
    text = str(error).lower()
    return "tools/list failed" in text and not any(marker in text for marker in NOT_TRANSIENT)


def fabric_consent_link(error: BaseException) -> str | None:
    """The user's Fabric IQ consent link when the toolbox refused the call for missing consent, else None."""
    return first_consent_link(error)


def consent_required(link: str) -> str:
    # The Fabric toolbox is called inside this tool, so Foundry can't raise its consent request in the app: the
    # agent shows the link instead. Each user consents once per agent.
    return json.dumps({
        "status": "FABRIC_CONSENT_REQUIRED",
        "consent_link": link,
        "instruction": ("Fabric IQ needs this user's one-time consent before it can answer. Show the user the "
                        "complete consent_link character for character as a Markdown link; never shorten, abbreviate or "
                        "elide any part of it, or it will not work. Ask them to open it, sign in, accept, "
                        "and then ask again. Do not retry now. Meanwhile call recall_client_memory and use the saved "
                        "portfolio snapshot, labelling every portfolio figure as stale with its as-of date, or say "
                        "that no snapshot exists."),
    })


def question_argument(function: object) -> str:
    try:
        schema = function.parameters() if callable(getattr(function, "parameters", None)) else {}
    except Exception:
        schema = {}
    properties = list((schema or {}).get("properties", {}))
    return "userQuestion" if not properties or "userQuestion" in properties else properties[0]


def portfolio_tool(
    credential: AsyncTokenCredential, url: str | None, faults: FaultSwitch, *, timeout_seconds: float = 120.0,
):
    @tool(approval_mode="never_require")
    async def query_portfolio_data(
        question: Annotated[str, "One complete question, e.g. 'Michael Scott: NVDA weight, IT sector weight and cash "
                                 "and equivalents on the latest date, with limits'."],
    ) -> str:
        """Optional governed portfolio data from the Fabric IQ Portfolio Analyst: holdings, weights against limits,
        cash, the IPS record and changes between dates. Slow (30-90 seconds). Use only when the advisor asks for live
        portfolio data or a figure is missing from the Microsoft 365 sources."""
        with tracer.start_as_current_span("advisor.fabric.call") as span:
            if await faults.active("fabric"):
                span.set_attribute("advisor.fallback.reason", "demo_fault")
                return unavailable("simulated outage (demo fault switch)")
            if not url:
                return unavailable("Fabric IQ is not configured for this agent")
            # One more try when the toolbox session fails to start; both tries share the one deadline.
            deadline = asyncio.get_running_loop().time() + timeout_seconds
            for attempt in (1, 2):
                try:
                    async with asyncio.timeout_at(deadline):
                        async with FoundryToolbox(credential, url=url, load_prompts=False, timeout=timeout_seconds) as box:
                            function = next((f for f in box.functions if f.name.startswith(FABRIC_TOOL_PREFIX)), None)
                            if function is None:
                                return unavailable("the Portfolio Analyst tool is not in the Fabric toolbox")
                            result = await function.invoke(arguments={question_argument(function): question})
                except TimeoutError:
                    reason = f"no answer within {int(timeout_seconds)} seconds (the Fabric capacity may be paused)"
                except Exception as exc:
                    link = fabric_consent_link(exc)
                    if link:
                        logger.warning("Portfolio Analyst needs the user's Fabric IQ consent.")
                        span.set_attribute("advisor.fallback.reason", "consent_required")
                        return consent_required(link)
                    reason = f"{type(exc).__name__}: {str(exc)[:160]}"
                    if attempt == 1 and transient_toolbox_failure(exc):
                        logger.warning("The Fabric toolbox didn't start (%s); trying once more.", reason)
                        span.set_attribute("advisor.fabric.retried", True)
                        await asyncio.sleep(RETRY_DELAY_SECONDS)
                        continue
                else:
                    if not looks_failed(result):
                        return json.dumps({"source": "Fabric IQ Portfolio Analyst", "answer": result_text(result)[:12_000]})
                    reason = result_text(result)[:200]
                break
            logger.warning("Portfolio Analyst unavailable: %s", reason)
            span.set_attribute("advisor.fallback.reason", reason[:200])
            return unavailable(reason)

    return query_portfolio_data
