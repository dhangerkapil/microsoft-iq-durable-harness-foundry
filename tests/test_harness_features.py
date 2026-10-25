import asyncio
import base64
import json
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from agent_framework.exceptions import ToolExecutionException
from mcp.types import CallToolResult, TextContent

from advisor_team.advisor import EvidenceCheck, FaultSwitch, InMemoryItemStore, TurnLedger, clock_note
from advisor_team.drafts import created_draft, draft_status, validate_draft
from advisor_team.durable import PHASES, DurableMeetingPack, ProcessStopped, compact
from advisor_team.pdfpage import render_page
from advisor_team.workiq import serialize_result
from advisor_team.workiq_tools import (
    ChartReader,
    classify,
    classify_failure,
    client_documents,
    drive_ids,
    error_detail,
    inspect_document_evidence,
    mutation_decision,
    onedrive_path_from_url,
    parse_reading,
    read_chart_evidence,
)


def metadata(data: dict) -> str:
    return serialize_result(CallToolResult(content=[], structuredContent={"results": [{"statusCode": 200, "data": data}]}))


def blob(data: bytes) -> str:
    return serialize_result(CallToolResult(content=[], structuredContent={
        "statusCode": 200, "contentType": "application/pdf", "sizeBytes": len(data),
        "base64Content": base64.b64encode(data).decode(),
    }))


def shared_store():
    store = InMemoryItemStore()

    async def factory():
        return store

    return store, factory


class Crash(Exception):
    pass


class FakeWorkIQ:
    def __init__(self):
        self.calls = []

    async def call_tool(self, tool_name, **kwargs):
        self.calls.append((tool_name, kwargs))
        if tool_name == "workiq___fetch" and "calendarView" in kwargs["entityUrls"][0]:
            return json.dumps({"results": [{"statusCode": 200, "data": {"value": [
                {"subject": "Year-end planning - Michael Scott", "start": {"dateTime": "2026-10-22T14:00:00"}},
                {"subject": "Dentist", "start": {"dateTime": "2026-10-21T14:00:00"}},
            ]}}]})
        if tool_name == "workiq___fetch":
            return json.dumps({"results": [{"statusCode": 200, "data": {"value": [
                {"subject": "Change of plans", "receivedDateTime": "2026-10-17T14:24:00Z",
                 "from": {"emailAddress": {"name": "Michael Scott", "address": "m@example.com"}},
                 "body": {"contentType": "html", "content": "<p>We found the house &amp; need $400,000.</p>"}},
            ]}}]})
        source = kwargs["capabilities"][0]["name"]
        return json.dumps({"application/vnd.ms-workiq.retrieval": {
            "markdown": f"{source} passage about Michael Scott", "resultCount": 1,
            "retrievalHits": [{"webUrl": "https://contoso.sharepoint.com/f", "entityUrl": "/drives/d/items/i"}]}})


def stop(code):
    """exit_process stand-in: returns, so the pack raises ProcessStopped as if the process had died."""


def run_base(pack, durable_run):
    return pack.base("Michael Scott", durable_run["run"])


