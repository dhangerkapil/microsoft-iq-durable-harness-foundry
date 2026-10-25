"""Show a cited email, meeting or Teams message in the app, read with the advisor's own delegated Graph token."""

from __future__ import annotations

import asyncio
import re
from datetime import UTC, datetime, timedelta
from html.parser import HTMLParser
from urllib.parse import quote, urlsplit

import httpx
from fastapi import HTTPException

from .entities import parse_entity_url
from .foundry import upstream_error
from .models import Reference

GRAPH = "https://graph.microsoft.com/v1.0"
OPEN_HOSTS = (
    "outlook.office365.com", "outlook.office.com", "outlook.cloud.microsoft",
    "teams.microsoft.com", "teams.cloud.microsoft",
)
MAX_BODY, MAX_CHAT_TEXT, CHAT_MESSAGES = 20_000, 4_000, 20
TEAMS_NOTE = (
    "If Teams offers to open the desktop app, choose \"Use the web app instead\": the desktop app may be "
    "signed in to a different organization."
)


class _Text(HTMLParser):
    BREAKS = {"br", "p", "div", "li", "tr", "h1", "h2", "h3", "h4", "blockquote"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self.BREAKS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def html_text(value: str) -> str:
    parser = _Text()
    parser.feed(value)
    parser.close()
    return tidy("".join(parser.parts))


def tidy(value: str) -> str:
    lines = [" ".join(line.split()) for line in value.replace("\r", "").split("\n")]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def bounded(value: str, limit: int) -> tuple[str, bool]:
    return (value, False) if len(value) <= limit else (value[:limit].rstrip() + "...", True)


def eastern(moment: datetime) -> str:
    """US Eastern time (the advisor's mailbox time zone), with the post-2007 daylight saving rules."""
    moment = moment.astimezone(UTC)
    march = datetime(moment.year, 3, 8, 7, tzinfo=UTC)
    november = datetime(moment.year, 11, 1, 6, tzinfo=UTC)
    starts = march + timedelta(days=(6 - march.weekday()) % 7)
    ends = november + timedelta(days=(6 - november.weekday()) % 7)
    local = moment + timedelta(hours=-4 if starts <= moment < ends else -5)
    return f"{local:%a %b} {local.day}, {local.year}, {local:%I:%M %p}".replace(", 0", ", ") + " ET"


def utc_time(value: object) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return eastern(datetime.fromisoformat(value.replace("Z", "+00:00")))
    except ValueError:
        return None


def local_time(value: object) -> str | None:
    """A Graph dateTimeTimeZone already converted to Eastern by the Prefer header."""
    if not isinstance(value, dict) or not isinstance(value.get("dateTime"), str):
        return None
    try:
        moment = datetime.fromisoformat(value["dateTime"][:19])
    except ValueError:
        return None
    return f"{moment:%a %b} {moment.day}, {moment.year}, {moment:%I:%M %p}".replace(", 0", ", ") + " ET"


def person(value: object) -> str | None:
    address = value.get("emailAddress") if isinstance(value, dict) else None
    if not isinstance(address, dict):
        return None
    name, email = address.get("name"), address.get("address")
    if isinstance(name, str) and name and isinstance(email, str) and email and name != email:
        return f"{name} <{email}>"
    return name if isinstance(name, str) and name else email if isinstance(email, str) else None


def people(values: object, limit: int = 12) -> str | None:
    if not isinstance(values, list):
        return None
    names = [name for name in (person(value) for value in values) if name]
    if not names:
        return None
    extra = len(names) - limit
    return "; ".join(names[:limit]) + (f"; and {extra} more" if extra > 0 else "")


def open_link(*candidates: object) -> str | None:
    for value in candidates:
        if not isinstance(value, str):
            continue
        parsed = urlsplit(value)
        if (
            parsed.scheme == "https" and parsed.hostname in OPEN_HOSTS and not parsed.username
            and not parsed.password and parsed.port in (None, 443)
            and not any(ord(character) < 32 for character in value)
        ):
            return value
    return None


def fields(*pairs: tuple[str, str | None]) -> list[dict]:
    return [{"label": label, "value": value} for label, value in pairs if value]


async def graph_get(
    client: httpx.AsyncClient, token: str, path: str, params: dict | None = None, prefer: str | None = None,
) -> dict:
    headers = {"Authorization": f"Bearer {token}"}
    if prefer:
        headers["Prefer"] = prefer
    response = await client.get(f"{GRAPH}{path}", headers=headers, params=params)
    if response.status_code in (400, 404):
        # Graph answers 400 for a malformed item ID and 404 for a missing one.
        raise HTTPException(status_code=404, detail="Microsoft Graph could not find the cited item.")
    upstream_error(response)
    payload = response.json()
    if not isinstance(payload, dict):
        raise HTTPException(status_code=502, detail="Graph returned an invalid item.")
    return payload


async def email_view(client: httpx.AsyncClient, token: str, reference: Reference, message_id: str) -> dict:
    item = await graph_get(
        client, token, f"/me/messages/{quote(message_id, safe='')}",
        {"$select": "subject,from,toRecipients,ccRecipients,receivedDateTime,body,webLink,hasAttachments"},
        'outlook.body-content-type="text"',
    )
    body = item.get("body") if isinstance(item.get("body"), dict) else {}
    content = body.get("content") if isinstance(body.get("content"), str) else ""
    text, truncated = bounded(html_text(content) if body.get("contentType") == "html" else tidy(content), MAX_BODY)
    return {
        "kind": "email", "title": item.get("subject") or reference.title,
        "fields": fields(
            ("From", person(item.get("from"))), ("To", people(item.get("toRecipients"))),
            ("Cc", people(item.get("ccRecipients"))), ("Received", utc_time(item.get("receivedDateTime"))),
            ("Attachments", "Yes" if item.get("hasAttachments") is True else None),
        ),
        "body": text, "body_truncated": truncated, "messages": [],
        "open_url": open_link(item.get("webLink"), reference.url), "open_label": "Open in Outlook", "note": None,
    }


async def event_view(client: httpx.AsyncClient, token: str, reference: Reference, event_id: str) -> dict:
    item = await graph_get(
        client, token, f"/me/events/{quote(event_id, safe='')}",
        {"$select": "subject,organizer,attendees,start,end,location,body,webLink,isOnlineMeeting"},
        'outlook.timezone="Eastern Standard Time", outlook.body-content-type="text"',
    )
    body = item.get("body") if isinstance(item.get("body"), dict) else {}
    content = body.get("content") if isinstance(body.get("content"), str) else ""
    text, truncated = bounded(html_text(content) if body.get("contentType") == "html" else tidy(content), MAX_BODY)
    starts, ends = local_time(item.get("start")), local_time(item.get("end"))
    when = f"{starts} to {ends.split(', ')[-1]}" if starts and ends and starts[:10] == ends[:10] else starts
    location = item.get("location") if isinstance(item.get("location"), dict) else {}
    return {
        "kind": "event", "title": item.get("subject") or reference.title,
        "fields": fields(
            ("When", when), ("Organizer", person(item.get("organizer"))),
            ("Attendees", people(item.get("attendees"))),
            ("Location", location.get("displayName") if isinstance(location.get("displayName"), str) else None),
            ("Teams meeting", "Yes" if item.get("isOnlineMeeting") is True else None),
        ),
        "body": text, "body_truncated": truncated, "messages": [],
        "open_url": open_link(item.get("webLink"), reference.url), "open_label": "Open in Outlook calendar",
        "note": None,
    }


def chat_message(value: dict, cited_id: str) -> dict | None:
    if value.get("messageType") not in (None, "message") or value.get("deletedDateTime"):
        return None
    sender = value.get("from") if isinstance(value.get("from"), dict) else {}
    who = next((
        part.get("displayName") for part in (sender.get("user"), sender.get("application"))
        if isinstance(part, dict) and isinstance(part.get("displayName"), str)
    ), "Unknown sender")
    body = value.get("body") if isinstance(value.get("body"), dict) else {}
    content = body.get("content") if isinstance(body.get("content"), str) else ""
    text, _ = bounded(html_text(content) if body.get("contentType") == "html" else tidy(content), MAX_CHAT_TEXT)
    identifier = value.get("id")
    if not isinstance(identifier, str) or not text:
        return None
    return {
        "id": identifier, "sender": who, "sent": utc_time(value.get("createdDateTime")),
        "created": value.get("createdDateTime") if isinstance(value.get("createdDateTime"), str) else "",
        "text": text, "cited": identifier == cited_id,
    }


async def chat_view(
    client: httpx.AsyncClient, token: str, reference: Reference, chat_id: str, message_id: str,
) -> dict:
    chat_path = f"/chats/{quote(chat_id, safe='')}"
    cited, recent, chat = await asyncio.gather(
        graph_get(client, token, f"{chat_path}/messages/{quote(message_id, safe='')}"),
        graph_get(client, token, f"{chat_path}/messages", {"$top": CHAT_MESSAGES}),
        graph_get(client, token, chat_path, {"$expand": "members"}),
    )
    values = recent.get("value") if isinstance(recent.get("value"), list) else []
    thread = {
        message["id"]: message
        for message in (chat_message(value, message_id) for value in [*values, cited] if isinstance(value, dict))
        if message
    }
    ordered = sorted(thread.values(), key=lambda message: message["created"])
    messages = [{key: value for key, value in message.items() if key != "created"} for message in ordered]
    if len(messages) > CHAT_MESSAGES:
        newest = messages[-CHAT_MESSAGES:]
        cited = next((message for message in messages if message["cited"]), None)
        if cited is not None and cited not in newest:
            # Keep the cited message even when the chat has many newer ones.
            newest = [cited, *messages[-(CHAT_MESSAGES - 1):]]
        messages = newest
    members = [
        member.get("displayName") for member in chat.get("members", [])
        if isinstance(member, dict) and isinstance(member.get("displayName"), str)
    ] if isinstance(chat.get("members"), list) else []
    topic = chat.get("topic") if isinstance(chat.get("topic"), str) and chat.get("topic") else None
    cited_message = thread.get(message_id)
    return {
        "kind": "chat", "title": topic or reference.title,
        "fields": fields(
            ("Chat", topic or (", ".join(members[:8]) if members else None)),
            ("Cited message", f"{cited_message['sender']}, {cited_message['sent']}"
             if cited_message and cited_message["sent"] else None),
        ),
        "body": "", "body_truncated": False, "messages": messages,
        "open_url": open_link(reference.url, chat.get("webUrl")), "open_label": "Open in Teams",
        "note": TEAMS_NOTE,
    }


async def source_view(client: httpx.AsyncClient, token: str, reference: Reference) -> dict:
    if not reference.entity_url:
        raise HTTPException(
            status_code=422, detail="This source has no Microsoft 365 item ID. Use Open original.",
        )
    entity = parse_entity_url(reference.entity_url)
    if entity.kind == "email":
        return await email_view(client, token, reference, entity.ids["id"])
    if entity.kind == "event":
        return await event_view(client, token, reference, entity.ids["id"])
    if entity.kind == "chat":
        return await chat_view(client, token, reference, entity.ids["chat"], entity.ids["message"])
    raise HTTPException(status_code=422, detail="Files open in the document preview.")
