"""Foundry IQ: the firm's own knowledge, from the firm library's knowledge base (Azure AI Search agentic retrieval).

Work IQ knows what happened with a client; Foundry IQ knows what the firm requires and offers: policies, procedures,
the approved product shelf, planning guides and the investment committee's house view. The knowledge base plans the
query, searches the library (hybrid text and vector search with semantic ranking) and returns the matching sections,
which the agent cites by document and section. It runs as the Foundry project's managed identity, inside Azure.

The toolbox is connected only when the tool runs, so a knowledge-base outage can never block a Work IQ request.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Annotated, Any

from agent_framework import tool
from agent_framework_foundry_hosting import FoundryToolbox
from azure.core.credentials_async import AsyncTokenCredential

from .advisor import result_text, tracer

logger = logging.getLogger(__name__)
KNOWLEDGE_TOOL = "search_firm_knowledge"
RETRIEVE_SUFFIX = "knowledge_base_retrieve"
SOURCE = "Foundry IQ (Contoso Wealth firm knowledge base)"


def unavailable(reason: str) -> str:
    return json.dumps({
        "status": "FIRM_KNOWLEDGE_UNAVAILABLE",
        "reason": reason,
        "instruction": "Do not retry now. Say the firm library was not checked, and continue with the other sources.",
    })


def shape(raw: str, limit: int = 8) -> list[dict[str, Any]]:
    """Each passage with its document, section and text, as the knowledge base returned it."""
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return [{"text": raw[:2_000]}] if raw else []
    items = data if isinstance(data, list) else next(
        (v for v in data.values() if isinstance(v, list)), []) if isinstance(data, dict) else []
    passages = []
    for item in items[:limit]:
        if not isinstance(item, dict):
            continue
        text = " ".join(str(item.get("content") or item.get("text") or "").split())
        passages.append({k: v for k, v in {
            "ref": item.get("ref_id"), "document": item.get("terms") or item.get("document"),
            "section": item.get("title"), "text": text[:4_000],
        }.items() if v not in (None, "")})
    return passages


def knowledge_tools(credential: AsyncTokenCredential, url: str | None, *, timeout_seconds: float = 60.0) -> list:
    """The Foundry IQ tool, or none when the deployment has no Foundry IQ toolbox."""
    if not url:
        return []

    @tool(name=KNOWLEDGE_TOOL, approval_mode="never_require")
    async def search_firm_knowledge(
        question: Annotated[str, "One complete question in the advisor's words, e.g. 'What must happen before a large "
                                 "wire for a home closing is released?' (under 400 characters)."],
    ) -> str:
        """Foundry IQ: the firm's policies, procedures, approved products, planning guides and house view (Contoso
        Wealth firm library). Use for what the firm requires or offers: concentrated-position reviews, large
        withdrawals and wires, client communication rules, approved ESG funds, 529 plans, the investment
        committee's view. Cite each passage by document code and section."""
        with tracer.start_as_current_span("advisor.foundryiq.retrieve") as span:
            question = " ".join(question.split())[:400]
            try:
                async with asyncio.timeout(timeout_seconds):
                    async with FoundryToolbox(credential, url=url, load_prompts=False, timeout=timeout_seconds) as box:
                        function = next((f for f in box.functions if f.name.endswith(RETRIEVE_SUFFIX)), None)
                        if function is None:
                            return unavailable("the Foundry IQ toolbox has no knowledge_base_retrieve tool")
                        result = await function.invoke(arguments={"query_variants": [question]})
            except TimeoutError:
                reason = f"no answer within {int(timeout_seconds)} seconds"
            except Exception as exc:
                reason = f"{type(exc).__name__}: {str(exc)[:160]}"
            else:
                text = result_text(result)
                if '"iserror": true' not in text[:300].lower():
                    passages = shape(text)
                    span.set_attribute("advisor.foundryiq.passages", len(passages))
                    return json.dumps({
                        "source": SOURCE, "question": question, "passages": passages,
                        "note": ("Firm policy and guidance (fictional demo library). Cite as document code and section, "
                                 "for example 'CP-104 §3'. A passage is the firm's rule, not a client fact."),
                    })
                reason = text[:200]
            span.set_attribute("advisor.foundryiq.failed", reason[:200])
            logger.warning("Foundry IQ unavailable: %s", reason)
            return unavailable(reason)

    return [search_firm_knowledge]
