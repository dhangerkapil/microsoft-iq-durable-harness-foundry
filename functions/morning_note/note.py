"""The morning note's pure parts: the watch list, the note's identity, and the firm library documents it publishes.

Kept free of Azure and Functions imports so the tests run anywhere.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any

TICKER = re.compile(r"^[A-Z]{1,5}(\.[A-Z])?$")
PARENTHESIZED_TICKER = re.compile(r"\(\s*([A-Za-z]{1,5}(?:\.[A-Za-z])?)\s*\)")
MAX_TICKERS = 8
# A run waiting for approval shows its request in the run's status, which the Functions host caps at 16 KB (measured
# as UTF-16). The request must fit with room for the status around it, so the approver always sees the exact draft.
# The budget also bounds the whole note (about 7,000 characters), so publishing never needs to cut a section.
APPROVAL_BUDGET_BYTES = 14 * 1024


@dataclass
class NoteRun:
    """Start a morning note. Empty fields fall back to the configured watch list and today's date."""

    tickers: list[str] = field(default_factory=list)
    date: str = ""


@dataclass
class Findings:
    source: str
    text: str
    date: str = ""
    tickers: list[str] = field(default_factory=list)


@dataclass
class DraftNote:
    date: str
    tickers: list[str]
    text: str


@dataclass
class ApprovalDecision:
    approve: bool
    reviewer: str = ""
    comment: str = ""


@dataclass
class PublishNote:
    date: str
    tickers: list[str]
    text: str
    reviewer: str


def watch_list(requested: list[str] | None, configured: str) -> list[str]:
    """Public symbols only: upper-case tickers, deduplicated, at most eight; anything else is dropped."""
    source = requested or [t for t in (configured or "").split(",")]
    tickers: list[str] = []
    for raw in source:
        ticker = str(raw).strip().upper()
        if TICKER.match(ticker) and ticker not in tickers:
            tickers.append(ticker)
    return tickers[:MAX_TICKERS]


def note_date(requested: str, today: date) -> str:
    try:
        return date.fromisoformat(requested).isoformat() if requested else today.isoformat()
    except ValueError:
        return today.isoformat()


def doc_code(note_day: str) -> str:
    """The note's code in the firm library, in the house-view family: IC-MN-20261021."""
    return "IC-MN-" + note_day.replace("-", "")


def approval_request(draft: DraftNote) -> dict[str, Any]:
    """The approval request as plain JSON, so every approver surface (the run's status endpoint, a Teams card) shows
    the exact draft; the workflow framework would store a dataclass pickled and base64-encoded instead."""
    return {"question": f"Publish the morning note for {draft.date} to the firm library?", "date": draft.date,
            "tickers": list(draft.tickers), "note": draft.text}


def stored_size(value: object) -> int:
    """The UTF-16 bytes a pending request takes in the run's status (the host measures the JSON in UTF-16)."""
    return len(json.dumps(value).encode("utf-16-le"))


def fits_for_approval(draft: DraftNote) -> bool:
    return stored_size(approval_request(draft)) <= APPROVAL_BUDGET_BYTES


def approval_answer(value: object) -> bool | None:
    """The head of research's answer: True or False for a boolean, or for "true" or "false" as an Adaptive Card toggle
    sends them; None for anything else. Only True publishes: the respond endpoint doesn't check the answer's types,
    and a string such as "no" would otherwise count as yes."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().lower() in ("true", "false"):
        return value.strip().lower() == "true"
    return None


def heading_ticker(line: str, tickers: list[str]) -> str | None:
    """The ticker a heading line is about: its first word ('## NVDA', '## **NVDA**', '## NVDA:') or a ticker in
    parentheses ('## NVIDIA (NVDA)'); None for any other line."""
    stripped = line.strip()
    if not stripped.startswith("#"):
        return None
    heading = stripped.lstrip("#").strip()
    words = heading.split()
    first = re.sub(r"[^A-Z0-9.]", "", words[0].upper()).strip(".") if words else ""
    if first in tickers:
        return first
    match = PARENTHESIZED_TICKER.search(heading)
    candidate = match.group(1).upper() if match else ""
    return candidate if candidate in tickers else None


def sections(text: str, tickers: list[str]) -> list[tuple[str, str]]:
    """Split the approved note into its summary and one section per ticker, by its ticker headings. A ticker's later
    headings ('### NVDA talking point') join its section, so a note has at most one section per ticker plus the
    summary. Nothing is cut: every line of the approved note is published."""
    parts: dict[str, list[str]] = {"Summary": []}
    current = "Summary"
    for line in text.splitlines():
        current = heading_ticker(line, tickers) or current
        parts.setdefault(current, []).append(line)
    return [(name, "\n".join(lines).strip()) for name, lines in parts.items() if any(l.strip() for l in lines)]


def library_documents(note: PublishNote) -> list[dict]:
    """The firm library index's documents for an approved note (the index's schema, without vectors)."""
    code = doc_code(note.date)
    return [{
        "@search.action": "mergeOrUpload",
        "id": f"{code}-{index}",
        "doc_code": code,
        "title": f"Morning note: the house view for {note.date}",
        "section": f"§{name}" if name != "Summary" else "§Summary",
        "effective": note.date,
        "owner": f"Head of Research (approved by {note.reviewer or 'the head of research'})",
        "source_file": f"{code}.md",
        "page": 1,
        "content": f"{code} {name if name != 'Summary' else ''} Morning note, house view, {note.date}. {body}".strip(),
    } for index, (name, body) in enumerate(sections(note.text, note.tickers), start=1)]


def stale_sections(note_day: str, kept: int) -> list[dict]:
    """Deletes for the sections an earlier note of the same day had beyond this one's, so the library holds exactly
    the approved text (deleting a missing document succeeds). A note has at most MAX_TICKERS + 1 sections."""
    code = doc_code(note_day)
    return [{"@search.action": "delete", "id": f"{code}-{index}"} for index in range(kept + 1, MAX_TICKERS + 2)]
