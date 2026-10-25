import asyncio
import json

import pytest
from agent_framework import FunctionInvocationContext, tool

from advisor_team.advisor import (
    ClientMemory,
    EvidenceCheck,
    FaultSwitch,
    InMemoryItemStore,
    ResilienceMiddleware,
    TurnLedger,
    client_key,
    liquidity_check,
    liquidity_math,
    rebalancing_check,
    rebalancing_math,
    to_eastern_time,
)

# Numbers from fabric/portfolio-story.json (Michael Scott, 2026-10-19).
NVDA_VALUE, TOTAL, PRICE, BASIS = 763857.78, 2926762.55, 202.83, 41.20


def store_factory():
    store = InMemoryItemStore()

    async def factory():
        return store

    return factory


def text_of(result) -> str:
    if isinstance(result, str):
        return result
    if isinstance(result, list):
        return "".join(getattr(item, "text", "") or "" for item in result)
    return str(result)


def test_client_key():
    assert client_key("  Michael Scott ") == "michael-scott"
    with pytest.raises(ValueError):
        client_key("  ")


def test_rebalancing_matches_story_numbers():
    result = rebalancing_math(NVDA_VALUE, TOTAL, 25, PRICE, BASIS, 20000)
    assert result["above_limit"] and result["current_weight_pct"] == 26.1
    assert result["trim_to_limit_usd"] == 32167.14
    assert abs(result["realized_gain_on_trim_usd"] - 25633.2) < 1
    assert result["trim_exceeds_tax_budget"]
    assert 25090 < result["max_trim_within_tax_budget_usd"] < 25110
    lower = rebalancing_math(NVDA_VALUE, TOTAL, 25, PRICE, BASIS, 15000)
    assert lower["max_trim_within_tax_budget_usd"] < result["max_trim_within_tax_budget_usd"]


def test_liquidity_shortfall_matches_story():
    result = liquidity_math(282219.15, 50000, [400000, 25000], ["home down payment", "529 plan"])
    assert result["available_above_reserve_usd"] == 232219.15
    assert result["shortfall_usd"] == 192780.85


async def test_to_eastern_time_handles_dst_and_bad_input():
    out = json.loads(text_of(await to_eastern_time.invoke(arguments={"timestamps": [
        "2026-10-22T14:00:00+00:00", "2026-10-16T15:00:00Z", "2026-11-05T15:00:00+00:00",
        "2026-11-01T05:59:00Z", "2026-11-01T06:00:00Z", "2026-03-08T07:00:00Z", "2026-10-22T10:00:00",
    ]})))
    assert out[0]["eastern"] == "Thu Oct 22, 2026 10:00 AM EDT"
    assert out[1]["eastern"] == "Fri Oct 16, 2026 11:00 AM EDT"
    assert out[2]["eastern"] == "Thu Nov 5, 2026 10:00 AM EST"
    assert out[3]["eastern"].endswith("1:59 AM EDT")
    assert out[4]["eastern"].endswith("1:00 AM EST")
    assert out[5]["eastern"].endswith("3:00 AM EDT")
    assert "error" in out[6]


async def test_calculator_tools_invoke():
    out = json.loads(text_of(await rebalancing_check.invoke(arguments={
        "position_value_usd": NVDA_VALUE, "total_portfolio_value_usd": TOTAL, "limit_pct": 25,
        "price_per_share": PRICE, "cost_basis_per_share": BASIS, "annual_tax_budget_usd": 15000})))
    assert out["trim_exceeds_tax_budget"] is True
    out = json.loads(text_of(await liquidity_check.invoke(arguments={
        "cash_and_equivalents_usd": 282219.15, "liquidity_reserve_usd": 50000,
        "needs_usd": [400000], "needs_labels": ["down payment"]})))
    assert out["shortfall_usd"] == 167780.85


async def test_memory_tools_roundtrip():
    ledger = TurnLedger()
    recall, save_baseline, save_brief = ClientMemory(store_factory()).tools(ledger)
    first = json.loads(text_of(await recall.invoke(arguments={"client_name": "Michael Scott"})))
    assert first["last_discussion"] is None and "note" in first and not ledger.client_turn
    await save_baseline.invoke(arguments={
        "client_name": "Michael Scott", "discussion_at": "2026-10-16T11:30:00-04:00",
        "summary": "Q4 review.", "source": "Q4 portfolio review - Michael Scott"})
    await save_brief.invoke(arguments={
        "client_name": "Michael Scott", "summary": "Brief.", "portfolio_as_of": "2026-10-19",
        "portfolio_figures": "NVDA 26.1% (limit 25%)", "sources": ["positions_daily"]})
    second = json.loads(text_of(await recall.invoke(arguments={"client_name": "michael scott"})))
    assert second["last_discussion"]["discussion_at"] == "2026-10-16T11:30:00-04:00"
    assert second["last_brief"]["portfolio_as_of"] == "2026-10-19"


