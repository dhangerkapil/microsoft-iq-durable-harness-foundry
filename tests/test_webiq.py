"""Web IQ: public context only; the boundary guard keeps client data inside Microsoft 365 and Azure."""

import json

import pytest

from advisor_team import webiq
from advisor_team.webiq import BoundaryGuard, shape_results

GUARD = BoundaryGuard(["Michael Scott", "Pam Beesly", "Alex Morgan"])


@pytest.mark.parametrize("text", [
    "NVIDIA AI data center outlook", "NVDA", "semiconductor sector news this week", "529 plan rules 2026",
    "Fed rate decision October 2026", "Vera Rubin rack MGX 1.2 250 kW",
])
def test_public_topics_pass(text):
    assert GUARD.check_text(text) is None


@pytest.mark.parametrize(("text", "why"), [
    ("Michael Scott NVDA position", "names 'Michael Scott'"),
    ("michael scott's house closing", "names 'Michael Scott'"),
    ("email michael.scott@scott-family.example about NVDA", "email address"),
    ("trim $400,000 of NVDA", "amount"),
    ("portfolio worth 2,926,762.55", "amount"),
    ("down payment 400k", "amount"),
    ("account 12345678 NVDA", "account-like number"),
])
def test_client_data_is_blocked(text, why):
    reason = GUARD.check_text(text)
    assert reason and why in reason


@pytest.mark.parametrize(("url", "ok"), [
    ("https://investor.nvidia.com/home/", True),
    ("https://www.reuters.com/technology/nvidia-2026-10-13/", True),
    ("https://contoso.sharepoint.com/sites/research/IPS.docx", False),
    ("https://contoso-my.sharepoint.com/personal/x/Documents/a.pdf", False),
    ("https://outlook.office.com/mail/inbox", False),
    ("https://graph.microsoft.com/v1.0/me/messages", False),
    ("ftp://example.com/file", False),
    ("https://user:pw@example.com/", False),
    ("https://example.com/search?q=Michael+Scott", False),
])
def test_urls(url, ok):
    assert (GUARD.check_url(url) is None) == ok


def test_terms_come_from_environment(monkeypatch):
    monkeypatch.setenv("WEB_BOUNDARY_TERMS", "Jim Halpert, Dwight Schrute ,x")
    guard = BoundaryGuard()
    assert guard.terms == ["Dwight Schrute", "Jim Halpert"]
    assert guard.check_text("Dwight Schrute beets") is not None


def test_shape_news_web_finance():
    news = json.dumps({"newsResults": [{"title": "NVIDIA opens designs", "url": "https://e.com/a", "content": "<P>x  y</P>",
                                        "datePublished": "2026-10-13T06:57:00Z", "provider": [{"name": "ET"}]}]})
    assert shape_results(news, "news", 5) == [{"title": "NVIDIA opens designs", "url": "https://e.com/a",
                                               "published": "2026-10-13T06:57:00Z", "provider": "ET", "excerpt": "x y"}]
    web = json.dumps({"webResults": [{"title": "Home", "url": "https://i.com", "content": "<H1>Hi</H1>",
                                      "crawledAt": "2026-10-17"}], "traceId": "t"})
    assert shape_results(web, "web", 5)[0] == {"title": "Home", "url": "https://i.com", "crawled_at": "2026-10-17",
                                               "excerpt": "Hi"}
    finance = json.dumps({"financeResults": [{"title": "NVIDIA", "url": "https://m.com", "data": {"instrument": {
        "symbol": "NVDA", "price": 214.13, "currency": "USD", "lastTradedAt": "2026-10-16T16:00:00-04:00"}}}]})
    assert shape_results(finance, "finance", 5)[0]["price"] == 214.13


async def run(function, **arguments) -> str:
    result = await function.invoke(arguments=arguments)
    if isinstance(result, list):
        return "".join(getattr(item, "text", "") or "" for item in result)
    return result if isinstance(result, str) else str(result)


class FakeFunction:
    def __init__(self, name, payload):
        self.name, self.payload, self.calls = name, payload, []

    async def invoke(self, arguments):
        self.calls.append(arguments)
        return self.payload


def fake_toolbox(functions):
    class Box:
        def __init__(self, *args, **kwargs):
            self.functions = functions

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    return Box


async def test_search_blocks_before_any_call(monkeypatch):
    news = FakeFunction("webiq___news", json.dumps({"newsResults": []}))
    monkeypatch.setattr(webiq, "FoundryToolbox", fake_toolbox([news]))
    search, _ = webiq.web_tools(object(), "https://x/toolboxes/advisor-webiq/mcp", GUARD)
    out = json.loads(await run(search, query="Michael Scott NVDA weight", kind="news"))
    assert out["status"] == "WEB_QUERY_BLOCKED" and news.calls == []


