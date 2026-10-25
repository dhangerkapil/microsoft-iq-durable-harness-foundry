from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable
from contextlib import AsyncExitStack, aclosing, asynccontextmanager, suppress
from dataclasses import dataclass
from typing import Annotated, TypeVar
from uuid import UUID

import httpx
import uvicorn
from ag_ui.core import (
    CustomEvent,
    RunAgentInput,
    RunErrorEvent,
    RunFinishedEvent,
    RunStartedEvent,
    TextMessageContentEvent,
)
from ag_ui.encoder import EventEncoder
from azure.core.exceptions import ClientAuthenticationError, ServiceRequestError
from azure.cosmos.aio import CosmosClient
from azure.cosmos.exceptions import CosmosHttpResponseError
from azure.identity.aio import ManagedIdentityCredential
from fastapi import Depends, FastAPI, HTTPException, Request, Security
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import ValidationError

from .auth import Authenticator, Principal, TokenBroker
from .config import UIConfig
from .events import DURABLE_STOP, ResponseEvents
from .foundry import FoundryGateway, document_pdf, preview_reference, render_page
from .grounding import Evidence
from .models import (
    ApprovalDecision, DocumentRequest, PreviewRequest, Reference, RenameSession, RunRecord, SourceRequest,
)
from .packs import PacksGateway, PacksNext, PacksReply, PacksStart, identifier
from .sources import source_view
from .store import RunCheckpointer, SessionStore, now, public_session, session_id

logger = logging.getLogger(__name__)
bearer = HTTPBearer(auto_error=False)
T = TypeVar("T")
# A review pack runs for minutes: its response sends a space this often until the JSON is ready, so idle connections
# between the browser and the app stay open.
PACKS_KEEPALIVE_SECONDS = 15.0


def approval_decision(forwarded: object) -> ApprovalDecision | None:
    """The advisor's approval decision from AG-UI forwardedProps, or None for an ordinary prompt."""
    if forwarded is None or forwarded == {}:
        return None
    if not isinstance(forwarded, dict) or set(forwarded) != {"approval"}:
        raise HTTPException(status_code=422, detail="Unsupported forwarded properties.")
    try:
        return ApprovalDecision.model_validate(forwarded["approval"])
    except ValidationError as error:
        raise HTTPException(status_code=422, detail="Invalid approval decision.") from error

async def with_heartbeats(
    source: AsyncGenerator[dict], interval: float = 15, *, watch: asyncio.Task[None] | None = None,
) -> AsyncGenerator[dict | None]:
    async with aclosing(source):
        pending = asyncio.create_task(anext(source))
        try:
            while True:
                done, _ = await asyncio.wait(
                    {pending, watch} if watch else {pending}, timeout=interval,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if watch is not None and watch in done:
                    watch.result()
                    raise RuntimeError("The run checkpoint writer stopped during streaming.")
                if not done:
                    yield None
                    continue
                try:
                    yield pending.result()
                except StopAsyncIteration:
                    return
                pending = asyncio.create_task(anext(source))
        finally:
            pending.cancel()
            with suppress(asyncio.CancelledError, StopAsyncIteration):
                await pending


@dataclass
class Services:
    auth: Authenticator
    tokens: TokenBroker
    store: SessionStore
    foundry: FoundryGateway
    client: httpx.AsyncClient
    packs: PacksGateway | None = None


async def grounded_reference(
    current: Services, principal: Principal, document: dict, reference: Reference,
) -> Reference | None:
    """The reference with IDs repaired from this conversation's tool output, or None if nothing changed."""
    token = await current.tokens.delegated(principal, "https://ai.azure.com")
    items = await current.foundry.items(
        token, document["conversation_id"], legacy=document.get("conversation_scope") != "hosted",
    )
    repaired = Evidence.from_items(items).repair(reference)
    return None if repaired == reference else repaired


async def with_cited_file(
    current: Services, principal: Principal, document: dict, reference: Reference,
    operation: Callable[[httpx.AsyncClient, str, Reference], Awaitable[T]],
) -> T:
    """Run a Graph file operation on a cited file, repairing IDs the model mis-copied."""
    token = await current.tokens.delegated(principal, "https://graph.microsoft.com")
    try:
        return await operation(current.client, token, reference)
    except HTTPException as error:
        # A 404 or 422 with cited IDs can mean the model mis-copied them.
        if error.status_code not in (404, 422) or not (reference.drive_id and reference.item_id):
            raise
        repaired = await grounded_reference(current, principal, document, reference)
        if repaired is not None:
            return await operation(current.client, token, repaired)
        try:
            # Last resort: resolve the file from its link instead of the cited IDs.
            return await operation(current.client, token, reference.model_copy(update={
                "drive_id": None, "item_id": None, "entity_url": None,
            }))
        except HTTPException:
            raise error from None


async def hosted_conversation(current: Services, document: dict, token: str) -> dict:
    if document.get("conversation_scope") == "hosted":
        return document
    if await current.foundry.has_legacy_history(token, document["conversation_id"]):
        raise HTTPException(
            status_code=409,
            detail=(
                "This session has history in the old conversation store. "
                "Start a new session; history is retained."
            ),
        )
    conversation_id = await current.foundry.create_conversation(token)
    try:
        return await current.store.attach_hosted_conversation(document, conversation_id)
    except (HTTPException, CosmosHttpResponseError, ClientAuthenticationError, ServiceRequestError):
        await current.foundry.delete_conversation(token, conversation_id)
        raise


async def signed_in_user(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Security(bearer)],
) -> Principal:
    if credentials is None:
        raise HTTPException(
            status_code=401, detail="Sign in to use this API.", headers={"WWW-Authenticate": "Bearer"},
        )
    services: Services = request.app.state.services
    return await asyncio.to_thread(services.auth.validate, credentials.credentials)


