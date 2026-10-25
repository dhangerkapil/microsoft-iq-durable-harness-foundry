"""Ground cited Microsoft 365 items in what Work IQ actually returned, and repair IDs the model mis-copied.

Language models occasionally drop or change a character when they copy a 150-character Outlook ID into the
references block, and an Outlook ID repeats one 22-character segment, so a model can also jump from the first copy to
the second and drop the 32 characters between them. Every real ID is in the conversation's tool output, so a cited ID
that is not there gets the one ID it clearly matches: the closest one, or the one it equals apart from a single run of
dropped or changed characters, ending included. Failing that, an email or meeting cited by its exact title gets the one
item with that title in the conversation's Graph reads. Use this only after Microsoft Graph can't find the cited item,
with the whole conversation's tool output: sibling Outlook IDs differ by a single character, so repairing a valid ID
against partial evidence could pick the wrong item.

With the team, the specialists read Work IQ inside the lead's tool calls, and the conversation keeps only the first
4,000 characters of each of their outputs: partial evidence. So the agent repairs a specialist's references when it
records them, against the specialists' full tool output (activity.py), and here the references the specialists wrote
count as evidence: a team source that still isn't found is left as cited ("Use Open original"), never matched to a
sibling item in the clipped copies.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Iterable, Iterator, Sequence
from difflib import SequenceMatcher
from urllib.parse import unquote

from .entities import parse_entity_url
from .models import Reference

OUTLOOK_ID = re.compile(r"A[AQ]Mk[A-Za-z0-9\-_+=%]{16,700}")
CHAT_MESSAGE = re.compile(r"/chats/(19(?::|%3[Aa])[^/\s\"'\\]{1,300})/messages/(\d{1,32})")
DRIVE_ITEM = re.compile(r"/drives/([A-Za-z0-9!\-_]{1,256})/items/([A-Za-z0-9!\-_]{1,256})")
ESCAPES = {"\\u003d": "=", "\\u003D": "=", "\\u002b": "+", "\\u002B": "+"}
MIN_SIMILARITY = 0.97
# Sibling Outlook IDs can differ by a single character, so require a clear winner rather than a unique match.
MIN_MARGIN = 0.002
MAX_SCAN = 2_000_000
# A spliced ID keeps the item's own ending (the last 12 characters differ between sibling items) and loses or changes
# at most one run of characters; the best fit must beat the next by a clear margin.
MIN_TAIL, MAX_SPLICE, MIN_LEAD = 12, 64, 8
EVENT_FIELDS = ("start", "end", "organizer", "isOnlineMeeting")
EMAIL_FIELDS = ("receivedDateTime", "sentDateTime", "from")


def output_text(output: object) -> str:
    text = output if isinstance(output, str) else json.dumps(output)
    text = text[:MAX_SCAN]
    for escape, character in ESCAPES.items():
        text = text.replace(escape, character)
    return text


def closest(value: str, candidates: Iterable[str]) -> str | None:
    """The candidate clearly closest to ``value``; None when none is close or two are equally close."""
    scored = sorted(
        ((SequenceMatcher(None, value, candidate, autojunk=False).ratio(), candidate) for candidate in set(candidates)),
        reverse=True,
    )
    if not scored or scored[0][0] < MIN_SIMILARITY:
        return None
    if len(scored) > 1 and scored[0][0] - scored[1][0] < MIN_MARGIN:
        return None
    return scored[0][1]


def spliced(value: str, candidates: Iterable[str]) -> str | None:
    """The candidate ``value`` equals apart from one run of dropped, added or changed characters, keeping the
    candidate's own ending; None when none fits or two fit about equally well."""
    scored = []
    for candidate in set(candidates):
        head = len(os.path.commonprefix([value, candidate]))
        tail = len(os.path.commonprefix([value[::-1], candidate[::-1]]))
        # A dropped run between two copies of the same segment lets head and tail overlap: cap the match at the ID.
        matched = min(head + tail, len(value), len(candidate))
        if tail >= MIN_TAIL and max(len(value), len(candidate)) - matched <= MAX_SPLICE:
            scored.append((matched, candidate))
    scored.sort(reverse=True)
    if not scored or (len(scored) > 1 and scored[0][0] - scored[1][0] < MIN_LEAD):
        return None
    return scored[0][1]


def graph_id(value: str) -> str:
    """Outlook item IDs in OWA links use '+' where Microsoft Graph IDs use '_'."""
    return unquote(value).replace("+", "_")


def title_key(value: str) -> str:
    return " ".join(value.split()).strip("\"'“”‘’").casefold()


