import asyncio
import base64
import json
import struct
import time
import zlib
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from urllib.parse import urlencode
from uuid import uuid4

import httpx
import jwt
import pytest
from ag_ui.core import EventType
from azure.cosmos.aio import ContainerProxy
from azure.cosmos.exceptions import CosmosHttpResponseError, CosmosResourceNotFoundError
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError

from advisor_team.execution import TIMING_KEY
from advisor_team.ui.auth import Authenticator, Principal
from advisor_team.ui.config import UIConfig
from advisor_team.ui.events import DURABLE_STOP, ResponseEvents, iq_detail
from advisor_team.ui.foundry import FoundryGateway, document_pdf, preview_reference, render_page
from advisor_team.ui.grounding import Evidence
from advisor_team.ui.models import Reference, RunRecord, ToolActivity
from advisor_team.ui.payloads import payload_text
from advisor_team.ui.references import (
    VerifiedPages,
    references_from_text,
    sharepoint_file,
    validate_preview_url,
)
from advisor_team.ui.server import Services, create_app, with_heartbeats
from advisor_team.ui.sources import eastern, html_text, source_view
from advisor_team.ui.store import RunCheckpointer, SessionStore, now

TENANT = "11111111-1111-4111-8111-111111111111"
API = "22222222-2222-4222-8222-222222222222"
BROWSER = "33333333-3333-4333-8333-333333333333"
ALICE = "44444444-4444-4444-8444-444444444444"
BOB = "55555555-5555-4555-8555-555555555555"


@pytest.fixture
def config(tmp_path: Path) -> UIConfig:
    return UIConfig(
        tenant_id=TENANT, api_client_id=API, browser_client_id=BROWSER,
        identity_client_id="66666666-6666-4666-8666-666666666666",
        origin="https://ui.example.com",
        project_endpoint="https://example.services.ai.azure.com/api/projects/workplace",
        agent_name="workplace-productivity",
        cosmos_endpoint="https://example.documents.azure.com",
        cosmos_database="workplace", cosmos_container="uiSessions", static_directory=tmp_path,
    )


@pytest.fixture(scope="module")
def signing_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def user_token(config: UIConfig, signing_key, **changed) -> str:
    claims = {
        "exp": int(time.time()) + 300, "iat": int(time.time()),
        "iss": f"https://login.microsoftonline.com/{config.tenant_id}/v2.0",
        "aud": config.api_client_id, "tid": config.tenant_id, "oid": ALICE,
        "azp": config.browser_client_id, "scp": "access_as_user",
    }
    claims.update(changed)
    return jwt.encode(claims, signing_key, algorithm="RS256", headers={"kid": "ui-test"})


def authenticator(config: UIConfig, signing_key) -> Authenticator:
    auth = Authenticator(config)
    auth.keys = MagicMock(get_signing_key_from_jwt=MagicMock(
        return_value=SimpleNamespace(key=signing_key.public_key()),
    ))
    return auth


@pytest.mark.parametrize("changed", [
    {"aud": BROWSER}, {"tid": BOB}, {"azp": BOB}, {"scp": "Files.Read"},
    {"oid": "not-an-object-id"}, {"exp": 1}, {"iss": "https://evil.example.com"},
])
def test_ui_rejects_wrong_audience_tenant_caller_scope_and_expired_users(
    config: UIConfig, signing_key, changed: dict,
) -> None:
    with pytest.raises(HTTPException) as error:
        authenticator(config, signing_key).validate(user_token(config, signing_key, **changed))
    assert error.value.status_code == 401


def test_ui_validates_signature_and_partitions_by_trusted_user(config: UIConfig, signing_key) -> None:
    auth = authenticator(config, signing_key)
    token = user_token(config, signing_key)
    principal = auth.validate(token)
    assert principal.object_id == ALICE
    assert token not in repr(principal)
    assert principal.owner(config) != Principal(TENANT, BOB, token).owner(config)
    other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    with pytest.raises(HTTPException):
        auth.validate(user_token(config, other_key))


@pytest.fixture
def memory_store() -> SessionStore:
    documents: dict[tuple[str, str], dict] = {}
    counter = 0
    container = MagicMock(spec=ContainerProxy)

    def save(body: dict) -> dict:
        nonlocal counter
        counter += 1
        result = {**deepcopy(body), "_etag": str(counter)}
        documents[(body["owner"], body["id"])] = result
        return deepcopy(result)

    async def read(item: str, partition_key: str) -> dict:
        document = documents.get((partition_key, item))
        if document is None:
            raise CosmosResourceNotFoundError(status_code=404, message="not found")
        return deepcopy(document)

    async def replace(item: str, body: dict, *, etag: str, **kwargs) -> dict:
        current = documents[(body["owner"], item)]
        if current["_etag"] != etag:
            raise CosmosHttpResponseError(status_code=412, message="changed")
        return save(body)

    def query(query: str, *, partition_key: str, parameters=None):
        async def values():
            wanted = "session" if "c.kind = 'session'" in query else "run"
            session = parameters[0]["value"] if parameters else None
            for (owner, _), document in list(documents.items()):
                if owner == partition_key and document["kind"] == wanted:
                    if session is None or document["session_id"] == session:
                        yield deepcopy(document)
        return values()

    async def delete(item: str, partition_key: str) -> None:
        del documents[(partition_key, item)]

    container.create_item = AsyncMock(side_effect=save)
    container.upsert_item = AsyncMock(side_effect=save)
    container.read_item = AsyncMock(side_effect=read)
    container.replace_item = AsyncMock(side_effect=replace)
    container.delete_item = AsyncMock(side_effect=delete)
    container.query_items.side_effect = query
    return SessionStore(container)


async def test_session_catalog_history_and_trace_cannot_cross_users(memory_store: SessionStore) -> None:
    alice = await memory_store.create("alice", "conv_alice")
    await memory_store.create("bob", "conv_bob")
    assert [session["id"] for session in await memory_store.list("alice")] == [alice["session_id"]]
    with pytest.raises(HTTPException) as error:
        await memory_store.get("bob", alice["session_id"])
    assert error.value.status_code == 404
    with pytest.raises(HTTPException):
        await memory_store.runs("bob", alice["session_id"])
    with pytest.raises(HTTPException):
        await memory_store.remove("bob", alice["session_id"])


async def test_session_claim_serializes_prompts_and_preserves_activity(memory_store: SessionStore) -> None:
    session = await memory_store.create("alice", "conv_alice")
    record = RunRecord(
        id=str(uuid4()), session_id=session["session_id"], prompt_preview="Say hello", started_at=now(),
    )
    await memory_store.claim("alice", record)
    with pytest.raises(HTTPException) as error:
        await memory_store.claim("alice", record.model_copy(update={"id": str(uuid4())}))
    assert error.value.status_code == 409
    adapter = ResponseEvents(record, "https://example.services.ai.azure.com/api/projects/workplace")
    adapter.start_call({"call_id": "call_1", "id": "item_1", "name": "workiq___ask"})
    adapter.finish_call({"call_id": "call_1", "output": '{"isError":false}'})
    record.status = "completed"
    await memory_store.save_run("alice", record)
    await memory_store.release("alice", record)
    runs = await memory_store.runs("alice", session["session_id"])
    assert runs[0]["activity"][0]["name"] == "workiq___ask"
    assert runs[0]["activity"][0]["status"] == "completed"
    assert not (await memory_store.list("alice"))[0]["running"]
    await memory_store.remove("alice", session["session_id"])
    assert await memory_store.list("alice") == []


async def test_delete_marker_blocks_new_prompts_and_can_be_rolled_back(memory_store: SessionStore) -> None:
    session = await memory_store.create("alice", "conv_alice")
    record = RunRecord(
        id=str(uuid4()), session_id=session["session_id"], prompt_preview="Hello", started_at=now(),
    )
    marked = await memory_store.begin_delete("alice", record.session_id)
    with pytest.raises(HTTPException) as error:
        await memory_store.claim("alice", record)
    assert error.value.status_code == 409
    with pytest.raises(HTTPException):
        await memory_store.rename("alice", record.session_id, "Changed")
    await memory_store.cancel_delete(marked)
    await memory_store.claim("alice", record)
    with pytest.raises(HTTPException):
        await memory_store.begin_delete("alice", record.session_id)


async def test_stream_heartbeats_preserve_pending_event_and_close_upstream() -> None:
    closed = False

    async def source():
        nonlocal closed
        try:
            await asyncio.sleep(0.03)
            yield {"type": "response.created"}
            await asyncio.sleep(10)
        finally:
            closed = True

    stream = with_heartbeats(source(), interval=0.01)
    assert await anext(stream) is None
    result = None
    while result is None:
        result = await anext(stream)
    assert result["type"] == "response.created"
    await stream.aclose()
    assert closed


async def test_activity_checkpoints_do_not_block_delivery_and_coalesce_to_a_durable_final_snapshot():
    first_write = asyncio.Event()
    release_write = asyncio.Event()
    snapshots = []

    async def save(owner: str, record: RunRecord):
        assert owner == "alice"
        snapshots.append(record)
        first_write.set()
        await release_write.wait()

    store = MagicMock(save_run=save)
    writer = RunCheckpointer(store, "alice", interval=0)
    record = RunRecord(id=str(uuid4()), session_id=str(uuid4()), prompt_preview="Synthetic", started_at=now())
    try:
        writer.submit(record)
        await asyncio.wait_for(first_write.wait(), timeout=1)
        for stage in ("planning", "searching", "answering"):
            record.stage = stage
            writer.submit(record)
        record.status = "completed"
        finish = asyncio.create_task(writer.finish(record))
        await asyncio.sleep(0)
        assert not finish.done()
        assert len(snapshots) == 1 and snapshots[0].status == "running"
        release_write.set()
        await finish
        assert len(snapshots) == 2
        assert snapshots[-1].status == "completed" and snapshots[-1].stage == "answering"
    finally:
        release_write.set()
        await writer.cancel()


async def test_stopping_checkpoints_drains_in_flight_writes_before_fallback_metadata_is_saved():
    started = asyncio.Event()
    release = asyncio.Event()
    finished = asyncio.Event()

    async def save(owner, record):
        started.set()
        await release.wait()
        finished.set()

    writer = RunCheckpointer(MagicMock(save_run=save), "alice", interval=0)
    writer.submit(RunRecord(
        id=str(uuid4()), session_id=str(uuid4()), prompt_preview="Synthetic", started_at=now(),
    ))
    await asyncio.wait_for(started.wait(), timeout=1)
    stop = asyncio.create_task(writer.cancel())
    await asyncio.sleep(0)
    assert not stop.done() and not finished.is_set()
    release.set()
    await stop
    assert finished.is_set()


async def test_checkpoint_failure_interrupts_an_idle_upstream_and_closes_it():
    failure = CosmosHttpResponseError(status_code=429, message="Synthetic storage failure")
    store = MagicMock(save_run=AsyncMock(side_effect=failure))
    writer = RunCheckpointer(store, "alice", interval=0)
    closed = asyncio.Event()

    async def source():
        try:
            await asyncio.Event().wait()
            yield {"type": "unreachable"}
        finally:
            closed.set()

    stream = with_heartbeats(source(), interval=1, watch=writer.task)
    writer.submit(RunRecord(
        id=str(uuid4()), session_id=str(uuid4()), prompt_preview="Synthetic", started_at=now(),
    ))
    try:
        with pytest.raises(CosmosHttpResponseError) as error:
            await asyncio.wait_for(anext(stream), timeout=1)
        assert error.value is failure
        assert closed.is_set()
    finally:
        await stream.aclose()
        await writer.cancel()