def test_evidence_check_requires_both_sources_when_fabric_is_required():
    ledger = TurnLedger(client_turn=True)
    check = EvidenceCheck(ledger, require_fabric=True)
    ledger.record("fabric_portfolio___DataAgent_Portfolio_Analyst", "failed")
    ledger.record("workiq___chat", "ok")
    again, feedback = check.should_continue(iteration=1)
    assert again and "Fabric" in feedback and "Work IQ" not in feedback
    assert check.should_continue(iteration=2) == (False, None)
    assert "recall_client_memory" in check.next_message(feedback=feedback)


def test_evidence_check_ignores_general_turns():
    assert EvidenceCheck(TurnLedger()).should_continue(iteration=1) == (False, None)


@pytest.mark.parametrize("name", ["workiq___chat", "work_iq_preview", "toolbox___WorkIQ_Ask", "Work-IQ",
                                  "list_client_documents"])
def test_workiq_tool_names_are_recognised(name):
    ledger = TurnLedger(client_turn=True)
    ledger.record("fabric_portfolio___DataAgent_Portfolio_Analyst", "ok")
    ledger.record(name, "ok")
    assert EvidenceCheck(ledger).should_continue(iteration=1) == (False, None)


@tool(name="load_skill")
async def fake_load_skill(skill_name: str) -> str:
    return "skill"


@tool(name="record_brief")
async def fake_record_brief(client_name: str) -> str:
    return "saved"


@tool(name="record_discussion_baseline")
async def fake_record_baseline(client_name: str) -> str:
    return "saved"


async def test_brief_markers_and_baseline_suppression(monkeypatch):
    monkeypatch.delenv("DEMO_FAULTS", raising=False)

    async def call_next():
        return None

    def context(function, **arguments):
        return FunctionInvocationContext(function=function, arguments=arguments)

    ledger = TurnLedger()
    middleware = ResilienceMiddleware(ledger, FaultSwitch(store_factory()))
    await middleware.process(context(fake_load_skill, skill_name="what-changed"), call_next)
    assert not ledger.client_turn
    await middleware.process(context(fake_load_skill, skill_name="client-meeting-prep"), call_next)
    assert ledger.client_turn and EvidenceCheck(ledger).find_gaps()

    ledger = TurnLedger()
    middleware = ResilienceMiddleware(ledger, FaultSwitch(store_factory()))
    await middleware.process(context(fake_record_brief, client_name="Michael Scott"), call_next)
    assert ledger.client_turn
    await middleware.process(context(fake_record_baseline, client_name="Michael Scott"), call_next)
    assert EvidenceCheck(ledger).find_gaps() == []


def test_next_message_is_a_visible_recovery_note():
    note = EvidenceCheck(TurnLedger()).next_message(feedback="the Fabric IQ Portfolio Analyst was not consulted")
    assert "Harness evidence check" in note and "Recovery" in note and "do not repeat the brief" in note


class FakeFunction:
    def __init__(self, name, calls):
        self.name, self.calls = name, calls

    async def invoke(self, arguments):
        self.calls.append(arguments)
        return "ok"


async def test_fabric_calls_use_their_own_toolbox_session(monkeypatch):
    import advisor_team.retrieval as retrieval

    calls, sessions = [], []

    class FakeToolbox:
        def __init__(self, *args, **kwargs):
            sessions.append(kwargs)
            self.functions = [FakeFunction("fabric_portfolio___DataAgent_Portfolio_Analyst", calls)]

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return None

    monkeypatch.setattr(retrieval, "FoundryToolbox", FakeToolbox)
    toolbox = retrieval.ParallelWorkIQToolbox.__new__(retrieval.ParallelWorkIQToolbox)
    toolbox.retrieval_credential, toolbox.retrieval_url = None, "https://toolbox"
    toolbox.retrieval_options, toolbox.progress = {"timeout": 240.0}, None
    result = await toolbox.call_tool("fabric_portfolio___DataAgent_Portfolio_Analyst", userQuestion="q")
    assert result == "ok" and calls == [{"userQuestion": "q"}] and sessions == [{"url": "https://toolbox", "load_prompts": False, "timeout": 240.0}]


