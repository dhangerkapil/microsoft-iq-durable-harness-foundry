"""Work IQ tools that need raw MCP results: page-verified document evidence, the client's document folders and the
governance probe."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
import time
from collections.abc import Awaitable, Callable
from contextlib import AsyncExitStack
from types import TracebackType
from typing import Annotated, Any
from urllib.parse import quote, unquote, urlsplit

import httpx
from agent_framework import tool
from agent_framework_foundry_hosting import FoundryToolbox
from azure.core.credentials_async import AsyncTokenCredential
from mcp.types import CallToolResult
from opentelemetry import trace

from . import pdfpage
from .workiq import DRIVE_PATH, READ_TOOLS, WorkIQ, entity_data, serialize_result

logger = logging.getLogger(__name__)
tracer = trace.get_tracer("advisor-team")
WORKIQ_PREFIX = "workiq___"
PROBE_TOOLS = ("fetch", "update_entity", "delete_entity")


class ToolboxWorkIQClient:
    """One Foundry toolbox session that returns serialized MCP results with structuredContent intact."""

    def __init__(
        self, credential: AsyncTokenCredential, url: str, *, allowed: tuple[str, ...] = READ_TOOLS,
        timeout: float = 120.0,
    ) -> None:
        self.credential, self.url, self.allowed, self.timeout = credential, url, allowed, timeout
        self._stack = AsyncExitStack()
        self._toolbox: FoundryToolbox | None = None

    async def __aenter__(self) -> ToolboxWorkIQClient:
        toolbox = FoundryToolbox(
            self.credential, url=self.url, load_prompts=False, timeout=self.timeout,
            parse_tool_results=serialize_result,
        )
        self._toolbox = await self._stack.enter_async_context(toolbox)
        return self

    async def __aexit__(
        self, exc_type: type[BaseException] | None, exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self._toolbox = None
        await self._stack.__aexit__(exc_type, exc_value, traceback)

    async def call_tool(self, name: str, **arguments: object) -> str:
        if name not in self.allowed:
            raise ValueError(f"Work IQ tool {name} is not permitted here.")
        if self._toolbox is None:
            raise RuntimeError("The Work IQ toolbox session is not open.")
        result = await self._toolbox.call_tool(f"{WORKIQ_PREFIX}{name}", **arguments)
        if not isinstance(result, str):
            raise TypeError("Expected the serialized MCP result.")
        return result


def onedrive_path_from_url(web_url: str) -> str | None:
    """Map the signed-in user's OneDrive web URL to a /me/drive/root:/path lookup."""
    parsed = urlsplit(web_url)
    host = (parsed.hostname or "").lower()
    path = unquote(parsed.path)
    if parsed.scheme != "https" or not host.endswith("-my.sharepoint.com") or "/Documents/" not in path:
        return None
    relative = path.split("/Documents/", 1)[1].strip("/")
    segments = relative.split("/")
    if not relative or any(segment in {"", ".", ".."} for segment in segments):
        return None
    return "/me/drive/root:/" + "/".join(quote(segment, safe="") for segment in segments)


def drive_ids(path: str) -> tuple[str | None, str | None]:
    parts = path.strip("/").split("/")
    if len(parts) == 4 and parts[0] == "drives" and parts[2] == "items":
        return parts[1], parts[3]
    if len(parts) == 4 and parts[:2] == ["me", "drive"] and parts[2] == "items":
        return None, parts[3]
    return None, None


async def resolve_document_path(workiq: WorkIQ, resource_path: str | None, web_url: str | None) -> str:
    """A drive item path for a document: the one Work IQ returned, or a lookup of the advisor's OneDrive URL."""
    path = (resource_path or "").strip() or None
    if path and "?" in path:
        path = path.split("?", 1)[0]
    if not (path and DRIVE_PATH.fullmatch(path)) and web_url:
        lookup = onedrive_path_from_url(web_url)
        if lookup is None:
            raise ValueError("Pass the drive item path from Work IQ (/drives/{id}/items/{id}) or a OneDrive file URL.")
        metadata = entity_data(await workiq.call("fetch", entityUrls=[f"{lookup}?$select=id,name,parentReference"]))
        drive = (metadata.get("parentReference") or {}).get("driveId")
        path = f"/drives/{drive}/items/{metadata['id']}" if drive else f"/me/drive/items/{metadata['id']}"
    if not path:
        raise ValueError("Pass resource_path or web_url for the document.")
    return path