def test_stream_maps_actual_tool_skill_and_text_without_private_reasoning() -> None:
    record = RunRecord(
        id=str(uuid4()), session_id=str(uuid4()), prompt_preview="Find a source", started_at=now(),
    )
    adapter = ResponseEvents(record, "https://example.services.ai.azure.com/api/projects/workplace")
    assert adapter.consume({"type": "response.reasoning_text.delta", "delta": "private reasoning"}) == []
    emitted = adapter.consume({
        "type": "response.output_item.added",
        "item": {"id": "item_1", "call_id": "call_1", "name": "load_skill", "type": "function_call"},
    })
    assert emitted[0].type == EventType.TOOL_CALL_START
    arguments = '{"skill_name":"document-finder"}'
    adapter.consume({
        "type": "response.function_call_arguments.delta", "item_id": "item_1", "delta": arguments,
    })
    adapter.consume({
        "type": "response.function_call_arguments.done", "item_id": "item_1", "arguments": arguments,
    })
    adapter.consume({
        "type": "response.output_item.done",
        "item": {"type": "function_call_output", "call_id": "call_1", "output": '{"isError":false}'},
    })
    adapter.consume({"type": "response.output_text.delta", "item_id": "msg_1", "delta": "A public answer."})
    emitted = adapter.consume({
        "type": "response.completed",
        "response": {"id": "resp_1", "status": "completed", "output": [{
            "type": "message", "role": "assistant", "id": "msg_1",
            "content": [{"type": "output_text", "text": "A public answer."}],
        }]},
    })
    assert any(event.type == EventType.TEXT_MESSAGE_END for event in emitted)
    assert record.activity[0].kind == "skill"
    assert record.activity[0].skill == "document-finder"
    assert record.activity[0].status == "completed"
    assert record.assistant_message_ids == ["msg_1"]
    assert adapter.terminal


def test_stream_accepts_arguments_the_hosting_adapter_sends_twice() -> None:
    # agent-framework-foundry-hosting 1.0.0b261015 streams the arguments, then sends them whole once more.
    record = RunRecord(id=str(uuid4()), session_id=str(uuid4()), prompt_preview="Prepare", started_at=now())
    adapter = ResponseEvents(record, "https://example.services.ai.azure.com/api/projects/workplace")
    adapter.consume({
        "type": "response.output_item.added",
        "item": {"id": "item_1", "call_id": "call_1", "name": "load_skill", "type": "function_call"},
    })
    whole = '{"skill_name":"meeting-prep"}'
    for delta in ('{"skill_', 'name":"meeting', '-prep"}', whole):
        adapter.consume({"type": "response.function_call_arguments.delta", "item_id": "item_1", "delta": delta})
    emitted = adapter.consume({
        "type": "response.function_call_arguments.done", "item_id": "item_1", "arguments": whole + whole,
    })
    assert any(event.type == EventType.TOOL_CALL_END for event in emitted)
    assert record.activity[0].skill == "meeting-prep"
    with pytest.raises(HTTPException) as error:
        adapter.completed_item({
            "type": "function_call", "name": "load_skill", "call_id": "call_2", "arguments": '{"a":1}{"b":2}',
        })
    assert error.value.detail == "Foundry returned invalid activity arguments."


def test_tool_result_error_is_not_reported_as_success_or_replayed() -> None:
    record = RunRecord(id=str(uuid4()), session_id=str(uuid4()), prompt_preview="Hello", started_at=now())
    adapter = ResponseEvents(record, "https://example.services.ai.azure.com/api/projects/workplace")
    item = {"type": "function_call", "name": "workiq___ask", "call_id": "call_1", "arguments": "{}"}
    adapter.completed_item(item)
    result = {"type": "function_call_output", "call_id": "call_1", "output": '{"isError":true}'}
    adapter.completed_item(result)
    assert record.activity[0].status == "failed"
    assert adapter.completed_item(result) == []
    assert adapter.completed_item(item) == []


@pytest.mark.parametrize("metadata_change", [
    None, {"call_id": "other"}, {"name": "other"}, {"duration_ms": -1},
])
def test_batched_tool_calls_use_only_matching_runtime_execution_timing(metadata_change: dict | None) -> None:
    record = RunRecord(id=str(uuid4()), session_id=str(uuid4()), prompt_preview="Hello", started_at=now())
    adapter = ResponseEvents(record, "https://example.services.ai.azure.com/api/projects/workplace")
    adapter.consume({
        "type": "response.output_item.added",
        "item": {"type": "function_call", "call_id": "call1", "name": "workiq___ask", "arguments": "{}"},
    })
    timing = {
        "version": 1, "call_id": "call1", "name": "workiq___ask",
        "started_at": "2026-10-15T10:00:00Z", "duration_ms": 35_020, "failed": False,
        **(metadata_change or {}),
    }
    result = {
        "type": "response.output_item.done", "item": {
            "type": "function_call_output", "call_id": "call1",
            "output": json.dumps({TIMING_KEY: timing, "output": '{"answer":"hello"}'}),
        },
    }
    if metadata_change:
        with pytest.raises(HTTPException) as error:
            adapter.consume(result)
        assert error.value.status_code == 502
    else:
        adapter.consume(result)
        call = record.activity[0]
        assert call.duration_ms == 35_020
        assert call.timing_source == "runtime"
        assert call.started_at == "2026-10-15T10:00:00+00:00"


def test_untimed_calls_do_not_turn_stream_delivery_gaps_into_execution_durations() -> None:
    record = RunRecord(id=str(uuid4()), session_id=str(uuid4()), prompt_preview="Hello", started_at=now())
    adapter = ResponseEvents(record, "https://example.services.ai.azure.com/api/projects/workplace")
    adapter.consume({
        "type": "response.output_item.added",
        "item": {"type": "function_call", "call_id": "call1", "name": "workiq___ask"},
    })
    adapter.completed_item({"type": "function_call_output", "call_id": "call1", "output": "{}"})
    assert record.activity[0].duration_ms is None
    assert record.activity[0].timing_source is None


def test_failed_skill_load_is_not_reported_as_loaded_and_remote_lookalikes_are_not_skills() -> None:
    record = RunRecord(
        id=str(uuid4()), session_id=str(uuid4()), prompt_preview="Find a file", started_at=now(),
    )
    adapter = ResponseEvents(record, "https://example.services.ai.azure.com/api/projects/workplace")
    adapter.completed_item({
        "type": "function_call", "name": "load_skill", "call_id": "skill1",
        "arguments": '{"skill_name":"missing-skill"}',
    })
    adapter.completed_item({
        "type": "function_call_output", "call_id": "skill1",
        "output": "Error: Skill 'missing-skill' not found.",
    })
    adapter.completed_item({
        "type": "function_call", "name": "workiq___load_skill", "call_id": "remote1",
        "arguments": '{"skill_name":"document-finder"}',
    })
    assert record.activity[0].kind == "skill"
    assert record.activity[0].skill == "missing-skill"
    assert record.activity[0].status == "failed"
    assert record.activity[1].kind == "tool"
    assert record.activity[1].skill is None


def test_harness_tools_show_the_work_iq_calls_they_made_inside() -> None:
    record = RunRecord(id=str(uuid4()), session_id=str(uuid4()), prompt_preview="Chart", started_at=now())
    adapter = ResponseEvents(record, "https://example.services.ai.azure.com/api/projects/workplace")
    outputs = {
        "inspect_document": {"verified_pages": [3], "workiq_calls": [
            {"tool": "fetch", "ms": 2817}, {"tool": "fetch_blob", "ms": 3262}], "reused_download": False},
        "read_chart": {"reading": {}, "workiq_calls": [], "reused_download": True,
                       "model_call": {"deployment": "gpt-5.6-luna", "ms": 4800}},
        "list_client_documents": {"workiq_calls": [{"tool": "fetch", "ms": 640}, {"tool": "fetch", "ms": 1200}],
                                  "documents": [{"where": "OneDrive"}, {"where": "OneDrive"}, {"where": "SharePoint"}]},
        "workiq___retrieve": {"workiq_calls": [{"tool": "fetch", "ms": 1}]},
    }
    for index, (name, output) in enumerate(outputs.items()):
        adapter.completed_item({"type": "function_call", "name": name, "call_id": f"c{index}", "arguments": "{}"})
        adapter.completed_item({"type": "function_call_output", "call_id": f"c{index}", "output": json.dumps(output)})
    inspect, chart, documents, retrieve = record.activity
    assert inspect.detail == "Inside: Work IQ fetch 2.8s · fetch_blob 3.3s, as you · page matched by the harness"
    assert chart.detail == ("Inside: the file Work IQ downloaded earlier in this turn, as you · "
                            "chart read by gpt-5.6-luna in Foundry, 4.8s")
    assert documents.detail == ("Inside: Work IQ fetch 640 ms · fetch 1.2s, as you · "
                                "the client's folders: OneDrive (2), SharePoint (1)")
    # A Work IQ call is its own X-ray entry; its line names the capability, not calls made inside it.
    assert retrieve.detail == "Work IQ retrieve: search by meaning in Microsoft 365, as you"
    failed = RunRecord(id=str(uuid4()), session_id=str(uuid4()), prompt_preview="Chart", started_at=now())
    adapter = ResponseEvents(failed, "https://example.services.ai.azure.com/api/projects/workplace")
    adapter.completed_item({"type": "function_call", "name": "inspect_document", "call_id": "f", "arguments": "{}"})
    adapter.completed_item({"type": "function_call_output", "call_id": "f", "output": "Error: Function failed."})
    assert failed.activity[0].detail is None


