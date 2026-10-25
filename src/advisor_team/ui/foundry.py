from __future__ import annotations

import base64
import json
from collections.abc import AsyncGenerator
from urllib.parse import quote, unquote, urlsplit

import httpx
from fastapi import HTTPException
from pydantic import ValidationError

from .. import pdfpage

from ..execution import unwrap_tool_output
from ..tool_arguments import call_arguments
from .config import UIConfig
from .models import Reference, RunRecord
from .payloads import payload_text
from .references import VerifiedPages, references_from_text, sharepoint_file, validate_preview_url


def upstream_error(response: httpx.Response) -> None:
    if response.is_success:
        return
    if response.status_code in (401, 403):
        raise HTTPException(
            status_code=403, detail="The signed-in user does not have access to this service or file.",
        )
    if response.status_code == 404:
        raise HTTPException(status_code=404, detail="The conversation or referenced file is unavailable.")
    if response.status_code == 429:
        raise HTTPException(status_code=429, detail="The upstream service is busy; retry shortly.")
    raise HTTPException(
        status_code=502, detail=f"Upstream service request failed (HTTP {response.status_code}).",
    )


def conversation_messages(items: list[dict]) -> list[dict]:
    # A cited page counts as verified only when an inspect_document result in this conversation matched it.
    verified = VerifiedPages.from_items(items)
    messages: list[dict] = []
    for item in items:
        if item.get("type") != "message" or item.get("role") not in ("user", "assistant"):
            continue
        content = item.get("content", [])
        text = "".join(
            block.get("refusal", "") if block.get("type") == "refusal" else block.get("text", "")
            for block in content if block.get("type") in ("input_text", "output_text", "refusal")
        )
        if text:
            annotations = [
                annotation for block in content for annotation in block.get("annotations", [])
            ]
            messages.append({
                "id": item["id"], "role": item["role"], "content": text,
                "references": [
                    reference.model_dump(mode="json")
                    for reference in references_from_text(text, annotations, verified=verified)
                ] if item["role"] == "assistant" else [],
            })
    return messages


