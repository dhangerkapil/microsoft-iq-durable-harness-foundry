"""Typed contracts between the lead and its specialists.

Specialists answer in structured output (``SpecialistReport`` as the agent's ``response_format``). Code validates every
result, including background agents' results, which come back as text; a result that fails validation gets one repair
attempt, then counts as ``unavailable``. Evidence carries its provenance, so the lead and the writer treat it as quoted
material, never as instructions.
"""

from __future__ import annotations

import json
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

Trust = Literal["firm", "people", "external"]


class Reference(BaseModel):
    """A Microsoft 365 item a specialist cited (the app's source chips), copied exactly as Work IQ returned it."""

    model_config = ConfigDict(extra="ignore")

    title: str = ""
    url: str | None = None
    kind: str | None = None
    entity_url: str | None = None
    drive_id: str | None = None
    item_id: str | None = None
    page: int | str | None = None
    slide: int | str | None = None
    location_verified: bool = False


class Figure(BaseModel):
    """A number a specialist reports, exactly as its source gave it."""

    model_config = ConfigDict(extra="ignore")

    label: str
    value: str
    source: str = ""
    as_of: str = ""


class SpecialistReport(BaseModel):
    """What every specialist returns."""

    model_config = ConfigDict(extra="ignore")

    status: Literal["ok", "unavailable", "consent_required"] = "ok"
    answer: str = Field(default="", description="Markdown answer, every fact labelled with its IQ and source.")
    unavailable_reason: str = ""
    consent_link: str = ""
    figures: list[Figure] = Field(default_factory=list)
    references: list[Reference] = Field(default_factory=list)


class Finding(BaseModel):
    model_config = ConfigDict(extra="ignore")

    sentence: str
    issue: str
    rule: str = ""


class ReviewVerdict(BaseModel):
    """The compliance reviewer's verdict on a draft brief."""

    model_config = ConfigDict(extra="ignore")

    cleared: bool
    findings: list[Finding] = Field(default_factory=list)


TRUST: dict[str, Trust] = {
    "client-context": "people",
    "portfolio": "firm",
    "firm-policy": "firm",
    "market": "external",
}

_FENCE = re.compile(r"```(?:json)?\s*(\{.*\})\s*```", re.S)
_REFERENCES = re.compile(r"```workiq_references\s*(\[.*?\])\s*```", re.S)
# References a specialist wrote into its answer instead of the references field: a JSON code block, or one JSON object
# (or array) per line, as a Markdown list under a "References" heading. Without the field, the advisor gets no sources.
_JSON_BLOCK = re.compile(r"```(?:json)?[ \t]*\n([\s\S]*?)\n[ \t]*```")
_JSON_LINE = re.compile(r"^\s*(?:[-*+]|\d+[.)])?\s*`?(\{.*\}|\[.*\])`?\s*,?\s*$")
_REFERENCES_HEADING = re.compile(
    r"^\s*(?:#{1,6}\s*)?\**\s*(?:microsoft 365 )?(?:references|sources|items cited)\s*:?\s*\**\s*:?\s*$", re.I)


def parse_report(text: str) -> SpecialistReport:
    """Parse a specialist's output into a report. Raises ValueError when it isn't a valid report."""
    candidate = text.strip()
    match = _FENCE.search(candidate)
    if match:
        candidate = match.group(1)
    elif not candidate.startswith("{"):
        start, end = candidate.find("{"), candidate.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("no JSON object in the specialist's output")
        candidate = candidate[start:end + 1]
    try:
        report = SpecialistReport.model_validate(json.loads(candidate))
    except (json.JSONDecodeError, ValidationError) as exc:
        raise ValueError(f"invalid specialist report: {exc}") from exc
    # A references block inside the answer (the single agent's habit) moves to the structured list.
    for block in _REFERENCES.findall(report.answer):
        try:
            report.references.extend(Reference.model_validate(r) for r in json.loads(block))
        except (json.JSONDecodeError, ValidationError):
            pass
    report.answer = _REFERENCES.sub("", report.answer).strip()
    written, report.answer = references_in_answer(report.answer)
    report.references.extend(written)
    return report


def _cited(value: object) -> list[Reference] | None:
    """The references, when value is a reference object or a list of them (a title and a link or an ID each)."""
    items = value if isinstance(value, list) else [value]
    if not items or not all(
        isinstance(item, dict) and isinstance(item.get("title"), str)
        and any(isinstance(item.get(key), str) and item[key] for key in ("url", "entity_url", "item_id"))
        for item in items
    ):
        return None
    try:
        return [Reference.model_validate(item) for item in items]
    except ValidationError:
        return None


def _parsed(text: str) -> object:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def references_in_answer(answer: str) -> tuple[list[Reference], str]:
    """References a specialist wrote into its answer's text instead of the references field (which the advisor's
    sources come from), and the answer without them. Anything that isn't a reference object stays."""
    found: list[Reference] = []

    def block(match: re.Match[str]) -> str:
        cited = _cited(_parsed(match.group(1)))
        if not cited:
            return match.group(0)
        found.extend(cited)
        return ""

    text = _JSON_BLOCK.sub(block, answer)
    lines: list[str] = []
    for line in text.splitlines():
        match = _JSON_LINE.match(line)
        cited = _cited(_parsed(match.group(1))) if match else None
        if cited:
            found.extend(cited)
        else:
            lines.append(line)
    if not found:
        return [], answer
    kept: list[str] = []
    for index, line in enumerate(lines):
        if _REFERENCES_HEADING.match(line):
            following = next((rest for rest in lines[index + 1:] if rest.strip()), None)
            if following is None or following.lstrip().startswith("#"):
                continue  # the heading of the list just moved, with nothing left under it
        kept.append(line)
    return found, re.sub(r"\n{3,}", "\n\n", "\n".join(kept)).strip()


def unavailable(reason: str) -> SpecialistReport:
    return SpecialistReport(status="unavailable", unavailable_reason=reason[:500])


def evidence_block(lane: str, title: str, report: SpecialistReport) -> str:
    """The report as quoted evidence for the lead or the writer, with its provenance and trust level."""
    trust = TRUST.get(lane, "people")
    head = f'<evidence lane="{lane}" source="{title}" trust="{trust}" status="{report.status}">'
    body = report.answer if report.status == "ok" else (report.unavailable_reason or report.status)
    if report.consent_link:
        body += f"\nConsent link: {report.consent_link}"
    if report.figures:
        body += "\nFigures: " + "; ".join(
            f"{f.label} = {f.value}" + (f" ({f.source}{', ' + f.as_of if f.as_of else ''})" if f.source else "")
            for f in report.figures)
    return f"{head}\n{body}\n</evidence>"