async def test_crash_between_writes_is_reconciled_without_repeating_or_rewriting():
    durable, durable_factory = shared_store()
    _, memory_factory = shared_store()
    first = FakeWorkIQ()
    pack = DurableMeetingPack(durable_factory, memory_factory, first, exit_process=stop)
    await pack.arm_crash(True)  # default: between_writes, after wave 2's checkpoints
    with pytest.raises(ProcessStopped):
        await pack.run("Michael Scott")
    assert [name for name, _ in first.calls] == ["workiq___retrieve", "workiq___retrieve", "workiq___fetch",
                                                 "workiq___fetch"]
    base = run_base(pack, await pack.latest_run("Michael Scott"))
    assert (await durable.get_item(f"{base}/progress")).value["next_wave"] == 2
    for name in ("1-documents", "2-meetings", "3-calendar", "4-email"):
        assert await durable.get_item(f"{base}/checkpoints/{name}") is not None
    assert not await pack.crash_armed()
    writes = durable.writes

    second = FakeWorkIQ()
    resumed = await DurableMeetingPack(durable_factory, memory_factory, second, exit_process=stop).run("Michael Scott")
    report = resumed["durability"]
    assert resumed["attempt"] == 2 and report["resumed_unfinished_run"] is True and second.calls == []
    assert report["restored_checkpoints"] == ["1-documents", "2-meetings"]
    assert report["reconciled_checkpoints"] == ["3-calendar", "4-email"]
    assert report["executed_in_this_process"] == [] and report["progress_marker"]["next_wave"] == "done"
    # The attempt record, one marker move, the run status and the attempt's end: no checkpoint was written twice.
    assert durable.writes - writes == 4
    events = [event["event"] for event in report["timeline"]]
    stopped = next(i for i, e in enumerate(events) if e.startswith("attempt 1 interrupted"))
    assert "between_writes" in events[stopped]
    assert events.index("checkpoint 4-email written (Work IQ)") < stopped
    assert events.index("progress marker 1 -> 2") < events.index("checkpoint 3-calendar written (Work IQ)")
    assert any(e.startswith("progress marker 2 -> done; reused checkpoints written before the stop: 3-calendar, "
                            "4-email") for e in events[stopped:])
    results = resumed["results"]
    assert results["documents"]["markdown"].startswith("OneDriveAndSharePoint passage")
    assert results["documents"]["hits"][0]["entityUrl"] == "/drives/d/items/i"
    assert results["email"]["messages"][0]["body"] == "We found the house & need $400,000."
    assert results["calendar"]["events"] == [
        {"subject": "Year-end planning - Michael Scott", "start": {"dateTime": "2026-10-22T14:00:00"},
         "end": None, "isOnlineMeeting": None}]
    assert set(results) == {phase.name for phase in PHASES}

    third = await DurableMeetingPack(durable_factory, memory_factory, FakeWorkIQ()).run("Michael Scott")
    assert third["durability"]["resumed_unfinished_run"] is False and third["attempt"] == 1
    state = await pack.describe("Michael Scott")
    assert state["latest_run"]["status"] == "completed" and len(state["latest_run"]["checkpoints"]) == 4
    assert all(checkpoint["verified"] for checkpoint in state["latest_run"]["checkpoints"])


async def test_mid_wave_stop_loses_only_the_phases_in_flight():
    durable, durable_factory = shared_store()
    _, memory_factory = shared_store()
    calls = []

    async def portfolio(client, topics):
        calls.append("portfolio")
        return {"answer": "NVDA 26.1% against a 25% limit"}

    async def firm_policy(client, topics):
        await asyncio.sleep(0.3)
        calls.append("firm_policy")
        return {"passages": [{"document": "CP-104", "section": "3"}]}

    async def market_news(client, topics):
        await asyncio.sleep(0.3)
        calls.append("market_news")
        return {"results": topics}

    runners = {"portfolio": portfolio, "firm_policy": firm_policy, "market_news": market_news}
    pack = DurableMeetingPack(durable_factory, memory_factory, FakeWorkIQ(), runners=runners, exit_process=stop)
    await pack.arm_crash(True, mode="mid_wave", wave=3)
    with pytest.raises(ProcessStopped):
        await pack.run("Michael Scott", public_topics=["NVIDIA AI data center outlook"])
    assert calls == ["portfolio"]
    base = run_base(pack, await pack.latest_run("Michael Scott"))
    assert (await durable.get_item(f"{base}/progress")).value["next_wave"] == 3
    assert await durable.get_item(f"{base}/checkpoints/5-portfolio") is not None
    assert await durable.get_item(f"{base}/checkpoints/6-firm_policy") is None

    resumed = await DurableMeetingPack(durable_factory, memory_factory, FakeWorkIQ(), runners=runners).run(
        "Michael Scott")
    report = resumed["durability"]
    assert report["restored_checkpoints"] == ["1-documents", "2-meetings", "3-calendar", "4-email"]
    assert report["reconciled_checkpoints"] == ["5-portfolio"]
    assert sorted(report["executed_in_this_process"]) == ["6-firm_policy", "7-market_news"]
    assert calls.count("portfolio") == 1
    assert resumed["sources"]["firm_policy"] == "Foundry IQ" and resumed["sources"]["market_news"] == "Web IQ"
    assert resumed["results"]["market_news"] == {"results": ["NVIDIA AI data center outlook"]}