class FoundryGateway:
    def __init__(self, config: UIConfig, client: httpx.AsyncClient) -> None:
        self.client = client
        self.openai_url = f"{config.project_endpoint}/openai/v1"
        self.agent_url = (
            f"{config.project_endpoint}/agents/{quote(config.agent_name, safe='')}"
            "/endpoint/protocols/openai"
        )

    async def create_conversation(self, token: str) -> str:
        response = await self.client.post(
            f"{self.agent_url}/conversations", params={"api-version": "v1"},
            headers={"Authorization": f"Bearer {token}"}, json={},
        )
        upstream_error(response)
        identifier = response.json().get("id")
        if not isinstance(identifier, str) or not identifier:
            raise HTTPException(status_code=502, detail="Foundry returned an invalid conversation ID.")
        return identifier

    async def has_legacy_history(self, token: str, conversation_id: str) -> bool:
        response = await self.client.get(
            f"{self.openai_url}/conversations/{quote(conversation_id, safe='')}/items",
            params={"limit": 1}, headers={"Authorization": f"Bearer {token}"},
        )
        upstream_error(response)
        items = response.json().get("data")
        if not isinstance(items, list):
            raise HTTPException(status_code=502, detail="Foundry returned invalid conversation items.")
        return bool(items)

    async def messages(self, token: str, conversation_id: str, *, legacy: bool = False) -> list[dict]:
        return conversation_messages(await self.items(token, conversation_id, legacy=legacy))

    async def items(self, token: str, conversation_id: str, *, legacy: bool = False) -> list[dict]:
        conversation: list[dict] = []
        cursor: str | None = None
        for _ in range(20):
            parameters: dict[str, str | int] = {"order": "asc", "limit": 100}
            if not legacy:
                parameters["api-version"] = "v1"
            if cursor:
                parameters["after"] = cursor
            response = await self.client.get(
                f"{self.openai_url if legacy else self.agent_url}"
                f"/conversations/{quote(conversation_id, safe='')}/items",
                headers={"Authorization": f"Bearer {token}"}, params=parameters,
            )
            upstream_error(response)
            payload = response.json()
            items = payload.get("data")
            if not isinstance(items, list):
                raise HTTPException(status_code=502, detail="Foundry returned invalid conversation items.")
            # Load every page first: verification and the answer citing it may be on different pages.
            conversation.extend(items)
            if not payload.get("has_more"):
                return conversation
            if not items or not isinstance(items[-1].get("id"), str):
                raise HTTPException(status_code=502, detail="Foundry returned an invalid pagination cursor.")
            cursor = items[-1]["id"]
        raise HTTPException(
            status_code=413, detail="This conversation is too large to load; start a new session.",
        )

    async def delete_conversation(self, token: str, conversation_id: str, *, legacy: bool = False) -> None:
        response = await self.client.delete(
            f"{self.openai_url if legacy else self.agent_url}"
            f"/conversations/{quote(conversation_id, safe='')}",
            params={} if legacy else {"api-version": "v1"},
            headers={"Authorization": f"Bearer {token}"},
        )
        if response.status_code == 404:
            return
        upstream_error(response)

    async def tool_details(self, token: str, record: RunRecord, call_id: str) -> dict:
        if not record.response_id or record.status == "running":
            raise HTTPException(
                status_code=409, detail="Tool payloads are available after the prompt finishes.",
            )
        async with self.client.stream(
            "GET", f"{self.agent_url}/responses/{quote(record.response_id, safe='')}",
            params={"api-version": "v1"}, headers={"Authorization": f"Bearer {token}"},
        ) as response:
            upstream_error(response)
            chunks = []
            size = 0
            async for chunk in response.aiter_bytes(chunk_size=64_000):
                size += len(chunk)
                if size > 10_000_000:
                    raise HTTPException(
                        status_code=413, detail="This stored tool response is too large to display.",
                    )
                chunks.append(chunk)
        try:
            payload = json.loads(b"".join(chunks))
        except json.JSONDecodeError as error:
            raise HTTPException(
                status_code=502, detail="Foundry returned invalid stored tool data.",
            ) from error
        if not isinstance(payload, dict):
            raise HTTPException(status_code=502, detail="Foundry returned invalid stored tool data.")
        items = payload.get("output")
        if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
            raise HTTPException(status_code=502, detail="Foundry returned invalid stored tool data.")
        calls = [
            item for item in items if item.get("type") == "function_call" and item.get("call_id") == call_id
        ]
        if len(calls) != 1 or not isinstance(calls[0].get("arguments"), str):
            raise HTTPException(
                status_code=404, detail="This tool call is unavailable in the stored response.",
            )
        results = [
            item for item in items
            if item.get("type") == "function_call_output" and item.get("call_id") == call_id
        ]
        if len(results) > 1 or (results and "output" not in results[0]):
            raise HTTPException(status_code=502, detail="Foundry returned ambiguous tool results.")
        inputs, input_truncated = payload_text(call_arguments(calls[0]["arguments"]))
        outputs, output_truncated = None, False
        if results:
            name = calls[0].get("name")
            try:
                output, _ = unwrap_tool_output(
                    results[0]["output"], call_id=call_id, name=name if isinstance(name, str) else None,
                )
            except ValueError as error:
                raise HTTPException(
                    status_code=502, detail="Foundry returned invalid tool timing.",
                ) from error
            outputs, output_truncated = payload_text(output)
        return {
            "input": inputs, "output": outputs,
            "input_truncated": input_truncated, "output_truncated": output_truncated,
        }

    async def stream(
        self, token: str, conversation_id: str, request: str | list[dict],
    ) -> AsyncGenerator[dict]:
        """Stream one response: ``request`` is the prompt, or input items such as an approval response."""
        async with self.client.stream(
            "POST", f"{self.agent_url}/responses",
            params={"api-version": "v1"}, headers={"Authorization": f"Bearer {token}"},
            json={"input": request, "conversation": conversation_id, "store": True, "stream": True},
        ) as response:
            if not response.is_success:
                await response.aread()
                upstream_error(response)
            data: list[str] = []
            async for line in response.aiter_lines():
                if line.startswith("data:"):
                    data.append(line[5:].lstrip())
                    if sum(map(len, data)) > 10_000_000:
                        raise HTTPException(
                            status_code=502, detail="Foundry returned an oversized stream event.",
                        )
                elif not line and data:
                    raw = "\n".join(data)
                    data.clear()
                    if raw == "[DONE]":
                        continue
                    payload = json.loads(raw)
                    if not isinstance(payload, dict) or not isinstance(payload.get("type"), str):
                        raise HTTPException(
                            status_code=502, detail="Foundry returned an invalid stream event.",
                        )
                    yield payload
            if data and "\n".join(data) != "[DONE]":
                payload = json.loads("\n".join(data))
                if not isinstance(payload, dict) or not isinstance(payload.get("type"), str):
                    raise HTTPException(status_code=502, detail="Foundry returned an invalid stream event.")
                yield payload


