"""Add-ons: the committee's speaking order and minutes guard (O4), the what-if sandbox (O3), Purview's user stamp (O2)."""

from __future__ import annotations

import pytest
from agent_framework import Message, MiddlewareTermination

from advisor_team.committee import next_speaker, transcript, unsupported
from advisor_team.purview import BLOCKED_PROMPT, AdvisorUserStamp, PurviewGate, purview_middleware
from advisor_team.whatif import LIMITS

USER = Message(role="user", contents=["Michael Scott's NVIDIA concentration"])


def said(name: str, text: str) -> Message:
    return Message(role="assistant", contents=[text], author_name=name)


def test_every_member_speaks_in_order_then_a_challenged_member_replies_once():
    conversation = [USER]
    assert next_speaker(conversation) == "portfolio_member"
    conversation += [said("portfolio_member", "Fabric IQ unavailable."), said("policy_member", "CP-104 §3.1 applies.")]
    assert next_speaker(conversation) == "market_member"
    conversation.append(said("market_member", "NVIDIA rose 1.1% (Reuters, Oct 19)."))
    assert next_speaker(conversation) == "risk_officer"
    conversation.append(said("risk_officer", "The policy member gave no effective date; the market member is fine."))
    assert next_speaker(conversation) in ("policy_member", "market_member")
    first = next_speaker(conversation)
    conversation.append(said(first, "Reply."))
    second = next_speaker(conversation)
    assert second in ("policy_member", "market_member") and second != first
    conversation.append(said(second, "Reply."))
    assert next_speaker(conversation) is None


def test_no_challenge_ends_the_meeting_after_the_risk_officer():
    conversation = [USER, *(said(n, "ok") for n in ("portfolio_member", "policy_member", "market_member")),
                    said("risk_officer", "No objections.")]
    assert next_speaker(conversation) is None


def test_the_transcript_keeps_members_only_once_each_line():
    turns = transcript([[USER, said("portfolio_member", "A"), said("portfolio_member", "A"),
                         said("group_chat_orchestrator", "done"), said("risk_officer", "B")]])
    assert turns == [("portfolio_member", "A"), ("risk_officer", "B")]


def test_minutes_with_a_code_or_figure_no_member_said_are_refused():
    record = "CP-104 §3.1 requires a review; the house view is IC-HV-2026-10 §3. NVDA is 26.1% of the portfolio."
    assert unsupported("Facts: NVDA 26.1% (portfolio member). Rules: CP-104 §3.1, IC-HV-2026-10 §3.", record) == []
    problems = unsupported("Rules: DATA-CONSENT-01 and LIMIT-VERIFICATION-01. NVDA is 27.4%.", record)
    assert {"DATA-CONSENT-01", "LIMIT-VERIFICATION-01"} <= set(problems) and any("27.4%" in p for p in problems)


@pytest.mark.asyncio
async def test_the_what_if_sandbox_computes_and_refuses_network_files_and_runaway_code():
    from agent_framework_monty import MontyExecuteCodeTool

    sandbox = MontyExecuteCodeTool(resource_limits={**LIMITS, "max_duration_secs": 1.0})
    math = await sandbox.invoke(arguments={"code": "t = 4250000\nn = t * 0.261\nprint(round(n * 0.8 / (t - n * 0.2) * 100, 2))"})
    assert math[0].type == "text" and math[0].text.strip() == "22.03"
    for code in ("import socket\nprint(socket.gethostname())", "print(open('/etc/hosts').read())", "while True:\n    pass"):
        assert (await sandbox.invoke(arguments={"code": code}))[0].type == "error"


class Context:
    def __init__(self) -> None:
        self.messages = [Message(role="user", contents=["hello"])]


@pytest.mark.asyncio
@pytest.mark.parametrize("user, stamped", [("44444444-4444-4444-8444-444444444444", True), ("opaque-key", False)])
async def test_purview_gets_the_advisors_object_id_only_when_the_platform_gives_one(user: str, stamped: bool):
    from azure.ai.agentserver.core import FoundryAgentRequestContext, reset_request_context, set_request_context

    token = set_request_context(FoundryAgentRequestContext(user_id=user))
    try:
        context = Context()
        calls = []

        async def call_next():
            calls.append(1)

        await AdvisorUserStamp().process(context, call_next)
    finally:
        reset_request_context(token)
    assert calls == [1]
    assert (context.messages[0].additional_properties.get("user_id") == user) is stamped