async def test_news_phase_is_skipped_without_public_topics():
    _, durable_factory = shared_store()
    _, memory_factory = shared_store()

    async def market_news(client, topics):
        raise AssertionError("must not run without a public topic")

    result = await DurableMeetingPack(durable_factory, memory_factory, FakeWorkIQ(),
                                      runners={"market_news": market_news}).run("Michael Scott")
    assert "skipped" in result["results"]["market_news"]


class FailingDocumentsWorkIQ(FakeWorkIQ):
    """Wave 1: the documents retrieve fails while the slower meetings retrieve is still running."""

    async def call_tool(self, tool_name, **kwargs):
        if tool_name == "workiq___retrieve" and kwargs["capabilities"][0]["name"] == "OneDriveAndSharePoint":
            await asyncio.sleep(0.01)
            raise RuntimeError("Work IQ retrieve failed")
        if tool_name == "workiq___retrieve":
            await asyncio.sleep(0.1)
        return await super().call_tool(tool_name, **kwargs)


async def test_failed_phase_waits_for_its_wave_and_the_next_attempt_resumes_only_the_rest():
    durable, durable_factory = shared_store()
    _, memory_factory = shared_store()
    pack = DurableMeetingPack(durable_factory, memory_factory, FailingDocumentsWorkIQ())
    with pytest.raises(RuntimeError, match="retrieve failed"):
        await pack.run("Michael Scott")
    run = await pack.latest_run("Michael Scott")
    base = run_base(pack, run)
    assert run["status"] == "running" and (await durable.get_item(f"{base}/progress")).value["next_wave"] == 1
    assert await durable.get_item(f"{base}/checkpoints/2-meetings") is not None
    assert await durable.get_item(f"{base}/checkpoints/1-documents") is None

    second = FakeWorkIQ()
    resumed = await DurableMeetingPack(durable_factory, memory_factory, second).run("Michael Scott")
    report = resumed["durability"]
    assert report["resumed_unfinished_run"] is True and report["reconciled_checkpoints"] == ["2-meetings"]
    assert sorted(report["executed_in_this_process"]) == ["1-documents", "3-calendar", "4-email"]
    assert [name for name, _ in second.calls] == ["workiq___retrieve", "workiq___fetch", "workiq___fetch"]
    assert second.calls[0][1]["capabilities"] == [{"name": "OneDriveAndSharePoint"}]
    assert resumed["attempt"] == 2 and set(resumed["results"]) == {phase.name for phase in PHASES}
    assert any(e["event"].startswith("attempt 1 failed: RuntimeError") for e in report["timeline"])

async def test_fault_switch_routes_process_crash_to_the_pack():
    _, memory_factory = shared_store()
    _, durable_factory = shared_store()
    pack = DurableMeetingPack(durable_factory, memory_factory, FakeWorkIQ())
    switch = FaultSwitch(memory_factory).tool(crash_toggle=pack.arm_crash)
    result = await switch.invoke(arguments={"component": "process_crash", "enabled": True})
    text = "".join(getattr(item, "text", "") or "" for item in result) if isinstance(result, list) else str(result)
    assert json.loads(text)["armed"] is True and await pack.crash_armed()


def test_compact_keeps_unparsed_text_short():
    phase = PHASES[1]
    out = compact(phase, "Michael Scott", "x" * 20_000)
    assert out["truncated"] and len(out["text"]) == 9_000


