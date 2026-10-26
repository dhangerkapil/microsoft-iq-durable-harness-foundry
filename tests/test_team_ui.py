"""The app's side of the team: specialists' calls become nested X-ray entries in their lanes, with the verdict."""

from __future__ import annotations

import json
from uuid import uuid4

from advisor_team.activity import TEAM_KEY, unwrap_team_output
from advisor_team.execution import TIMING_KEY
from advisor_team.ui.events import ResponseEvents
from advisor_team.ui.models import RunRecord

ENVELOPE = {
    "version": 1,
    "calls": [
        {"lane": "firm-policy", "tool": "search_firm_knowledge", "call_id": "c1", "started_at": "2026-10-21T06:45:15.551+00:00",
         "duration_ms": 2500, "failed": False, "arguments": '{"question": "wires"}', "output": "[]"},
        {"lane": "market", "tool": "search_public_web", "call_id": "c2", "started_at": "2026-10-21T06:45:16.000+00:00",
         "duration_ms": 1800, "failed": True, "arguments": "{}", "output": "WEB_UNAVAILABLE"},
    ],
    "steps": [{"lane": "workflow", "step": "writer", "status": "finished", "at": "2026-10-21T06:46:00+00:00"}],
    "verdict": {"cleared": False, "attempts": 2, "findings": [{"sentence": "Sell now.", "issue": "advice", "rule": "COM-310 §2"}]},
}


def timing(call_id: str, name: str) -> dict:
    return {"version": 1, "call_id": call_id, "name": name, "started_at": "2026-10-21T06:45:15+00:00",
            "duration_ms": 9000, "failed": False}


def events() -> ResponseEvents:
    record = RunRecord(id=str(uuid4()), session_id=str(uuid4()), prompt_preview="q", started_at="2026-10-21T06:45:00+00:00")
    return ResponseEvents(record, "https://example.services.ai.azure.com/api/projects/p")


def run_call(output: object) -> ResponseEvents:
    stream = events()
    stream.start_call({"call_id": "lead1", "name": "ask_firm_policy"})
    stream.finish_arguments("lead1", '{"task": "wires"}')
    stream.finish_call({"call_id": "lead1", "output": json.dumps(output)})
    return stream


def test_unwrap_team_output_keeps_other_outputs():
    assert unwrap_team_output("plain") == ("plain", None)
    assert unwrap_team_output({TEAM_KEY: ENVELOPE, "output": "x"}) == ("x", ENVELOPE)


def test_team_outside_timing_becomes_nested_lanes_and_keeps_timing():
    stream = run_call({TEAM_KEY: ENVELOPE, "output": {TIMING_KEY: timing("lead1", "ask_firm_policy"), "output": "answer"}})
    lead = stream.calls["lead1"]
    assert lead.duration_ms == 9000 and lead.status == "completed"
    nested = [a for a in stream.record.activity if a.lane]
    assert [a.lane for a in nested] == ["firm-policy", "market"]
    assert nested[0].parent_call_id == "lead1" and nested[0].lane_title == "Firm policy · Foundry IQ"
    assert nested[1].status == "failed"
    assert stream.record.team_verdict["cleared"] is False
    assert stream.record.team_steps[0]["step"] == "writer"


def test_team_inside_timing_works_too():
    stream = run_call({TIMING_KEY: timing("lead1", "ask_firm_policy"), "output": {TEAM_KEY: ENVELOPE, "output": "answer"}})
    assert stream.calls["lead1"].duration_ms == 9000
    assert len([a for a in stream.record.activity if a.lane]) == 2


