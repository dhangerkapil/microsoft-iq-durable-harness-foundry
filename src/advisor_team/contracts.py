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
    return report


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