def test_purview_is_off_unless_turned_on(monkeypatch):
    monkeypatch.delenv("TEAM_PURVIEW", raising=False)
    assert purview_middleware(object()) == []
    monkeypatch.setenv("TEAM_PURVIEW", "on")
    monkeypatch.setenv("AZURE_TENANT_ID", "11111111-1111-4111-8111-111111111111")
    middleware = purview_middleware(lambda: "token")
    assert [type(m).__name__ for m in middleware] == ["AdvisorUserStamp", "PurviewGate"]
    assert type(middleware[1].inner).__name__ == "PurviewPolicyMiddleware"


class Run:
    def __init__(self, stream: bool) -> None:
        self.messages = [Message(role="user", contents=["My client's SSN is 524-77-3612."])]
        self.stream = stream
        self.result = None


class Policy:
    """Stands in for Purview's middleware: blocks before the run, terminates after it, or lets it through."""

    def __init__(self, mode: str) -> None:
        self.mode = mode

    async def process(self, context, call_next):
        if self.mode == "block":
            raise MiddlewareTermination
        await call_next()
        if self.mode == "terminate-later":
            raise MiddlewareTermination


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [True, False])
async def test_a_prompt_purview_blocks_gets_the_policy_message_and_the_agents_never_run(stream: bool):
    from agent_framework import ResponseStream

    context, ran = Run(stream), []

    async def call_next():
        ran.append(1)

    await PurviewGate(Policy("block")).process(context, call_next)
    assert ran == []
    if stream:
        assert isinstance(context.result, ResponseStream)
        texts = [update.text async for update in context.result]
        assert "".join(texts) == BLOCKED_PROMPT
        final = await context.result.get_final_response()
    else:
        final = context.result
    assert [m.role for m in final.messages] == ["assistant"] and final.text == BLOCKED_PROMPT


@pytest.mark.asyncio
async def test_the_purview_gate_passes_other_runs_and_later_terminations_through():
    context, ran = Run(True), []

    async def call_next():
        ran.append(1)

    await PurviewGate(Policy("allow")).process(context, call_next)
    assert ran == [1] and context.result is None
    with pytest.raises(MiddlewareTermination):
        await PurviewGate(Policy("terminate-later")).process(Run(True), call_next)


@pytest.mark.asyncio
async def test_a_specialist_outage_lasts_for_one_question():
    from advisor_team.activity import ActivityRecorder
    from advisor_team.advisor import FaultSwitch, InMemoryItemStore
    from advisor_team.roles import PORTFOLIO
    from advisor_team.team import Specialist

    store = InMemoryItemStore("memory")

    async def factory():
        return store

    await store.set_item(FaultSwitch.KEY, {"specialist_portfolio": True})

    class Agent:
        calls = 0

        def create_session(self, **_):
            return None

        async def run(self, task, session=None):
            Agent.calls += 1
            return type("R", (), {"text": '{"status": "ok", "answer": "NVDA 26.1% (Fabric IQ)"}'})()

    specialist = Specialist(PORTFOLIO, Agent(), ActivityRecorder(), faults=FaultSwitch(factory))
    first = await specialist.report("Michael Scott's NVDA weight")
    assert first.status == "unavailable" and "offline" in first.unavailable_reason and Agent.calls == 0
    second = await specialist.report("Michael Scott's NVDA weight")
    assert second.status == "ok" and Agent.calls == 1


@pytest.mark.asyncio
async def test_a_market_task_that_names_the_client_never_reaches_the_market_and_says_how_to_retry():
    from advisor_team.activity import ActivityRecorder
    from advisor_team.roles import MARKET
    from advisor_team.team import Specialist
    from advisor_team.webiq import BoundaryGuard

    class Agent:
        calls = 0

        def create_session(self, **_):
            return None

        async def run(self, task, session=None):
            Agent.calls += 1
            return type("R", (), {"text": '{"status": "ok", "answer": "NVIDIA news (Web IQ)"}'})()

    specialist = Specialist(MARKET, Agent(), ActivityRecorder(), guard=BoundaryGuard(["Michael Scott"]))
    # The lead once explained why it asked: the client's name in the explanation is still the client's name.
    blocked = await specialist.report("Public news on NVIDIA (NVDA), since Michael Scott's last brief names it.")
    assert blocked.status == "unavailable" and Agent.calls == 0
    assert blocked.unavailable_reason.startswith("WEB_TASK_BLOCKED")
    assert "once more with the public topic alone" in blocked.unavailable_reason
    retried = await specialist.report("Public news this week on NVIDIA (NVDA) and AI data-center demand")
    assert retried.status == "ok" and Agent.calls == 1