async def test_tool_errors_in_own_session_are_not_retried(monkeypatch):
    import advisor_team.retrieval as retrieval

    shared_calls = []

    async def denied(self, tool_name, arguments):
        raise RuntimeError("Request denied by the tenant policy")

    async def shared(self, tool_name, **kwargs):
        shared_calls.append(tool_name)
        return "shared"

    monkeypatch.setattr(retrieval.ParallelWorkIQToolbox, "call_in_own_session", denied)
    monkeypatch.setattr(retrieval.FoundryToolbox, "call_tool", shared)
    toolbox = retrieval.ParallelWorkIQToolbox.__new__(retrieval.ParallelWorkIQToolbox)
    with pytest.raises(RuntimeError, match="policy"):
        await toolbox.call_tool("workiq___fetch", entityUrls=["/users"])
    assert shared_calls == []


async def test_own_session_failure_falls_back_to_shared_session(monkeypatch):
    import advisor_team.retrieval as retrieval

    async def broken(self, tool_name, arguments):
        raise retrieval.SessionUnavailable("connect failed")

    async def shared(self, tool_name, **kwargs):
        return f"shared:{tool_name}:{kwargs['userQuestion']}"

    monkeypatch.setattr(retrieval.ParallelWorkIQToolbox, "call_in_own_session", broken)
    monkeypatch.setattr(retrieval.FoundryToolbox, "call_tool", shared)
    toolbox = retrieval.ParallelWorkIQToolbox.__new__(retrieval.ParallelWorkIQToolbox)
    result = await toolbox.call_tool("fabric_portfolio___DataAgent_Portfolio_Analyst", userQuestion="q")
    assert result == "shared:fabric_portfolio___DataAgent_Portfolio_Analyst:q"


async def test_connect_retries_transient_connection_resolution(monkeypatch):
    import advisor_team.retrieval as retrieval

    attempts = []

    async def flaky(self, *, reset=False):
        attempts.append(reset)
        if len(attempts) < 3:
            raise RuntimeError("tools/list failed ... INTERNAL_ERROR: Error resolving connection '/x/connections/work-iq'.")

    monkeypatch.setattr(retrieval.FoundryToolbox, "connect", flaky)
    toolbox = retrieval.ParallelWorkIQToolbox.__new__(retrieval.ParallelWorkIQToolbox)
    toolbox.connect_backoff_seconds = 0
    await toolbox.connect()
    assert attempts == [False, True, True]


async def test_connect_does_not_retry_other_errors(monkeypatch):
    import advisor_team.retrieval as retrieval

    attempts = []

    async def failing(self, *, reset=False):
        attempts.append(reset)
        raise RuntimeError("CONSENT_REQUIRED: Error resolving connection until the user consents")

    monkeypatch.setattr(retrieval.FoundryToolbox, "connect", failing)
    toolbox = retrieval.ParallelWorkIQToolbox.__new__(retrieval.ParallelWorkIQToolbox)
    toolbox.connect_backoff_seconds = 0
    with pytest.raises(RuntimeError):
        await toolbox.connect()
    assert attempts == [False]


@tool(name="fabric_portfolio___DataAgent_Portfolio_Analyst")
async def fake_fabric(userQuestion: str) -> str:
    return "unused"


async def test_resilience_demo_fault(monkeypatch):
    monkeypatch.setenv("DEMO_FAULTS", "fabric")
    ledger = TurnLedger()
    context = FunctionInvocationContext(function=fake_fabric, arguments={"userQuestion": "q"})
    called = False

    async def call_next():
        nonlocal called
        called = True

    await ResilienceMiddleware(ledger, FaultSwitch(store_factory())).process(context, call_next)
    assert not called and "PORTFOLIO_DATA_UNAVAILABLE" in context.result
    assert ledger.outcomes == [("fabric_portfolio___DataAgent_Portfolio_Analyst", "fault")]


async def test_resilience_timeout_degrades(monkeypatch):
    monkeypatch.delenv("DEMO_FAULTS", raising=False)
    ledger = TurnLedger()
    context = FunctionInvocationContext(function=fake_fabric, arguments={"userQuestion": "q"})

    async def call_next():
        await asyncio.sleep(1)

    middleware = ResilienceMiddleware(ledger, FaultSwitch(store_factory()), timeout_seconds=0.05)
    await middleware.process(context, call_next)
    assert "PORTFOLIO_DATA_UNAVAILABLE" in context.result and ledger.outcomes[-1][1] == "failed"


@tool(name="workiq___retrieve")
async def fake_retrieve(query: str) -> str:
    return "unused"


