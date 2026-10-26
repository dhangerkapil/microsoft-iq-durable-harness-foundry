"""The what-if and committee results reach the advisor in full, even when the lead answers with one line about them."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from unittest.mock import MagicMock

from agent_framework import AgentContext, AgentResponse, AgentResponseUpdate, Content, Message, ResponseStream
from agent_framework_foundry_hosting._responses import _OutputItemTracker
from azure.ai.agentserver.responses.aio import ResponseEventStream

from advisor_team.activity import ActivityRecorder
from advisor_team.execution import ExecutionTiming, ToolTimingRecorder
from advisor_team.full_results import ShowResultInFull, missing_from
from advisor_team.progress import SourceProgressOutput, SourceSearchProgress

MINUTES = ("## Investment committee: NVIDIA concentration\n\n### Minutes\n- CP-104 §3.1: a review within five business "
           "days (Foundry IQ).\n\n### The meeting\n**Risk officer:** Which policy version applies?")


def recorder_with(call_id: str, name: str) -> ToolTimingRecorder:
    recorder = ToolTimingRecorder()
    recorder.timings[call_id] = ExecutionTiming(call_id=call_id, name=name, started_at=datetime.now(UTC),
                                                duration_ms=1, failed=False)
    return recorder


def turn(result: str, answer: str, name: str = "convene_investment_committee"):
    async def updates():
        yield AgentResponseUpdate(role="assistant", contents=[Content.from_function_call("c1", name, arguments={})])
        yield AgentResponseUpdate(role="tool", contents=[Content.from_function_result("c1", result=result)])
        yield AgentResponseUpdate(role="assistant", contents=[Content.from_text(answer)], message_id="m1")

    return ResponseStream(updates(), finalizer=AgentResponse.from_updates)


async def run_lead(recorder: ToolTimingRecorder, original: ResponseStream,
                   activity: ActivityRecorder | None = None) -> tuple[list, AgentResponse, list[dict]]:
    """The lead's order: SourceProgressOutput outside ShowResultInFull; the host's tracker turns updates into events."""
    context = AgentContext(agent=MagicMock(), messages=[], stream=True)
    progress, guard = SourceProgressOutput(SourceSearchProgress(recorder)), ShowResultInFull(recorder, activity)

    async def innermost():
        context.result = original

    async def inner():
        await guard.process(context, innermost)

    await progress.process(context, inner)
    native = ResponseEventStream(response_id="resp_test")
    native.emit_created()
    native.emit_in_progress()
    tracker = _OutputItemTracker(native, frozenset())
    updates, events = [], []
    async with asyncio.timeout(5):
        async for update in context.result:
            updates.append(update)
            events.extend([dict(event) async for event in tracker.handle_update(update)])
    return updates, await context.result.get_final_response(), events


def test_an_answer_counts_as_showing_a_result_only_with_its_first_and_last_lines():
    assert missing_from("Investment committee team: Portfolio, Firm policy, Market, Risk officer.", MINUTES)
    reformatted = MINUTES.replace("## Investment committee", "**Investment committee").replace("**Risk officer:**",
                                                                                               "Risk officer:")
    assert not missing_from("Here is the committee.\n\n" + reformatted, MINUTES)
    assert not missing_from("anything", "   ")


async def test_a_one_line_answer_gets_the_committee_result_verbatim_in_the_stream_and_the_final_response():
    recorder = recorder_with("c1", "convene_investment_committee")
    updates, final, events = await run_lead(recorder, turn(MINUTES, "Investment committee team: Portfolio, Chair."))
    assert updates[-1].role == "assistant" and updates[-1].text.strip() == MINUTES
    assert updates[-1].message_id == "m1"
    assert final.text.startswith("Investment committee team") and final.text.endswith(MINUTES)
    assert any("Which policy version applies?" in str(event) for event in events)


async def test_an_answer_that_holds_the_result_passes_unchanged():
    recorder = recorder_with("c1", "run_what_if")
    result = "NVDA falls to 22.03%.\n\n**Computed in code** (what-if analyst); assumptions labelled."
    updates, final, _ = await run_lead(recorder, turn(result, "The scenario:\n\n" + result, name="run_what_if"))
    assert len(updates) == 3
    assert final.text == "The scenario:\n\n" + result


async def test_other_tools_results_are_never_added():
    recorder = recorder_with("c1", "ask_firm_policy")
    updates, final, _ = await run_lead(recorder, turn(MINUTES, "CP-104 applies.", name="ask_firm_policy"))
    assert len(updates) == 3 and final.text == "CP-104 applies."


def test_a_non_streamed_answer_gets_the_missing_result_too():
    recorder = recorder_with("c1", "convene_investment_committee")
    response = AgentResponse(messages=[
        Message(role="assistant", contents=[Content.from_function_call("c1", "convene_investment_committee",
                                                                       arguments={})]),
        Message(role="tool", contents=[Content.from_function_result("c1", result=MINUTES)]),
        Message(role="assistant", contents=[Content.from_text("Investment committee team: Portfolio, Chair.")]),
    ])
    ShowResultInFull(recorder).complete(response)
    assert response.messages[-1].text.strip() == MINUTES


BRIEF = ("## Client brief: Michael Scott\n\n- NVDA is 26.1% against a 25% limit (Fabric IQ).\n\n## Sources\n"
         "- Work IQ: [Meeting notes 2026-10-16 Q4 review](https://contoso.sharepoint.com/Client%20Files/notes.docx)"
         "\n\n<!-- verdict: cleared; attempts: 1 -->")


async def test_a_brief_answered_with_one_line_is_shown_exactly_as_returned_below_it():
    # The lead once changed a link while retyping the brief; it now answers with one line and the brief is added.
    line = "Prepared by the team: client context, portfolio, firm policy, market, writer and reviewer."
    updates, final, _ = await run_lead(recorder_with("c1", "prepare_client_brief"),
                                       turn(BRIEF, line, name="prepare_client_brief"))
    assert updates[-1].text.strip() == BRIEF
    assert final.text.startswith(line) and final.text.endswith(BRIEF)
    _, copied, _ = await run_lead(recorder_with("c1", "prepare_client_brief"),
                                  turn(BRIEF, line + "\n\n" + BRIEF, name="prepare_client_brief"))
    assert copied.text.count("## Client brief") == 1


EMAIL = {"title": "Change of plans: closing moved to December 15", "kind": "email",
         "url": "https://outlook.office365.com/owa/?ItemID=AAMkADBjMGU3YTUxLTJmNGItNGQxYS05ZTNj%3D&exvsurl=1",
         "entity_url": "/me/messages/AAMkADBjMGU3YTUxLTJmNGItNGQxYS05ZTNj="}


def cited(*references: dict) -> ActivityRecorder:
    activity = ActivityRecorder()
    activity.add_references(list(references))
    return activity


async def test_the_cited_items_are_attached_in_code_exactly_as_recorded():
    # A model retyping this block once looped on an Outlook item ID for 15,000 characters; code adds it instead.
    from advisor_team.ui.references import references_from_text

    recorder = recorder_with("c1", "ask_client_context")
    updates, final, _ = await run_lead(recorder, turn("Report", "The closing moved (email, October 17).",
                                                      name="ask_client_context"), cited(EMAIL))
    assert updates[-1].message_id == "m1" and final.text.count("```workiq_references") == 1
    [reference] = references_from_text(final.text)
    assert reference.url == EMAIL["url"] and reference.title == EMAIL["title"]


async def test_an_answer_with_its_own_block_or_no_citations_gets_nothing_added():
    recorder = recorder_with("c1", "run_durable_meeting_pack")
    own = 'Pack done.\n```workiq_references\n[{"title": "Notes", "url": "https://contoso.sharepoint.com/n"}]\n```'
    updates, final, _ = await run_lead(recorder, turn("pack", own, name="run_durable_meeting_pack"), cited(EMAIL))
    assert len(updates) == 3 and final.text.count("```workiq_references") == 1
    updates, final, _ = await run_lead(recorder, turn("pack", "No sources.", name="run_durable_meeting_pack"), cited())
    assert len(updates) == 3 and final.text == "No sources."


def test_a_non_streamed_answer_gets_the_cited_items_too():
    response = AgentResponse(messages=[Message(role="assistant", contents=[Content.from_text("Answer.")])])
    ShowResultInFull(ToolTimingRecorder(), cited(EMAIL)).complete(response)
    assert response.messages[-1].text.strip().startswith("```workiq_references") and EMAIL["url"] in response.text


FILE = {"title": "IPS v2 DRAFT.docx", "kind": "file", "drive_id": "b!c3ludGhldGlj",
        "url": "https://contoso.sharepoint.com/_layouts/15/Doc.aspx?sourcedoc=%7B1A2B%7D&file=IPS%20v2%20DRAFT.docx",
        "entity_url": "/drives/b!c3ludGhldGlj/items/01SYNTHETIC", "item_id": "01SYNTHETIC"}


def test_references_a_specialist_writes_into_its_answer_still_become_the_advisors_sources():
    # A client-context report once left its references field empty and listed every item as a JSON line under
    # "### References" in its answer: the brief reached the advisor with no sources. Code moves them to the field.
    from advisor_team.contracts import parse_report
    from advisor_team.ui.references import references_from_text

    body = "## Next meeting\n- Thursday, 10:00-11:00 AM EDT (calendar event; Work IQ)\n\n**Client documents:** IPS v2 DRAFT"
    answer = f"{body}\n\n### References\n- {json.dumps(EMAIL)}\n- {json.dumps(FILE)}\n"
    report = parse_report(json.dumps({"status": "ok", "answer": answer, "references": []}))
    assert [r.title for r in report.references] == [EMAIL["title"], FILE["title"]]
    assert report.answer == body  # the list and its heading leave the answer; everything else stays

    activity = ActivityRecorder()
    activity.add_references([r.model_dump() for r in report.references], lane="client-context")
    response = AgentResponse(messages=[Message(role="assistant", contents=[Content.from_text("Brief.")])])
    ShowResultInFull(ToolTimingRecorder(), activity).complete(response)
    assert [r.url for r in references_from_text(response.text)] == [EMAIL["url"], FILE["url"]]


def test_reference_objects_in_a_json_block_or_one_line_array_are_recovered_and_nothing_else_moves():
    from advisor_team.contracts import parse_report

    fenced = f"Facts.\n\n**Sources**\n```json\n{json.dumps([EMAIL, FILE], indent=2)}\n```"
    report = parse_report(json.dumps({"answer": fenced, "references": [EMAIL]}))
    assert len(report.references) == 3 and report.answer == "Facts."  # the recorder drops the repeated email
    report = parse_report(json.dumps({"answer": f"Facts.\n{json.dumps([FILE])}"}))
    assert [r.item_id for r in report.references] == ["01SYNTHETIC"] and report.answer == "Facts."

    # JSON that isn't a reference object, links and a References section with prose all stay as written.
    kept = ('Facts.\n- {"label": "NVDA weight", "value": "26.1%"}\n\n### References\n'
            "- [IPS v2 DRAFT.docx](https://contoso.sharepoint.com/ips) (SharePoint)\n- [1] the calendar event")
    report = parse_report(json.dumps({"answer": kept}))
    assert report.references == [] and report.answer == kept

