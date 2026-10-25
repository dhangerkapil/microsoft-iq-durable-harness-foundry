from __future__ import annotations

import json
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from urllib.parse import parse_qs, unquote, urlsplit
from uuid import UUID

from pydantic import ValidationError

from ..execution import tool_output_failed, unwrap_tool_output
from .models import Reference

REFERENCE_BLOCK = re.compile(r"```workiq_references\s*\n([\s\S]*?)\n```")
PREVIEW_DOMAINS = (
    "sharepoint.com", "sharepoint-df.com", "officeapps.live.com",
    "office.com", "onedrive.com", "onedrive.live.com", "microsoft365.com",
)
INSPECT_TOOL = "inspect_document"


@dataclass(frozen=True)
class SharePointFile:
    hostname: str
    site_path: str
    file_path: str | None
    unique_id: str | None = None


def sharepoint_file(value: str) -> SharePointFile:
    parsed = urlsplit(value)
    hostname = (parsed.hostname or "").lower()
    if (
        parsed.scheme != "https" or parsed.username or parsed.password
        or parsed.port not in (None, 443)
        or not hostname.endswith((".sharepoint.com", ".sharepoint-df.com"))
    ):
        raise ValueError("This source has no file IDs or supported SharePoint file URL. Use Open original.")
    path = unquote(parsed.path)
    if (
        path.startswith("//") or "\\" in path or any(ord(character) < 32 for character in path)
        or any(segment in {".", ".."} for segment in path.split("/"))
    ):
        raise ValueError("Invalid SharePoint file path.")
    viewer_suffix = "/_layouts/15/doc.aspx"
    if path.lower().endswith(viewer_suffix):
        targets = [
            value for key, values in parse_qs(parsed.query, keep_blank_values=True).items()
            if key.lower() == "sourcedoc" for value in values
        ]
        if len(targets) != 1:
            raise ValueError("This SharePoint viewer link does not identify one file. Use Open original.")
        try:
            unique_id = str(UUID(targets[0]))
        except ValueError as error:
            raise ValueError(
                "This SharePoint viewer link has an invalid file ID. Use Open original.",
            ) from error
        return SharePointFile(hostname, path[:-len(viewer_suffix)], None, unique_id)
    if path.lower().endswith("/forms/allitems.aspx"):
        targets = parse_qs(parsed.query).get("id", [])
        if len(targets) != 1:
            raise ValueError("This library link does not identify one file. Use its file-specific link.")
        file_path = targets[0]
        library_path = path[:-len("/Forms/AllItems.aspx")].rstrip("/")
        if not file_path.casefold().startswith(f"{library_path}/".casefold()):
            raise ValueError("The referenced file is outside this library link.")
        site_path = library_path.rsplit("/", 1)[0]
    else:
        if "/_layouts/" in path.lower() or path.lower().endswith(".aspx"):
            raise ValueError(
                "This SharePoint viewer link has no file path. Use a file-specific library link.",
            )
        file_path = path
        segments = path.strip("/").split("/")
        site_path = (
            f"/{segments[0]}/{segments[1]}"
            if len(segments) >= 3 and segments[0].lower() in {"sites", "teams", "personal"}
            else ""
        )
    if (
        not file_path.startswith("/") or file_path.startswith("//")
        or "\\" in file_path or any(ord(character) < 32 for character in file_path)
        or any(segment in {".", ".."} for segment in file_path.split("/"))
        or not file_path.rsplit("/", 1)[-1]
    ):
        raise ValueError("Invalid SharePoint file path.")
    return SharePointFile(hostname, site_path, file_path)


def normalized_url(value: str) -> str:
    """Scheme and host lowercased, path decoded and case-folded; the query (?web=1) and fragment dropped."""
    parsed = urlsplit(value)
    return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}{unquote(parsed.path).casefold()}"


def inspection_evidence(output: object, depth: int = 0) -> dict | None:
    """The evidence object in an inspect_document output: JSON text, an object or text content blocks."""
    if depth > 3:
        return None
    if isinstance(output, str):
        try:
            return inspection_evidence(json.loads(output), depth + 1)
        except json.JSONDecodeError:
            return None
    if isinstance(output, list):
        for block in output:
            if isinstance(block, dict) and block.get("type") == "text":
                evidence = inspection_evidence(block.get("text"), depth + 1)
                if evidence is not None:
                    return evidence
        return None
    if isinstance(output, dict) and isinstance(output.get("verified_pages"), list):
        return output
    return None