def test_a_specialists_consent_link_becomes_one_banner_with_the_full_link():
    # Fabric IQ and Work IQ Chat run inside specialists; their consent links reach the app only in the team's
    # activity record (a JSON list of strings), and the specialist's model would retell them, garbled.
    fabric = "https://logic-apis-eastus2.consent.azure-apim.net/login?data=" + "eyJ" + "A" * 2500
    chat = "https://logic-apis-eastus2.consent.azure-apim.net/login?data=" + "eyJ" + "B" * 2500

    def call(call_id: str, lane: str, tool: str, output: dict) -> dict:
        return {"lane": lane, "tool": tool, "call_id": call_id, "started_at": "2026-10-21T06:45:15+00:00",
                "duration_ms": 900, "failed": True, "arguments": "{}", "output": json.dumps([json.dumps(output)])}

    needs_fabric = {"status": "FABRIC_CONSENT_REQUIRED", "consent_link": fabric, "instruction": "..."}
    envelope = {"version": 1, "calls": [
        call("f1", "portfolio", "query_portfolio_data", needs_fabric),
        call("f2", "portfolio", "query_portfolio_data", needs_fabric),
        call("a1", "client-context", "delegate_to_work_iq", {"status": "WORKIQ_CHAT_CONSENT_REQUIRED", "consent_link": chat}),
        call("w1", "market", "search_public_web", needs_fabric),
        call("e1", "portfolio", "query_portfolio_data", {"status": "FABRIC_CONSENT_REQUIRED",
                                                         "consent_link": "https://evil.example/login"}),
    ]}
    stream = events()
    stream.start_call({"call_id": "lead1", "name": "prepare_client_brief"})
    stream.finish_arguments("lead1", "{}")
    result = stream.finish_call({"call_id": "lead1", "output": json.dumps(
        {TEAM_KEY: envelope, "output": {TIMING_KEY: timing("lead1", "prepare_client_brief"), "output": "brief"}})})
    banners = [event for event in result if getattr(event, "name", None) == "workiq.consent"]
    assert [(event.value["server"], event.value["url"]) for event in banners] == [
        ("fabric_portfolio", fabric), ("workiq_chat_a2a", chat)]
    assert len([a for a in stream.record.activity if a.lane]) == 5


def test_specialists_input_and_output_reach_the_browser_but_never_the_stored_run():
    # Foundry stores only the lead's calls, so the app can't load a specialist's payload later: it streams it once.
    sign_in = "https://login.microsoftonline.com/t/oauth2/v2.0/authorize?code=secret-code"
    answer = "As of 10/19/2026, the total portfolio value is $2,926,762.55."
    service_error = ('Error: ToolExecutionException: {"application/vnd.ms-workiq.retrieval": {"markdown": "x", '
                     '"retrievalHits": [], "resultCount": 0, "stoppedReason": "error", "error": "The retrieval '
                     'request could not be completed.", "errorCode": "InternalError"}, "requestId": "r-1"}')

    def call(call_id: str, lane: str, tool: str, arguments: dict, output: str, failed: bool = False) -> dict:
        return {"lane": lane, "tool": tool, "call_id": call_id, "started_at": "2026-10-21T06:45:15+00:00",
                "duration_ms": 900, "failed": failed, "arguments": json.dumps(arguments), "output": output}

    trim = {"current_weight_pct": 26.1, "limit_pct": 25.0, "above_limit": True, "trim_to_limit_usd": 32167.14,
            "realized_gain_on_trim_usd": 25633.17, "trim_exceeds_tax_budget": True}
    envelope = {"version": 1, "calls": [
        call("p1", "portfolio", "query_portfolio_data", {"question": "Michael Scott: the total value today"},
             json.dumps({"answer": answer, "link": sign_in})),
        call("w1", "client-context", "workiq___retrieve",
             {"query": "Michael Scott meeting topics", "capabilities": [{"name": "Meetings"}]}, service_error, True),
        call("r1", "writer", "rebalancing_check", {"annual_tax_budget_usd": 20000.0}, json.dumps(trim)),
        call("r2", "writer", "rebalancing_check", {"annual_tax_budget_usd": 15000.0}, json.dumps(trim)),
        # The agent keeps the first 4,000 characters of a long result: too short to read back, so named by kind.
        call("m1", "market", "search_public_web", {"query": "NVDA", "kind": "finance"}, '{"results": [' + "x" * 4000 + " …"),
        call("s1", "market", "load_skill", {"skill_name": "public-web-research"}, "Skill text"),
    ]}
    stream = events()
    stream.start_call({"call_id": "lead1", "name": "prepare_client_brief"})
    stream.finish_arguments("lead1", "{}")
    result = stream.finish_call({"call_id": "lead1", "output": json.dumps(
        {TEAM_KEY: envelope, "output": {TIMING_KEY: timing("lead1", "prepare_client_brief"), "output": "brief"}})})

    sent = [event for event in result if getattr(event, "name", None) == "workiq.lane_payloads"]
    assert len(sent) == 1 and sent[0].value["run_id"] == stream.record.id
    calls = sent[0].value["calls"]
    assert set(calls) == {"lead1/portfolio/p1", "lead1/client-context/w1", "lead1/writer/r1", "lead1/writer/r2",
                          "lead1/market/m1"}  # tools only: a skill's text isn't a payload
    portfolio = calls["lead1/portfolio/p1"]
    assert '"question": "Michael Scott: the total value today"' in portfolio["input"]
    assert answer in portfolio["output"] and "secret-code" not in portfolio["output"]
    assert "[redacted authentication or temporary URL]" in portfolio["output"]
    assert calls["lead1/market/m1"]["output_truncated"] is True
    assert calls["lead1/portfolio/p1"]["output_truncated"] is False

    lines = {a.call_id.rsplit("/", 1)[-1]: a.detail for a in stream.record.activity if a.lane}
    assert lines["p1"] == ("Fabric IQ: the Portfolio Analyst data agent answered from the portfolio lakehouse, "
                           'as of 10/19/2026, as you · "Michael Scott: the total value today"')
    assert lines["w1"] == ('Failed (InternalError): The retrieval request could not be completed. · '
                           '"Michael Scott meeting topics"')
    assert lines["r1"].endswith("over the $20,000 tax budget") and lines["r2"].endswith("over the $15,000 tax budget")
    assert lines["m1"] == 'Web IQ finance: public results, outside the Microsoft 365 boundary · "NVDA"'
    assert next(a for a in stream.record.activity if a.call_id == "lead1/market/s1").skill == "public-web-research"

    # The stored run keeps the X-ray lines, never a payload.
    stored = stream.record.model_dump_json()
    assert answer not in stored and "secret-code" not in stored and '"input"' not in stored


