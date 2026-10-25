"""Foundry IQ: the firm library through the knowledge base's MCP tool, shaped for citation."""

import json

from advisor_team import knowledge
from advisor_team.knowledge import shape

RAW = json.dumps([
    {"ref_id": 0, "title": "3. Review triggers and timeline", "terms": "CP-104 Concentrated Position Policy",
     "content": "CP-104 Concentrated Position Policy. 3. Review triggers\n3.1 A position   that closes above"},
    {"ref_id": 1, "title": "4. Funding order", "terms": "OPS-220 Large Withdrawal and Wire Procedure",
     "content": "4.1 Use cash above the reserve first."},
])


def test_shape_keeps_document_section_and_text():
    assert shape(RAW) == [
        {"ref": 0, "document": "CP-104 Concentrated Position Policy", "section": "3. Review triggers and timeline",
         "text": "CP-104 Concentrated Position Policy. 3. Review triggers 3.1 A position that closes above"},
        {"ref": 1, "document": "OPS-220 Large Withdrawal and Wire Procedure", "section": "4. Funding order",
         "text": "4.1 Use cash above the reserve first."},
    ]
    assert shape("not json") == [{"text": "not json"}] and shape("") == []


class Function:
    name = "firm_knowledge___knowledge_base_retrieve"

    def __init__(self):
        self.calls = []

    async def invoke(self, arguments):
        self.calls.append(arguments)
        return RAW


def toolbox_with(function):
    class Box:
        def __init__(self, *args, **kwargs):
            self.functions = [function]

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    return Box


async def run(tool, **arguments):
    result = await tool.invoke(arguments=arguments)
    if isinstance(result, list):
        return "".join(getattr(item, "text", "") or "" for item in result)
    return result if isinstance(result, str) else str(result)


async def test_search_firm_knowledge_sends_one_question(monkeypatch):
    function = Function()
    monkeypatch.setattr(knowledge, "FoundryToolbox", toolbox_with(function))
    (search,) = knowledge.knowledge_tools(object(), "https://x/toolboxes/advisor-foundryiq/mcp")
    out = json.loads(await run(search, question="What  must happen before a large wire? " + "x" * 500))
    assert function.calls[0]["query_variants"][0].startswith("What must happen before a large wire?")
    assert len(function.calls[0]["query_variants"][0]) == 400
    assert out["source"].startswith("Foundry IQ") and out["passages"][1]["section"] == "4. Funding order"


async def test_outage_and_missing_toolbox(monkeypatch):
    class Broken:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            raise RuntimeError("tools/list failed")

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(knowledge, "FoundryToolbox", Broken)
    (search,) = knowledge.knowledge_tools(object(), "https://x")
    assert json.loads(await run(search, question="q"))["status"] == "FIRM_KNOWLEDGE_UNAVAILABLE"
    assert knowledge.knowledge_tools(object(), "") == []