async def test_a_workiq_retrieval_the_service_could_not_complete_is_retried_once_and_a_denial_never():
    # The failure Work IQ returned live on 2026-10-17.
    failure = json.dumps({"application/vnd.ms-workiq.retrieval": {
        "markdown": "The retrieval request could not be completed. Contact support if the problem persists.",
        "retrievalHits": [], "resultCount": 0, "stoppedReason": "error"}})
    ledger = TurnLedger()
    context = FunctionInvocationContext(function=fake_retrieve, arguments={"query": "q"})
    calls = []

    async def flaky():
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError(failure)
        context.result = "ranked passages"

    await ResilienceMiddleware(ledger, FaultSwitch(store_factory())).process(context, flaky)
    assert len(calls) == 2 and context.result == "ranked passages"
    assert ledger.outcomes == [("workiq___retrieve", "ok")]

    denial = failure.replace("could not be completed", "is blocked by your organization's Work IQ policy")
    denied_calls = []

    async def denied():
        denied_calls.append(1)
        raise RuntimeError(denial)

    ledger = TurnLedger()
    with pytest.raises(RuntimeError, match="policy"):
        await ResilienceMiddleware(ledger, FaultSwitch(store_factory())).process(
            FunctionInvocationContext(function=fake_retrieve, arguments={"query": "q"}), denied)
    assert len(denied_calls) == 1 and ledger.outcomes == [("workiq___retrieve", "failed")]


@tool(name="workiq___fetch")
async def fake_fetch(entityUrls: list[str]) -> str:
    return "unused"


@tool(name="inspect_document")
async def fake_inspect(phrase: str) -> str:
    return "unused"


async def test_a_toolbox_that_could_not_resolve_its_connection_is_retried_once_for_any_tool(monkeypatch):
    import advisor_team.advisor as advisor_module

    monkeypatch.setattr(advisor_module, "CONNECTION_RETRY_DELAY_SECONDS", 0)
    # The error the Foundry toolbox raised live on 2026-10-22, before the request reached Work IQ.
    unresolved = ("Error resolving connection '/subscriptions/0000/resourceGroups/rg/providers/"
                  "Microsoft.CognitiveServices/accounts/a/projects/p/connections/work-iq-mcp-x'.")
    for function in (fake_fetch, fake_inspect):
        ledger = TurnLedger()
        context = FunctionInvocationContext(function=function, arguments={})
        calls = []

        async def once():
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError(unresolved)
            context.result = "the item"

        await ResilienceMiddleware(ledger, FaultSwitch(store_factory())).process(context, once)
        assert len(calls) == 2 and context.result == "the item"
        assert ledger.outcomes == [(function.name, "ok")]

    always = []

    async def unresolvable():
        always.append(1)
        raise RuntimeError(unresolved)

    ledger = TurnLedger()
    with pytest.raises(RuntimeError, match="resolving connection"):
        await ResilienceMiddleware(ledger, FaultSwitch(store_factory())).process(
            FunctionInvocationContext(function=fake_fetch, arguments={}), unresolvable)
    assert len(always) == 2 and ledger.outcomes == [("workiq___fetch", "failed")]


async def test_fault_switch_tool_toggles():
    factory = store_factory()
    switch = FaultSwitch(factory)
    await switch.tool().invoke(arguments={"component": "fabric", "enabled": True})
    assert await FaultSwitch(factory).active("fabric")


async def test_state_store_history_roundtrip_and_chunks(monkeypatch):
    from agent_framework import Message

    import advisor_team.history as history_module
    from advisor_team.history import StateStoreHistoryProvider

    monkeypatch.setattr(history_module, "CHUNK_BYTES", 300)
    factory = store_factory()
    provider = StateStoreHistoryProvider(factory, scope=["tenant", "project", "platform-session"])
    first = [Message(role="user", contents=["Prepare me for Michael Scott."]),
             Message(role="assistant", contents=["x" * 400])]
    await provider.save_messages("conv-1", first)
    await provider.save_messages("conv-1", [*first, Message(role="user", contents=["What changed?"])])
    reloaded = await StateStoreHistoryProvider(factory, scope=["tenant", "project", "platform-session"]).get_messages("conv-1")
    assert [m.text for m in reloaded] == ["Prepare me for Michael Scott.", "x" * 400, "What changed?"]
    store = await factory()
    index = store.items[f"{provider.key('conv-1')}--index"].value
    assert index["chunks"] >= 2
    other = StateStoreHistoryProvider(factory, scope=["tenant", "project", "other-session"])
    assert await other.get_messages("conv-1") == []
