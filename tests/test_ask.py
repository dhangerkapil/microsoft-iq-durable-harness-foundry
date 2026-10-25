import asyncio
import json
from copy import deepcopy
from unittest.mock import AsyncMock

import pytest
from agent_framework import AgentSession, Content, FunctionInvocationContext, FunctionTool
from agent_framework.exceptions import ToolExecutionException

from advisor_team.ask import ASK_STATE_KEY, DEFAULT_AGENT_ID, AskSessionContext


def ask_context(session: AgentSession, **arguments) -> FunctionInvocationContext:
    return FunctionInvocationContext(
        function=FunctionTool(name="workiq___ask", func=lambda: "unused"),
        arguments={"question": "Synthetic question", **arguments}, session=session,
    )


def ask_result(identifier: str) -> list[Content]:
    return [Content.from_text(json.dumps({
        "response": "Synthetic answer", "conversationId": identifier,
    }), additional_properties={"_meta": {"source": "synthetic"}})]


async def test_ask_continues_after_session_restore_without_trusting_model_ids():
    session = AgentSession()
    context = ask_context(session, conversationId="another-session", fileUrls=["https://example.com/file"])
    result = ask_result("workiq-thread")
    assert isinstance(context.arguments, dict)
    original = deepcopy(context.arguments)

    async def first_call():
        assert context.arguments == {
            **original, "conversationId": None, "agentId": DEFAULT_AGENT_ID,
        }
        context.result = result

    await AskSessionContext().process(context, first_call)
    assert context.result is result
    assert session.state[ASK_STATE_KEY] == {
        "agent_id": DEFAULT_AGENT_ID, "conversation_id": "workiq-thread",
    }
    restored = AgentSession.from_dict(json.loads(json.dumps(session.to_dict())))
    follow_up = ask_context(restored, conversationId="invented-id")

    async def next_call():
        assert isinstance(follow_up.arguments, dict)
        assert follow_up.arguments["conversationId"] == "workiq-thread"
        follow_up.result = result

    await AskSessionContext().process(follow_up, next_call)
    unrelated = ask_context(AgentSession(), conversationId="workiq-thread")

    async def separate_call():
        assert isinstance(unrelated.arguments, dict)
        assert unrelated.arguments["conversationId"] is None
        unrelated.result = ask_result("separate-thread")

    await AskSessionContext().process(unrelated, separate_call)
    assert restored.state[ASK_STATE_KEY]["conversation_id"] == "workiq-thread"


async def test_concurrent_first_ask_calls_share_one_remote_conversation():
    middleware = AskSessionContext()
    session = AgentSession()
    first, second = ask_context(session), ask_context(session)
    entered, release = asyncio.Event(), asyncio.Event()
    calls = []

    async def first_call():
        assert isinstance(first.arguments, dict)
        calls.append(first.arguments["conversationId"])
        entered.set()
        await release.wait()
        first.result = ask_result("one-thread")

    async def second_call():
        assert isinstance(second.arguments, dict)
        calls.append(second.arguments["conversationId"])
        second.result = ask_result("one-thread")

    task = asyncio.create_task(middleware.process(first, first_call))
    await entered.wait()
    other = asyncio.create_task(middleware.process(second, second_call))
    try:
        await asyncio.sleep(0)
        assert calls == [None]
    finally:
        release.set()
        await asyncio.gather(task, other)
    assert calls == [None, "one-thread"]


@pytest.mark.parametrize("result", [
    [Content.from_text("Plain answer without metadata")],
    [Content.from_text('{"response":{"conversationId":"outlook-mail-thread"}}')],
    [Content.from_text('{"conversationId":null}')],
    [Content.from_text('{"conversationId":"first"}'), Content.from_text('{"conversationId":"second"}')],
])
async def test_unusable_success_metadata_cannot_silently_start_another_thread(result):
    session = AgentSession()
    context = ask_context(session)

    async def execute():
        context.result = result

    middleware = AskSessionContext()
    with pytest.raises(ToolExecutionException, match="conversation"):
        await middleware.process(context, execute)
    next_call = AsyncMock()
    with pytest.raises(ToolExecutionException, match="start a new session"):
        await middleware.process(ask_context(session), next_call)
    next_call.assert_not_awaited()


async def test_ask_errors_and_changed_ids_never_reset_established_context():
    session = AgentSession()
    session.state[ASK_STATE_KEY] = {"agent_id": "selected-agent", "conversation_id": "original-thread"}
    context = ask_context(session)
    failure = [Content.from_text('{"isError":true,"error":"Access denied"}')]

    async def denied():
        assert isinstance(context.arguments, dict)
        assert context.arguments["agentId"] == "selected-agent"
        assert context.arguments["conversationId"] == "original-thread"
        context.result = failure

    middleware = AskSessionContext()
    await middleware.process(context, denied)
    assert context.result is failure

    async def changed():
        context.result = ask_result("replacement-thread")

    with pytest.raises(ToolExecutionException, match="different conversation ID"):
        await middleware.process(context, changed)
    assert session.state[ASK_STATE_KEY]["conversation_id"] == "original-thread"

    async def unavailable():
        raise RuntimeError("Synthetic transport failure")

    with pytest.raises(RuntimeError, match="Synthetic transport failure"):
        await middleware.process(context, unavailable)
    assert session.state[ASK_STATE_KEY]["conversation_id"] == "original-thread"


