"""Work IQ Chat over A2A: a whole question delegated to Work IQ's own agent, and its A2A task shaped for the agent."""

import json

from agent_framework.exceptions import ToolExecutionException
from mcp.shared.exceptions import McpError
from mcp.types import ErrorData

from advisor_team import workiq_chat
from advisor_team.workiq_chat import part_text, shape_task

LINK = "https://logic-apis-eastus2.consent.azure-apim.net/login?data=abc"
ANSWER = "Michael asked about a 529 plan [1](https://outlook.office365.com/owa/?ItemID=x)."
# The A2A task Work IQ returned live on 2026-10-18 (shortened), including the connector's repeated answer text.
TASK = json.dumps({
    "kind": "task", "id": "932bc23f277e4762967b26ccdf6d4f74", "contextId": "b206a3dd66014981ba7536c4605d82a6",
    "status": {"state": "completed", "message": {"kind": "message", "role": "agent", "parts": []}},
    "artifacts": [{"artifactId": "cb3e", "name": "Answer", "parts": [
        {"kind": "text", "text": ANSWER + ANSWER},
        {"kind": "data", "data": {"id": None, "priority": 0}},
    ]}],
})


def test_shape_task_keeps_ids_state_and_one_copy_of_the_answer():
    assert shape_task(TASK) == {
        "task_id": "932bc23f277e4762967b26ccdf6d4f74", "context_id": "b206a3dd66014981ba7536c4605d82a6",
        "state": "completed", "answer": ANSWER,
    }
    assert part_text([{"kind": "text", "text": "Yes."}, {"kind": "text", "text": " No."}]) == "Yes. No."
    assert part_text([{"kind": "text", "text": "abab"}]) == "ab"
    # Repeated with a trailing newline, or in two parts with a separator: still one copy.
    assert part_text([{"kind": "text", "text": ANSWER + "\n" + ANSWER + "\n"}]) == ANSWER
    assert part_text([{"kind": "text", "text": ANSWER + "\n\n"}, {"kind": "text", "text": ANSWER}]) == ANSWER
    assert part_text([{"kind": "text", "text": ANSWER + " And one more point."}]) == ANSWER + " And one more point."
    assert part_text("not parts") == "" and shape_task("") == {} and shape_task("[]") == {}
    assert shape_task("plain answer")["answer"] == "plain answer"
    # No artifact: the answer can come in the status message instead.
    message = json.dumps({"kind": "task", "id": "t", "status": {"state": "input-required", "message": {
        "kind": "message", "parts": [{"kind": "text", "text": "Which client do you mean?"}]}}})
    assert shape_task(message) == {"task_id": "t", "state": "input-required", "answer": "Which client do you mean?"}


class Function:
    name = "work-iq___SendMessage"

    def __init__(self, result=TASK):
        self.calls = []
        self.result = result

    async def invoke(self, arguments):
        self.calls.append(arguments)
        return self.result


def toolbox_with(function=None, error=None):
    class Box:
        def __init__(self, *args, **kwargs):
            self.functions = [function] if function else []

        async def __aenter__(self):
            if error:
                raise error
            return self

        async def __aexit__(self, *exc):
            return False

    return Box


async def run(tool, **arguments):
    result = await tool.invoke(arguments=arguments)
    if isinstance(result, list):
        return "".join(getattr(item, "text", "") or "" for item in result)
    return result if isinstance(result, str) else str(result)


async def test_delegation_sends_one_a2a_text_part_with_kind_first(monkeypatch):
    function = Function()
    monkeypatch.setattr(workiq_chat, "FoundryToolbox", toolbox_with(function))
    (delegate,) = workiq_chat.chat_tools(object(), "https://x/toolboxes/investment-workiq-chat/mcp")
    out = json.loads(await run(delegate, task="What did   Michael ask for?"))
    (part,) = function.calls[0]["message"]["parts"]
    assert list(part) == ["kind", "text"] and part == {"kind": "text", "text": "What did Michael ask for?"}
    assert out["status"] == "completed" and out["protocol"] == "A2A" and out["answer"] == ANSWER
    assert out["task_id"].startswith("932bc23f") and out["source"].startswith("Work IQ Chat over A2A")


async def test_consent_outage_and_missing_toolbox(monkeypatch):
    details = {"errors": [{"name": "work-iq", "type": "a2a_preview", "error": {"code": "CONSENT_REQUIRED",
                                                                              "message": LINK}}]}
    consent = ToolExecutionException("Failed to enter context manager.", McpError(ErrorData(
        code=-32006, message="tools/list failed for 1 tool source(s) " + json.dumps(details))))
    monkeypatch.setattr(workiq_chat, "FoundryToolbox", toolbox_with(error=consent))
    (delegate,) = workiq_chat.chat_tools(object(), "https://x")
    out = json.loads(await run(delegate, task="q"))
    assert out == {**out, "status": "WORKIQ_CHAT_CONSENT_REQUIRED", "consent_link": LINK}
    monkeypatch.setattr(workiq_chat, "FoundryToolbox", toolbox_with(error=RuntimeError("tools/list failed")))
    assert json.loads(await run(delegate, task="q"))["status"] == "WORKIQ_CHAT_UNAVAILABLE"
    monkeypatch.setattr(workiq_chat, "FoundryToolbox", toolbox_with(Function(result='{"isError": true}')))
    assert json.loads(await run(delegate, task="q"))["status"] == "WORKIQ_CHAT_UNAVAILABLE"
    monkeypatch.setattr(workiq_chat, "FoundryToolbox", toolbox_with())
    assert "no SendMessage" in json.loads(await run(delegate, task="q"))["reason"]
    assert workiq_chat.chat_tools(object(), "") == []