async def inspect_document_evidence(
    client: ToolboxWorkIQClient, phrase: str, resource_path: str | None, web_url: str | None,
    documents: dict | None = None,
) -> dict[str, Any]:
    workiq = WorkIQ(client, documents)
    path = await resolve_document_path(workiq, resource_path, web_url)
    evidence = await workiq.inspect(path, phrase)
    drive_id, item_id = drive_ids(path)
    evidence["drive_id"], evidence["item_id"] = drive_id, item_id
    pages = sorted({match["page_number"] for match in evidence["matches"]})
    evidence["verified_pages"] = pages
    evidence["how_verified"] = (
        "The harness downloaded the file bytes through Work IQ fetch_blob and matched the phrase in the PDF text "
        "of each page. A match proves the phrase is on that page; it does not read values inside images."
    )
    evidence["workiq_calls"], evidence["reused_download"] = workiq.calls, workiq.reused_download
    return evidence


def inspect_document_tool(credential: AsyncTokenCredential, toolbox_url: str, documents: dict | None = None):
    @tool(approval_mode="never_require")
    async def inspect_document(
        phrase: Annotated[str, "Literal text to find, e.g. a caption such as 'Figure 1' or 'Table 1', 3-200 characters."],
        resource_path: Annotated[str | None, "Drive item path from Work IQ, e.g. /drives/{driveId}/items/{itemId}."] = None,
        web_url: Annotated[str | None, "The file's OneDrive web URL when no drive item path is available."] = None,
    ) -> str:
        """Page-verified evidence from a PDF in OneDrive or SharePoint: downloads the file through Work IQ
        (fetch_blob) and returns every page whose text contains the phrase, with a quote. Use it before citing
        the page of any chart, table or passage."""
        started = time.perf_counter()
        async with ToolboxWorkIQClient(credential, toolbox_url) as client:
            evidence = await inspect_document_evidence(client, phrase, resource_path, web_url, documents)
        evidence["elapsed_ms"] = int((time.perf_counter() - started) * 1000)
        return json.dumps(evidence)

    return inspect_document


# --- Chart reading --------------------------------------------------------------------------------
CHART_PROMPT = """You are reading a chart from a client document for a wealth advisor. Answer only from what the chart \
shows.
Question: {question}
Rules: first trace each plotted line from left to right and note every local peak and trough with its date and value; \
then answer. Read each value against the axis gridlines and labels, and give it with its date. Mark a value "printed" \
if it is written on the chart as a label, otherwise "estimated from gridlines" (about plus or minus 0.2 points on a \
percentage axis). Dates between axis labels are approximate; say so. If two or more points are within that \
precision of the highest (or the lowest) value, give a reading for each and say in the answer that the image can't \
rank them. Don't infer anything that isn't visible.
Return only JSON: {{"chart_title": str, "y_axis": str, "x_axis": str, "peaks_and_troughs": [{{"date": str, \
"value": number}}], "readings": [{{"what": str, "date": str, "value": number, "unit": str, "how": "printed" | \
"estimated from gridlines"}}], "answer": str}}"""
CHART_SCOPE = "https://ai.azure.com/.default"


def parse_reading(text: str) -> dict[str, Any]:
    start, end = text.find("{"), text.rfind("}")
    if 0 <= start < end:
        try:
            value = json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            value = None
        if isinstance(value, dict):
            return value
    return {"answer": text.strip()[:2000]}


class ChartReader:
    """Reads a rendered page image with the agent's own model deployment, through the Foundry project's Responses
    API and the agent's identity."""

    def __init__(
        self, credential: AsyncTokenCredential, project_endpoint: str, deployment: str,
        headers: dict[str, str] | None = None, *, timeout: float = 90.0, transport: Any = None,
    ) -> None:
        self.credential, self.deployment, self.timeout, self.transport = credential, deployment, timeout, transport
        self.url = f"{project_endpoint.rstrip('/')}/openai/v1/responses"
        self.headers = dict(headers or {})

    async def read(self, png: bytes, question: str) -> dict[str, Any]:
        token = await self.credential.get_token(CHART_SCOPE)
        body = {
            "model": self.deployment,
            "reasoning": {"effort": "low"},
            "input": [{"role": "user", "content": [
                {"type": "input_text", "text": CHART_PROMPT.format(question=question)},
                {"type": "input_image", "image_url": "data:image/png;base64," + base64.b64encode(png).decode()},
            ]}],
        }
        async with httpx.AsyncClient(timeout=self.timeout, transport=self.transport) as http:
            response = await http.post(
                self.url, json=body, headers={**self.headers, "Authorization": f"Bearer {token.token}"},
            )
        if response.status_code >= 400:
            raise RuntimeError(f"The model could not read the chart (HTTP {response.status_code}).")
        payload = response.json()
        text = "".join(
            part.get("text", "") for item in payload.get("output", []) if item.get("type") == "message"
            for part in item.get("content", []) if part.get("type") == "output_text"
        )
        return parse_reading(text)