def test_a_specialists_result_reads_as_json_not_as_escaped_text():
    from advisor_team.ui.events import lane_payload

    # The agent records a tool's result as its text items, a JSON list of JSON strings; a passage holds JSON too.
    passage = json.dumps([{"ref_id": 0, "content": "CP-104 §3.1 A position above the limit"}])
    recorded = json.dumps([json.dumps({"source": "Foundry IQ", "passages": [{"text": passage}], "token": "abc"})])
    shown = lane_payload(json.dumps({"question": "limits"}), recorded)
    assert '"ref_id": 0' in shown["output"] and '\\"' not in shown["output"]
    assert '"passages": [' in shown["output"]  # only the text items' list is unwrapped; the tool's own lists stay
    assert "CP-104 §3.1" in shown["output"]
    assert '"token": "[redacted]"' in shown["output"]  # redaction by field name reaches inside the text item
    assert shown["output_truncated"] is False and '"question": "limits"' in shown["input"]

    # Cut at 4,000 characters, a result is no longer JSON: it still reads one level down, emoji included.
    item = json.dumps({"results": [{"subject": "Closing moved 🏠", "body": "x" * 5000}]}, ensure_ascii=False)
    shown = lane_payload("{}", json.dumps([item])[:4000] + " …")
    assert shown["output"].startswith('{"results": [{"subject": "Closing moved 🏠", "body": "xxx')
    assert shown["output"].endswith(" …") and shown["output_truncated"] is True

    # A cut between an emoji's two escaped halves leaves no half behind to break the stream's UTF-8.
    shown = lane_payload("{}", '["{\\"t\\": \\"' + "x" * 3990 + "\\ud83d" + " …")
    assert shown["output"].endswith("x\ufffd …")
    shown["output"].encode("utf-8")

