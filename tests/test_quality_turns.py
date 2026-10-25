"""Saved turns as the judges read them: what the lead read from a specialist, and the answer the lead wrote."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "evals"))
import foundry_quality as quality  # noqa: E402

REPORT = "Work IQ report: the Q4 review was on October 16; Michael asked to lower the tax budget to $15,000."


def delegation_output(timing_outside: bool) -> str:
    records = {"version": 1, "steps": [], "references": [{"title": "Q4 portfolio review"}], "calls": [
        {"lane": "client-context", "tool": "load_skill", "arguments": "{}", "output": "x" * 20_000},
        {"lane": "client-context", "tool": "workiq___fetch", "arguments": '{"paths": ["/me/events"]}',
         "output": '{"subject": "Q4 portfolio review - Michael Scott"}' + " " * 30_000},
    ]}
    timing = {"version": 1, "call_id": "c1", "name": "ask_client_context", "started_at": "2026-10-22T06:57:00+00:00",
              "duration_ms": 1, "failed": False}
    if timing_outside:
        return json.dumps({quality.TIMING_KEY: timing, "output": {quality.TEAM_KEY: records, "output": REPORT}})
    return json.dumps({quality.TEAM_KEY: records, "output": json.dumps({quality.TIMING_KEY: timing, "output": REPORT})})


def test_a_delegation_shows_the_report_first_then_the_specialists_calls():
    for timing_outside in (False, True):
        text = quality.tool_result_text(delegation_output(timing_outside))
        assert text.startswith(REPORT)
        assert "client-context: workiq___fetch" in text and "Q4 portfolio review - Michael Scott" in text
        assert "load_skill" not in text and quality.TEAM_KEY not in text


def test_the_appended_sources_block_is_not_judged_as_the_agents_text():
    references = [{"title": "Q4 portfolio review - Michael Scott", "url": "https://outlook.office365.com/owa/?ItemID=x"},
                  {"title": "IPS v2 DRAFT", "url": "https://contoso.sharepoint.com/ips.docx"}]
    answer = "The review was on October 16.\n\n```workiq_references\n" + json.dumps(references) + "\n```"
    turn = quality.turn_item("t", {"advisor_prompt": "What did Michael say?", "output": [
        {"type": "function_call", "call_id": "c1", "name": "ask_client_context", "arguments": "{}"},
        {"type": "function_call_output", "call_id": "c1", "output": delegation_output(False)},
        {"type": "message", "content": [{"type": "output_text", "text": answer}]},
    ]}, "instructions", [])
    assert turn["response"][-1]["content"][0]["text"] == "The review was on October 16."
    assert turn["response"][1]["content"][0]["tool_result"].startswith(REPORT)