class VerifiedPages:
    """PDF pages that inspect_document actually matched. The model reports location_verified in its
    workiq_references block, but only these tool results can confirm it."""

    def __init__(self) -> None:
        self.keys: set[tuple[str, str, int]] = set()

    @classmethod
    def from_items(cls, items: Sequence[object]) -> VerifiedPages:
        """Pages verified in a stored conversation: inspect_document calls matched to outputs by call ID."""
        verified = cls()
        calls = {
            item["call_id"] for item in items
            if isinstance(item, dict) and item.get("type") == "function_call"
            and item.get("name") == INSPECT_TOOL and isinstance(item.get("call_id"), str)
        }
        for item in items:
            if (
                not isinstance(item, dict) or item.get("type") != "function_call_output"
                or not isinstance(item.get("call_id"), str) or item["call_id"] not in calls
            ):
                continue
            try:
                output, timing = unwrap_tool_output(
                    item.get("output"), call_id=item["call_id"], name=INSPECT_TOOL,
                )
            except ValueError:
                continue
            if not (timing and timing.failed) and not tool_output_failed(output, skill=False):
                verified.record(output)
        return verified

    def record(self, output: object) -> None:
        """Record the pages that one successful inspect_document output verified."""
        evidence = inspection_evidence(output)
        if evidence is None:
            return
        item_id = evidence.get("item_id")
        source = evidence.get("source")
        web_url = source.get("web_url") if isinstance(source, dict) else None
        try:
            url = normalized_url(web_url) if isinstance(web_url, str) and web_url else None
        except ValueError:
            url = None
        for page in evidence["verified_pages"]:
            if not isinstance(page, int) or isinstance(page, bool) or page < 1:
                continue
            if isinstance(item_id, str) and item_id:
                self.keys.add(("item", item_id, page))
            if url:
                self.keys.add(("url", url, page))

    def confirms(self, reference: Reference) -> bool:
        # Only PDF pages are matched; a slide is never verified.
        if reference.page is None or reference.slide is not None:
            return False
        return (
            reference.item_id is not None and ("item", reference.item_id, reference.page) in self.keys
        ) or ("url", normalized_url(reference.url), reference.page) in self.keys

    def apply(self, references: Iterable[Reference]) -> list[Reference]:
        """Keep location_verified only where an inspect_document result confirms the cited page."""
        return [
            reference.model_copy(update={
                "location_verified": reference.location_verified and self.confirms(reference),
            })
            for reference in references
        ]


def references_from_text(
    text: str, annotations: Iterable[dict] = (), verified: VerifiedPages | None = None,
) -> list[Reference]:
    """Sources an answer cites. A page stays location_verified only when ``verified`` holds a matching
    inspect_document result; the model's claim alone is never enough. Item IDs are left as cited: the source
    endpoints repair a mis-copied ID only when Microsoft Graph can't find it (see grounding.py)."""
    references: dict[tuple[str, int | None, int | None], Reference] = {}
    for match in REFERENCE_BLOCK.finditer(text):
        try:
            values = json.loads(match.group(1))
        except json.JSONDecodeError:
            continue
        if not isinstance(values, list) or len(values) > 50:
            continue
        for value in values:
            try:
                reference = Reference.model_validate(value)
            except ValidationError:
                continue
            references[(reference.url, reference.page, reference.slide)] = reference
    for annotation in annotations:
        if annotation.get("type") != "url_citation":
            continue
        try:
            reference = Reference(title=annotation.get("title") or "Source", url=annotation["url"])
        except (ValidationError, KeyError):
            continue
        if not any(key[0] == reference.url for key in references):
            references[(reference.url, None, None)] = reference
    return (verified or VerifiedPages()).apply(references.values())


def validate_preview_url(value: str) -> str:
    parsed = urlsplit(value)
    hostname = (parsed.hostname or "").lower()
    if (
        parsed.scheme != "https" or parsed.username or parsed.password
        or parsed.port not in (None, 443)
        or not any(hostname == domain or hostname.endswith(f".{domain}") for domain in PREVIEW_DOMAINS)
    ):
        raise ValueError("Graph returned an unsupported document-preview host.")
    return value