async def read_chart_evidence(
    client: ToolboxWorkIQClient, reader: ChartReader, page: int, question: str,
    resource_path: str | None, web_url: str | None, documents: dict | None = None,
) -> dict[str, Any]:
    workiq = WorkIQ(client, documents)
    path = await resolve_document_path(workiq, resource_path, web_url)
    data, source, _ = await workiq.document_pdf(path)
    # The chart itself at about the resolution it was exported at reads far more reliably than a whole-page render.
    rendered = await asyncio.to_thread(pdfpage.render_page, data, page, figure=True)
    started = time.perf_counter()
    reading = await reader.read(rendered.png, question)
    model_ms = int((time.perf_counter() - started) * 1000)
    drive_id, item_id = drive_ids(path)
    image = (f"the largest picture on page {rendered.page} (the chart), {rendered.width}x{rendered.height} pixels"
             if rendered.figure else f"page {rendered.page}, {rendered.width}x{rendered.height} pixels")
    return {
        "source": {"title": source.title, "web_url": source.web_url, "resource_path": path,
                   "drive_id": drive_id, "item_id": item_id},
        "page": rendered.page, "page_count": rendered.page_count,
        "reading": reading,
        "page_text": " ".join(rendered.text.split())[:1500],
        "how_read": (
            f"The harness downloaded the file through Work IQ fetch_blob as the advisor, rendered {image} and had "
            "the model read it. Values marked 'printed' are labels on the chart; 'estimated from gridlines' values "
            "are approximate. Compare them with figures in page_text."
        ),
        "workiq_calls": workiq.calls, "reused_download": workiq.reused_download,
        "model_call": {"deployment": reader.deployment, "ms": model_ms},
    }


def read_chart_tool(
    credential: AsyncTokenCredential, toolbox_url: str, project_endpoint: str, deployment: str,
    headers: dict[str, str] | None = None, documents: dict | None = None,
):
    reader = ChartReader(credential, project_endpoint, deployment, headers)

    @tool(approval_mode="never_require")
    async def read_chart(
        page: Annotated[int, "The PDF page with the chart; verify it first with inspect_document."],
        question: Annotated[str, "What to read from the chart, e.g. 'the highest and lowest NVDA weight, with dates, "
                                 "and the last value'."],
        resource_path: Annotated[str | None, "Drive item path from Work IQ, e.g. /drives/{driveId}/items/{itemId}."] = None,
        web_url: Annotated[str | None, "The file's OneDrive web URL when no drive item path is available."] = None,
    ) -> str:
        """The harness's own chart reader, a fallback for when workiq___ask can't read a chart's values: downloads
        the file through Work IQ (fetch_blob, as the advisor), renders the chart and has the agent's own model
        read it. Each value comes back marked 'printed' (a label on the chart) or 'estimated from gridlines'
        (approximate), with the page's own text for a cross-check."""
        started = time.perf_counter()
        with tracer.start_as_current_span("advisor.chart.read") as span:
            span.set_attribute("advisor.chart.page", page)
            async with ToolboxWorkIQClient(credential, toolbox_url) as client:
                evidence = await read_chart_evidence(client, reader, page, question, resource_path, web_url, documents)
            evidence["elapsed_ms"] = int((time.perf_counter() - started) * 1000)
            span.set_attribute("advisor.chart.readings", len(evidence["reading"].get("readings") or []))
        return json.dumps(evidence)

    return read_chart


# --- Client documents -----------------------------------------------------------------------------
CLIENT_FILES = "Client Files"
TEAM_SITE = "/sites/root"
FILE_FIELDS = "id,name,webUrl,lastModifiedDateTime,file,parentReference"
GRAPH_ROOT = "https://graph.microsoft.com/v1.0"
# Pages of 200 per folder: 2,000 entries before a listing reports that it stopped early.
MAX_PAGES = 10
# A client's display name as a folder name: no path separators, wildcards, control or URL-escape characters.
CLIENT_NAME = re.compile(r"[^\x00-\x1f/\\:*?\"<>|%#~]{1,100}")


