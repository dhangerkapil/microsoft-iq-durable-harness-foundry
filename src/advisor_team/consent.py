"""Foundry toolbox OAuth consent: the per-user consent links in a toolbox's CONSENT_REQUIRED error."""

from __future__ import annotations

import json
from urllib.parse import urlsplit

from agent_framework.exceptions import AgentFrameworkException
from mcp.shared.exceptions import McpError


def consent_links(error: AgentFrameworkException) -> list[str]:
    mcp_error = next((arg for arg in error.args if isinstance(arg, McpError)), None)
    if mcp_error is None or mcp_error.error.code != -32006:
        return []
    start = mcp_error.error.message.find("{")
    if start == -1:
        raise RuntimeError("Foundry returned an OAuth consent error without structured details.") from error
    details = json.loads(mcp_error.error.message[start:])
    if not isinstance(details, dict) or not isinstance(details.get("errors"), list):
        raise RuntimeError("Foundry returned invalid OAuth consent details.") from error
    links: list[str] = []
    for item in details["errors"]:
        if not isinstance(item, dict) or not isinstance(item.get("error"), dict):
            raise RuntimeError("Foundry returned invalid OAuth consent details.") from error
        if item["error"].get("code") != "CONSENT_REQUIRED":
            continue
        link = item["error"].get("message")
        if not isinstance(link, str) or any(char.isspace() or ord(char) < 32 for char in link):
            raise RuntimeError("Foundry returned an invalid OAuth consent link.") from error
        parsed = urlsplit(link)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise RuntimeError("Foundry returned an invalid OAuth consent link.") from error
        links.append(link)
    if not links:
        raise RuntimeError("Foundry requires OAuth consent but did not provide a consent link.") from error
    return list(dict.fromkeys(links))


def first_consent_link(error: BaseException) -> str | None:
    """The user's consent link when a toolbox refused a call for missing OAuth consent, else None."""
    if not isinstance(error, AgentFrameworkException):
        return None
    try:
        links = consent_links(error)
    except RuntimeError:
        return None
    return links[0] if links else None