def json_objects(value: object, depth: int = 0) -> Iterator[dict]:
    """Every JSON object in a tool output, including JSON text nested in strings (Work IQ's timing envelope)."""
    if depth > 8:
        return
    if isinstance(value, str):
        text = value.strip()
        if text[:1] in ("{", "[") and len(text) <= MAX_SCAN:
            try:
                decoded = json.loads(text)
            except ValueError:
                return
            yield from json_objects(decoded, depth + 1)
    elif isinstance(value, dict):
        yield value
        for item in value.values():
            yield from json_objects(item, depth + 1)
    elif isinstance(value, list):
        for item in value:
            yield from json_objects(item, depth + 1)


class Evidence:
    """Item IDs that appeared in tool output (Work IQ retrieve hits, fetch results, inspect_document evidence), and
    the emails and meetings that Graph reads returned, by title."""

    def __init__(self) -> None:
        self.outlook_ids: set[str] = set()
        self.chat_messages: set[tuple[str, str]] = set()
        self.drive_items: set[tuple[str, str]] = set()
        self.titles: dict[tuple[str, str], set[str]] = {}

    @classmethod
    def from_items(cls, items: Sequence[object]) -> Evidence:
        evidence = cls()
        for item in items:
            if isinstance(item, dict) and item.get("type") == "function_call_output":
                evidence.record(item.get("output"))
        return evidence

    def record(self, output: object) -> None:
        text = output_text(output)
        self.outlook_ids.update(graph_id(match.group(0)) for match in OUTLOOK_ID.finditer(text))
        self.chat_messages.update((unquote(chat), message) for chat, message in CHAT_MESSAGE.findall(text))
        self.drive_items.update(DRIVE_ITEM.findall(text))
        for value in json_objects(output):
            subject, identifier = value.get("subject"), value.get("id")
            if not (isinstance(subject, str) and subject.strip() and isinstance(identifier, str)
                    and OUTLOOK_ID.fullmatch(identifier)):
                continue
            kind = ("event" if any(field in value for field in EVENT_FIELDS)
                    else "email" if any(field in value for field in EMAIL_FIELDS) else None)
            if kind:
                self.titles.setdefault((kind, title_key(subject)), set()).add(graph_id(identifier))

    def titled(self, kind: str, title: str) -> str | None:
        """The one email or meeting with this exact title in the conversation's Graph reads."""
        found = self.titles.get((kind, title_key(title)), set())
        return next(iter(found)) if len(found) == 1 else None

    def holds(self, reference: Reference) -> bool:
        """Whether the tool output returned the cited item itself, with exactly these IDs (or the reference names no
        item to check). Without Graph's answer, an item the tools returned is never changed: repair only the others."""
        if reference.entity_url:
            entity = parse_entity_url(reference.entity_url)
            if entity.kind in ("email", "event"):
                return graph_id(entity.ids["id"]) in self.outlook_ids
            if entity.kind == "chat":
                return (entity.ids["chat"], entity.ids["message"]) in self.chat_messages
        if reference.kind == "file" and reference.drive_id and reference.item_id:
            return (reference.drive_id, reference.item_id) in self.drive_items
        return True

    def repair(self, reference: Reference) -> Reference:
        """The reference with its item IDs matched to the tool output; unchanged when nothing matches."""
        if reference.entity_url:
            entity = parse_entity_url(reference.entity_url)
            if entity.kind in ("email", "event"):
                prefix = "/me/messages/" if entity.kind == "email" else "/me/events/"
                identifier = graph_id(entity.ids["id"])
                match = identifier if identifier in self.outlook_ids else (
                    closest(identifier, self.outlook_ids) or spliced(identifier, self.outlook_ids))
                if match is None or match == identifier:
                    # Graph could not find the cited ID: the item with its exact title, if the reads returned one.
                    match = self.titled(entity.kind, reference.title) or match
                if match is None or f"{prefix}{match}" == reference.entity_url:
                    return reference
                return reference.model_copy(update={"entity_url": f"{prefix}{match}"})
            if entity.kind == "chat":
                chat, message = entity.ids["chat"], entity.ids["message"]
                if (chat, message) in self.chat_messages:
                    return reference
                chats = {found for found, found_message in self.chat_messages if found_message == message}
                if len(chats) != 1:
                    return reference
                return reference.model_copy(update={"entity_url": f"/chats/{chats.pop()}/messages/{message}"})
        if reference.kind == "file" and reference.drive_id and reference.item_id:
            if (reference.drive_id, reference.item_id) in self.drive_items:
                return reference
            item = closest(reference.item_id, (found for _, found in self.drive_items))
            drives = {drive for drive, found in self.drive_items if found == item}
            if item is None or len(drives) != 1:
                return reference
            drive = drives.pop()
            return reference.model_copy(update={
                "drive_id": drive, "item_id": item,
                "entity_url": f"/drives/{drive}/items/{item}" if reference.entity_url else None,
            })
        return reference
