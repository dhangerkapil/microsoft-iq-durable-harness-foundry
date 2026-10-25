"""A specialist's mis-copied item ID is repaired where the specialists' full tool output is, before the app sees it.

The conversation keeps only the first 4,000 characters of each specialist call, so the app can't repair a team source
safely: sibling Outlook IDs differ by one character, and the cited item can sit past the cut while its sibling doesn't.
"""

import json

import pytest
from agent_framework import Content, FunctionInvocationContext, FunctionTool

from advisor_team.activity import MAX_TEXT, ActivityRecorder, LaneRecorder
from advisor_team.ui.grounding import Evidence
from advisor_team.ui.models import Reference

PREFIX = "AAMkADBjMGU3YTUxLTJmNGItNGQxYS05ZTNjLTZiNWE0ZjNlMmQxYwBGAAAAAAD_c3ludGhldGljRm9sZGVycwDU3ludGhldGljU3RvcmUxUAAAAAAE"
CITED = PREFIX + "MAADU3ludGhldGljU3RvcmUxUAABZGhndAAA="
SIBLING = PREFIX + "MAADU3ludGhldGljU3RvcmUxUAABZGhneAAA="


def fetch_output() -> str:
    """A Graph read through Work IQ: the sibling first, then enough mail that the cited email is past the cut."""
    filler = [{"id": f"filler-{n}", "subject": f"Newsletter {n}", "bodyPreview": "x" * 300} for n in range(14)]
    value = [{"id": SIBLING, "subject": "Agenda for tomorrow's review", "receivedDateTime": "2026-10-15T13:12:00Z"},
             *filler,
             {"id": CITED, "subject": "Change of plans: closing moved to December 15",
              "receivedDateTime": "2026-10-17T14:05:00Z"}]
    return json.dumps({"value": value})


async def record_call(recorder: ActivityRecorder, output: str) -> None:
    function = FunctionTool(name="workiq___fetch", func=lambda: "unused")
    context = FunctionInvocationContext(function=function, arguments={}, metadata={"call_id": "c1"})

    async def execute() -> None:
        context.result = [Content.from_text(output)]

    await LaneRecorder("client-context").process(context, execute)


def cite(identifier: str, url: str | None = "https://outlook.office365.com/owa/?ItemID=x",
         title: str = "Change of plans: closing moved to December 15") -> dict:
    return {"title": title, "url": url, "kind": "email",
            "entity_url": f"/me/messages/{identifier}", "drive_id": None, "item_id": None, "page": None,
            "slide": None, "location_verified": False}


async def test_a_mis_copied_id_is_repaired_to_the_cited_item_not_its_sibling():
    recorder = ActivityRecorder()
    output = fetch_output()
    await record_call(recorder, output)
    # The call record (what the app gets) is cut before the cited email; its sibling is inside the cut.
    clipped = recorder.turn.calls[0].output
    assert len(output) > MAX_TEXT and CITED not in clipped and SIBLING in clipped
    for position in (12, 60, 101, 140):
        replacement = "Q" if CITED[position] != "Q" else "R"
        miscopied = CITED[:position] + replacement + CITED[position + 1:]
        recorder.turn.references.clear()
        # A title the Graph read doesn't have, so the ID match alone decides.
        recorder.add_references([cite(miscopied, title="Closing update")], lane="client-context")
        assert recorder.turn.references[0]["entity_url"] == f"/me/messages/{CITED}", position
    dropped = CITED[:70] + CITED[71:]
    recorder.turn.references.clear()
    recorder.add_references([cite(dropped, url=None, title="Closing update")], lane="client-context")
    assert recorder.turn.references[0]["entity_url"] == f"/me/messages/{CITED}"
    assert recorder.turn.references[0]["url"] is None
    repaired = [s for s in recorder.turn.steps if s.step == "references" and s.status == "repaired"]
    assert repaired and repaired[-1].lane == "client-context"
    # The step says where the IDs differed (the end of a long ID is usually the same).
    first = next(n for n, (old, new) in enumerate(zip(dropped, CITED)) if old != new) + 1
    assert f"from character {first}," in repaired[-1].detail


