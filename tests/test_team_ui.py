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