async def all_items(
    workiq: WorkIQ, path: str, until: Callable[[list[dict]], bool] | None = None,
) -> tuple[list[dict], bool]:
    """Every item of a Graph collection read through Work IQ fetch, following @odata.nextLink, and whether the
    listing is complete. With ``until``, it stops as soon as the items read so far satisfy it."""
    items: list[dict] = []
    for _ in range(MAX_PAGES):
        data = entity_data(await workiq.call("fetch", entityUrls=[path]))
        page = data.get("value")
        if not isinstance(page, list):
            raise ValueError("Work IQ returned no folder listing.")
        items.extend(item for item in page if isinstance(item, dict))
        link = data.get("@odata.nextLink")
        if not isinstance(link, str) or not link or (until is not None and until(items)):
            return items, True
        # Work IQ takes Graph paths; a next link can come back absolute or already relative.
        path = link.removeprefix(GRAPH_ROOT)
        if not path.startswith("/"):
            return items, False
    return items, False


def document_entry(item: dict, where: str, folder: str) -> dict[str, Any]:
    drive = (item.get("parentReference") or {}).get("driveId")
    identifier = item.get("id")
    return {
        "name": item.get("name"), "where": where, "folder": folder, "web_url": item.get("webUrl"),
        "last_modified": item.get("lastModifiedDateTime"), "drive_id": drive, "item_id": identifier,
        "entity_url": f"/drives/{drive}/items/{identifier}" if drive and identifier else None,
    }


async def onedrive_documents(workiq: WorkIQ, client_name: str) -> tuple[list[dict], bool]:
    path = "/".join(quote(part, safe="") for part in (CLIENT_FILES, client_name))
    items, complete = await all_items(workiq, f"/me/drive/root:/{path}:/children?$select={FILE_FIELDS}&$top=200")
    return [item for item in items if "file" in item], complete


def named_folder(items: list[dict], name: str) -> dict | None:
    return next((item for item in items if "folder" in item and isinstance(item.get("id"), str)
                 and str(item.get("name", "")).casefold() == name.casefold()), None)


async def team_site_documents(workiq: WorkIQ, client_name: str) -> tuple[list[dict], bool]:
    # Work IQ reads the team site's folders by item ID, not by path: from the library root to Client Files, then
    # to the client's folder.
    drive = entity_data(await workiq.call("fetch", entityUrls=[f"{TEAM_SITE}/drive?$select=id"])).get("id")
    if not isinstance(drive, str) or not drive:
        raise ValueError("The team site has no document library.")
    folder = "root"
    for name in (CLIENT_FILES, client_name):
        listing, complete = await all_items(
            workiq, f"/drives/{drive}/items/{folder}/children?$select=id,name,folder&$top=200",
            until=lambda items, name=name: named_folder(items, name) is not None)
        match = named_folder(listing, name)
        if match is None:
            return [], complete
        folder = match["id"]
    items, complete = await all_items(workiq, f"/drives/{drive}/items/{folder}/children?$select={FILE_FIELDS}&$top=200")
    return [item for item in items if "file" in item], complete