def test_evidence_check_needs_work_iq_and_fabric_only_when_required():
    ledger = TurnLedger(client_turn=True)
    ledger.record("run_durable_meeting_pack", "ok")
    assert EvidenceCheck(ledger).should_continue(iteration=1) == (False, None)
    again, feedback = EvidenceCheck(ledger, require_fabric=True).should_continue(iteration=1)
    assert again and "Fabric" in feedback
    empty = TurnLedger(client_turn=True)
    again, feedback = EvidenceCheck(empty).should_continue(iteration=1)
    assert again and "Work IQ" in feedback


def probe_result(payload=None, text=None, error=False) -> str:
    content = [TextContent(type="text", text=text)] if text else []
    return serialize_result(CallToolResult(content=content, structuredContent=payload, isError=error))


# A successful read whose data names a policy document; the text block repeats the data, as MCP servers do.
IPS_READ = {"results": [{"statusCode": 200, "data": {"value": [
    {"name": "Michael Scott - Investment Policy Statement (signed).pdf"}]}}]}


@pytest.mark.parametrize(("raw", "decision", "status"), [
    (probe_result({"results": [{"statusCode": 200, "data": {"value": []}}]}), "allowed", 200),
    (probe_result({"results": [{"statusCode": 403, "data": {"error": {"message": "Denied by policy"}}}]}),
     "denied by Work IQ tenant policy", 403),
    (probe_result(text="Request blocked: mutations are disabled by the tenant policy.", error=True),
     "denied by Work IQ tenant policy", None),
    (probe_result({"statusCode": 403, "error": {"code": "Forbidden"}}), "denied: insufficient permissions", 403),
    (probe_result({"statusCode": 201, "data": {"id": "x"}}), "allowed", 201),
    (probe_result(IPS_READ, text=json.dumps(IPS_READ)), "allowed", 200),
])
def test_probe_classification(raw, decision, status):
    got_status, got_decision, _ = classify(raw)
    assert got_decision == decision and got_status == status


GRAPH_404 = {"results": [{"statusCode": 404, "data": {"error": {"code": "ErrorItemNotFound",
                                                                "message": "The specified object was not found."}}}]}


@pytest.mark.parametrize(("raw", "expected"), [
    (probe_result(GRAPH_404), "allowed by policy (Microsoft Graph rejected the test item; nothing changed)"),
    (probe_result({"statusCode": 400, "error": {"code": "ErrorInvalidIdMalformed"}}),
     "allowed by policy (Microsoft Graph rejected the test item; nothing changed)"),
    (probe_result({"results": [{"statusCode": 403, "data": {"error": {"message": "Write actions are disabled"}}}]}),
     "denied by Work IQ tenant policy"),
    (probe_result(text="Delete is not allowed for this tenant.", error=True), "denied by Work IQ tenant policy"),
    (probe_result(text="Tool workiq___delete_entity not found", error=True), "failed"),
])
def test_mutation_probe_decisions(raw, expected):
    status, decision, detail = classify(raw)
    assert mutation_decision(status, decision, detail) == expected


def test_probe_error_detail_drops_toolbox_envelope():
    payload = {"meta": {"toolbox": "advisor-governance", "config": "x" * 900}, "error": None, "message": "Denied"}
    assert "meta" not in error_detail("", payload, None) and "Denied" in error_detail("", payload, None)
    assert error_detail("plain text", None, None) == "plain text"
    assert error_detail("", payload, {"code": "Forbidden"}) == '{"code": "Forbidden"}'


def test_clock_note_gives_the_model_the_real_date_in_utc_and_eastern_time():
    note = clock_note(datetime(2026, 10, 17, 1, 5, 9, tzinfo=UTC))
    assert note.startswith("Current time: 2026-10-17T01:05:09Z (Friday, October 16, 2026, 9:05 PM EDT).")
    assert "(Tuesday, December 1, 2026, 10:00 AM EST)" in clock_note(datetime(2026, 12, 1, 15, 0, tzinfo=UTC))