def test_every_iq_call_says_which_iq_it_reached_and_as_whom() -> None:
    record = RunRecord(id=str(uuid4()), session_id=str(uuid4()), prompt_preview="Brief", started_at=now())
    adapter = ResponseEvents(record, "https://example.services.ai.azure.com/api/projects/workplace")
    graph = "https://graph.microsoft.com/v1.0/$metadata#users('u')"
    calls = [
        ("workiq___fetch", {"entityUrls": ["/me/calendarView", "/me/messages"]}, {"results": [
            {"statusCode": 200, "data": {"@odata.context": f"{graph}/calendarView(id)", "value": [{}, {}]}},
            {"statusCode": 200, "data": {"@odata.context": f"{graph}/messages(id)", "value": [{}] * 3}}]}),
        ("workiq___retrieve", {"query": "q", "capabilities": [{"name": "Meetings"}]},
         {"application/vnd.ms-workiq.retrieval": {"markdown": "…", "resultCount": 1}}),
        ("workiq___ask", {"question": "Compare", "fileUrls": ["https://a", "https://b"]}, {"answer": "…"}),
        ("query_portfolio_data", {"question": "q"},
         {"source": "Fabric IQ Portfolio Analyst", "answer": "As of 10/19/2026:\n\n- Total portfolio value: …"}),
        ("query_portfolio_data", {"question": "q"}, {"status": "PORTFOLIO_DATA_UNAVAILABLE", "reason": "timeout"}),
        ("recall_client_memory", {"client_name": "Michael Scott"},
         {"client": "Michael Scott", "last_discussion": {"at": "…"}, "last_brief": {"at": "…"}, "advisor_preferences": []}),
        ("record_brief", {"client_name": "Michael Scott"}, {"saved": True, "saved_at": "…"}),
        ("liquidity_check", {}, {"available_above_reserve_usd": 232219.15, "shortfall_usd": 167780.85}),
        ("rebalancing_check", {}, {"current_weight_pct": 26.1, "limit_pct": 25.0, "above_limit": True,
                                   "trim_to_limit_usd": 32167.14, "realized_gain_on_trim_usd": 25633.17,
                                   "trim_exceeds_tax_budget": True}),
    ]
    for index, (name, arguments, output) in enumerate(calls):
        adapter.completed_item({"type": "function_call", "name": name, "call_id": f"c{index}",
                                "arguments": json.dumps(arguments)})
        adapter.completed_item({"type": "function_call_output", "call_id": f"c{index}", "output": json.dumps(output)})
    assert [call.detail for call in record.activity] == [
        "Work IQ fetch: exact Microsoft Graph reads, as you: calendar (2), mail (3)",
        "Work IQ retrieve: search by meaning in meetings and recordings, as you · 1 result(s)",
        "Work IQ ask: Microsoft 365 Copilot reasoned over 2 exact file(s), as you",
        "Fabric IQ: the Portfolio Analyst data agent answered from the portfolio lakehouse, as of 10/19/2026, as you",
        "Fabric IQ: unavailable; the portfolio system was not checked",
        "Durable client memory in the Foundry state store, yours only: last discussion, last brief",
        "Durable client memory: this brief saved in the Foundry state store for next time",
        "Harness check, in code: $232,219.15 above the reserve; shortfall $167,780.85",
        "Harness check, in code: 26.1% against the 25.0% limit; a trim to the limit sells $32,167.14 and realizes "
        "$25,633.17, over the tax budget",
    ]
    # Streamed arguments can hold the whole document twice; the line still names the Microsoft 365 source.
    twice = json.dumps({"query": "q", "capabilities": [{"name": "OneDriveAndSharePoint"}]}) * 2
    retrieved = json.dumps({"application/vnd.ms-workiq.retrieval": {"markdown": "…", "resultCount": 5}})
    assert iq_detail("workiq___retrieve", retrieved, twice) == (
        "Work IQ retrieve: search by meaning in OneDrive and SharePoint, as you · 5 result(s)")
    # A Teams message's context also says "messages"; it's Teams, not mail. Only 401 and 403 are a denial.
    reads = json.dumps({"results": [
        {"statusCode": 200, "data": {"@odata.context": "https://graph.microsoft.com/v1.0/$metadata#chats('19%3Aa')/messages/$entity"}},
        {"statusCode": 200, "data": {"@odata.context": f"{graph}/messages(id)", "value": [{}]}},
        {"statusCode": 403, "data": {}}, {"statusCode": 404, "data": {}}]})
    assert iq_detail("workiq___fetch", reads, "{}") == (
        "Work IQ fetch: exact Microsoft Graph reads, as you: Teams, mail (1), Graph denied (403), Graph failed (404)")


def test_fabric_consent_from_a_tool_result_becomes_a_consent_banner_with_the_full_link() -> None:
    link = "https://logic-apis-eastus2.consent.azure-apim.net/login?data=" + "eyJ" + "A" * 2500
    record = RunRecord(id=str(uuid4()), session_id=str(uuid4()), prompt_preview="Holdings", started_at=now())
    adapter = ResponseEvents(record, "https://example.services.ai.azure.com/api/projects/workplace")
    adapter.completed_item({"type": "function_call", "name": "query_portfolio_data", "call_id": "p", "arguments": "{}"})
    events = adapter.completed_item({
        "type": "function_call_output", "call_id": "p",
        "output": json.dumps({"status": "FABRIC_CONSENT_REQUIRED", "consent_link": link, "instruction": "..."}),
    })
    consent = [event for event in events if getattr(event, "name", None) == "workiq.consent"]
    assert len(consent) == 1
    assert consent[0].value["url"] == link
    assert consent[0].value["server"] == "fabric_portfolio"
    assert record.activity[0].detail is None or "Authorize" in record.activity[0].detail
    chat = ResponseEvents(
        RunRecord(id=str(uuid4()), session_id=str(uuid4()), prompt_preview="Delegate", started_at=now()),
        "https://example.services.ai.azure.com/api/projects/workplace",
    )
    chat.completed_item({"type": "function_call", "name": "delegate_to_work_iq", "call_id": "c", "arguments": "{}"})
    chat_events = chat.completed_item({
        "type": "function_call_output", "call_id": "c",
        "output": json.dumps({"status": "WORKIQ_CHAT_CONSENT_REQUIRED", "consent_link": link}),
    })
    assert [event.value["server"] for event in chat_events
            if getattr(event, "name", None) == "workiq.consent"] == ["workiq_chat_a2a"]
    # A link to anywhere else is never offered as a consent button, and other tools never raise one.
    for name, output in (
        ("query_portfolio_data", {"status": "FABRIC_CONSENT_REQUIRED", "consent_link": "https://evil.example/login"}),
        ("search_public_web", {"status": "FABRIC_CONSENT_REQUIRED", "consent_link": link}),
    ):
        other = ResponseEvents(
            RunRecord(id=str(uuid4()), session_id=str(uuid4()), prompt_preview="x", started_at=now()),
            "https://example.services.ai.azure.com/api/projects/workplace",
        )
        other.completed_item({"type": "function_call", "name": name, "call_id": "q", "arguments": "{}"})
        result = other.completed_item({"type": "function_call_output", "call_id": "q", "output": json.dumps(output)})
        assert not [event for event in result if getattr(event, "name", None) == "workiq.consent"]


def test_reported_pages_default_to_unverified_and_harness_verified_pages_are_kept() -> None:
    source = {"title": "Plan.pdf", "url": "https://contoso.sharepoint.com/plan", "page": 3}
    text = "A result.\n```workiq_references\n" + json.dumps([source]) + "\n```"
    references = references_from_text(text)
    assert references[0].page == 3
    assert references[0].location_verified is False
    source["location_verified"] = True
    claimed = "```workiq_references\n" + json.dumps([source]) + "\n```"
    assert references_from_text(claimed)[0].location_verified is False
    pages = VerifiedPages()
    pages.record({"verified_pages": [3], "source": {"web_url": source["url"]}})
    assert references_from_text(claimed, verified=pages)[0].location_verified is True
    source["location_verified"] = "yes"
    assert references_from_text("```workiq_references\n" + json.dumps([source]) + "\n```") == []
    # null is how the model writes "unknown": the source is kept, unverified (seen live on 2026-10-17).
    source["location_verified"] = None
    kept = references_from_text("```workiq_references\n" + json.dumps([source]) + "\n```")
    assert [reference.location_verified for reference in kept] == [False]
    assert references_from_text("```workiq_references\n" + json.dumps([source]) + "\n```", verified=pages)[0] \
        .location_verified is False
    with pytest.raises(ValidationError):
        Reference(title="Plan", url="javascript:alert(1)")
    with pytest.raises(ValidationError):
        Reference(title="Plan", url="https://contoso.sharepoint.com/plan", item_id="../other")


IPS_URL = (
    "https://contoso-my.sharepoint.com/personal/advisor_contoso_com/Documents/Client%20Files/"
    "Michael%20Scott/Michael%20Scott%20-%20IPS%20v1%20(signed).pdf"
)
CITED_IPS = {
    "title": "Michael Scott - IPS v1 (signed).pdf", "url": IPS_URL, "drive_id": "b!drive",
    "item_id": "01ITEM", "page": 3, "slide": None, "location_verified": True,
}


def inspect_evidence(*pages: int, web_url: str | None = None) -> str:
    source = {
        "kind": "onedrive", "title": CITED_IPS["title"], "resource_path": "/drives/b!drive/items/01ITEM",
    }
    if web_url:
        source["web_url"] = web_url
    return json.dumps({
        "source": source, "matches": [], "verified_pages": list(pages),
        "drive_id": "b!drive", "item_id": "01ITEM",
    })


def timed(output: str, call_id: str = "inspect0") -> str:
    return json.dumps({TIMING_KEY: {
        "version": 1, "call_id": call_id, "name": "inspect_document",
        "started_at": "2026-10-16T10:00:00Z", "duration_ms": 900, "failed": False,
    }, "output": output})


def streamed_references(outputs: list[str], cited: dict) -> list[dict]:
    record = RunRecord(id=str(uuid4()), session_id=str(uuid4()), prompt_preview="Cite", started_at=now())
    adapter = ResponseEvents(record, "https://example.services.ai.azure.com/api/projects/workplace")
    for index, output in enumerate(outputs):
        adapter.completed_item({
            "type": "function_call", "name": "inspect_document", "call_id": f"inspect{index}",
            "arguments": '{"phrase":"Figure 2"}',
        })
        adapter.completed_item({
            "type": "function_call_output", "call_id": f"inspect{index}", "output": output,
        })
    text = "The chart is on page 3.\n```workiq_references\n" + json.dumps([cited]) + "\n```"
    emitted = adapter.consume({"type": "response.output_item.done", "item": {
        "type": "message", "role": "assistant", "id": "msg_1",
        "content": [{"type": "output_text", "text": text}],
    }})
    events = [
        event for event in emitted if event.type == EventType.CUSTOM and event.name == "workiq.references"
    ]
    return events[0].value["references"]


@pytest.mark.parametrize(("outputs", "cited", "verified"), [
    pytest.param([], CITED_IPS, False, id="claimed-without-inspect-document"),
    pytest.param([timed(inspect_evidence(3))], CITED_IPS, True, id="item-id-verified-page"),
    pytest.param([inspect_evidence(3)], {**CITED_IPS, "page": 2}, False, id="other-page-verified"),
    pytest.param(
        [inspect_evidence(3, web_url=IPS_URL)],
        {**CITED_IPS, "drive_id": None, "item_id": None,
         "url": IPS_URL.replace("(signed)", "%28signed%29") + "?web=1"},
        True, id="url-only-match",
    ),
    pytest.param(
        ['{"isError":true,"verified_pages":[3],"item_id":"01ITEM"}'], CITED_IPS, False,
        id="failed-inspection",
    ),
])
def test_only_pages_matched_by_inspect_document_are_shown_as_verified(
    outputs: list[str], cited: dict, verified: bool,
) -> None:
    references = streamed_references(outputs, cited)
    assert len(references) == 1 and references[0]["location_verified"] is verified


async def test_history_marks_only_pages_verified_by_stored_inspect_results(config: UIConfig) -> None:
    cited = [CITED_IPS, {**CITED_IPS, "page": 2}, {**CITED_IPS, "page": None, "slide": 3}]
    answer = "Pages.\n```workiq_references\n" + json.dumps(cited) + "\n```"
    pages = [
        [{
            "id": "fc_1", "type": "function_call", "call_id": "call_1", "name": "inspect_document",
            "arguments": "{}",
        }],
        [
            {
                "id": "fco_1", "type": "function_call_output", "call_id": "call_1",
                "output": inspect_evidence(3),
            },
            {"id": "msg_1", "type": "message", "role": "assistant",
             "content": [{"type": "output_text", "text": answer}]},
        ],
    ]

    def respond(request: httpx.Request) -> httpx.Response:
        data = pages.pop(0)
        return httpx.Response(200, json={"data": data, "has_more": bool(pages)})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        messages = await FoundryGateway(config, client).messages("delegated", "conv_1")
    assert [
        (reference["page"], reference["slide"], reference["location_verified"])
        for reference in messages[0]["references"]
    ] == [(3, None, True), (2, None, False), (None, 3, False)]


@pytest.mark.parametrize("url", [
    "https://evil.example.com/document",
    "https://sharepoint.com.evil.example.com/document",
    "https://contoso.sharepoint.com@evil.example.com/document",
    "http://contoso.sharepoint.com/document",
])
def test_preview_rejects_untrusted_hosts(url: str) -> None:
    with pytest.raises(ValueError):
        validate_preview_url(url)