async def resolve_reference(client: httpx.AsyncClient, token: str, reference: Reference) -> Reference:
    try:
        source = sharepoint_file(reference.url)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    headers = {"Authorization": f"Bearer {token}"}
    site_address = (
        f"{source.hostname}:{quote(source.site_path, safe='/')}"
        if source.site_path else source.hostname
    )
    response = await client.get(
        f"https://graph.microsoft.com/v1.0/sites/{site_address}",
        headers=headers, params={"$select": "id"},
    )
    upstream_error(response)
    site_id = response.json().get("id")
    if not isinstance(site_id, str) or not site_id:
        raise HTTPException(status_code=502, detail="Graph did not return a site ID.")
    drives_url = f"https://graph.microsoft.com/v1.0/sites/{quote(site_id, safe='')}/drives"
    next_url = drives_url
    for page in range(10):
        response = await client.get(
            next_url, headers=headers, params={"$select": "id,webUrl", "$top": 100} if page == 0 else None,
        )
        upstream_error(response)
        payload = response.json()
        drives = payload.get("value")
        if not isinstance(drives, list):
            raise HTTPException(status_code=502, detail="Graph returned invalid document libraries.")
        for drive in drives:
            if (
                not isinstance(drive, dict)
                or not isinstance(drive.get("webUrl"), str) or not isinstance(drive.get("id"), str)
            ):
                raise HTTPException(status_code=502, detail="Graph returned an invalid document library.")
            library = urlsplit(drive["webUrl"])
            library_path = unquote(library.path).rstrip("/")
            if library_path.lower().endswith("/forms/allitems.aspx"):
                library_path = library_path[:-len("/Forms/AllItems.aspx")]
            if (
                library.hostname != source.hostname
                or library.scheme != "https" or library.username or library.password
                or library.port not in (None, 443)
            ):
                continue
            if source.unique_id:
                # Graph accepts a SharePoint list item's unique GUID as an item lookup ID.
                item_path = f"items/{source.unique_id}"
            elif source.file_path and source.file_path.casefold().startswith(f"{library_path}/".casefold()):
                relative_path = source.file_path[len(library_path):].lstrip("/")
                item_path = f"root:/{quote(relative_path, safe='/')}"
            else:
                continue
            response = await client.get(
                f"https://graph.microsoft.com/v1.0/drives/{quote(drive['id'], safe='')}/{item_path}",
                headers=headers, params={"$select": "id,file"},
            )
            if source.unique_id and response.status_code == 404:
                continue
            upstream_error(response)
            item = response.json()
            if not isinstance(item.get("file"), dict):
                raise HTTPException(status_code=422, detail="The referenced item is not a previewable file.")
            try:
                return Reference.model_validate({
                    **reference.model_dump(), "drive_id": drive["id"], "item_id": item["id"],
                })
            except (ValidationError, KeyError) as error:
                raise HTTPException(
                    status_code=502, detail="Graph returned invalid file identifiers.",
                ) from error
        next_link = payload.get("@odata.nextLink")
        if next_link is None:
            raise HTTPException(
                status_code=404,
                detail="The referenced file or document library is unavailable to this user.",
            )
        if not isinstance(next_link, str):
            raise HTTPException(status_code=502, detail="Graph returned an invalid library pagination link.")
        parsed = urlsplit(next_link)
        if (
            parsed.scheme != "https" or parsed.hostname != "graph.microsoft.com"
            or parsed.port not in (None, 443) or parsed.username or parsed.password
            or unquote(parsed.path) != unquote(urlsplit(drives_url).path) or parsed.fragment
        ):
            raise HTTPException(
                status_code=502, detail="Graph returned an unsupported library pagination link.",
            )
        next_url = next_link
    raise HTTPException(status_code=413, detail="Too many document libraries to resolve this source.")