def test_story_clock_floor_applies_only_before_it_and_ignores_bad_values(monkeypatch):
    monkeypatch.setenv("ADVISOR_CLOCK_NOT_BEFORE", "2026-10-20T13:00:00Z")
    # Before the floor (the story's Monday morning), the agent's now is the floor: Tuesday 9:00 AM Eastern.
    assert clock_note(datetime(2026, 10, 19, 14, 30, tzinfo=UTC)).startswith(
        "Current time: 2026-10-20T13:00:00Z (Tuesday, October 20, 2026, 9:00 AM EDT).")
    # After it, real time again: nothing to remove before the next demo.
    assert clock_note(datetime(2026, 10, 21, 14, 0, tzinfo=UTC)).startswith("Current time: 2026-10-21T14:00:00Z")
    monkeypatch.setenv("ADVISOR_CLOCK_NOT_BEFORE", "next Tuesday")
    assert clock_note(datetime(2026, 10, 19, 14, 30, tzinfo=UTC)).startswith("Current time: 2026-10-19T14:30:00Z")
    monkeypatch.setenv("ADVISOR_CLOCK_NOT_BEFORE", "2026-10-20T09:00:00-04:00")
    assert clock_note(datetime(2026, 10, 19, 14, 30, tzinfo=UTC)).startswith("Current time: 2026-10-20T13:00:00Z")


def test_story_clock_ceiling_keeps_the_story_day_until_removed(monkeypatch):
    monkeypatch.setenv("ADVISOR_CLOCK_NOT_AFTER", "2026-10-21T18:00:00Z")
    # After the story's week, the agent stays on its Wednesday afternoon: Thursday's client meeting is still ahead.
    assert clock_note(datetime(2026, 10, 23, 3, 5, tzinfo=UTC)).startswith(
        "Current time: 2026-10-21T18:00:00Z (Wednesday, October 21, 2026, 2:00 PM EDT).")
    assert clock_note(datetime(2026, 10, 20, 14, 0, tzinfo=UTC)).startswith("Current time: 2026-10-20T14:00:00Z")
    monkeypatch.setenv("ADVISOR_CLOCK_NOT_AFTER", "after the demo")
    assert clock_note(datetime(2026, 10, 23, 3, 5, tzinfo=UTC)).startswith("Current time: 2026-10-23T03:05:00Z")


async def test_the_meeting_pack_reads_the_calendar_around_the_story_clock(monkeypatch):
    monkeypatch.setenv("ADVISOR_CLOCK_NOT_AFTER", "2026-10-21T18:00:00Z")
    _, durable_factory = shared_store()
    _, memory_factory = shared_store()
    workiq = FakeWorkIQ()
    await DurableMeetingPack(durable_factory, memory_factory, workiq,
                             clock=lambda: datetime(2026, 11, 5, 12, 0, tzinfo=UTC)).run("Michael Scott")
    calendar = next(kwargs["entityUrls"][0] for name, kwargs in workiq.calls
                    if name == "workiq___fetch" and "calendarView" in kwargs["entityUrls"][0])
    assert "startDateTime=2026-10-11T18:00:00Z&endDateTime=2026-11-11T18:00:00Z" in calendar


def test_tool_errors_carrying_the_mcp_result_are_classified_from_that_result():
    denied = probe_result({"statusCode": 403, "error": {"message": "Mutations are disabled by the Work IQ policy"}},
                          error=True)
    status, decision, detail = classify_failure(RuntimeError(denied))
    assert (status, decision) == (403, "denied by Work IQ tenant policy") and "Mutations" in detail
    status, decision, detail = classify_failure(RuntimeError(probe_result(GRAPH_404, error=True)))
    assert mutation_decision(status, decision, detail).startswith("allowed by policy")
    assert classify_failure(TimeoutError("timed out")) == (None, "failed", "timed out")
    assert classify_failure(RuntimeError("Request blocked by tenant policy"))[1] == "denied by Work IQ tenant policy"
    assert classify_failure(RuntimeError(probe_result({"statusCode": 200, "data": {}})))[1] == "failed"
    assert [draft_status(value) for value in (
        "denied by Work IQ tenant policy", "denied: insufficient permissions", "failed",
    )] == ["denied_by_work_iq_policy", "denied", "failed"]