async def client_documents(
    open_session: Callable[[], ToolboxWorkIQClient], client_name: str,
) -> dict[str, Any]:
    """The files in the client's folder in the advisor's OneDrive and on the team site, listed through Work IQ fetch
    as the advisor. Each location gets its own toolbox session (a session runs one call at a time), and a location
    that fails (no such folder for this advisor, a policy denial, a throttle) never hides the other's files."""
    client_name = " ".join(client_name.split())
    if not CLIENT_NAME.fullmatch(client_name) or client_name in {".", ".."}:
        raise ValueError("Give the client's name as it appears on their folder, for example 'Michael Scott'.")
    calls: list[dict] = []

    async def listing(lookup: Callable[[WorkIQ, str], Awaitable[tuple[list[dict], bool]]]) -> tuple[list[dict], bool]:
        async with open_session() as session:
            workiq = WorkIQ(session)
            try:
                return await lookup(workiq, client_name)
            finally:
                calls.extend(workiq.calls)

    folder = f"{CLIENT_FILES}/{client_name}"
    found = await asyncio.gather(listing(onedrive_documents), listing(team_site_documents), return_exceptions=True)
    documents: list[dict[str, Any]] = []
    locations: dict[str, str] = {}
    complete = True
    for where, result in zip(("OneDrive", "SharePoint"), found, strict=True):
        if isinstance(result, BaseException):
            if not isinstance(result, Exception):
                raise result
            # The toolbox raises a Work IQ tool error (a missing folder, a denial) as ToolExecutionException.
            logger.warning("Listing the client's %s folder failed: %s", where, str(result)[:300])
            locations[where] = "not found or not available to you"
            continue
        files, whole = result
        complete = complete and whole
        locations[where] = ((f"{len(files)} file(s)" if files else "no files, or no folder for this client")
                            + ("" if whole else f"; stopped after {MAX_PAGES} pages, so there may be more"))
        documents.extend(document_entry(item, where, folder) for item in files)
    return {
        "client": client_name, "folder": folder, "locations": locations, "documents": documents, "complete": complete,
        "how_listed": (
            f"The harness listed the folder '{folder}' in the advisor's OneDrive and on the team site through Work IQ "
            "fetch (Microsoft Graph), as the advisor, page by page: current, with no search index involved. "
            "OneDrive is the advisor's own files; SharePoint is the team's site."
            + ("" if complete else " A listing stopped early, so there may be more files than these.")
        ),
        "workiq_calls": calls,
    }


def client_documents_tool(credential: AsyncTokenCredential, toolbox_url: str):
    @tool(approval_mode="never_require")
    async def list_client_documents(
        client_name: Annotated[str, "The client's full name, for example 'Michael Scott'."],
    ) -> str:
        """Every document in the client's folder ('Client Files/<client>') in the advisor's OneDrive and on the team
        site (SharePoint), listed through Work IQ fetch as the advisor, with each file's link, where it lives,
        last-modified time and drive item path. Read page by page and current, unlike a search; `locations` says
        what each place returned, including a folder the advisor can't reach."""
        started = time.perf_counter()
        with tracer.start_as_current_span("advisor.documents.list") as span:
            result = await client_documents(lambda: ToolboxWorkIQClient(credential, toolbox_url), client_name)
            result["elapsed_ms"] = int((time.perf_counter() - started) * 1000)
            span.set_attribute("advisor.documents.count", len(result["documents"]))
        return json.dumps(result)

    return list_client_documents


# --- Governance probe -----------------------------------------------------------------------------
# A well-formed message ID that doesn't exist: if policy lets the request through, Microsoft Graph rejects it,
# so the update and delete checks can never change data.
PROBE_MISSING_ITEM = "/me/messages/AAMkAGdvdmVybmFuY2UtcHJvYmUtdGhpcy1pdGVtLWRvZXMtbm90LWV4aXN0AAA="
PROBES: tuple[tuple[str, str, str, dict[str, Any]], ...] = (
    ("own_mail", "Read the advisor's own mail", "fetch",
     {"entityUrls": ["/me/messages?$top=1&$select=subject,receivedDateTime"]}),
    ("own_files", "Read the advisor's own OneDrive", "fetch",
     {"entityUrls": ["/me/drive/root/children?$top=3&$select=name"]}),
    ("directory", "Directory lookup: other people in the organization", "fetch",
     {"entityUrls": ["/users?$top=3&$select=displayName,jobTitle"]}),
    ("update", "Partial update of an email (test item that doesn't exist)", "update_entity",
     {"entityUrl": PROBE_MISSING_ITEM, "jsonBody": json.dumps({"categories": ["Governance probe"]})}),
    ("delete", "Delete an email (test item that doesn't exist)", "delete_entity",
     {"entityUrl": PROBE_MISSING_ITEM}),
)
MUTATION_CHECKS = {"update", "delete"}
# Microsoft Graph error codes for a missing or malformed item: proof the request got past Work IQ policy.
GRAPH_REJECTED = ("itemnotfound", "resourcenotfound", "invalididmalformed", "errorinvalidid")
DENIAL_MARKERS = ("polic", "not allowed", "not permitted", "disabled", "blocked")


def error_detail(texts: str, payload: Any, error: Any) -> str:
    """The useful part of an error: drop the toolbox's configuration envelope ("meta")."""
    if error:
        return json.dumps(error)[:500]
    if isinstance(payload, dict):
        trimmed = {key: value for key, value in payload.items() if key != "meta"}
        if trimmed:
            return json.dumps(trimmed)[:500]
    return texts[:500]