async def preview_reference(client: httpx.AsyncClient, token: str, reference: Reference) -> dict:
    if not reference.drive_id or not reference.item_id:
        reference = await resolve_reference(client, token, reference)
    drive_id, item_id = reference.drive_id, reference.item_id
    if not drive_id or not item_id:
        raise HTTPException(status_code=502, detail="Graph did not resolve the file identifiers.")
    url = (
        f"https://graph.microsoft.com/v1.0/drives/{quote(drive_id, safe='')}"
        f"/items/{quote(item_id, safe='')}/preview"
    )
    body = {}
    position = reference.slide or reference.page
    if position is not None:
        body["page"] = str(position)
    response = await client.post(url, headers={"Authorization": f"Bearer {token}"}, json=body)
    warning = None
    if response.status_code == 400 and position is not None:
        response = await client.post(url, headers={"Authorization": f"Bearer {token}"}, json={})
        warning = (
            "The file viewer rejected the requested position. "
            "The preview opens at its default location."
        )
    if response.status_code in (400, 415):
        raise HTTPException(
            status_code=422, detail="This file cannot be previewed. Use Open original.",
        )
    upstream_error(response)
    payload = response.json()
    result: dict = {"warning": warning, "position_verified": False}
    try:
        if isinstance(payload.get("getUrl"), str):
            result["get_url"] = validate_preview_url(payload["getUrl"])
        elif isinstance(payload.get("postUrl"), str) and isinstance(payload.get("postParameters"), str):
            result["post_url"] = validate_preview_url(payload["postUrl"])
            result["post_parameters"] = payload["postParameters"]
        else:
            raise ValueError("Graph did not return a supported preview URL.")
    except ValueError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error
    return result


MAX_DOCUMENT_BYTES = 30 * 1024 * 1024
# Office formats Microsoft Graph converts to PDF with GET .../content?format=pdf.
PDF_CONVERTIBLE = {
    "csv", "doc", "docx", "odp", "ods", "odt", "pot", "potm", "potx", "pps", "ppsx", "ppt", "pptm", "pptx", "rtf",
    "xls", "xlsm", "xlsx",
}
# Hosts of the short-lived, pre-authenticated download and conversion URLs that Graph redirects to.
DOWNLOAD_DOMAINS = ("sharepoint.com", "sharepoint-df.com", "svc.ms")


def download_url(location: str) -> str:
    parsed = urlsplit(location)
    hostname = (parsed.hostname or "").lower()
    if (
        parsed.scheme != "https" or parsed.username or parsed.password or parsed.port not in (None, 443)
        or not any(hostname == domain or hostname.endswith(f".{domain}") for domain in DOWNLOAD_DOMAINS)
    ):
        raise HTTPException(status_code=502, detail="Graph redirected the download to an unsupported host.")
    return location