def test_draft_validation_and_created_result():
    assert validate_draft(" michael.scott@example.com ", "Thursday   meeting", " Hi ") == (
        "michael.scott@example.com", "Thursday meeting", "Hi")
    for to, subject, body in (("not-an-address", "s", "b"), ("a@b.com", "", "b"), ("a@b.com", "s", "x" * 4001),
                              ("a@b.com; c@d.com", "s", "b")):
        with pytest.raises(ValueError):
            validate_draft(to, subject, body)
    created = probe_result({"statusCode": 201, "data": {"id": "AAMk1", "webLink": "https://outlook.office365.com/x"}})
    assert created_draft(created) == {"id": "AAMk1", "webLink": "https://outlook.office365.com/x"}
    assert created_draft(probe_result({"statusCode": 403, "data": {"id": "AAMk1"}})) is None
    assert created_draft(probe_result(text="Denied by policy", error=True)) is None


def test_onedrive_urls_and_drive_paths():
    url = ("https://contoso-my.sharepoint.com/personal/admin_contoso_com/Documents/Client%20Files/"
           "Michael%20Scott/Michael%20Scott%20-%20Portfolio%20Overview%20(Oct%202026).pdf")
    assert onedrive_path_from_url(url) == (
        "/me/drive/root:/Client%20Files/Michael%20Scott/Michael%20Scott%20-%20Portfolio%20Overview%20%28Oct%202026%29.pdf")
    assert onedrive_path_from_url("https://contoso.sharepoint.com/sites/x/Shared%20Documents/a.pdf") is None
    assert onedrive_path_from_url("https://contoso-my.sharepoint.com/personal/a/Documents/../x.pdf") is None
    assert drive_ids("/drives/b!abc/items/01XYZ") == ("b!abc", "01XYZ")
    assert drive_ids("/me/drive/items/01XYZ") == (None, "01XYZ")


class FakeClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def call_tool(self, name, **arguments):
        self.calls.append((name, arguments))
        return self.responses.pop(0)


class FolderSession:
    """A Work IQ toolbox session over a fake tenant. A path it doesn't know fails the way the real toolbox does:
    Work IQ answers with an MCP tool error, and the toolbox raises it."""

    def __init__(self, routes: dict[str, str]) -> None:
        self.routes = routes

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return None

    async def call_tool(self, name, **arguments):
        [path] = arguments["entityUrls"]
        if path not in self.routes:
            raise ToolExecutionException("Error: Function failed.")
        return self.routes[path]