SignedInUser = Annotated[Principal, Depends(signed_in_user)]


def create_app(config: UIConfig | None = None, services: Services | None = None) -> FastAPI:
    settings = config or UIConfig.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if services is not None:
            app.state.services = services
            yield
            return
        async with AsyncExitStack() as resources:
            credential = ManagedIdentityCredential(client_id=settings.identity_client_id)
            resources.push_async_callback(credential.close)
            cosmos = CosmosClient(settings.cosmos_endpoint, credential=credential)
            resources.push_async_callback(cosmos.close)
            container = cosmos.get_database_client(settings.cosmos_database).get_container_client(
                settings.cosmos_container,
            )
            client = await resources.enter_async_context(httpx.AsyncClient(
                timeout=httpx.Timeout(180, connect=20), follow_redirects=False,
            ))
            tokens = TokenBroker(settings)
            resources.callback(tokens.close)
            app.state.services = Services(
                Authenticator(settings), tokens, SessionStore(container),
                FoundryGateway(settings, client), client, PacksGateway(settings, client),
            )
            yield

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware("http")
    async def response_headers(request: Request, call_next):
        origin = request.headers.get("origin")
        if (
            request.method not in ("GET", "HEAD") and origin
            and origin not in (settings.origin, *settings.additional_origins)
        ):
            return JSONResponse(status_code=403, content={"detail": "Unexpected request origin."})
        response = await call_next(request)
        auth_bridge = request.url.path == "/auth.html"
        frame_ancestors = "'self'" if auth_bridge else "'none'"
        response.headers.update({
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "no-referrer",
            "Content-Security-Policy": (
                "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
                "connect-src 'self' https://login.microsoftonline.com; "
                "img-src 'self' data:; object-src 'none'; base-uri 'self'; "
                f"frame-ancestors {frame_ancestors}; "
                "frame-src 'self' https://login.microsoftonline.com "
                "https://*.sharepoint.com https://*.sharepoint-df.com "
                "https://*.officeapps.live.com https://*.office.com https://*.onedrive.com "
                "https://onedrive.live.com https://*.microsoft365.com; "
                "form-action 'self' https://*.sharepoint.com https://*.officeapps.live.com "
                "https://*.office.com https://*.onedrive.com https://onedrive.live.com"
            ),
        })
        response.headers.setdefault("Cache-Control", "no-store")
        if not auth_bridge:
            response.headers["Cross-Origin-Opener-Policy"] = "same-origin-allow-popups"
        return response

    @app.get("/healthz")
    async def health() -> dict:
        return {"status": "ok"}

    @app.get("/readyz")
    async def ready(request: Request) -> dict:
        try:
            await request.app.state.services.store.ready()
        except (
            CosmosHttpResponseError, httpx.HTTPError, ClientAuthenticationError, ServiceRequestError,
        ) as error:
            logger.warning("UI session store readiness failed: %s", type(error).__name__)
            raise HTTPException(status_code=503, detail="Session storage is not ready.") from error
        return {"status": "ready"}

    @app.get("/api/config")
    async def public_config() -> dict:
        return settings.public()

    def packs_gateway(request: Request) -> PacksGateway:
        gateway = request.app.state.services.packs
        if gateway is None:
            raise HTTPException(status_code=404, detail="Review packs are unavailable in this environment.")
        return gateway

    @app.post("/api/packs", status_code=202)
    async def start_packs(body: PacksStart, request: Request, principal: SignedInUser) -> dict:
        gateway = packs_gateway(request)
        token = await request.app.state.services.tokens.delegated(principal, "https://ai.azure.com")
        return await gateway.start(token, body)

    @app.post("/api/packs/{conversation_id}/reply", status_code=202)
    async def packs_reply(
        conversation_id: str, body: PacksReply, request: Request, principal: SignedInUser,
    ) -> dict:
        gateway = packs_gateway(request)
        token = await request.app.state.services.tokens.delegated(principal, "https://ai.azure.com")
        return await gateway.reply(token, identifier(conversation_id, "conversation ID"), body)

    @app.post("/api/packs/{conversation_id}/next")
    async def packs_next(
        conversation_id: str, body: PacksNext, request: Request, principal: SignedInUser,
    ) -> StreamingResponse:
        """One client's pack (minutes): JSON, preceded by keep-alive whitespace while the pack runs."""
        gateway = packs_gateway(request)
        conversation = identifier(conversation_id, "conversation ID")
        token = await request.app.state.services.tokens.delegated(principal, "https://ai.azure.com")

        async def body_chunks() -> AsyncIterator[str]:
            step = asyncio.create_task(gateway.next(token, conversation, body))
            try:
                while True:
                    done, _ = await asyncio.wait({step}, timeout=PACKS_KEEPALIVE_SECONDS)
                    if done:
                        break
                    yield " "
                try:
                    view = step.result()
                except HTTPException as error:
                    view = {"run": body.run, "status": "failed", "cards": [], "text": "", "progress": None,
                            "error": str(error.detail)}
                yield json.dumps(view)
            finally:
                if not step.done():
                    step.cancel()

        return StreamingResponse(body_chunks(), media_type="application/json", headers={"Cache-Control": "no-store"})

    @app.get("/api/sessions")
    async def list_sessions(request: Request, principal: SignedInUser) -> list[dict]:
        return await request.app.state.services.store.list(principal.owner(settings))

    @app.post("/api/sessions", status_code=201)
    async def create_session(request: Request, principal: SignedInUser) -> dict:
        current: Services = request.app.state.services
        token = await current.tokens.delegated(principal, "https://ai.azure.com")
        conversation_id = await current.foundry.create_conversation(token)
        try:
            document = await current.store.create(principal.owner(settings), conversation_id)
        except (CosmosHttpResponseError, ServiceRequestError, ClientAuthenticationError):
            await current.foundry.delete_conversation(token, conversation_id)
            raise
        return public_session(document)

    @app.get("/api/sessions/{identifier}")
    async def get_session(
        identifier: str, request: Request, principal: SignedInUser,
    ) -> dict:
        current: Services = request.app.state.services
        owner = principal.owner(settings)
        document = await current.store.get(owner, identifier)
        token = await current.tokens.delegated(principal, "https://ai.azure.com")
        messages = asyncio.create_task(current.foundry.messages(
            token, document["conversation_id"], legacy=document.get("conversation_scope") != "hosted",
        ))
        runs = asyncio.create_task(current.store.runs(owner, identifier))
        try:
            await asyncio.gather(messages, runs)
        finally:
            for task in (messages, runs):
                if not task.done():
                    task.cancel()
            await asyncio.gather(messages, runs, return_exceptions=True)
        return {
            "session": public_session(document), "messages": messages.result(), "runs": runs.result(),
        }

    @app.patch("/api/sessions/{identifier}")
    async def rename_session(
        identifier: str, body: RenameSession, request: Request,
        principal: SignedInUser,
    ) -> dict:
        document = await request.app.state.services.store.rename(
            principal.owner(settings), identifier, body.title,
        )
        return public_session(document)

    @app.get("/api/sessions/{identifier}/runs/{run_id}/calls/{call_id}")
    async def tool_details(
        identifier: str, run_id: str, call_id: str, request: Request, principal: SignedInUser,
    ) -> dict:
        current: Services = request.app.state.services
        record = await current.store.get_run(principal.owner(settings), identifier, run_id)
        if not any(call.call_id == call_id for call in record.activity):
            raise HTTPException(status_code=404, detail="Tool call not found.")
        token = await current.tokens.delegated(principal, "https://ai.azure.com")
        return await current.foundry.tool_details(token, record, call_id)

    @app.delete("/api/sessions/{identifier}")
    async def delete_session(
        identifier: str, request: Request, principal: SignedInUser,
    ) -> dict:
        current: Services = request.app.state.services
        owner = principal.owner(settings)
        await current.store.get(owner, identifier)
        token = await current.tokens.delegated(principal, "https://ai.azure.com")
        document = await current.store.begin_delete(owner, identifier)
        try:
            if document.get("legacy_conversation_id"):
                await current.foundry.delete_conversation(
                    token, document["legacy_conversation_id"], legacy=True,
                )
            await current.foundry.delete_conversation(
                token, document["conversation_id"], legacy=document.get("conversation_scope") != "hosted",
            )
        except (HTTPException, httpx.HTTPError):
            await current.store.cancel_delete(document)
            raise
        await current.store.remove(owner, identifier)
        return {"deleted": True}

    @app.post("/api/sessions/{identifier}/preview")
    async def preview(
        identifier: str, body: PreviewRequest, request: Request,
        principal: SignedInUser,
    ) -> dict:
        current: Services = request.app.state.services
        document = await current.store.get(principal.owner(settings), identifier)
        return await with_cited_file(current, principal, document, body.reference, preview_reference)

    @app.post("/api/sessions/{identifier}/document")
    async def document_view(
        identifier: str, body: DocumentRequest, request: Request,
        principal: SignedInUser,
    ) -> dict:
        """One page of the cited file, rendered by the app: a PDF as it is, an Office file converted by Graph."""
        current: Services = request.app.state.services
        document = await current.store.get(principal.owner(settings), identifier)
        content, _, web_url = await with_cited_file(current, principal, document, body.reference, document_pdf)
        page = body.page or body.reference.slide or body.reference.page
        rendered = await asyncio.to_thread(render_page, content, page)
        return {**rendered, "web_url": web_url}

    @app.post("/api/sessions/{identifier}/source")
    async def source(
        identifier: str, body: SourceRequest, request: Request,
        principal: SignedInUser,
    ) -> dict:
        current: Services = request.app.state.services
        document = await current.store.get(principal.owner(settings), identifier)
        token = await current.tokens.delegated(principal, "https://graph.microsoft.com")
        try:
            return await source_view(current.client, token, body.reference)
        except HTTPException as error:
            if error.status_code != 404 or not body.reference.entity_url:
                raise
            repaired = await grounded_reference(current, principal, document, body.reference)
            if repaired is None:
                raise HTTPException(
                    status_code=404,
                    detail="This item isn't among the results Work IQ returned in this session. Use Open original.",
                ) from error
            return await source_view(current.client, token, repaired)

    @app.post("/api/chat")
    async def chat(
        body: RunAgentInput, request: Request, principal: SignedInUser,
    ) -> StreamingResponse:
        started = time.perf_counter_ns()
        current: Services = request.app.state.services
        identifier = session_id(body.thread_id)
        try:
            run_id = str(UUID(body.run_id))
        except ValueError as error:
            raise HTTPException(status_code=422, detail="Invalid run ID.") from error
        if body.tools or body.context or body.resume:
            raise HTTPException(
                status_code=422, detail="Client-supplied tools, context and approvals are unsupported.",
            )
        approval = approval_decision(body.forwarded_props)
        request_input: str | list[dict]
        if approval is None:
            if not body.messages or body.messages[-1].role != "user":
                raise HTTPException(status_code=422, detail="A final user message is required.")
            prompt = body.messages[-1].content
            if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 20_000:
                raise HTTPException(
                    status_code=422, detail="Send a nonempty text prompt of at most 20,000 characters.",
                )
            request_input, preview_text = prompt, prompt.strip()[:160]
        else:
            # The hosted agent resumes the approval-gated call only after this explicit decision.
            request_input = [{
                "type": "mcp_approval_response", "approval_request_id": approval.id, "approve": approval.approve,
            }]
            preview_text = "Approved the proposed action" if approval.approve else "Rejected the proposed action"
        owner = principal.owner(settings)
        document = await current.store.get(owner, identifier)
        token = await current.tokens.delegated(principal, "https://ai.azure.com")
        document = await hosted_conversation(current, document, token)
        record = RunRecord(
            id=run_id, session_id=identifier, prompt_preview=preview_text, started_at=now(),
        )
        document = await current.store.claim(owner, record)
        record.latency.preparation_ms = (time.perf_counter_ns() - started) // 1_000_000
        encoder = EventEncoder(accept="text/event-stream")

        async def events() -> AsyncIterator[str]:
            adapter = ResponseEvents(record, settings.project_endpoint, continuation=approval is not None)
            checkpoints = RunCheckpointer(current.store, owner)
            released = False
            try:
                yield encoder.encode(RunStartedEvent(thread_id=identifier, run_id=run_id))
                yield encoder.encode(adapter.activity_event())
                async with asyncio.timeout(540):
                    async for raw in with_heartbeats(
                        current.foundry.stream(token, document["conversation_id"], request_input),
                        watch=checkpoints.task,
                    ):
                        if raw is None:
                            yield ": keep-alive\n\n"
                            continue
                        elapsed = (time.perf_counter_ns() - started) // 1_000_000
                        if record.latency.first_event_ms is None:
                            record.latency.first_event_ms = elapsed
                        for event in adapter.consume(raw):
                            if record.latency.first_text_ms is None and isinstance(
                                event, TextMessageContentEvent,
                            ):
                                record.latency.first_text_ms = elapsed
                            if isinstance(event, CustomEvent) and event.name == "workiq.activity":
                                checkpoints.submit(record)
                            yield encoder.encode(event)
                if not adapter.terminal:
                    raise HTTPException(
                        status_code=502, detail="The hosted agent stream ended without a result.",
                    )
                record.latency.response_ms = (time.perf_counter_ns() - started) // 1_000_000
                await checkpoints.finish(record)
                await current.store.release(owner, record)
                released = True
                yield encoder.encode(adapter.activity_event())
                yield encoder.encode(RunFinishedEvent(
                    thread_id=identifier, run_id=run_id, result={"status": record.status},
                ))
            except asyncio.CancelledError:
                adapter.interrupt(failed=False)
                raise
            except (
                HTTPException, httpx.HTTPError, json.JSONDecodeError, CosmosHttpResponseError, TimeoutError,
                ClientAuthenticationError, ServiceRequestError,
            ) as error:
                stopped = adapter.stopped_durable_pack()
                adapter.interrupt(failed=True, stopped=stopped)
                logger.warning("Hosted UI run failed: %s", type(error).__name__)
                detail = (
                    DURABLE_STOP if stopped
                    else error.detail if isinstance(error, HTTPException)
                    else "The request failed; reload and retry."
                )
                yield encoder.encode(adapter.activity_event())
                yield encoder.encode(RunErrorEvent(message=str(detail), code="hosted_request_failed"))
            finally:
                if not released and record.status not in ("failed", "cancelled"):
                    adapter.interrupt(failed=True)
                await checkpoints.cancel()
                if not released:
                    await asyncio.shield(current.store.save_run(owner, record))
                    await asyncio.shield(current.store.release(owner, record))
                logger.info(
                    "UI run finished: status=%s preparation_ms=%s first_event_ms=%s first_text_ms=%s "
                    "response_ms=%s elapsed_ms=%d checkpoint_count=%d checkpoint_ms=%d",
                    record.status, record.latency.preparation_ms, record.latency.first_event_ms,
                    record.latency.first_text_ms, record.latency.response_ms,
                    (time.perf_counter_ns() - started) // 1_000_000,
                    checkpoints.write_count, checkpoints.write_ms,
                )

        return StreamingResponse(
            events(), media_type="text/event-stream",
            headers={"X-Accel-Buffering": "no", "Cache-Control": "no-store"},
        )

    @app.get("/{path:path}")
    async def frontend(path: str) -> FileResponse:
        if path.startswith("api/"):
            raise HTTPException(status_code=404, detail="API route not found.")
        root = settings.static_directory.resolve()
        candidate = (root / path).resolve()
        if not candidate.is_relative_to(root):
            raise HTTPException(status_code=404, detail="File not found.")
        if candidate.is_file():
            cacheable = (
                candidate.parent == root / "assets"
                and re.fullmatch(r".+-[A-Za-z0-9_-]{8,}\.(?:js|css)", candidate.name)
            )
            return FileResponse(candidate, headers={
                "Cache-Control": "public, max-age=31536000, immutable" if cacheable else "no-store",
            })
        index = root / "index.html"
        if not index.is_file():
            raise HTTPException(status_code=503, detail="The UI assets have not been built.")
        return FileResponse(index)

    return app


def main() -> None:
    logging.basicConfig(level=logging.WARNING)
    logger.setLevel(logging.INFO)
    uvicorn.run(create_app(), host="0.0.0.0", port=8080, access_log=False)


if __name__ == "__main__":
    main()