async def test_an_item_the_tools_returned_or_nothing_matches_is_kept_as_cited():
    recorder = ActivityRecorder()
    await record_call(recorder, fetch_output())
    for reference in (cite(SIBLING, title="Agenda for tomorrow's review"), cite(CITED),
                      cite("AAMkAGUNRELATEDxxxxxxxxxxxxxxxxxxxxxxxxxx=", title="Lunch on Friday")):
        recorder.turn.references.clear()
        recorder.add_references([reference])
        assert recorder.turn.references == [reference]
    assert not [s for s in recorder.turn.steps if s.step == "references"]


async def test_an_unrecognizable_id_is_found_by_its_exact_title_in_the_graph_reads():
    # The specialist garbled the ID past matching, but the Graph read returned one email with the cited title.
    recorder = ActivityRecorder()
    await record_call(recorder, fetch_output())
    recorder.add_references([cite("AAMkAGUNRELATEDxxxxxxxxxxxxxxxxxxxxxxxxxx=")])
    assert recorder.turn.references[0]["entity_url"] == f"/me/messages/{CITED}"


async def test_a_failed_call_or_no_evidence_repairs_nothing():
    recorder = ActivityRecorder()
    miscopied = CITED[:60] + "Q" + CITED[61:]
    recorder.add_references([cite(miscopied)])
    assert recorder.turn.references[0]["entity_url"] == f"/me/messages/{miscopied}"
    # A failed call, or an error message, can echo the ID it was asked for: neither is evidence.
    await record_call(recorder, json.dumps({"isError": True, "content": f"Not found: /me/messages/{CITED}"}))
    await record_call(recorder, f"Error: Function failed. /me/messages/{CITED} was not found.")
    assert recorder.turn.evidence is None and len(recorder.turn.calls) == 2


async def test_a_call_that_raised_is_recorded_with_its_error_not_its_unawaited_handler():
    recorder = ActivityRecorder()
    function = FunctionTool(name="workiq___fetch", func=lambda: "unused")
    context = FunctionInvocationContext(function=function, arguments={}, metadata={"call_id": "c1"})

    async def handler():
        return "never awaited"

    async def execute() -> None:
        context.result = handler()  # what the framework holds until the tool returns
        context.result.close()
        raise RuntimeError("Error resolving connection '/subscriptions/0000/connections/work-iq-mcp-x'.")

    with pytest.raises(RuntimeError):
        await LaneRecorder("client-context").process(context, execute)
    call = recorder.turn.calls[0]
    assert call.failed and call.output.startswith("Error: RuntimeError: Error resolving connection")
    assert "coroutine" not in call.output and recorder.turn.evidence is None


def test_the_app_leaves_a_team_source_it_cannot_find_as_cited():
    # In the conversation, the team's records hold the clipped calls and the references the specialists wrote. A
    # source Graph can't find is left alone (Use Open original), never matched to the sibling in the clipped copy.
    miscopied = CITED[:60] + "Q" + CITED[61:]
    records = {"version": 1, "steps": [], "references": [cite(miscopied)], "calls": [
        {"lane": "client-context", "tool": "workiq___fetch", "call_id": "c1", "failed": False, "arguments": "{}",
         "started_at": "2026-10-22T06:57:05.874+00:00", "duration_ms": 1, "parent": "lead-1",
         "output": fetch_output()[:MAX_TEXT] + " …"}]}
    output = json.dumps({"__team_activity__": records, "output": "Work IQ report."})
    evidence = Evidence.from_items([{"type": "function_call_output", "call_id": "lead-1", "output": output}])
    reference = Reference.model_validate({key: value for key, value in cite(miscopied).items()
                                          if key in ("title", "url", "kind", "entity_url")})
    assert evidence.repair(reference) is reference