async def read_capped(client: httpx.AsyncClient, url: str, **kwargs) -> tuple[int, httpx.Headers, bytes]:
    async with client.stream("GET", url, follow_redirects=False, **kwargs) as response:
        if not response.is_success:
            await response.aread()
            return response.status_code, response.headers, response.content
        body = bytearray()
        async for chunk in response.aiter_bytes():
            body.extend(chunk)
            if len(body) > MAX_DOCUMENT_BYTES:
                raise HTTPException(status_code=413, detail="This file is too large to show here. Use Open original.")
        return response.status_code, response.headers, bytes(body)


async def document_pdf(
    client: httpx.AsyncClient, token: str, reference: Reference,
) -> tuple[bytes, str, str | None]:
    """The cited file as PDF bytes, its name, and the web link Graph reports for it, read with the user's delegated
    Graph token, so the app can render it itself: PDFs as they are, Office files converted by Graph. Graph's
    embeddable viewer stays blank in a cross-site frame when the browser blocks third-party storage, as InPrivate
    windows do. The web link comes from Graph, not from the model, so Open original always reaches the file."""
    if not reference.drive_id or not reference.item_id:
        reference = await resolve_reference(client, token, reference)
    drive_id, item_id = reference.drive_id, reference.item_id
    if not drive_id or not item_id:
        raise HTTPException(status_code=502, detail="Graph did not resolve the file identifiers.")
    headers = {"Authorization": f"Bearer {token}"}
    item_url = (
        f"https://graph.microsoft.com/v1.0/drives/{quote(drive_id, safe='')}/items/{quote(item_id, safe='')}"
    )
    response = await client.get(item_url, headers=headers, params={"$select": "id,name,size,file,webUrl"})
    upstream_error(response)
    item = response.json()
    name = item.get("name") if isinstance(item.get("name"), str) else ""
    web_url = None
    if isinstance(item.get("webUrl"), str):
        try:
            web_url = validate_preview_url(item["webUrl"])
        except ValueError:
            web_url = None
    if not isinstance(item.get("file"), dict):
        raise HTTPException(status_code=422, detail="The referenced item is not a previewable file.")
    mime = str(item["file"].get("mimeType") or "").lower()
    extension = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if mime == "application/pdf" or extension == "pdf":
        params: dict[str, str] | None = None
        size = item.get("size")
        if isinstance(size, int) and size > MAX_DOCUMENT_BYTES:
            raise HTTPException(status_code=413, detail="This file is too large to show here. Use Open original.")
    elif extension in PDF_CONVERTIBLE:
        params = {"format": "pdf"}
    else:
        raise HTTPException(status_code=415, detail="This file type can't be shown here. Use Open original.")
    status, response_headers, body = await read_capped(
        client, f"{item_url}/content", headers=headers, params=params,
    )
    if status in (301, 302, 303, 307, 308):
        location = download_url(response_headers.get("location") or "")
        # A pre-authenticated URL: the Graph token is never forwarded to it.
        status, response_headers, body = await read_capped(client, location)
    if params and status in (400, 406, 415, 501):
        raise HTTPException(status_code=415, detail="This file can't be converted for preview. Use Open original.")
    upstream_error(httpx.Response(status, headers=response_headers))
    if not body.startswith(b"%PDF-"):
        raise HTTPException(status_code=502, detail="The file service did not return a PDF.")
    return body, name, web_url


def render_page(content: bytes, page: int | None) -> dict:
    """One page of a PDF as a PNG, so the app shows exactly the cited page the same way in every browser."""
    try:
        rendered = pdfpage.render_page(content, page)
    except pdfpage.EmptyPdf as error:
        raise HTTPException(status_code=422, detail="The PDF has no pages.") from error
    except pdfpage.UnreadablePdf as error:
        raise HTTPException(status_code=502, detail="The file service returned an unreadable PDF.") from error
    return {
        "page": rendered.page, "page_count": rendered.page_count, "width": rendered.width,
        "height": rendered.height, "image": base64.b64encode(rendered.png).decode("ascii"),
    }
