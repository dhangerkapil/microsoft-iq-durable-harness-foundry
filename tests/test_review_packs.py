"""W3, review packs on the hosted agent: durable steps in the state store, one pack per request, draft approvals bound to
the exact text, and the crash test (fakes, in memory)."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from advisor_team import advisor
from advisor_team.activity import ActivityRecorder
from advisor_team.advisor import FaultSwitch, InMemoryItemStore
from advisor_team.contracts import ReviewVerdict, SpecialistReport
from advisor_team.durable import digest
from advisor_team.packs_protocol import command_text, view_in, view_text
from advisor_team.workflows import review_packs
from advisor_team.workflows.review_packs import (
    ReviewPacks,
    ReviewPacksGate,
    clients_from,
    command_in,
    draft_hash,
    summarize,
)


class Spec:
    def __init__(self, report: SpecialistReport) -> None:
        self.r = report

    async def report(self, task: str, session=None) -> SpecialistReport:
        return self.r


class Reply:
    def __init__(self, text: str) -> None:
        self.text, self.value = text, None


class Writer:
    def __init__(self, briefs: list[str] | None = None) -> None:
        self.briefs = briefs if briefs is not None else []

    async def run(self, prompt, **_):
        if "follow-up email" in prompt:
            return Reply("Subject: Quarterly review next week\nBody: Could we meet next week for your review?")
        if "Write the meeting brief for" in prompt:
            self.briefs.append(prompt.split("Write the meeting brief for ")[1].split(" (")[0].split(".")[0])
        return Reply("## Snapshot\nAll good.\n## Sources\n- **Fabric IQ:** Oct 19")


class Reviewer:
    async def run(self, prompt, **_):
        return Reply(ReviewVerdict(cleared=True).model_dump_json())


class Kit:
    def __init__(self, writer: Writer | None = None, faults=None) -> None:
        report = SpecialistReport(answer="ok")
        self.team = {k: Spec(report) for k in ("client_context", "portfolio", "firm_policy", "market")}
        self.writer, self.reviewer, self.recorder = writer or Writer(), Reviewer(), ActivityRecorder()
        self.faults = faults


class Stop(BaseException):
    """What os._exit does to the process: nothing after it runs."""


def stop(code: int) -> None:
    raise Stop(code)


def stores():
    durable, memory = InMemoryItemStore("durable"), InMemoryItemStore("memory")

    async def durable_factory():
        return durable

    async def memory_factory():
        return memory

    return durable, durable_factory, memory, memory_factory


def runner(durable_factory, kit=None, **kwargs) -> ReviewPacks:
    return ReviewPacks(durable_factory, kit or Kit(), exit_process=stop, **kwargs)


async def approve_all(packs: ReviewPacks, view: dict, approve: bool = True) -> dict:
    while view["status"] == "working":
        view = await packs.next(view["run"])
    while view["status"] == "waiting" and view["cards"]:
        card = view["cards"][0]
        answer = ({"approve": approve, "sha256": card["sha256"] if approve else ""} if card["kind"] == "draft"
                  else {"approve": True})
        view = await packs.answer(view["run"], card["call_id"], answer)
        while view["status"] == "working":
            view = await packs.next(view["run"])
    return view


@pytest.mark.asyncio
async def test_confirm_then_one_pack_per_step_then_each_draft_one_at_a_time(monkeypatch):
    monkeypatch.delenv("DRAFT_MODE", raising=False)
    _, factory, *_ = stores()
    packs = runner(factory)
    view = await packs.start("Prepare review packs for my three clients")
    assert view["status"] == "waiting" and view["cards"][0]["kind"] == "clients"
    assert view["cards"][0]["clients"] == ["Michael Scott", "Jan Levinson", "Dwight Schrute"]
    run = view["run"]

    view = await packs.answer(run, view["cards"][0]["call_id"], {"approve": True})
    assert view["status"] == "working" and view["progress"] == {"done": 0, "total": 3, "next": "Michael Scott"}
    view = await packs.next(run)
    assert view["status"] == "working" and view["progress"]["done"] == 1 and view["progress"]["next"] == "Jan Levinson"
    view = await packs.next(run)
    view = await packs.next(run)
    decisions = []
    for _ in range(3):
        assert view["status"] == "waiting" and view["cards"][0]["kind"] == "draft"
        draft = view["cards"][0]
        assert draft["sha256"] == draft_hash(draft["to"], draft["subject"], draft["body"])
        decisions.append(draft["client"])
        view = await packs.answer(run, draft["call_id"], {"approve": True, "sha256": draft["sha256"]})
    assert decisions == ["Michael Scott", "Jan Levinson", "Dwight Schrute"]
    assert view["status"] == "completed" and view["cards"] == []
    text = view["text"]
    assert "## Review packs: 3 clients" in text and "### Durability timeline" in text
    assert "Michael Scott: previewed" in text and "Dwight Schrute: previewed" in text
    assert "degraded: no Microsoft 365 history" in text  # the fakes cite no Microsoft 365 items
    assert "restored" not in text  # one process did everything


@pytest.mark.asyncio
async def test_an_approval_for_other_text_or_without_the_hash_is_refused():
    _, factory, *_ = stores()
    packs = runner(factory)
    view = await packs.start("packs")
    view = await packs.answer(view["run"], view["cards"][0]["call_id"], {"approve": True, "clients": ["Michael Scott"]})
    view = await packs.next(view["run"])
    card = view["cards"][0]
    view = await packs.answer(view["run"], card["call_id"], {"approve": True, "sha256": "0" * 64})
    assert view["status"] == "completed" and "Michael Scott: approval refused: the text changed" in view["text"]

    view = await packs.start("packs")
    view = await packs.answer(view["run"], view["cards"][0]["call_id"], {"approve": True, "clients": ["Jan Levinson"]})
    view = await packs.next(view["run"])
    view = await packs.answer(view["run"], view["cards"][0]["call_id"], {"approve": True})
    assert "Jan Levinson: approval refused: the text changed" in view["text"]


@pytest.mark.asyncio
async def test_only_the_pending_card_can_be_answered_and_only_once():
    _, factory, *_ = stores()
    packs = runner(factory)
    view = await packs.start("packs for Michael Scott")
    run, card = view["run"], view["cards"][0]
    stale = await packs.answer(run, "someone-else", {"approve": True})
    assert stale["error"] and stale["cards"][0]["call_id"] == card["call_id"]
    view = await packs.answer(run, card["call_id"], {"approve": True})
    again = await packs.answer(run, card["call_id"], {"approve": False})
    assert again["error"] and again["status"] == "working"


@pytest.mark.asyncio
async def test_cancel_prepares_nothing():
    _, factory, *_ = stores()
    packs = runner(factory)
    view = await packs.start("packs")
    view = await packs.answer(view["run"], view["cards"][0]["call_id"], {"approve": False})
    assert view["status"] == "completed" and "cancelled" in view["text"]
    assert (await packs.next(view["run"]))["status"] == "completed"


@pytest.mark.asyncio
async def test_the_user_can_send_the_client_list_and_a_bad_list_is_refused():
    _, factory, *_ = stores()
    packs = runner(factory)
    view = await packs.start("packs")
    bad = await packs.answer(view["run"], view["cards"][0]["call_id"],
                             {"approve": True, "clients": [f"Client {i}" for i in range(7)]})
    assert bad["error"] and bad["status"] == "waiting"
    view = await packs.answer(view["run"], view["cards"][0]["call_id"],
                              {"approve": True, "clients": ["Jan Levinson", "Dwight Schrute"]})
    assert view["progress"]["total"] == 2
    view = await approve_all(packs, view)
    assert "## Review packs: 2 clients" in view["text"]


@pytest.mark.asyncio
async def test_crash_test_stops_once_before_the_last_pack_and_a_new_process_redoes_nothing(monkeypatch):
    durable, factory, _, memory_factory = stores()
    writer = Writer()
    kit = Kit(writer, FaultSwitch(memory_factory))
    first = runner(factory, kit)
    view = await first.start("Review packs for my clients, with the crash test")
    assert "Crash test armed" in view["cards"][0]["note"]
    run = view["run"]
    view = await first.answer(run, view["cards"][0]["call_id"], {"approve": True})
    view = await first.next(run)
    view = await first.next(run)
    with pytest.raises(Stop):
        await first.next(run)
    assert writer.briefs == ["Michael Scott", "Jan Levinson"]
    assert f"packs/{run}/crash" in durable.items

    # A new process: a fresh runner, a new process identity; the marker stops a second crash.
    monkeypatch.setitem(advisor.PROCESS, "id", "host-2-second")
    second = runner(factory, kit)
    view = await second.next(run)
    assert view["status"] == "waiting" and view["cards"][0]["client"] == "Michael Scott"
    view = await approve_all(second, view, approve=False)
    assert writer.briefs == ["Michael Scott", "Jan Levinson", "Dwight Schrute"]
    text = view["text"]
    assert "plan; pack 1: Michael Scott; pack 2: Jan Levinson; stopped before pack 3 (crash test)" in text
    assert f"Process 2 ({'host-2-second'[-8:]}): restored packs 1-2 from the state store; pack 3: Dwight Schrute; summary" in text
    assert "restored from the Foundry state store, not redone" in text


@pytest.mark.asyncio
async def test_the_presenter_control_arms_the_crash_test_once():
    _, factory, memory, memory_factory = stores()
    faults = FaultSwitch(memory_factory)
    await memory.set_item(FaultSwitch.KEY, {"packs_crash": True})
    packs = runner(factory, Kit(faults=faults))
    view = await packs.start("Review packs")
    view = await packs.answer(view["run"], view["cards"][0]["call_id"], {"approve": True})
    await packs.next(view["run"])
    await packs.next(view["run"])
    with pytest.raises(Stop):
        await packs.next(view["run"])
    assert (await memory.get_item(FaultSwitch.KEY)).value["packs_crash"] is False


@pytest.mark.asyncio
async def test_no_crash_without_the_request_or_the_control():
    _, factory, _, memory_factory = stores()
    packs = runner(factory, Kit(faults=FaultSwitch(memory_factory)))
    view = await packs.start("Review packs")
    view = await packs.answer(view["run"], view["cards"][0]["call_id"], {"approve": True})
    for _ in range(3):
        view = await packs.next(view["run"])
    assert view["status"] == "waiting" and view["cards"][0]["kind"] == "draft"


@pytest.mark.asyncio
async def test_a_checkpoint_that_fails_its_hash_is_prepared_again():
    durable, factory, *_ = stores()
    writer = Writer()
    packs = runner(factory, Kit(writer))
    view = await packs.start("packs for Michael Scott")
    view = await packs.answer(view["run"], view["cards"][0]["call_id"], {"approve": True})
    await packs.next(view["run"])
    key = f"packs/{view['run']}/pack-1"
    tampered = durable.items[key].value
    tampered["pack"]["brief"] = "changed"
    await durable.set_item(key, tampered)
    run_doc = durable.items[f"packs/{view['run']}/run"].value
    await durable.set_item(f"packs/{view['run']}/run", {**run_doc, "status": "packing", "pending": None})
    await packs.next(view["run"])
    assert writer.briefs == ["Michael Scott", "Michael Scott"]
    assert digest(durable.items[key].value["pack"]) == durable.items[key].value["sha256"]  # the bad one is replaced


@pytest.mark.asyncio
async def test_drafts_use_the_story_addresses(monkeypatch):
    monkeypatch.delenv("DEMO_CLIENT_EMAIL", raising=False)
    _, factory, *_ = stores()
    packs = runner(factory)
    view = await packs.start("packs")
    view = await packs.answer(view["run"], view["cards"][0]["call_id"], {"approve": True})
    for _ in range(3):
        view = await packs.next(view["run"])
    addresses = []
    while view["cards"]:
        addresses.append(view["cards"][0]["to"])
        view = await packs.answer(view["run"], view["cards"][0]["call_id"], {"approve": False})
    assert addresses == ["michael.scott@scott-family.example", "jan.levinson@example.com", "dwight.schrute@example.com"]


@pytest.mark.asyncio
async def test_unknown_runs_and_steps_are_errors_not_exceptions():
    _, factory, *_ = stores()
    packs = runner(factory)
    assert (await packs.handle({"op": "next", "run": "nope"}))["error"] == "Unknown review-packs run."
    assert (await packs.handle({"op": "next", "run": "0" * 32}))["error"] == "Unknown review-packs run."
    view = await packs.handle({"op": "start", "request": "packs"})
    assert (await packs.handle({"op": "dance", "run": view["run"]}))["error"] == "Unknown review-packs step."
    assert (await packs.handle({"op": "view", "run": view["run"]}))["cards"][0]["kind"] == "clients"


def test_the_plan_proposes_only_the_clients_the_request_names(monkeypatch):
    monkeypatch.delenv("TEAM_REVIEW_CLIENTS", raising=False)
    assert clients_from("Prepare a review pack for Jan Levinson.") == ["Jan Levinson"]
    assert clients_from("Packs for Levinson and Schrute, please") == ["Jan Levinson", "Dwight Schrute"]
    assert clients_from("Prepare review packs for my three clients") == ["Michael Scott", "Jan Levinson", "Dwight Schrute"]
    monkeypatch.setenv("TEAM_REVIEW_CLIENTS", "Ann Lee, Bo Chan")
    assert clients_from("all of them") == ["Ann Lee", "Bo Chan"]


def test_the_summary_says_why_a_pack_was_held_back():
    base = {"degraded": False, "finished_at": "2026-10-21T09:00:00+00:00", "process": "5-abcdef12", "brief": "b"}
    packs = [{**base, "client": "Michael Scott", "cleared": True, "attempts": 2, "findings": []},
             {**base, "client": "Jan Levinson", "cleared": False, "attempts": 2,
              "findings": [{"issue": "a trade recommendation", "sentence": "Sell NVDA now."}]}]
    text = summarize(packs, [], []).text
    assert "**Michael Scott**: cleared after one revision" in text
    assert "**Jan Levinson**: NOT CLEARED: held back by the checks" in text
    assert '  - a trade recommendation ("Sell NVDA now.")' in text


# --- The gate and the wire format ---------------------------------------------------------------------------------
def user(text: str):
    return SimpleNamespace(role="user", text=text)


def test_only_a_review_packs_command_in_the_last_user_message_is_a_step():
    assert command_in([user(command_text({"op": "next", "run": "r"}))]) == {"op": "next", "run": "r"}
    assert command_in([user("Prepare me for my Thursday meeting with Michael Scott.")]) is None
    assert command_in([user(command_text({"op": "start"})), user("hello")]) is None
    assert command_in([user("review-packs: not json")]) == {"op": "invalid"}
    assert command_in([user(command_text({"op": "x"})), SimpleNamespace(role="assistant", text="")]) == {"op": "x"}


def test_the_view_is_the_answers_last_view_line():
    view = {"run": "r", "status": "working"}
    assert view_in("Working on pack 1 of 3: 15 s\n" + view_text(view)) == view
    assert view_in("Working: 15 s\n") is None
    assert view_in(view_text({"a": 1}) + "\n" + view_text(view)) == view


@pytest.mark.asyncio
async def test_the_gate_passes_chat_on_and_answers_steps_in_code(monkeypatch):
    _, factory, *_ = stores()
    gate = ReviewPacksGate(runner(factory))
    called = []

    async def call_next():
        called.append(True)

    chat = SimpleNamespace(messages=[user("Prepare me for my meeting.")], stream=True, result=None)
    await gate.process(chat, call_next)
    assert called == [True] and chat.result is None

    step = SimpleNamespace(messages=[user(command_text({"op": "start", "request": "packs"}))], stream=False, result=None)
    await gate.process(step, call_next)
    assert called == [True]
    assert view_in(step.result.text)["cards"][0]["kind"] == "clients"


@pytest.mark.asyncio
async def test_a_streamed_step_sends_progress_lines_then_the_view(monkeypatch):
    monkeypatch.setattr(review_packs, "PROGRESS_SECONDS", 0.01)
    _, factory, *_ = stores()

    async def slow_pack(kit, client):
        await asyncio.sleep(0.05)
        return await review_packs.make_pack(kit, client)

    packs = runner(factory, pack_maker=slow_pack)
    view = await packs.start("packs for Michael Scott")
    await packs.answer(view["run"], view["cards"][0]["call_id"], {"approve": True})
    context = SimpleNamespace(messages=[user(command_text({"op": "next", "run": view["run"]}))], stream=True, result=None)
    await ReviewPacksGate(packs).process(context, lambda: None)
    texts = [update.text async for update in context.result]
    assert len(texts) >= 2 and texts[0].startswith("Working on pack 1 of 1 (Michael Scott)")
    final = await context.result.get_final_response()
    assert view_in(final.text)["cards"][0]["kind"] == "draft"


@pytest.mark.asyncio
async def test_a_failed_step_comes_back_as_an_error_view():
    async def broken_store():
        raise RuntimeError("the state store is down")

    context = SimpleNamespace(messages=[user(command_text({"op": "start", "request": "packs"}))], stream=False,
                              result=None)
    await ReviewPacksGate(ReviewPacks(broken_store)).process(context, lambda: None)
    view = view_in(context.result.text)
    assert view["status"] == "failed" and "send it again" in view["error"]
    assert json.loads(json.dumps(view)) == view
