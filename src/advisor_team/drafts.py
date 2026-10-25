"""Approval-gated drafting: the harness's only write path.

The model can only propose a draft. The advisor approves or rejects each one in the app (Microsoft Agent
Framework function approval), and only then does Work IQ create it, as the advisor, in Outlook Drafts.
Nothing is ever sent. The Work IQ tenant policy still decides whether the create is allowed.
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Annotated, Any

from agent_framework import tool
from azure.core.credentials_async import AsyncTokenCredential
from mcp.types import CallToolResult

from .advisor import annotate
from .workiq_tools import ToolboxWorkIQClient, classify, classify_failure

logger = logging.getLogger(__name__)
DRAFT_TOOL = "draft_follow_up_email"
EMAIL_ADDRESS = re.compile(r"^[A-Za-z0-9._%+'-]{1,64}@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,24}$")
MAX_SUBJECT, MAX_BODY = 150, 4000


def validate_draft(to: str, subject: str, body: str) -> tuple[str, str, str]:
    to, subject, body = to.strip(), " ".join(subject.split()), body.strip()
    if not EMAIL_ADDRESS.fullmatch(to):
        raise ValueError("Provide one valid recipient email address.")
    if not subject or len(subject) > MAX_SUBJECT:
        raise ValueError(f"The subject must be 1-{MAX_SUBJECT} characters.")
    if not body or len(body) > MAX_BODY:
        raise ValueError(f"The body must be 1-{MAX_BODY} characters.")
    return to, subject, body


def created_draft(raw: str) -> dict[str, Any] | None:
    """Return the created message (id, webLink) when Work IQ reports success."""
    result = CallToolResult.model_validate_json(raw)
    if result.isError:
        return None
    payload: Any = result.structuredContent
    if payload is None:
        texts = " ".join(block.text for block in result.content if block.type == "text")
        try:
            payload = json.loads(texts)
        except (json.JSONDecodeError, TypeError):
            return None
    entries = payload.get("results") if isinstance(payload, dict) and isinstance(payload.get("results"), list) else [payload]
    first = entries[0] if entries and isinstance(entries[0], dict) else {}
    data = first.get("data")
    status = first.get("statusCode")
    if isinstance(data, dict) and data.get("id") and (status is None or 200 <= int(status) < 300):
        return data
    return None


def draft_status(decision: str) -> str:
    if "policy" in decision:
        return "denied_by_work_iq_policy"
    return "denied" if decision.startswith("denied") else "failed"


def draft_tool(credential: AsyncTokenCredential, toolbox_url: str):
    @tool(approval_mode="always_require")
    async def draft_follow_up_email(
        to: Annotated[str, "Recipient address, e.g. the client's address from his latest email."],
        subject: Annotated[str, "Subject line, at most 150 characters."],
        body: Annotated[str, "Plain-text body: short and factual; no investment, tax or legal advice."],
    ) -> str:
        """Create a DRAFT email in the advisor's Outlook Drafts folder through Work IQ. It never sends. The app asks
        the advisor to approve or reject the draft first; only an approved draft is created."""
        to, subject, body = validate_draft(to, subject, body)
        message = {
            "subject": subject,
            "body": {"contentType": "Text", "content": body},
            "toRecipients": [{"emailAddress": {"address": to}}],
        }
        started = time.perf_counter()
        async with ToolboxWorkIQClient(credential, toolbox_url, allowed=("create_entity",)) as client:
            try:
                raw = await client.call_tool("create_entity", parentUrl="/me/messages", jsonBody=json.dumps(message))
            except Exception as exc:  # A policy denial can surface as an MCP error.
                status, decision, detail = classify_failure(exc)
                annotate(**{"draft.status": decision})
                return json.dumps({
                    "status": draft_status(decision), "status_code": status, "decision": decision,
                    "detail": detail[:400],
                    "instruction": "Report this to the advisor. Do not retry or try another way to write.",
                })
        elapsed = int((time.perf_counter() - started) * 1000)
        draft = created_draft(raw)
        if draft is not None:
            annotate(**{"draft.status": "created"})
            return json.dumps({
                "status": "draft_created",
                "to": to, "subject": subject,
                "web_link": draft.get("webLink"),
                "entity_url": f"/me/messages/{draft['id']}",
                "elapsed_ms": elapsed,
                "note": "Saved in the advisor's Outlook Drafts folder. Not sent.",
            })
        status, decision, detail = classify(raw)
        annotate(**{"draft.status": decision})
        return json.dumps({
            "status": draft_status(decision),
            "status_code": status, "decision": decision, "detail": detail,
            "instruction": "Report this to the advisor. Do not retry or try another way to write.",
        })

    return draft_follow_up_email