async def test_search_and_read(monkeypatch):
    news = FakeFunction("webiq___news", json.dumps({"newsResults": [{"title": "T", "url": "https://e.com/a",
                                                                     "content": "c"}]}))
    browse = FakeFunction("webiq___browse", json.dumps({"url": "https://e.com/a", "title": "T", "content": "<P>body</P>",
                                                        "crawledAt": "2026-10-17"}))
    monkeypatch.setattr(webiq, "FoundryToolbox", fake_toolbox([news, browse]))
    search, read = webiq.web_tools(object(), "https://x/toolboxes/advisor-webiq/mcp", GUARD)
    found = json.loads(await run(search, query="NVIDIA AI data center outlook", max_results=20))
    assert found["results"][0]["url"] == "https://e.com/a" and news.calls == [
        {"query": "NVIDIA AI data center outlook", "maxResults": 8}]
    page = json.loads(await run(read, url="https://e.com/a"))
    assert page["content"] == "body" and page["crawled_at"] == "2026-10-17"
    assert json.loads(await run(read, url="https://contoso.sharepoint.com/x"))["status"] == "WEB_QUERY_BLOCKED"


async def test_no_toolbox_means_no_tools():
    assert webiq.web_tools(object(), None) == []


async def test_outage_degrades(monkeypatch):
    class Broken:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            raise RuntimeError("tools/list failed")

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(webiq, "FoundryToolbox", Broken)
    search, _ = webiq.web_tools(object(), "https://x", GUARD)
    assert json.loads(await run(search, query="NVDA"))["status"] == "WEB_UNAVAILABLE"


class FlakyFunction(FakeFunction):
    """Fails with the given errors first (an exception, or an MCP error result), then returns the payload."""

    def __init__(self, name, payload, failures):
        super().__init__(name, payload)
        self.failures = list(failures)

    async def invoke(self, arguments):
        self.calls.append(arguments)
        if self.failures:
            failure = self.failures.pop(0)
            if isinstance(failure, Exception):
                raise failure
            return failure
        return self.payload


SERVER_ERROR = "[Failed Dependency] the dependency call to the MCP server failed with status code 500"
NEWS = json.dumps({"newsResults": [{"title": "T", "url": "https://e.com/a", "content": "c"}]})


@pytest.mark.parametrize("failure", [
    RuntimeError(SERVER_ERROR),
    json.dumps({"isError": True, "content": SERVER_ERROR}),
])
async def test_transient_web_iq_error_is_retried(monkeypatch, failure):
    monkeypatch.setattr(webiq, "RETRY_DELAY", 0)
    news = FlakyFunction("webiq___news", NEWS, [failure])
    monkeypatch.setattr(webiq, "FoundryToolbox", fake_toolbox([news]))
    search, _ = webiq.web_tools(object(), "https://x/toolboxes/advisor-webiq/mcp", GUARD)
    found = json.loads(await run(search, query="NVIDIA AI data center outlook"))
    assert found["results"][0]["url"] == "https://e.com/a" and len(news.calls) == 2


async def test_persistent_web_iq_outage_gives_up_after_three_attempts(monkeypatch):
    monkeypatch.setattr(webiq, "RETRY_DELAY", 0)
    news = FlakyFunction("webiq___news", NEWS, [RuntimeError(SERVER_ERROR)] * 5)
    monkeypatch.setattr(webiq, "FoundryToolbox", fake_toolbox([news]))
    search, _ = webiq.web_tools(object(), "https://x/toolboxes/advisor-webiq/mcp", GUARD)
    out = json.loads(await run(search, query="NVDA"))
    assert out["status"] == "WEB_UNAVAILABLE" and len(news.calls) == 3


async def test_other_web_iq_errors_are_not_retried(monkeypatch):
    monkeypatch.setattr(webiq, "RETRY_DELAY", 0)
    news = FlakyFunction("webiq___news", NEWS, [ValueError("invalid argument: maxResults")])
    monkeypatch.setattr(webiq, "FoundryToolbox", fake_toolbox([news]))
    search, _ = webiq.web_tools(object(), "https://x/toolboxes/advisor-webiq/mcp", GUARD)
    out = json.loads(await run(search, query="NVDA"))
    assert out["status"] == "WEB_UNAVAILABLE" and len(news.calls) == 1