async def test_each_agent_keeps_its_own_conversation_and_never_borrows_another_agents_thread():
    middleware = AskSessionContext()
    session = AgentSession()
    seen: list[tuple[str, str | None]] = []

    def answering(context: FunctionInvocationContext, identifier: str):
        async def execute():
            assert isinstance(context.arguments, dict)
            seen.append((context.arguments["agentId"], context.arguments["conversationId"]))
            context.result = ask_result(identifier)
        return execute

    copilot = ask_context(session)
    await middleware.process(copilot, answering(copilot, "copilot-thread"))
    planner = ask_context(session, agentId="P_planner.declarativeAgent", conversationId="copilot-thread")
    await middleware.process(planner, answering(planner, "planner-thread"))
    planner_again = ask_context(session, agentId="P_planner.declarativeAgent")
    await middleware.process(planner_again, answering(planner_again, "planner-thread"))
    copilot_again = ask_context(session)
    await middleware.process(copilot_again, answering(copilot_again, "copilot-thread"))
    assert seen == [
        (DEFAULT_AGENT_ID, None), ("P_planner.declarativeAgent", None),
        ("P_planner.declarativeAgent", "planner-thread"), (DEFAULT_AGENT_ID, "copilot-thread"),
    ]
    assert session.state[ASK_STATE_KEY] == {"agent_id": DEFAULT_AGENT_ID, "conversation_id": "copilot-thread"}
    assert session.state[f"{ASK_STATE_KEY}:P_planner.declarativeAgent"] == {
        "agent_id": "P_planner.declarativeAgent", "conversation_id": "planner-thread",
    }
    # A second agent's broken continuity blocks only that agent.
    session.state[f"{ASK_STATE_KEY}:P_planner.declarativeAgent"] = {
        "agent_id": "P_planner.declarativeAgent", "conversation_id": None,
    }
    blocked = AsyncMock()
    with pytest.raises(ToolExecutionException, match="start a new session"):
        await middleware.process(ask_context(session, agentId="P_planner.declarativeAgent"), blocked)
    blocked.assert_not_awaited()
    still_copilot = ask_context(session)
    await middleware.process(still_copilot, answering(still_copilot, "copilot-thread"))
    assert seen[-1] == (DEFAULT_AGENT_ID, "copilot-thread")


async def test_a_service_rejection_is_asked_once_more_in_a_new_conversation_and_a_denial_never():
    session = AgentSession()
    session.state[ASK_STATE_KEY] = {"agent_id": DEFAULT_AGENT_ID, "conversation_id": "old-thread"}
    context = ask_context(session)
    seen = []

    async def rejected_then_answered():
        assert isinstance(context.arguments, dict)
        seen.append(context.arguments["conversationId"])
        if len(seen) == 1:
            # The error Work IQ returned live on 2026-10-17.
            raise ToolExecutionException(json.dumps({
                "response": None, "conversationId": "old-thread", "errorCode": "InternalError",
                "error": "The dependency rejected the request. Contact support if the problem persists.",
            }))
        context.result = ask_result("new-thread")

    await AskSessionContext().process(context, rejected_then_answered)
    assert seen == ["old-thread", None]
    assert session.state[ASK_STATE_KEY] == {"agent_id": DEFAULT_AGENT_ID, "conversation_id": "new-thread"}

    attempts = []

    async def denied():
        attempts.append(1)
        raise ToolExecutionException('{"errorCode": "InternalError", "error": "Blocked by your organization policy"}')

    with pytest.raises(ToolExecutionException, match="policy"):
        await AskSessionContext().process(ask_context(session), denied)
    assert attempts == [1]
    assert session.state[ASK_STATE_KEY]["conversation_id"] == "new-thread"


async def test_ask_requires_session_but_does_not_intercept_other_tools():
    middleware = AskSessionContext()
    execute = AsyncMock()
    context = FunctionInvocationContext(
        function=FunctionTool(name="workiq___ask", func=lambda: "unused"), arguments={},
    )
    with pytest.raises(ToolExecutionException, match="active conversation session"):
        await middleware.process(context, execute)
    execute.assert_not_awaited()
    context.function = FunctionTool(name="workiq___retrieve", func=lambda: "unused")
    await middleware.process(context, execute)
    execute.assert_awaited_once()