async def test_client_documents_lists_the_onedrive_and_team_site_folders_through_work_iq():
    fields = "id,name,webUrl,lastModifiedDateTime,file,parentReference"
    folders = "?$select=id,name,folder&$top=200"
    files = f"?$select={fields}&$top=200"

    def listing(*items, next_link=None):
        return metadata({"value": list(items), **({"@odata.nextLink": next_link} if next_link else {})})

    def document(identifier, name, drive):
        return {"id": identifier, "name": name, "webUrl": f"https://contoso.sharepoint.com/{identifier}",
                "file": {}, "parentReference": {"driveId": drive}}

    onedrive = f"/me/drive/root:/Client%20Files/Michael%20Scott:/children{files}"
    second_page = f"/drives/b!team/items/01MS/children{files}&$skiptoken=2"
    routes = {
        onedrive: listing(document("01IPS1", "IPS v1 (signed).pdf", "b!me"),
                          {"id": "01OLD", "name": "Archive", "folder": {}, "parentReference": {"driveId": "b!me"}}),
        "/sites/root/drive?$select=id": metadata({"id": "b!team"}),
        # Client Files is on the library's second page: the lookup follows the next link, given here in full.
        f"/drives/b!team/items/root/children{folders}": listing(
            {"id": "01RS", "name": "Research", "folder": {}},
            next_link=f"https://graph.microsoft.com/v1.0/drives/b!team/items/root/children{folders}&$skiptoken=2"),
        f"/drives/b!team/items/root/children{folders}&$skiptoken=2": listing(
            {"id": "01CF", "name": "Client Files", "folder": {}}),
        f"/drives/b!team/items/01CF/children{folders}": listing({"id": "01MS", "name": "michael scott", "folder": {}}),
        # The client's files span two pages; this next link comes back relative.
        f"/drives/b!team/items/01MS/children{files}": listing(
            document("01IPS2", "IPS v2 DRAFT.docx", "b!team"), next_link=second_page),
        second_page: listing(document("01NOTES", "Meeting notes.docx", "b!team")),
    }
    result = await client_documents(lambda: FolderSession(routes), "  Michael   Scott ")
    assert [(item["where"], item["name"], item["entity_url"]) for item in result["documents"]] == [
        ("OneDrive", "IPS v1 (signed).pdf", "/drives/b!me/items/01IPS1"),
        ("SharePoint", "IPS v2 DRAFT.docx", "/drives/b!team/items/01IPS2"),
        ("SharePoint", "Meeting notes.docx", "/drives/b!team/items/01NOTES")]
    assert result["locations"] == {"OneDrive": "1 file(s)", "SharePoint": "2 file(s)"}
    assert result["complete"] is True
    assert [call["tool"] for call in result["workiq_calls"]] == ["fetch"] * 7

    # A colleague has no such folder in their own OneDrive: Work IQ fails that read, and the team site's files
    # still come back, with the gap reported rather than raised.
    del routes[onedrive]
    colleague = await client_documents(lambda: FolderSession(routes), "Michael Scott")
    assert [item["name"] for item in colleague["documents"]] == ["IPS v2 DRAFT.docx", "Meeting notes.docx"]
    assert colleague["locations"]["OneDrive"] == "not found or not available to you"
    assert any(call.get("failed") for call in colleague["workiq_calls"])

    # A next link Work IQ can't follow ends the listing, and the result says it may be incomplete.
    routes[second_page] = listing(document("01NOTES", "Meeting notes.docx", "b!team"),
                                  next_link="https://contoso.example/elsewhere")
    stopped = await client_documents(lambda: FolderSession(routes), "Michael Scott")
    assert stopped["complete"] is False and "may be more" in stopped["locations"]["SharePoint"]
    with pytest.raises(ValueError):
        await client_documents(lambda: FolderSession(routes), "../Another Client")


async def test_inspect_document_resolves_a_onedrive_url_and_verifies_the_page(pdf_bytes):
    client = FakeClient([
        metadata({"id": "01ITEM", "name": "Overview.pdf", "parentReference": {"driveId": "b!drive"}}),
        metadata({"id": "01ITEM", "name": "Overview.pdf", "webUrl": "https://contoso-my.sharepoint.com/x",
                  "parentReference": {"driveId": "b!drive", "path": "/drive/root:/Client Files"}}),
        blob(pdf_bytes),
    ])
    evidence = await inspect_document_evidence(
        client, "renewal deadline", None,
        "https://contoso-my.sharepoint.com/personal/a/Documents/Client%20Files/Overview.pdf")
    assert evidence["verified_pages"] == [2]
    assert (evidence["drive_id"], evidence["item_id"]) == ("b!drive", "01ITEM")
    assert client.calls[1] == ("fetch", {"entityUrls": ["/drives/b!drive/items/01ITEM"]})
    assert client.calls[2] == ("fetch_blob", {"path": "/drives/b!drive/items/01ITEM/content"})


