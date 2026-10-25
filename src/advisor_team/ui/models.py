from __future__ import annotations

from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .entities import SourceKind, parse_entity_url


class Reference(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=512)
    url: str = Field(max_length=4096)
    kind: SourceKind = "file"
    # Work IQ's Graph path for the cited item (entityUrl); the source viewer opens it with the user's token.
    entity_url: str | None = Field(default=None, max_length=1024)
    drive_id: str | None = Field(default=None, max_length=256)
    item_id: str | None = Field(default=None, max_length=256)
    page: int | None = Field(default=None, ge=1, le=100_000, strict=True)
    slide: int | None = Field(default=None, ge=1, le=100_000, strict=True)
    # True only for a page the agent's inspect_document tool matched in the PDF text.
    location_verified: bool = Field(default=False, strict=True)

    @field_validator("url")
    @classmethod
    def valid_url(cls, value: str) -> str:
        parsed = urlsplit(value)
        if (
            parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
            or any(ord(character) < 32 for character in value)
        ):
            raise ValueError("Source links must be HTTPS URLs without embedded credentials.")
        return value

    @field_validator("kind", mode="before")
    @classmethod
    def known_kind(cls, value: object) -> object:
        # Tolerate the model's synonyms; the item path, when present, overrides the label anyway.
        if not isinstance(value, str):
            return value
        label = value.strip().lower()
        synonyms = {"mail": "email", "meeting": "event", "calendar": "event", "teams": "chat",
                    "document": "file", "pdf": "file"}
        label = synonyms.get(label, label)
        return label if label in ("file", "email", "event", "chat") else "file"

    @field_validator("entity_url")
    @classmethod
    def valid_entity_url(cls, value: str | None) -> str | None:
        # An unusable item path only loses the in-app viewer; the source and its link are kept.
        if value is None:
            return None
        try:
            return parse_entity_url(value).path
        except ValueError:
            return None

    @field_validator("drive_id", "item_id")
    @classmethod
    def valid_identifier(cls, value: str | None) -> str | None:
        if value is not None and (
            not value or any(character in value for character in "/\\?#%")
            or any(ord(character) < 32 for character in value)
        ):
            raise ValueError("Use an opaque Graph drive/item ID, not a path.")
        return value

    @field_validator("location_verified", mode="before")
    @classmethod
    def unverified_when_unknown(cls, value: object) -> object:
        # The model writes null for an unknown value; that means unverified. Only an inspect_document result can make
        # it true (VerifiedPages.apply), and any other non-boolean still rejects the reference.
        return False if value is None else value

    @model_validator(mode="after")
    def kind_from_entity(self) -> Reference:
        """The item path, not the model's label, decides what kind of source this is; for a file it also wins over
        separately copied drive and item IDs."""
        if self.entity_url:
            entity = parse_entity_url(self.entity_url)
            self.kind = entity.kind
            if entity.kind == "file":
                self.drive_id, self.item_id = entity.ids["drive"], entity.ids["item"]
        return self


class ToolActivity(BaseModel):
    call_id: str
    name: str
    kind: Literal["tool", "skill"] = "tool"
    skill: str | None = None
    source: Literal["Email", "OneDriveAndSharePoint"] | None = None
    parent_call_id: str | None = None
    status: Literal["running", "awaiting_approval", "completed", "failed", "cancelled"] = "running"
    started_at: str
    duration_ms: int | None = None
    timing_source: Literal["runtime"] | None = None
    # For a harness tool that calls Work IQ itself (out of the model's sight): which Work IQ calls it made.
    detail: str | None = Field(default=None, max_length=300)
    # A specialist's call inside the lead's delegation (the team's X-ray lanes).
    lane: str | None = Field(default=None, max_length=64)
    lane_title: str | None = Field(default=None, max_length=64)


class RunLatency(BaseModel):
    preparation_ms: int | None = Field(default=None, ge=0)
    first_event_ms: int | None = Field(default=None, ge=0)
    first_text_ms: int | None = Field(default=None, ge=0)
    response_ms: int | None = Field(default=None, ge=0)


class RunRecord(BaseModel):
    id: str
    session_id: str
    prompt_preview: str
    status: Literal["running", "completed", "incomplete", "failed", "cancelled"] = "running"
    started_at: str
    response_id: str | None = None
    assistant_message_ids: list[str] = Field(default_factory=list)
    activity: list[ToolActivity] = Field(default_factory=list)
    stage: Literal[
        "connecting", "planning", "loading_skill", "searching", "running_tool", "answering"
    ] = "connecting"
    latency: RunLatency = Field(default_factory=RunLatency)
    # The team: workflow steps (W1) and the compliance review's verdict.
    team_steps: list[dict] = Field(default_factory=list)
    team_verdict: dict | None = None


class RenameSession(BaseModel):
    title: str = Field(min_length=1, max_length=100)

    @field_validator("title")
    @classmethod
    def nonempty_title(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("A session title cannot be blank.")
        return value


class PreviewRequest(BaseModel):
    reference: Reference


class DocumentRequest(BaseModel):
    reference: Reference
    # Defaults to the cited page; out-of-range pages are clamped to the document.
    page: int | None = Field(default=None, ge=1, le=100_000, strict=True)


class SourceRequest(BaseModel):
    reference: Reference


class ApprovalDecision(BaseModel):
    """The advisor's answer to one approval request, sent as AG-UI forwardedProps.approval."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=256, pattern=r"^[\x21-\x7E]+$")
    approve: bool = Field(strict=True)