def mutation_decision(status: int | None, decision: str, detail: str) -> str:
    """Interpret a write to a missing item: a Graph rejection means the policy let the request through."""
    if decision.startswith("denied"):
        return decision
    lowered = detail.lower()
    if status in (400, 404) or any(code in lowered for code in GRAPH_REJECTED):
        return "allowed by policy (Microsoft Graph rejected the test item; nothing changed)"
    return decision


def classify(raw: str) -> tuple[int | None, str, str]:
    """Return (status code, decision, detail) for one probe result."""
    result = CallToolResult.model_validate_json(raw)
    texts = " ".join(block.text for block in result.content if block.type == "text")
    payload: Any = result.structuredContent
    if payload is None:
        try:
            payload = json.loads(texts)
        except (json.JSONDecodeError, TypeError):
            payload = None
    status = None
    detail = texts
    error: Any = None
    if isinstance(payload, dict):
        entries = payload.get("results") if isinstance(payload.get("results"), list) else [payload]
        first = entries[0] if entries and isinstance(entries[0], dict) else {}
        status = first.get("statusCode") if isinstance(first.get("statusCode"), int) else None
        data = first.get("data")
        error = first.get("error") or (data.get("error") if isinstance(data, dict) else None)
        if status and status < 300 and not error:
            detail = "OK"
        else:
            detail = error_detail(texts, payload, error)
    if not result.isError and (status is None or status < 300):
        return status, "allowed", (detail or "")[:400]
    reason = ""
    if result.isError or (status is not None and status >= 400):
        # Only the error can show a policy denial; returned data (an Investment Policy Statement) never does.
        reason = f"{json.dumps(error) if error else ''} {texts if result.isError else ''}".lower()
    if any(marker in reason for marker in DENIAL_MARKERS):
        decision = "denied by Work IQ tenant policy"
    elif status in (401, 403) or "forbidden" in reason:
        decision = "denied: insufficient permissions"
    else:
        decision = "failed"
    return status, decision, (detail or "")[:400]


def classify_failure(error: Exception) -> tuple[int | None, str, str]:
    """A failed tool call can carry the serialized MCP result in its message; classify that result."""
    message = str(error)
    try:
        status, decision, detail = classify(message)
    except ValueError:
        denied = any(marker in message.lower() for marker in DENIAL_MARKERS)
        return None, "denied by Work IQ tenant policy" if denied else "failed", message[:500]
    # The call raised, so it did not succeed, whatever the payload looks like.
    return status, "failed" if decision == "allowed" else decision, detail


def probe_tool(credential: AsyncTokenCredential, governance_url: str):
    @tool(approval_mode="never_require")
    async def probe_workiq_policy() -> str:
        """Ask Work IQ, as the signed-in advisor, to do five things and report what its tenant policy decides:
        read own mail, read own files, look up other people in the directory, and update and delete an email.
        The update and delete target a test item that doesn't exist, so nothing can change. Creating content is
        shown separately by the approval-gated draft. Never retry a denied request."""
        rows = []
        async with ToolboxWorkIQClient(credential, governance_url, allowed=PROBE_TOOLS) as client:
            for key, label, name, arguments in PROBES:
                started = time.perf_counter()
                try:
                    raw = await asyncio.wait_for(client.call_tool(name, **arguments), timeout=60)
                    status, decision, detail = classify(raw)
                except Exception as exc:  # A denial can surface as an MCP error.
                    status, decision, detail = classify_failure(exc)
                if key in MUTATION_CHECKS:
                    decision = mutation_decision(status, decision, detail)
                rows.append({
                    "check": key, "what": label, "work_iq_tool": name,
                    "request": arguments.get("entityUrls", [arguments.get("entityUrl")])[0],
                    "status_code": status, "decision": decision, "detail": detail,
                    "elapsed_ms": int((time.perf_counter() - started) * 1000),
                })
        return json.dumps({
            "identity": "the signed-in advisor (delegated, on-behalf-of)",
            "policy_scope_today": ("tenant-wide: Microsoft 365 admin center > Agents > Tools > MCP servers > Work IQ MCP "
                                   "> Policies (Tenant Default; Trusted Platform covers trusted Microsoft platforms "
                                   "such as Copilot Studio)"),
            "results": rows,
            "create": "Shown by the approval-gated draft_follow_up_email, which only creates a draft after approval.",
            "note": "No data was changed.",
        })

    return probe_workiq_policy
