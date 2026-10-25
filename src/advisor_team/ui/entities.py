"""Microsoft Graph item paths that a cited source may carry (Work IQ's entityUrl), validated strictly."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal
from urllib.parse import unquote

SourceKind = Literal["file", "email", "event", "chat"]

_OUTLOOK_ID = r"[A-Za-z0-9\-_+=]{8,512}"
_PATTERNS: tuple[tuple[SourceKind, re.Pattern[str]], ...] = (
    ("email", re.compile(rf"/me/messages/(?P<id>{_OUTLOOK_ID})")),
    ("event", re.compile(rf"/me/events/(?P<id>{_OUTLOOK_ID})")),
    ("chat", re.compile(r"/chats/(?P<chat>19:[A-Za-z0-9\-_.@:=]{1,256})/messages/(?P<message>\d{1,32})")),
    ("file", re.compile(r"/drives/(?P<drive>[A-Za-z0-9!\-_]{1,256})/items/(?P<item>[A-Za-z0-9!\-_]{1,256})")),
)


@dataclass(frozen=True)
class Entity:
    kind: SourceKind
    path: str
    ids: dict[str, str]


def parse_entity_url(value: str) -> Entity:
    """Decode once, then accept only the four item shapes the source viewer can open."""
    if not isinstance(value, str) or len(value) > 1024:
        raise ValueError("Unsupported Microsoft 365 item path.")
    path = unquote(value.strip())
    if any(ord(character) < 32 for character in path):
        raise ValueError("Unsupported Microsoft 365 item path.")
    for kind, pattern in _PATTERNS:
        match = pattern.fullmatch(path)
        if match:
            return Entity(kind, path, match.groupdict())
    raise ValueError("Unsupported Microsoft 365 item path.")