async def test_preview_uses_delegated_graph_token_and_explains_position_fallback() -> None:
    requests = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(400)
        return httpx.Response(200, json={"getUrl": "https://contoso.sharepoint.com/preview"})

    reference = Reference(
        title="Plan.pptx", url="https://contoso.sharepoint.com/plan",
        drive_id="b!drive", item_id="item1", slide=5,
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        value = await preview_reference(client, "delegated-graph-token", reference)
    assert requests[0].headers["Authorization"] == "Bearer delegated-graph-token"
    assert json.loads(requests[0].content) == {"page": "5"}
    assert json.loads(requests[1].content) == {}
    assert value["warning"]
    assert value["position_verified"] is False


PDF_BYTES = b"%PDF-1.4\n% synthetic\n"


@pytest.mark.parametrize(("name", "mime", "download", "query"), [
    ("Overview.pdf", "application/pdf",
     "https://contoso.sharepoint.com/_layouts/15/download.aspx?UniqueId=1&tempauth=x", ""),
    ("IPS v2 DRAFT.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
     "https://southcentralus1-mediap.svc.ms/transform/pdf?provider=spo&docid=1", "format=pdf"),
])
async def test_document_returns_pdf_bytes_without_forwarding_the_graph_token(
    name: str, mime: str, download: str, query: str,
) -> None:
    requests = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.host == "graph.microsoft.com" and request.url.path.endswith("/content"):
            return httpx.Response(302, headers={"Location": download})
        if request.url.host == "graph.microsoft.com":
            return httpx.Response(200, json={"id": "item1", "name": name, "size": 10, "file": {"mimeType": mime},
                                             "webUrl": "https://contoso-my.sharepoint.com/personal/a/Documents/f"})
        return httpx.Response(200, content=PDF_BYTES)

    reference = Reference(title=name, url="https://contoso.sharepoint.com/f", drive_id="b!drive", item_id="item1")
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        content, file_name, web_url = await document_pdf(client, "delegated-graph-token", reference)
    assert (content, file_name) == (PDF_BYTES, name)
    # Open original uses the link Graph reports for the resolved file, never the model's copy.
    assert web_url == "https://contoso-my.sharepoint.com/personal/a/Documents/f"
    assert requests[1].url.query.decode() == query
    assert all(request.headers["Authorization"] == "Bearer " + "delegated-graph-token" for request in requests[:2])
    assert str(requests[2].url) == download and "Authorization" not in requests[2].headers


@pytest.mark.parametrize(("name", "location", "body", "status"), [
    ("Photo.png", None, PDF_BYTES, 415),
    ("Overview.pdf", "https://evil.example.com/download?x=1", PDF_BYTES, 502),
    ("Overview.pdf", "https://contoso.sharepoint.com.evil.example.com/d", PDF_BYTES, 502),
    ("Overview.pdf", "https://contoso.sharepoint.com/download.aspx", b"<html>sign in</html>", 502),
])
async def test_document_rejects_unsupported_files_hosts_and_non_pdf_bodies(
    name: str, location: str | None, body: bytes, status: int,
) -> None:
    hosts = []

    def respond(request: httpx.Request) -> httpx.Response:
        hosts.append(request.url.host)
        if request.url.host == "graph.microsoft.com" and request.url.path.endswith("/content"):
            return httpx.Response(302, headers={"Location": location or ""})
        if request.url.host == "graph.microsoft.com":
            return httpx.Response(200, json={"id": "item1", "name": name, "file": {"mimeType": "x"}})
        return httpx.Response(200, content=body)

    reference = Reference(title=name, url="https://contoso.sharepoint.com/f", drive_id="b!drive", item_id="item1")
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(HTTPException) as raised:
            await document_pdf(client, "delegated-graph-token", reference)
    assert raised.value.status_code == status
    assert "evil.example.com" not in " ".join(hosts)


def synthetic_pdf(pages: int) -> bytes:
    """A minimal valid PDF with one line of text per Letter-size page."""
    objects = ["<< /Type /Catalog /Pages 2 0 R >>",
               f"<< /Type /Pages /Kids [{' '.join(f'{3 + 2 * i} 0 R' for i in range(pages))}] /Count {pages} >>"]
    font = 3 + 2 * pages
    for index in range(pages):
        text = f"BT /F1 36 Tf 72 700 Td (PAGE {index + 1}) Tj ET"
        objects.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                       f"/Resources << /Font << /F1 {font} 0 R >> >> /Contents {4 + 2 * index} 0 R >>")
        objects.append(f"<< /Length {len(text)} >>\nstream\n{text}\nendstream")
    objects.append("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    body, offsets = b"%PDF-1.4\n", []
    for number, value in enumerate(objects, start=1):
        offsets.append(len(body))
        body += f"{number} 0 obj\n{value}\nendobj\n".encode()
    xref = len(body)
    body += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    body += "".join(f"{offset:010d} 00000 n \n" for offset in offsets).encode()
    return body + f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()


def test_render_page_returns_the_requested_page_as_png_and_clamps_out_of_range_pages() -> None:
    pdf = synthetic_pdf(3)
    second = render_page(pdf, 2)
    assert (second["page"], second["page_count"]) == (2, 3)
    assert abs(second["width"] - 1224) <= 1 and abs(second["height"] - 1584) <= 1
    image = base64.b64decode(second["image"])
    assert image[:8] == b"\x89PNG\r\n\x1a\n"
    width, height = struct.unpack(">II", image[16:24])
    idat = image.index(b"IDAT")
    (length,) = struct.unpack(">I", image[idat - 4:idat])
    rows = zlib.decompress(image[idat + 4:idat + 4 + length])
    assert (width, height) == (second["width"], second["height"]) and len(rows) == height * (1 + width * 3)
    assert set(rows[1:width * 3 + 1]) == {255}
    assert render_page(pdf, 9)["page"] == 3 and render_page(pdf, None)["page"] == 1
    with pytest.raises(HTTPException) as raised:
        render_page(b"%PDF-1.4 truncated", 1)
    assert raised.value.status_code == 502


async def test_foundry_history_omits_private_reasoning_and_paginates(config: UIConfig) -> None:
    requests = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(200, json={"data": [
                {"id": "reasoning_1", "type": "reasoning", "summary": "private"},
                {"id": "user_1", "type": "message", "role": "user",
                 "content": [{"type": "input_text", "text": "Hello"}]},
            ], "has_more": True})
        return httpx.Response(200, json={"data": [
            {"id": "assistant_1", "type": "message", "role": "assistant",
             "content": [{"type": "output_text", "text": "Hello back"}]},
        ], "has_more": False})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        messages = await FoundryGateway(config, client).messages("delegated-user-token", "conv_1")
    assert [message["content"] for message in messages] == ["Hello", "Hello back"]
    assert requests[1].url.params["after"] == "user_1"
    assert all("/agents/workplace-productivity/endpoint/protocols/openai/conversations/" in str(
        request.url,
    ) for request in requests)
    assert all(request.url.params["api-version"] == "v1" for request in requests)
    assert all(request.headers["Authorization"] == "Bearer delegated-user-token" for request in requests)

async def test_all_new_conversation_operations_share_the_hosted_responses_scope(config: UIConfig) -> None:
    requests = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert "/agents/workplace-productivity/endpoint/protocols/openai/" in request.url.path
        assert request.url.params["api-version"] == "v1"
        if request.url.path.endswith("/responses"):
            assert json.loads(request.content)["conversation"] == "conv_hosted"
            return httpx.Response(200, text='data: {"type":"response.completed"}\n\n')
        if request.method == "POST":
            return httpx.Response(200, json={"id": "conv_hosted"})
        if request.method == "GET":
            return httpx.Response(200, json={"data": [], "has_more": False})
        return httpx.Response(200, json={"deleted": True})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        gateway = FoundryGateway(config, client)
        identifier = await gateway.create_conversation("delegated")
        assert await gateway.messages("delegated", identifier) == []
        assert [event async for event in gateway.stream("delegated", identifier, "Hello")]
        await gateway.delete_conversation("delegated", identifier)
    assert len(requests) == 4


@pytest.fixture
def api_client(config: UIConfig, signing_key, memory_store: SessionStore):
    gateway = MagicMock()
    gateway.create_conversation = AsyncMock(return_value="conv_test")
    gateway.messages = AsyncMock(return_value=[])
    gateway.delete_conversation = AsyncMock()
    tokens = MagicMock(delegated=AsyncMock(return_value="delegated-user-token"))
    client = MagicMock()
    services = Services(authenticator(config, signing_key), tokens, memory_store, gateway, client)
    with TestClient(create_app(config, services)) as api:
        yield api, services


def test_api_requires_valid_user_and_checks_session_ownership_before_foundry(
    api_client, config: UIConfig, signing_key,
) -> None:
    client, services = api_client
    assert client.get("/api/sessions").status_code == 401
    alice = {"Authorization": f"Bearer {user_token(config, signing_key)}"}
    response = client.post("/api/sessions", headers=alice, json={})
    assert response.status_code == 201
    identifier = response.json()["id"]
    bob = {"Authorization": f"Bearer {user_token(config, signing_key, oid=BOB)}"}
    services.tokens.delegated.reset_mock()
    assert client.get(f"/api/sessions/{identifier}", headers=bob).status_code == 404
    services.tokens.delegated.assert_not_awaited()
    assert client.get("/api/sessions", headers=bob).json() == []
    response = client.post("/api/sessions", headers={**alice, "Origin": "https://evil.example.com"})
    assert response.status_code == 403


def test_history_reads_overlap_without_weakening_the_ownership_gate(
    api_client, config: UIConfig, signing_key,
):
    client, services = api_client
    headers = {"Authorization": f"Bearer {user_token(config, signing_key)}"}
    identifier = client.post("/api/sessions", headers=headers, json={}).json()["id"]
    arrived = set()
    both_started = asyncio.Event()

    async def read(name: str):
        arrived.add(name)
        if len(arrived) == 2:
            both_started.set()
        await asyncio.wait_for(both_started.wait(), timeout=1)
        return []

    async def messages(*args, **kwargs):
        return await read("messages")

    async def runs(*args, **kwargs):
        return await read("runs")

    services.foundry.messages = messages
    services.store.runs = runs
    response = client.get(f"/api/sessions/{identifier}", headers=headers)
    assert response.status_code == 200
    assert response.json()["messages"] == response.json()["runs"] == []
    assert arrived == {"messages", "runs"}


def test_history_access_denial_remains_explicit_and_cancels_its_sibling(
    api_client, config: UIConfig, signing_key,
):
    client, services = api_client
    headers = {"Authorization": f"Bearer {user_token(config, signing_key)}"}
    identifier = client.post("/api/sessions", headers=headers, json={}).json()["id"]
    started = asyncio.Event()
    closed = asyncio.Event()

    async def messages(*args, **kwargs):
        await started.wait()
        raise HTTPException(status_code=403, detail="Synthetic upstream access denial")

    async def runs(*args, **kwargs):
        try:
            started.set()
            await asyncio.Event().wait()
        finally:
            closed.set()

    services.foundry.messages = messages
    services.store.runs = runs
    response = client.get(f"/api/sessions/{identifier}", headers=headers)
    assert response.status_code == 403
    assert response.json()["detail"] == "Synthetic upstream access denial"
    assert closed.is_set()


def test_agui_stream_persists_per_prompt_activity_and_rejects_injected_tools(
    api_client, config: UIConfig, signing_key,
) -> None:
    client, services = api_client
    headers = {"Authorization": f"Bearer {user_token(config, signing_key)}"}
    identifier = client.post("/api/sessions", headers=headers, json={}).json()["id"]

    async def stream(token: str, conversation_id: str, prompt: str):
        assert token == "delegated-user-token"
        assert conversation_id == "conv_test"
        assert prompt == "Say hello only"
        yield {"type": "response.created", "response": {"id": "resp_1"}}
        yield {"type": "response.output_item.added", "item": {
            "type": "function_call", "id": "fc_1", "call_id": "call_1",
            "name": "workiq___ask", "arguments": "",
        }}
        yield {"type": "response.output_item.done", "item": {
            "type": "function_call", "call_id": "call_1",
            "name": "workiq___ask", "arguments": '{"question":"hello"}',
        }}
        yield {"type": "response.output_item.done", "item": {
            "type": "function_call_output", "call_id": "call_1", "output": '{"isError":false}',
        }}
        yield {"type": "response.completed", "response": {
            "id": "resp_1", "status": "completed", "output": [{
                "id": "msg_1", "type": "message", "role": "assistant",
                "content": [{"type": "output_text", "text": "Hello"}],
            }],
        }}

    services.foundry.stream = stream
    body = {
        "threadId": identifier, "runId": str(uuid4()), "state": {}, "tools": [], "context": [],
        "forwardedProps": {}, "messages": [{"id": "user_1", "role": "user", "content": "Say hello only"}],
    }
    response = client.post("/api/chat", headers=headers, json=body)
    assert response.status_code == 200
    events = [
        json.loads(line.removeprefix("data: "))
        for line in response.text.splitlines() if line.startswith("data: ")
    ]
    assert events[0]["type"] == "RUN_STARTED"
    assert events[-1]["type"] == "RUN_FINISHED"
    assert any(
        event["type"] == "TOOL_CALL_START" and event["toolCallName"] == "workiq___ask" for event in events
    )
    runs = client.get(f"/api/sessions/{identifier}", headers=headers).json()["runs"]
    assert runs[0]["activity"][0]["name"] == "workiq___ask"
    assert runs[0]["assistant_message_ids"] == ["msg_1"]
    assert runs[0]["latency"]["preparation_ms"] is not None
    assert runs[0]["latency"]["first_event_ms"] is not None
    assert runs[0]["latency"]["first_text_ms"] is not None
    assert runs[0]["latency"]["response_ms"] is not None
    body["context"] = [{"description": "injected context", "value": "do not authenticate"}]
    assert client.post("/api/chat", headers=headers, json=body).status_code == 422


def test_background_checkpoint_error_is_visible_and_leaves_a_failed_released_run(
    api_client, config: UIConfig, signing_key,
):
    client, services = api_client
    headers = {"Authorization": f"Bearer {user_token(config, signing_key)}"}
    identifier = client.post("/api/sessions", headers=headers, json={}).json()["id"]
    original_save = services.store.save_run
    failed = False

    async def save(owner, record):
        nonlocal failed
        if not failed:
            failed = True
            raise CosmosHttpResponseError(status_code=429, message="Synthetic checkpoint failure")
        await original_save(owner, record)

    async def stream(*args):
        yield {"type": "response.created", "response": {"id": "resp_failed"}}
        await asyncio.sleep(10)

    services.store.save_run = save
    services.foundry.stream = stream
    response = client.post("/api/chat", headers=headers, json={
        "threadId": identifier, "runId": str(uuid4()), "state": {}, "tools": [], "context": [],
        "forwardedProps": {}, "messages": [{"id": "user", "role": "user", "content": "Synthetic"}],
    })
    assert "RUN_ERROR" in response.text and "RUN_FINISHED" not in response.text
    conversation = client.get(f"/api/sessions/{identifier}", headers=headers).json()
    assert conversation["runs"][0]["status"] == "failed"
    assert conversation["session"]["running"] is False


def test_session_delete_rolls_back_marker_if_foundry_rejects_request(
    api_client, config: UIConfig, signing_key,
) -> None:
    client, services = api_client
    headers = {"Authorization": f"Bearer {user_token(config, signing_key)}"}
    identifier = client.post("/api/sessions", headers=headers, json={}).json()["id"]
    services.foundry.delete_conversation.side_effect = HTTPException(
        status_code=403, detail="Access denied.",
    )
    assert client.delete(f"/api/sessions/{identifier}", headers=headers).status_code == 403
    assert client.patch(
        f"/api/sessions/{identifier}", headers=headers, json={"title": "Still usable"},
    ).status_code == 200
    services.foundry.delete_conversation.side_effect = None
    assert client.delete(f"/api/sessions/{identifier}", headers=headers).status_code == 200
    assert client.get(f"/api/sessions/{identifier}", headers=headers).status_code == 404


def test_auth_bridge_is_uncached_and_can_return_popup_and_same_origin_iframe_responses(
    api_client, config: UIConfig,
) -> None:
    client, _ = api_client
    (config.static_directory / "auth.html").write_text("<html>Microsoft Authentication</html>")
    (config.static_directory / "index.html").write_text("<html>Chat</html>")
    bridge = client.get("/auth.html")
    assert bridge.status_code == 200
    assert bridge.headers["Cache-Control"] == "no-store"
    assert "Cross-Origin-Opener-Policy" not in bridge.headers
    assert "frame-ancestors 'self'" in bridge.headers["Content-Security-Policy"]
    main = client.get("/")
    assert main.headers["Cross-Origin-Opener-Policy"] == "same-origin-allow-popups"
    assert "frame-ancestors 'none'" in main.headers["Content-Security-Policy"]


def test_only_existing_fingerprinted_public_bundles_are_cached(api_client, config: UIConfig):
    client, _ = api_client
    assets = config.static_directory / "assets"
    assets.mkdir()
    (assets / "app-abc12345.js").write_text("console.log('Public build code')")
    (assets / "unversioned.js").write_text("console.log('Unversioned code')")
    (config.static_directory / "index.html").write_text("<html>Application shell</html>")
    versioned = client.get("/assets/app-abc12345.js")
    assert versioned.status_code == 200
    assert versioned.headers["Cache-Control"] == "public, max-age=31536000, immutable"
    for path in ("/", "/api/config", "/assets/unversioned.js", "/assets/missing-abc12345.js"):
        assert client.get(path).headers["Cache-Control"] == "no-store"


async def test_first_prompt_upgrades_an_empty_legacy_session_without_losing_catalog_or_trace(
    api_client, config: UIConfig, signing_key,
) -> None:
    client, services = api_client
    owner = Principal(TENANT, ALICE, "").owner(config)
    document = await services.store.create(owner, "conv_legacy")
    document.pop("conversation_scope")
    document["title"] = "Existing session"
    document = await services.store.replace(document)
    services.foundry.has_legacy_history = AsyncMock(return_value=False)
    services.foundry.create_conversation.return_value = "conv_hosted"

    async def stream(token: str, conversation_id: str, prompt: str):
        assert conversation_id == "conv_hosted"
        yield {"type": "response.completed", "response": {
            "id": "resp_hosted", "status": "completed", "output": [],
        }}

    services.foundry.stream = stream
    headers = {"Authorization": f"Bearer {user_token(config, signing_key)}"}
    identifier = document["session_id"]
    body = {
        "threadId": identifier, "runId": str(uuid4()), "state": {}, "tools": [], "context": [],
        "forwardedProps": {},
        "messages": [{"id": "user_1", "role": "user", "content": "Hello"}],
    }
    response = client.post("/api/chat", headers=headers, json=body)
    assert response.status_code == 200
    assert "RUN_FINISHED" in response.text
    updated = await services.store.get(owner, identifier)
    assert updated["conversation_id"] == "conv_hosted"
    assert updated["legacy_conversation_id"] == "conv_legacy"
    assert updated["conversation_scope"] == "hosted"
    assert updated["title"] == "Existing session"
    assert len(await services.store.runs(owner, identifier)) == 1
    assert client.delete(f"/api/sessions/{identifier}", headers=headers).status_code == 200
    deleted = services.foundry.delete_conversation.await_args_list
    assert [(call.args[1], call.kwargs["legacy"]) for call in deleted] == [
        ("conv_legacy", True), ("conv_hosted", False),
    ]


async def test_legacy_session_with_history_is_never_silently_replaced(
    api_client, config: UIConfig, signing_key,
) -> None:
    client, services = api_client
    owner = Principal(TENANT, ALICE, "").owner(config)
    document = await services.store.create(owner, "conv_legacy")
    document.pop("conversation_scope")
    document = await services.store.replace(document)
    services.foundry.has_legacy_history = AsyncMock(return_value=True)
    headers = {"Authorization": f"Bearer {user_token(config, signing_key)}"}
    response = client.post("/api/chat", headers=headers, json={
        "threadId": document["session_id"], "runId": str(uuid4()), "state": {},
        "tools": [], "context": [], "forwardedProps": {},
        "messages": [{"id": "user_1", "role": "user", "content": "Hello"}],
    })
    assert response.status_code == 409
    assert "history is retained" in response.json()["detail"]
    services.foundry.create_conversation.assert_not_awaited()
    assert (await services.store.get(owner, document["session_id"]))["conversation_id"] == "conv_legacy"


@pytest.mark.parametrize("library_url", [
    "https://contoso.sharepoint.com/sites/Research/Shared%20Documents",
    "https://contoso.sharepoint.com/sites/Research/Shared%20Documents/Forms/AllItems.aspx",
])
async def test_preview_resolves_library_view_url_using_only_delegated_graph_requests(
    library_url: str,
) -> None:
    requests = []
    target = "/sites/Research/Shared Documents/Folder/Plan.pdf"
    source = Reference(
        title="Plan", page=2,
        url="https://contoso.sharepoint.com/sites/Research/Shared%20Documents/Forms/AllItems.aspx?"
        + urlencode({"id": target}),
    )

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.url.host == "graph.microsoft.com"
        assert request.headers["Authorization"] == "Bearer delegated-graph-token"
        path = request.url.path
        if path.endswith("/sites/contoso.sharepoint.com:/sites/Research"):
            return httpx.Response(200, json={"id": "site1"})
        if path.endswith("/sites/site1/drives"):
            return httpx.Response(200, json={"value": [{
                "id": "b!drive1", "webUrl": library_url,
            }]})
        if path.endswith("/drives/b!drive1/root:/Folder/Plan.pdf"):
            return httpx.Response(200, json={"id": "item1", "file": {"mimeType": "application/pdf"}})
        assert path.endswith("/drives/b!drive1/items/item1/preview")
        assert json.loads(request.content) == {"page": "2"}
        return httpx.Response(200, json={"getUrl": "https://contoso.sharepoint.com/preview"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        result = await preview_reference(client, "delegated-graph-token", source)
    assert len(requests) == 4
    assert result["position_verified"] is False
    assert result["get_url"] == "https://contoso.sharepoint.com/preview"


@pytest.mark.parametrize("site_path", ["", "/sites/Research", "/personal/alice_contoso_com"])
async def test_preview_resolves_viewer_guid_to_actual_graph_ids_without_guessing_a_file_path(
    site_path: str,
) -> None:
    unique_id = "11111111-2222-4333-8444-555555555555"
    source = Reference(
        title="Slides", slide=3,
        url=f"https://contoso.sharepoint.com{site_path}/_layouts/15/Doc.aspx?"
        + urlencode({
            "sourceDoc": "{" + unique_id.upper() + "}", "file": "Misleading-name.pptx", "action": "edit",
        }),
    )
    requests = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.url.host == "graph.microsoft.com"
        assert request.headers["Authorization"] == "Bearer delegated-graph-token"
        site_address = f"contoso.sharepoint.com:{site_path}" if site_path else "contoso.sharepoint.com"
        if request.url.path == f"/v1.0/sites/{site_address}":
            return httpx.Response(200, json={"id": "site1"})
        if request.url.path == "/v1.0/sites/site1/drives":
            return httpx.Response(200, json={"value": [
                {"id": "b!other", "webUrl": f"https://contoso.sharepoint.com{site_path}/Other"},
                {"id": "b!actual", "webUrl": f"https://contoso.sharepoint.com{site_path}/Documents"},
            ]})
        if request.url.path == f"/v1.0/drives/b!other/items/{unique_id}":
            return httpx.Response(404)
        if request.url.path == f"/v1.0/drives/b!actual/items/{unique_id}":
            assert request.url.params["$select"] == "id,file"
            return httpx.Response(200, json={"id": "opaque-item1", "file": {}})
        assert request.url.path == "/v1.0/drives/b!actual/items/opaque-item1/preview"
        assert request.method == "POST"
        assert json.loads(request.content) == {"page": "3"}
        return httpx.Response(200, json={"getUrl": "https://contoso.sharepoint.com/preview"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        result = await preview_reference(client, "delegated-graph-token", source)
    assert len(requests) == 5
    assert result["position_verified"] is False
    assert result["get_url"] == "https://contoso.sharepoint.com/preview"
    assert all("Misleading-name" not in str(request.url) for request in requests)


@pytest.mark.parametrize("status", [403, 404])
async def test_viewer_guid_resolution_reports_access_denial_or_missing_file_without_preview(
    status: int,
) -> None:
    source = Reference(
        title="Slides",
        url="https://contoso.sharepoint.com/sites/Research/_layouts/15/Doc.aspx?"
        "sourcedoc=11111111-2222-4333-8444-555555555555",
    )
    requests = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.method == "GET"
        if len(requests) == 1:
            return httpx.Response(200, json={"id": "site1"})
        if len(requests) == 2:
            return httpx.Response(200, json={"value": [{
                "id": "b!drive", "webUrl": "https://contoso.sharepoint.com/sites/Research/Docs",
            }]})
        return httpx.Response(status)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(HTTPException) as error:
            await preview_reference(client, "delegated-graph-token", source)
    assert error.value.status_code == status
    assert len(requests) == 3


@pytest.mark.parametrize("url", [
    "https://evil.example.com/Plan.pdf",
    "https://contoso.sharepoint.com/_layouts/15/Doc.aspx?sourcedoc=unknown",
    "https://contoso.sharepoint.com/_layouts/15/Doc.aspx?file=Slides.pptx",
    "https://contoso.sharepoint.com/_layouts/15/Doc.aspx?sourcedoc=",
    "https://contoso.sharepoint.com/_layouts/15/Doc.aspx?"
    "sourcedoc=11111111-2222-4333-8444-555555555555&SourceDoc=11111111-2222-4333-8444-555555555555",
    "https://contoso.sharepoint.com/sites/../_layouts/15/Doc.aspx?"
    "sourcedoc=11111111-2222-4333-8444-555555555555",
    "https://contoso.sharepoint.com/sites/Research/Shared%20Documents/Forms/AllItems.aspx",
    "https://contoso.sharepoint.com/sites/Research/Shared%20Documents/Forms/AllItems.aspx?id=%2Fother%2FPlan.pdf",
    "https://contoso.sharepoint.com/sites/Research/Shared%20Documents/../private.pdf",
])
def test_sharepoint_file_resolution_rejects_unsupported_or_ambiguous_targets(url: str) -> None:
    with pytest.raises(ValueError):
        sharepoint_file(url)


async def test_preview_does_not_forward_credentials_to_an_untrusted_pagination_link() -> None:
    requests = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(200, json={"id": "site1"})
        return httpx.Response(200, json={
            "value": [], "@odata.nextLink": "https://evil.example.com/v1.0/sites/site1/drives",
        })

    source = Reference(title="Plan", url="https://contoso.sharepoint.com/sites/Research/Docs/Plan.pdf")
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(HTTPException) as error:
            await preview_reference(client, "delegated-graph-token", source)
    assert error.value.status_code == 502
    assert len(requests) == 2
    assert all(request.url.host == "graph.microsoft.com" for request in requests)


async def test_preview_keeps_graph_access_denials_explicit() -> None:
    source = Reference(title="Plan", url="https://contoso.sharepoint.com/sites/Research/Docs/Plan.pdf")
    transport = httpx.MockTransport(lambda request: httpx.Response(403))
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(HTTPException) as error:
            await preview_reference(client, "delegated-graph-token", source)
    assert error.value.status_code == 403


def test_tool_payloads_are_bounded_and_redact_credentials_and_temporary_authentication_urls() -> None:
    text, truncated = payload_text(json.dumps({
        "question": "Hello", "nested": {"access_token": "private-token", "clientSecret": "private-secret"},
        "header": "Bearer private-bearer",
        "url": "https://contoso.sharepoint.com/file?sig=private-signature",
    }))
    assert "Hello" in text
    assert all(
        value not in text
        for value in ("private-token", "private-secret", "private-bearer", "private-signature")
    )
    assert "[redacted" in text
    assert not truncated
    large, truncated = payload_text("x" * 70_000)
    assert truncated
    assert len(large) < 70_000


async def test_tool_payload_lookup_returns_only_the_requested_actual_call_not_private_reasoning(
    config: UIConfig,
) -> None:
    record = RunRecord(
        id=str(uuid4()), session_id=str(uuid4()), response_id="resp1", status="completed",
        prompt_preview="Hello", started_at=now(),
    )

    def respond(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/endpoint/protocols/openai/responses/resp1")
        assert request.url.params["api-version"] == "v1"
        return httpx.Response(200, json={"output": [
            {"type": "reasoning", "content": "private reasoning must not be returned"},
            {"type": "function_call", "call_id": "other", "arguments": '{"secret":"other call"}'},
            {"type": "function_call", "call_id": "call1", "arguments": '{"question":"hello"}'},
            {"type": "function_call_output", "call_id": "call1", "output": '{"answer":"hello"}'},
        ]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        result = await FoundryGateway(config, client).tool_details("delegated", record, "call1")
    assert json.loads(result["input"]) == {"question": "hello"}
    assert json.loads(result["output"]) == {"answer": "hello"}
    assert "private reasoning" not in json.dumps(result)
    assert "other call" not in json.dumps(result)


async def test_tool_payload_lookup_unwraps_runtime_metadata_before_redacting_actual_output(
    config: UIConfig,
) -> None:
    record = RunRecord(
        id=str(uuid4()), session_id=str(uuid4()), response_id="resp1", status="completed",
        prompt_preview="Hello", started_at=now(),
    )
    payload = {"output": [
        {"type": "function_call", "call_id": "call1", "name": "workiq___ask", "arguments": "{}"},
        {"type": "function_call_output", "call_id": "call1", "output": json.dumps({
            TIMING_KEY: {
                "version": 1, "call_id": "call1", "name": "workiq___ask", "started_at": now(),
                "duration_ms": 35_020, "failed": False,
            },
            "output": {"answer": "hello", "access_token": "private-token"},
        })},
    ]}
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(
        200, json=payload,
    ))) as client:
        result = await FoundryGateway(config, client).tool_details("delegated", record, "call1")
    assert json.loads(result["output"]) == {"answer": "hello", "access_token": "[redacted]"}
    assert TIMING_KEY not in result["output"]


async def test_tool_payload_api_checks_user_session_run_and_call_before_any_upstream_access(
    api_client, config: UIConfig, signing_key,
) -> None:
    client, services = api_client
    owner = Principal(TENANT, ALICE, "").owner(config)
    session = await services.store.create(owner, "conv1")
    record = RunRecord(
        id=str(uuid4()), session_id=session["session_id"], response_id="resp1", status="completed",
        prompt_preview="Hello", started_at=now(),
        activity=[ToolActivity(call_id="call1", name="workiq___ask", started_at=now(), status="completed")],
    )
    await services.store.save_run(owner, record)
    services.foundry.tool_details = AsyncMock(return_value={
        "input": '{"question":"hello"}', "output": '{"answer":"hello"}',
        "input_truncated": False, "output_truncated": False,
    })
    bob = {"Authorization": f"Bearer {user_token(config, signing_key, oid=BOB)}"}
    route = f"/api/sessions/{record.session_id}/runs/{record.id}/calls/"
    assert client.get(route + "call1", headers=bob).status_code == 404
    alice = {"Authorization": f"Bearer {user_token(config, signing_key)}"}
    assert client.get(route + "unknown", headers=alice).status_code == 404
    services.tokens.delegated.assert_not_awaited()
    response = client.get(route + "call1", headers=alice)
    assert response.status_code == 200
    assert json.loads(response.json()["output"]) == {"answer": "hello"}
    stored = (await services.store.runs(owner, record.session_id))[0]["activity"][0]
    assert "input" not in stored and "output" not in stored


ENDPOINT = "https://example.services.ai.azure.com/api/projects/workplace"


def test_reference_kind_comes_from_the_validated_item_path() -> None:
    email = Reference(
        title="Agenda", url="https://outlook.office365.com/owa/?ItemID=x", kind="file",
        entity_url="/me/messages/AAMkAGI2TG93AAA%3D",
    )
    assert (email.kind, email.entity_url) == ("email", "/me/messages/AAMkAGI2TG93AAA=")
    chat = Reference.model_validate({
        "title": "Chat", "url": "https://teams.microsoft.com/l/message/1", "kind": "teams",
        "entity_url": "/chats/19%3Aabc_def%40unq.gbl.spaces/messages/1792189544345",
    })
    assert chat.kind == "chat"
    document = Reference(title="IPS", url=IPS_URL, entity_url="/drives/b!d1/items/01ITEM")
    assert (document.kind, document.drive_id, document.item_id) == ("file", "b!d1", "01ITEM")
    miscopied = Reference(title="IPS", url=IPS_URL, entity_url="/drives/b!d1/items/01ITEM", drive_id="b!d", item_id="01")
    assert (miscopied.drive_id, miscopied.item_id) == ("b!d1", "01ITEM")
    unsafe = Reference(
        title="Agenda", url="https://outlook.office365.com/owa/", kind="email",
        entity_url="/me/messages/../../users",
    )
    assert unsafe.entity_url is None and unsafe.kind == "email"
    assert Reference.model_validate({"title": "Q4", "url": IPS_URL, "kind": "meeting"}).kind == "event"
    text = "Answer\n```workiq_references\n" + json.dumps([{
        "title": "Agenda", "url": "https://outlook.office365.com/owa/?ItemID=x", "kind": "email",
        "entity_url": "/me/messages/AAMkAGI2TG93AAA%3D",
    }]) + "\n```"
    [cited] = references_from_text(text)
    assert cited.kind == "email" and cited.entity_url == "/me/messages/AAMkAGI2TG93AAA="


def test_approval_request_is_offered_to_the_advisor_not_rejected() -> None:
    record = RunRecord(id=str(uuid4()), session_id=str(uuid4()), prompt_preview="Draft", started_at=now())
    adapter = ResponseEvents(record, ENDPOINT)
    adapter.completed_item({
        "type": "function_call", "name": "draft_follow_up_email", "call_id": "call_1", "arguments": "{}",
    })
    request = {
        "type": "mcp_approval_request", "id": "apr_1", "name": "draft_follow_up_email",
        "server_label": "advisor", "arguments": json.dumps({
            "to": "michael.scott@example.com", "subject": "Thursday", "body": "Hi Michael", "count": 5,
        }),
    }
    emitted = adapter.completed_item(request)
    [approval] = [event for event in emitted if event.type == EventType.CUSTOM and event.name == "workiq.approval"]
    assert approval.value["id"] == "apr_1"
    assert approval.value["arguments"] == {
        "to": "michael.scott@example.com", "subject": "Thursday", "body": "Hi Michael",
    }
    assert record.activity[0].status == "awaiting_approval"
    assert adapter.completed_item(request) == []
    with pytest.raises(HTTPException):
        adapter.completed_item({**request, "id": "has space"})
    # The preview hosting adapter can repeat the arguments; the advisor still sees the exact email.
    doubled = {**request, "id": "apr_2", "arguments": request["arguments"] + request["arguments"]}
    [again] = [
        event for event in adapter.completed_item(doubled)
        if event.type == EventType.CUSTOM and event.name == "workiq.approval"
    ]
    assert again.value["arguments"]["subject"] == "Thursday"


def test_only_an_approval_continuation_accepts_the_approved_call_output() -> None:
    output = json.dumps({TIMING_KEY: {
        "version": 1, "call_id": "call_1", "name": "draft_follow_up_email",
        "started_at": "2026-10-16T10:00:00Z", "duration_ms": 1200, "failed": False,
    }, "output": '{"status":"draft_created"}'})
    item = {"type": "function_call_output", "call_id": "call_1", "output": output}
    plain = ResponseEvents(
        RunRecord(id=str(uuid4()), session_id=str(uuid4()), prompt_preview="x", started_at=now()), ENDPOINT,
    )
    with pytest.raises(HTTPException):
        plain.completed_item(item)
    record = RunRecord(id=str(uuid4()), session_id=str(uuid4()), prompt_preview="Approved", started_at=now())
    adapter = ResponseEvents(record, ENDPOINT, continuation=True)
    emitted = adapter.completed_item(item)
    kinds = [event.type for event in emitted]
    assert kinds.index(EventType.TOOL_CALL_START) < kinds.index(EventType.TOOL_CALL_END) < kinds.index(
        EventType.TOOL_CALL_RESULT)
    assert record.activity[0].name == "draft_follow_up_email"
    assert record.activity[0].status == "completed"
    assert record.activity[0].duration_ms == 1200


def test_approval_decision_resumes_the_hosted_conversation(api_client, config: UIConfig, signing_key) -> None:
    client, services = api_client
    headers = {"Authorization": f"Bearer {user_token(config, signing_key)}"}
    identifier = client.post("/api/sessions", headers=headers, json={}).json()["id"]
    sent = []

    async def stream(token: str, conversation_id: str, request):
        sent.append(request)
        yield {"type": "response.created", "response": {"id": "resp_2"}}
        yield {"type": "response.output_item.done", "item": {
            "type": "function_call_output", "call_id": "call_1", "output": '{"status":"draft_created"}',
        }}
        yield {"type": "response.completed", "response": {"id": "resp_2", "status": "completed", "output": [{
            "id": "msg_2", "type": "message", "role": "assistant",
            "content": [{"type": "output_text", "text": "Draft saved in Outlook Drafts. Not sent."}],
        }]}}

    services.foundry.stream = stream
    body = {
        "threadId": identifier, "runId": str(uuid4()), "state": {}, "tools": [], "context": [],
        "forwardedProps": {"approval": {"id": "apr_1", "approve": True}}, "messages": [],
    }
    response = client.post("/api/chat", headers=headers, json=body)
    assert response.status_code == 200
    assert sent == [[{"type": "mcp_approval_response", "approval_request_id": "apr_1", "approve": True}]]
    assert '"RUN_FINISHED"' in response.text
    run = client.get(f"/api/sessions/{identifier}", headers=headers).json()["runs"][0]
    assert run["prompt_preview"] == "Approved the proposed action"
    assert run["activity"][0]["status"] == "completed"
    for forwarded in ({"approval": {"id": "apr_1", "approve": "yes"}}, {"approval": {"id": "a b", "approve": True}},
                      {"other": True}):
        body = {**body, "runId": str(uuid4()), "forwardedProps": forwarded}
        assert client.post("/api/chat", headers=headers, json=body).status_code == 422
    assert len(sent) == 1


def test_eastern_time_follows_daylight_saving_rules() -> None:
    assert eastern(datetime(2026, 3, 8, 6, 59, tzinfo=UTC)) == "Sun Mar 8, 2026, 1:59 AM ET"
    assert eastern(datetime(2026, 3, 8, 7, 0, tzinfo=UTC)) == "Sun Mar 8, 2026, 3:00 AM ET"
    assert eastern(datetime(2026, 10, 15, 13, 12, tzinfo=UTC)) == "Thu Oct 15, 2026, 9:12 AM ET"
    assert eastern(datetime(2026, 11, 1, 6, 0, tzinfo=UTC)) == "Sun Nov 1, 2026, 1:00 AM ET"
    assert html_text("<p>NVDA note &amp; chart</p><div>Line<br>two</div>") == "NVDA note & chart\nLine\ntwo"


GRAPH_ITEMS = {
    "/v1.0/me/messages/AAMkAGI2TG93AAA=": {
        "subject": "Agenda for Thursday", "receivedDateTime": "2026-10-15T13:12:00Z",
        "from": {"emailAddress": {"name": "Michael Scott", "address": "michael.scott@example.com"}},
        "toRecipients": [{"emailAddress": {"name": "Alex Morgan", "address": "alex@contoso.com"}}],
        "body": {"contentType": "text", "content": "Hi Alex,\r\n\r\nThree topics for Thursday."},
        "webLink": "https://outlook.office365.com/owa/?ItemID=AAMk&exvsurl=1",
    },
    "/v1.0/me/events/AAMkEVENT01": {
        "subject": "Year-end planning", "isOnlineMeeting": False,
        "start": {"dateTime": "2026-10-22T10:00:00.0000000", "timeZone": "Eastern Standard Time"},
        "end": {"dateTime": "2026-10-22T10:45:00.0000000", "timeZone": "Eastern Standard Time"},
        "organizer": {"emailAddress": {"name": "Alex Morgan", "address": "alex@contoso.com"}},
        "body": {"contentType": "html", "content": "<p>Agenda: 529 plan</p>"},
        "webLink": "https://evil.example.com/phish",
    },
    "/v1.0/chats/19:abc@unq.gbl.spaces/messages/2": {
        "id": "2", "messageType": "message", "createdDateTime": "2026-10-16T22:05:00Z",
        "from": {"user": {"displayName": "Jordan Lee"}},
        "body": {"contentType": "html", "content": "<p>NVDA note &amp; chart</p>"},
    },
    "/v1.0/chats/19:abc@unq.gbl.spaces/messages": {"value": [
        {"id": "3", "messageType": "systemEventMessage", "createdDateTime": "2026-10-16T22:06:00Z",
         "body": {"contentType": "html", "content": "<systemEventMessage/>"}},
        {"id": "2", "messageType": "message", "createdDateTime": "2026-10-16T22:05:00Z",
         "from": {"user": {"displayName": "Jordan Lee"}},
         "body": {"contentType": "html", "content": "<p>NVDA note &amp; chart</p>"}},
        {"id": "1", "messageType": "message", "createdDateTime": "2026-10-16T22:04:00Z",
         "from": {"user": {"displayName": "Alex Morgan"}}, "body": {"contentType": "text", "content": "Hi"}},
    ]},
    "/v1.0/chats/19:abc@unq.gbl.spaces": {
        "topic": None, "members": [{"displayName": "Alex Morgan"}, {"displayName": "Jordan Lee"}],
    },
}


async def test_source_view_reads_email_meeting_and_chat_with_the_users_graph_token() -> None:
    requests = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        item = GRAPH_ITEMS.get(request.url.path)
        return httpx.Response(200, json=item) if item is not None else httpx.Response(404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        email = await source_view(client, "graph-token", Reference(
            title="Agenda", url="https://outlook.office365.com/owa/?ItemID=x",
            entity_url="/me/messages/AAMkAGI2TG93AAA%3D",
        ))
        event = await source_view(client, "graph-token", Reference(
            title="Year-end", url="https://outlook.office365.com/calendar/item", entity_url="/me/events/AAMkEVENT01",
        ))
        chat = await source_view(client, "graph-token", Reference(
            title="Chat", url="https://teams.microsoft.com/l/message/19:abc@unq.gbl.spaces/2",
            entity_url="/chats/19%3Aabc%40unq.gbl.spaces/messages/2",
        ))
        with pytest.raises(HTTPException) as missing:
            await source_view(client, "graph-token", Reference(title="Agenda", url=IPS_URL, kind="email"))
    assert all(request.headers["Authorization"] == "Bearer graph-token" for request in requests)
    assert b"AAMkAGI2TG93AAA%3D" in requests[0].url.raw_path
    assert 'outlook.body-content-type="text"' in requests[0].headers["Prefer"]
    assert {"label": "From", "value": "Michael Scott <michael.scott@example.com>"} in email["fields"]
    assert {"label": "Received", "value": "Thu Oct 15, 2026, 9:12 AM ET"} in email["fields"]
    assert email["body"] == "Hi Alex,\n\nThree topics for Thursday."
    assert email["open_url"].startswith("https://outlook.office365.com/owa/")
    assert {"label": "When", "value": "Thu Oct 22, 2026, 10:00 AM ET to 10:45 AM ET"} in event["fields"]
    assert event["body"] == "Agenda: 529 plan"
    assert event["open_url"] == "https://outlook.office365.com/calendar/item"
    assert [message["id"] for message in chat["messages"]] == ["1", "2"]
    assert [message["cited"] for message in chat["messages"]] == [False, True]
    assert chat["messages"][1]["text"] == "NVDA note & chart"
    assert {"label": "Chat", "value": "Alex Morgan, Jordan Lee"} in chat["fields"]
    assert chat["open_url"].startswith("https://teams.microsoft.com/") and chat["note"]
    assert missing.value.status_code == 422


ID_PREFIX = ("AAMkADBjMGU3YTUxLTJmNGItNGQxYS05ZTNjLTZiNWE0ZjNlMmQxYwBGAAAAAAD_c3ludGhldGljRm9sZGVycwDU3ludGhldGljU3Rvc"
             "mUxUAAAAAAE")
EVENT_ID = ID_PREFIX + "NAADU3ludGhldGljU3RvcmUxUAABY7FMeAAA="
MAIL_ID = ID_PREFIX + "MAADU3ludGhldGljU3RvcmUxUAABZGhndAAA="
SIBLING_ID = ID_PREFIX + "MAADU3ludGhldGljU3RvcmUxUAABZGhneAAA="
WORKIQ_OUTPUT = json.dumps({"hits": [
    {"entityUrl": f"/me/events/{EVENT_ID.replace('=', '%3D')}"},
    {"entityUrl": f"/me/messages/{MAIL_ID.replace('=', '%3D')}",
     "webUrl": "https://outlook.office365.com/owa/?ItemID=" + MAIL_ID.replace("_", "%2b").replace("=", "%3d")},
    {"entityUrl": f"/me/messages/{SIBLING_ID.replace('=', '%3D')}"},
    {"entityUrl": "/chats/19%3Aabc_def%40unq.gbl.spaces/messages/1792189544345"},
    {"entityUrl": "/drives/b!drive-1/items/01ABCDEFGHIJKLMNOPQRSTUVWXYZ2345"},
]})


def test_mis_copied_item_ids_are_repaired_from_work_iq_output_and_ties_are_left_alone() -> None:
    evidence = Evidence()
    evidence.record(WORKIQ_OUTPUT)

    def cite(entity_url: str, **fields) -> Reference:
        return Reference(title="Item", url="https://outlook.office365.com/owa/", entity_url=entity_url, **fields)

    mangled_event = cite("/me/events/" + EVENT_ID.replace("MmQxYwBG", "MmQxwBG"))
    assert evidence.repair(mangled_event).entity_url == f"/me/events/{EVENT_ID}"
    mangled_mail = cite("/me/messages/" + MAIL_ID[:60] + MAIL_ID[61:])
    assert evidence.repair(mangled_mail).entity_url == f"/me/messages/{MAIL_ID}"
    owa_form = cite("/me/messages/" + MAIL_ID.replace("_", "+"))
    assert evidence.repair(owa_form).entity_url == f"/me/messages/{MAIL_ID}"
    exact = cite(f"/me/messages/{MAIL_ID}")
    assert evidence.repair(exact) is exact
    tie = cite("/me/messages/" + MAIL_ID.replace("ZGhnd", "ZGhn"))
    assert evidence.repair(tie) is tie
    unrelated = cite("/me/messages/AAMkAGUNRELATEDxxxxxxxxxxxxxxxxxxxxxxx=")
    assert evidence.repair(unrelated) is unrelated
    chat = cite("/chats/19:abc_dxf@unq.gbl.spaces/messages/1792189544345")
    assert evidence.repair(chat).entity_url == "/chats/19:abc_def@unq.gbl.spaces/messages/1792189544345"
    document = Reference(title="IPS", url=IPS_URL, drive_id="b!drive-1", item_id="01ABCDEFGHIJKLMNOPQRSTUVWXYZ245")
    assert evidence.repair(document).item_id == "01ABCDEFGHIJKLMNOPQRSTUVWXYZ2345"


def test_an_id_missing_the_span_between_its_repeated_segments_is_repaired_to_its_own_item() -> None:
    # Outlook IDs repeat one segment; a model copying the ID can jump from the first copy to the second.
    evidence = Evidence()
    evidence.record(WORKIQ_OUTPUT)
    segment = "DU3ludGhldGljU3RvcmUxU"
    first = MAIL_ID.index(segment)
    dropped = MAIL_ID[:first] + MAIL_ID[MAIL_ID.index(segment, first + 1):]
    assert len(MAIL_ID) - len(dropped) == 32
    cited = Reference(title="Item", url="https://outlook.office365.com/owa/", entity_url=f"/me/messages/{dropped}")
    # The sibling shares everything but the item's own ending, so only MAIL_ID fits.
    assert evidence.repair(cited).entity_url == f"/me/messages/{MAIL_ID}"


def test_an_item_whose_id_cannot_be_matched_is_found_by_its_exact_title_in_the_graph_reads() -> None:
    def envelope(values: list[dict]) -> str:
        data = json.dumps({"results": [{"data": {"value": values}, "statusCode": 200}]})
        return json.dumps({"__workiq_tool_execution__": {"version": 1, "name": "workiq___fetch"}, "output": data})

    evidence = Evidence()
    evidence.record(envelope([
        {"id": MAIL_ID, "subject": "NVDA +11.2% today", "receivedDateTime": "2026-10-19T21:00:00Z"},
        {"id": SIBLING_ID, "subject": "Q4 portfolio review", "receivedDateTime": "2026-10-14T21:00:00Z"},
        {"id": EVENT_ID, "subject": "Q4 portfolio review", "start": {"dateTime": "2026-10-16T15:30:00"}},
    ]))

    def cite(title: str, entity_url: str) -> Reference:
        return Reference(title=title, url="https://outlook.office365.com/owa/", entity_url=entity_url)

    garbled = MAIL_ID[:-12] + "XXXXXXXXAAA="
    assert evidence.repair(cite("NVDA +11.2% today", f"/me/messages/{garbled}")).entity_url == f"/me/messages/{MAIL_ID}"
    # The meeting and its invitation share a title: each kind keeps its own item.
    meeting = cite("Q4 portfolio review", "/me/events/" + EVENT_ID[:-12] + "XXXXXXXXAAA=")
    assert evidence.repair(meeting).entity_url == f"/me/events/{EVENT_ID}"
    invitation = cite("Q4 portfolio review", "/me/messages/" + SIBLING_ID[:-12] + "XXXXXXXXAAA=")
    assert evidence.repair(invitation).entity_url == f"/me/messages/{SIBLING_ID}"
    evidence.record(envelope([{"id": MAIL_ID.replace("ZGhnd", "ZGhnx"), "subject": "NVDA +11.2% today",
                               "receivedDateTime": "2026-10-20T21:00:00Z"}]))
    ambiguous = cite("NVDA +11.2% today", f"/me/messages/{garbled}")
    assert evidence.repair(ambiguous) is ambiguous


def test_streamed_references_keep_the_cited_ids_even_when_a_sibling_was_retrieved() -> None:
    # Repair happens only after Graph can't find an item; a valid ID must never be swapped for a sibling's.
    record = RunRecord(id=str(uuid4()), session_id=str(uuid4()), prompt_preview="Brief", started_at=now())
    adapter = ResponseEvents(record, ENDPOINT)
    adapter.completed_item({
        "type": "function_call", "name": "workiq___retrieve", "call_id": "call_1", "arguments": "{}",
    })
    sibling_only = json.dumps({"hits": [{"entityUrl": f"/me/messages/{SIBLING_ID}"}]})
    adapter.completed_item({"type": "function_call_output", "call_id": "call_1", "output": sibling_only})
    cited = {"title": "Agenda", "url": "https://outlook.office365.com/owa/", "kind": "email",
             "entity_url": f"/me/messages/{MAIL_ID}"}
    text = "Brief\n```workiq_references\n" + json.dumps([cited]) + "\n```"
    emitted = adapter.completed_item({
        "type": "message", "role": "assistant", "id": "msg_1", "content": [{"type": "output_text", "text": text}],
    })
    [event] = [event for event in emitted if event.type == EventType.CUSTOM and event.name == "workiq.references"]
    assert event.value["references"][0]["entity_url"] == f"/me/messages/{MAIL_ID}"


async def test_teams_pane_keeps_an_older_cited_message() -> None:
    newer = [
        {"id": str(100 + index), "messageType": "message", "createdDateTime": f"2026-10-16T23:{index:02d}:00Z",
         "from": {"user": {"displayName": "Jordan Lee"}}, "body": {"contentType": "text", "content": f"m{index}"}}
        for index in range(25)
    ]
    cited = {"id": "7", "messageType": "message", "createdDateTime": "2026-10-16T22:00:00Z",
             "from": {"user": {"displayName": "Jordan Lee"}}, "body": {"contentType": "text", "content": "NVDA"}}

    def respond(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/messages/7"):
            return httpx.Response(200, json=cited)
        if path.endswith("/messages"):
            return httpx.Response(200, json={"value": newer[::-1][:20]})
        return httpx.Response(200, json={"topic": "Michael Scott", "members": []})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        view = await source_view(client, "graph-token", Reference(
            title="Chat", url="https://teams.microsoft.com/l/message/19:abc@unq.gbl.spaces/7",
            entity_url="/chats/19:abc@unq.gbl.spaces/messages/7",
        ))
    assert len(view["messages"]) == 20
    assert view["messages"][0]["id"] == "7" and view["messages"][0]["cited"]
    assert view["messages"][-1]["id"] == "124"


def test_rejected_approval_is_shown_as_cancelled_not_returned() -> None:
    record = RunRecord(id=str(uuid4()), session_id=str(uuid4()), prompt_preview="Rejected", started_at=now())
    adapter = ResponseEvents(record, ENDPOINT, continuation=True)
    adapter.completed_item({
        "type": "function_call_output", "call_id": "call_9",
        "output": "Error: Tool call invocation was rejected by user.",
    })
    assert (record.activity[0].name, record.activity[0].status) == ("rejected_action", "cancelled")


def test_skill_that_quotes_the_rejection_text_is_still_shown_as_loaded() -> None:
    record = RunRecord(id=str(uuid4()), session_id=str(uuid4()), prompt_preview="Draft", started_at=now())
    adapter = ResponseEvents(record, ENDPOINT)
    adapter.completed_item({
        "type": "function_call", "name": "load_skill", "call_id": "skill1",
        "arguments": '{"skill_name":"client-follow-up"}',
    })
    adapter.completed_item({
        "type": "function_call_output", "call_id": "skill1",
        "output": '---\nname: client-follow-up\n---\nIf the result is "Tool call invocation was rejected by user", '
                  "say that nothing was created.",
    })
    assert (record.activity[0].kind, record.activity[0].status) == ("skill", "completed")


def test_a_stopped_durable_pack_marks_unfinished_calls_interrupted_and_explains_resume() -> None:
    record = RunRecord(id=str(uuid4()), session_id=str(uuid4()), prompt_preview="Run the pack", started_at=now())
    adapter = ResponseEvents(record, ENDPOINT)
    for call_id, name in (("c1", "recall_client_memory"), ("c2", "run_durable_meeting_pack")):
        adapter.completed_item({"type": "function_call", "name": name, "call_id": call_id, "arguments": "{}"})
    assert adapter.stopped_durable_pack()
    adapter.interrupt(failed=True, stopped=True)
    assert record.status == "failed"
    assert [call.status for call in record.activity] == ["cancelled", "cancelled"]
    assert "state store" in DURABLE_STOP and "resume" in DURABLE_STOP
    other = RunRecord(id=str(uuid4()), session_id=str(uuid4()), prompt_preview="Prepare", started_at=now())
    plain = ResponseEvents(other, ENDPOINT)
    plain.completed_item({"type": "function_call", "name": "workiq___fetch", "call_id": "c3", "arguments": "{}"})
    assert not plain.stopped_durable_pack()
    plain.interrupt(failed=True)
    assert other.activity[0].status == "failed"


def test_source_click_repairs_a_mis_copied_id_from_the_conversation(
    api_client, config: UIConfig, signing_key,
) -> None:
    client, services = api_client
    headers = {"Authorization": f"Bearer {user_token(config, signing_key)}"}
    identifier = client.post("/api/sessions", headers=headers, json={}).json()["id"]
    paths = []

    def respond(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if request.url.path == f"/v1.0/me/events/{EVENT_ID}":
            return httpx.Response(200, json={"subject": "Year-end planning - Michael Scott",
                                             "webLink": "https://outlook.office365.com/calendar/item"})
        return httpx.Response(400)

    services.client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    services.foundry.items = AsyncMock(return_value=[{"type": "function_call_output", "output": WORKIQ_OUTPUT}])
    reference = {"title": "Year-end planning", "url": "https://outlook.office365.com/calendar/item",
                 "kind": "event", "entity_url": "/me/events/" + EVENT_ID.replace("MmQxYwBG", "MmQxwBG")}
    response = client.post(f"/api/sessions/{identifier}/source", headers=headers, json={"reference": reference})
    assert response.status_code == 200
    assert response.json()["title"] == "Year-end planning - Michael Scott"
    assert len(paths) == 2 and paths[-1] == f"/v1.0/me/events/{EVENT_ID}"
    services.foundry.items = AsyncMock(return_value=[])
    response = client.post(f"/api/sessions/{identifier}/source", headers=headers, json={"reference": reference})
    assert response.status_code == 404 and "Work IQ returned" in response.json()["detail"]