async def test_read_chart_renders_the_page_and_has_the_model_read_it(pdf_bytes):
    requests = []
    reading = {"chart_title": "Budget", "readings": [
        {"what": "High", "date": "Oct 14", "value": 24.7, "unit": "%", "how": "estimated from gridlines"}],
        "answer": "About 24.7% around October 14."}

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"output": [
            {"type": "reasoning", "summary": []},
            {"type": "message", "content": [{"type": "output_text", "text": "```json\n" + json.dumps(reading) + "\n```"}]},
        ]})

    class Credential:
        async def get_token(self, *scopes):
            assert scopes == ("https://ai.azure.com/.default",)
            return SimpleNamespace(token="agent-token")

    reader = ChartReader(Credential(), "https://contoso.services.ai.azure.com/api/projects/p", "gpt-test",
                         {"x-agent-foundry-call-id": "call-1"}, transport=httpx.MockTransport(respond))
    client = FakeClient([
        metadata({"id": "01ITEM", "name": "Overview.pdf", "webUrl": "https://contoso-my.sharepoint.com/x"}),
        blob(pdf_bytes),
    ])
    evidence = await read_chart_evidence(client, reader, 3, "the highest value and its date",
                                         "/drives/b!drive/items/01ITEM", None)
    assert (evidence["page"], evidence["page_count"]) == (3, 3)
    assert evidence["reading"] == reading
    assert "Budget appendix" in evidence["page_text"]
    assert (evidence["source"]["drive_id"], evidence["source"]["item_id"]) == ("b!drive", "01ITEM")
    assert client.calls[1] == ("fetch_blob", {"path": "/drives/b!drive/items/01ITEM/content"})
    [request] = requests
    assert str(request.url) == "https://contoso.services.ai.azure.com/api/projects/p/openai/v1/responses"
    assert request.headers["Authorization"] == "Bearer " + "agent-token"
    assert request.headers["x-agent-foundry-call-id"] == "call-1"
    body = json.loads(request.content)
    assert body["model"] == "gpt-test"
    prompt, image = body["input"][0]["content"]
    assert "the highest value and its date" in prompt["text"]
    assert base64.b64decode(image["image_url"].split(",", 1)[1])[:8] == b"\x89PNG\r\n\x1a\n"
    assert parse_reading("no json here") == {"answer": "no json here"}
    assert "can't rank them" in prompt["text"]


async def test_inspect_document_and_read_chart_share_one_download_in_a_turn(pdf_bytes):
    reading = {"readings": [], "answer": "About 24.7%."}
    reader = ChartReader(
        SimpleNamespace(get_token=AsyncMock(return_value=SimpleNamespace(token="t"))),
        "https://contoso.services.ai.azure.com/api/projects/p", "gpt-test",
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"output": [
            {"type": "message", "content": [{"type": "output_text", "text": json.dumps(reading)}]}]})),
    )
    client = FakeClient([
        metadata({"id": "01ITEM", "name": "Overview.pdf", "webUrl": "https://contoso-my.sharepoint.com/x"}),
        blob(pdf_bytes),
    ])
    documents: dict = {}
    path = "/drives/b!drive/items/01ITEM"
    evidence = await inspect_document_evidence(client, "Budget appendix", path, None, documents)
    assert evidence["verified_pages"] == [3]
    chart = await read_chart_evidence(client, reader, 3, "the high", path, None, documents)
    assert chart["reading"] == reading and chart["page"] == 3
    assert [name for name, _ in client.calls] == ["fetch", "fetch_blob"]
    # Each harness tool reports the Work IQ calls it made itself, for the X-ray.
    assert [call["tool"] for call in evidence["workiq_calls"]] == ["fetch", "fetch_blob"]
    assert all(isinstance(call["ms"], int) and call["ms"] >= 0 for call in evidence["workiq_calls"])
    assert evidence["reused_download"] is False
    assert chart["workiq_calls"] == [] and chart["reused_download"] is True
    assert chart["model_call"]["deployment"] == "gpt-test" and chart["model_call"]["ms"] >= 0


def test_a_chart_is_sent_to_the_model_at_its_own_resolution_and_a_logo_is_not(pdf_with_image):
    chart = render_page(pdf_with_image(400, 200), 1, figure=True)
    assert chart.figure is True
    assert abs(chart.width - 1600) <= 2 and abs(chart.height - 800) <= 2
    assert chart.png[:8] == b"\x89PNG\r\n\x1a\n"
    whole = render_page(pdf_with_image(400, 200), 1)
    assert whole.figure is False and (whole.width, whole.height) == (1224, 1584)
    logo = render_page(pdf_with_image(60, 30), 1, figure=True)
    assert logo.figure is False and (logo.width, logo.height) == (1224, 1584)
