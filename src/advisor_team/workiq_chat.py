"""Work IQ Chat over A2A: the agent hands a whole question to Work IQ's own agent as a peer, over the Agent2Agent
protocol, and gets back a finished, Copilot-quality answer with its sources (an A2A task and its "Answer" artifact).

MCP gives this agent Work IQ's tools (retrieve, fetch, ask, discovery, create) to compose step by step; A2A delegates
the whole task to another agent. Through the Foundry toolbox each A2A message starts a new task (the toolbox does not
forward a context ID), so a follow-up goes through workiq___ask, which keeps one conversation per agent.

The toolbox is connected only when the tool runs, so a missing consent or an outage never blocks the agent; a missing
consent comes back as a link the app shows as an Authorize button.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Annotated, Any

from agent_framework import tool
from agent_framework_foundry_hosting import FoundryToolbox
from azure.core.credentials_async import AsyncTokenCredential

from .advisor import result_text, tracer
from .consent import first_consent_link

logger = logging.getLogger(__name__)
CHAT_TOOL = "delegate_to_work_iq"
SEND_SUFFIX = "SendMessage"
CONSENT_STATUS = "WORKIQ_CHAT_CONSENT_REQUIRED"
SOURCE = "Work IQ Chat over A2A (Copilot reasoning over the advisor's Microsoft 365 data, as the advisor)"


def unavailable(reason: str) -> str:
    return json.dumps({
        "status": "WORKIQ_CHAT_UNAVAILABLE",
        "reason": reason,
        "instruction": "Do not retry now. Say Work IQ Chat could not answer, and use the Work IQ tools instead.",
    })


def consent_required(link: str) -> str:
    return json.dumps({
        "status": CONSENT_STATUS,
        "consent_link": link,
        "instruction": ("Work IQ Chat (A2A) needs this user's one-time consent. Show the complete consent_link "
                        "character for character as a Markdown link; never shorten it. Ask them to open it, sign in, "
                        "accept, and ask again. Do not retry now; meanwhile answer with the Work IQ tools."),
    })


def part_text(parts: object) -> str:
    """The text of an A2A message's or artifact's parts, once: the connector can deliver the streamed answer and
    the final answer together, which repeats the text."""
    texts = [part["text"] for part in parts if isinstance(part, dict) and part.get("kind") == "text"
             and isinstance(part.get("text"), str)] if isinstance(parts, list) else []
    text = "".join(texts)
    for candidate in (text, text.strip()):
        half = len(candidate) // 2
        if candidate and len(candidate) % 2 == 0 and candidate[:half] == candidate[half:]:
            return candidate[:half].strip()
    text = text.strip()
    head = text[:min(200, max(40, len(text) // 3))]
    repeat = text.find(head, 1) if len(head) >= 40 else -1
    if repeat > 0 and text[repeat:].strip() == text[:repeat].strip():
        return text[:repeat].strip()
    return text


def shape_task(raw: str) -> dict[str, Any]:
    """The A2A task's ID, context, state and answer."""
    try:
        data: Any = json.loads(raw)
    except (TypeError, ValueError):
        return {"answer": part_text([{"kind": "text", "text": raw}])[:12_000]} if raw else {}
    if isinstance(data, list):
        data = next((item for item in data if isinstance(item, dict)), {})
    if not isinstance(data, dict):
        return {}
    status = data.get("status") if isinstance(data.get("status"), dict) else {}
    answers = [part_text(artifact.get("parts")) for artifact in data.get("artifacts") or []
               if isinstance(artifact, dict)]
    if not any(answers):
        message = status.get("message") if isinstance(status.get("message"), dict) else data
        answers = [part_text(message.get("parts"))]
    return {key: value for key, value in {
        "task_id": data.get("id") or data.get("taskId"),
        "context_id": data.get("contextId"),
        "state": status.get("state") or data.get("kind"),
        "answer": "\n\n".join(answer for answer in answers if answer)[:12_000],
    }.items() if value}


def chat_tools(credential: AsyncTokenCredential, url: str | None, *, timeout_seconds: float = 180.0) -> list:
    """The Work IQ Chat (A2A) tool, or none when the deployment has no Work IQ Chat toolbox."""
    if not url:
        return []

    @tool(name=CHAT_TOOL, approval_mode="never_require")
    async def delegate_to_work_iq(
        task: Annotated[str, "The whole question for Work IQ, self-contained and in the advisor's words, e.g. 'What "
                             "did Michael Scott ask me for in his recent emails, and what is still open?' (under "
                             "2,000 characters)."],
    ) -> str:
        """Work IQ Chat over A2A: hand a whole question to Work IQ's own agent (Copilot reasoning over the advisor's
        email, meetings, chats and files, as the advisor) and get back its finished answer with links. Use when the
        advisor asks to delegate to Work IQ or Copilot, or for a broad question Work IQ can answer end to end. Each
        call is a new A2A task, so make the task self-contained."""
        with tracer.start_as_current_span("advisor.workiq.a2a") as span:
            task = " ".join(task.split())[:2_000]
            started = time.monotonic()
            try:
                async with asyncio.timeout(timeout_seconds):
                    async with FoundryToolbox(credential, url=url, load_prompts=False, timeout=timeout_seconds) as box:
                        function = next((f for f in box.functions if f.name.endswith(SEND_SUFFIX)), None)
                        if function is None:
                            return unavailable("the Work IQ Chat toolbox has no SendMessage tool")
                        # A2A message parts are polymorphic and Work IQ reads "kind" first.
                        result = await function.invoke(
                            arguments={"message": {"parts": [{"kind": "text", "text": task}]}})
            except TimeoutError:
                reason = f"no answer within {int(timeout_seconds)} seconds"
            except Exception as exc:
                link = first_consent_link(exc)
                if link:
                    logger.warning("Work IQ Chat needs the user's consent.")
                    span.set_attribute("advisor.workiq.a2a.consent_required", True)
                    return consent_required(link)
                reason = f"{type(exc).__name__}: {str(exc)[:160]}"
            else:
                text = result_text(result)
                shaped = shape_task(text) if '"iserror": true' not in text[:300].lower() else {}
                if shaped.get("answer"):
                    elapsed = int((time.monotonic() - started) * 1000)
                    span.set_attribute("advisor.workiq.a2a.state", str(shaped.get("state", "")))
                    return json.dumps({
                        "status": "completed" if shaped.get("state") in (None, "completed", "task") else shaped["state"],
                        "protocol": "A2A", "source": SOURCE, **shaped, "duration_ms": elapsed,
                        "note": ("Work IQ's own answer: keep its links, say it came from Work IQ over A2A, and verify "
                                 "any figure you rely on with the other sources."),
                    })
                reason = text[:200] or "Work IQ returned no answer"
            span.set_attribute("advisor.workiq.a2a.failed", reason[:200])
            logger.warning("Work IQ Chat unavailable: %s", reason)
            return unavailable(reason)

    return [delegate_to_work_iq]
