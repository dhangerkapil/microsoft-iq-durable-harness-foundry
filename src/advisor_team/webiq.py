"""Web IQ: the public web, news and market data, for public context only.

Microsoft Web IQ is not a Microsoft Azure online service: anything the agent sends to it leaves the Azure compliance
and geo boundary, and the Microsoft Data Protection Addendum doesn't apply. So the agent sends only public topics
(tickers, companies, sectors, market events), and a guard in code blocks any query or URL that carries client data:
a client or staff name, an email address, a dollar amount or an account-like number, or a Microsoft 365 link. The
block goes back to the model, which rephrases the query as a public topic, and shows in the trace and the X-ray.

The toolbox is connected only when a web tool runs, so a Web IQ outage can never block a Work IQ request. A transient
Web IQ server error is retried twice, briefly, before the tool reports the public web as unavailable.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from datetime import UTC, datetime
from typing import Annotated, Any, Literal
from urllib.parse import unquote_plus, urlsplit

from agent_framework import tool
from agent_framework_foundry_hosting import FoundryToolbox
from azure.core.credentials_async import AsyncTokenCredential

from .advisor import result_text, tracer

logger = logging.getLogger(__name__)
WEB_TOOL_PREFIX = "webiq___"
SEARCH_TOOL = "search_public_web"
READ_TOOL = "read_public_web_page"
SOURCE = "Web IQ (public web; outside the Microsoft 365 and Azure boundary)"

EMAIL = re.compile(r"[\w.+-]+@[\w-]+(\.[\w-]+)+")
AMOUNT = re.compile(r"[$€£]\s?\d|\b\d{1,3}(,\d{3})+(\.\d+)?\b|\b\d+(\.\d+)?\s?(k|mm?|bn|million|thousand)\b",
                    re.IGNORECASE)
ACCOUNT = re.compile(r"\b\d{6,}\b")
# Hosts that only serve the tenant's own content: sending their links to the public web would leak them.
PRIVATE_HOSTS = ("sharepoint.com", "sharepoint-df.com", "office.com", "office365.com", "office.net", "outlook.com",
                 "live.com", "microsoft365.com", "cloud.microsoft", "onmicrosoft.com", "teams.microsoft.com",
                 "graph.microsoft.com", "azure.com", "windows.net", "fabric.microsoft.com", "powerbi.com")


class BoundaryGuard:
    """Decides whether text may leave the boundary for Web IQ. Terms are names that must never leave it."""

    def __init__(self, terms: list[str] | None = None) -> None:
        if terms is None:
            terms = [t for t in os.environ.get("WEB_BOUNDARY_TERMS", "").split(",")]
        self.terms = sorted({t.strip() for t in terms if len(t.strip()) >= 3}, key=len, reverse=True)
        self.patterns = [(t, re.compile(rf"(?<!\w){re.escape(t)}(?!\w)", re.IGNORECASE)) for t in self.terms]

    def check_text(self, text: str) -> str | None:
        for term, pattern in self.patterns:
            if pattern.search(text):
                return f"it names {term!r}, a client or staff member"
        if EMAIL.search(text):
            return "it contains an email address"
        if AMOUNT.search(text):
            return "it contains an amount"
        if ACCOUNT.search(text):
            return "it contains an account-like number"
        return None

    def check_url(self, url: str) -> str | None:
        parts = urlsplit(url.strip())
        if parts.scheme not in ("http", "https") or not parts.hostname or parts.username or parts.password:
            return "it is not a plain public http(s) link"
        host = parts.hostname.lower()
        if any(host == private or host.endswith("." + private) for private in PRIVATE_HOSTS):
            return f"{host} serves the firm's own Microsoft 365 or Azure content, which must not go to the public web"
        return self.check_text(unquote_plus(f"{parts.path} {parts.query} {parts.fragment}"))


def blocked(reason: str, what: str) -> str:
    return json.dumps({
        "status": "WEB_QUERY_BLOCKED",
        "reason": f"The {what} was not sent to Web IQ because {reason}.",
        "instruction": ("Web IQ is outside the Microsoft 365 and Azure boundary. Send only public topics: tickers, "
                        "company names, sectors, market events. Never client names, amounts, account details, email "
                        "addresses or Microsoft 365 content or links. Rephrase as a public topic and try once more, "
                        "or continue without the web."),
    })


def unavailable(reason: str) -> str:
    return json.dumps({
        "status": "WEB_UNAVAILABLE",
        "reason": reason,
        "instruction": "Do not retry now. Say the public web was not checked, and continue with the other sources.",
    })


TAG = re.compile(r"<[^>]{1,40}>")
# Web IQ (preview) sometimes answers a call with a server error and the same call succeeds moments later.
TRANSIENT = re.compile(r"status code (?:429|5\d\d)|Failed Dependency|Internal Server Error|Service Unavailable|"
                       r"Bad Gateway|Gateway Timeout|Too Many Requests", re.IGNORECASE)
ATTEMPTS = 3
RETRY_DELAY = 0.6


def trimmed(value: Any, limit: int) -> str:
    text = " ".join(TAG.sub(" ", str(value or "")).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def shape_results(raw: str, kind: str, limit: int) -> list[dict[str, Any]]:
    """The fields the model needs from a Web IQ result, with long page text cut down."""
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return [{"content": trimmed(raw, 1_500)}] if raw else []
    lists = [v for v in data.values() if isinstance(v, list)] if isinstance(data, dict) else []
    items = next((v for v in lists if v and isinstance(v[0], dict)), [])
    shaped = []
    for item in items[:limit]:
        if kind == "finance" and isinstance(item.get("data"), dict):
            instrument = item["data"].get("instrument") or {}
            shaped.append({k: v for k, v in {
                "title": item.get("title"), "url": item.get("url"), "symbol": instrument.get("symbol"),
                "price": instrument.get("price"), "currency": instrument.get("currency"),
                "last_traded_at": instrument.get("lastTradedAt"), "change_percent": instrument.get("changePercent"),
                "high_52_week": instrument.get("price52WeekHigh"), "low_52_week": instrument.get("price52WeekLow"),
                "market_cap": instrument.get("marketCap"), "data_provider": instrument.get("primaryDataProvider"),
            }.items() if v is not None})
            continue
        published = next((item[k] for k in ("datePublished", "publishedAt", "published", "date") if item.get(k)), None)
        provider = item.get("provider") or item.get("source") or item.get("siteName")
        if isinstance(provider, list):
            provider = ", ".join(str(p.get("name", p)) if isinstance(p, dict) else str(p) for p in provider)
        elif isinstance(provider, dict):
            provider = provider.get("name")
        shaped.append({k: v for k, v in {
            "title": item.get("title") or item.get("name"), "url": item.get("url"), "published": published,
            "crawled_at": item.get("crawledAt") or item.get("dateLastCrawled"), "provider": provider,
            "excerpt": trimmed(item.get("content") or item.get("snippet") or item.get("description"), 900),
        }.items() if v})
    return shaped


def web_tools(credential: AsyncTokenCredential, url: str | None, guard: BoundaryGuard | None = None,
              *, timeout_seconds: float = 60.0) -> list:
    """The two Web IQ tools, or none when the deployment has no Web IQ toolbox."""
    if not url:
        return []
    guard = guard or BoundaryGuard()

    async def call(name: str, arguments: dict[str, Any]) -> tuple[str | None, str | None]:
        try:
            async with asyncio.timeout(timeout_seconds):
                async with FoundryToolbox(credential, url=url, load_prompts=False, timeout=timeout_seconds) as box:
                    function = next((f for f in box.functions if f.name == WEB_TOOL_PREFIX + name), None)
                    if function is None:
                        return None, f"the Web IQ toolbox has no {name} tool"
                    for attempt in range(1, ATTEMPTS + 1):
                        try:
                            result = await function.invoke(arguments=arguments)
                        except Exception as exc:
                            if attempt == ATTEMPTS or not TRANSIENT.search(str(exc)):
                                raise
                            logger.warning("Web IQ %s: transient error on attempt %d; retrying", name, attempt)
                            await asyncio.sleep(RETRY_DELAY * attempt)
                            continue
                        text = result_text(result)
                        if '"iserror": true' not in text[:300].lower():
                            return text, None
                        if attempt == ATTEMPTS or not TRANSIENT.search(text[:1_000]):
                            return None, trimmed(text, 200)
                        logger.warning("Web IQ %s: transient error result on attempt %d; retrying", name, attempt)
                        await asyncio.sleep(RETRY_DELAY * attempt)
        except TimeoutError:
            return None, f"no answer within {int(timeout_seconds)} seconds"
        except Exception as exc:
            return None, f"{type(exc).__name__}: {str(exc)[:160]}"
        return None, "no answer"

    @tool(name=SEARCH_TOOL, approval_mode="never_require")
    async def search_public_web(
        query: Annotated[str, "A public topic only, e.g. 'NVIDIA AI data center outlook' or 'NVDA'. Never client "
                              "names, amounts, account details or Microsoft 365 content."],
        kind: Annotated[Literal["news", "web", "finance"], "news (default): recent articles; web: pages; finance: "
                                                           "a public quote for a ticker or fund."] = "news",
        max_results: Annotated[int, "1 to 8 results."] = 5,
    ) -> str:
        """Web IQ: public web pages, recent news or a public market quote, with links. Use for public context
        (company and market news, public filings and prices), never for client facts. Web IQ is outside the
        Microsoft 365 and Azure boundary, so a guard blocks queries that carry client data."""
        with tracer.start_as_current_span("advisor.webiq.search") as span:
            span.set_attribute("advisor.webiq.kind", kind)
            reason = guard.check_text(query)
            if reason:
                span.set_attribute("advisor.webiq.blocked", reason)
                logger.warning("Web IQ query blocked at the boundary: %s", reason)
                return blocked(reason, "query")
            limit = max(1, min(int(max_results or 5), 8))
            arguments: dict[str, Any] = {"query": query}
            if kind != "finance":
                arguments["maxResults"] = limit
            text, error = await call(kind, arguments)
            if error:
                span.set_attribute("advisor.webiq.failed", error[:200])
                return unavailable(error)
            results = shape_results(text or "", kind, limit)
            span.set_attribute("advisor.webiq.results", len(results))
            return json.dumps({
                "source": SOURCE, "kind": kind, "query": query,
                "retrieved_at": datetime.now(UTC).isoformat(timespec="seconds"), "results": results,
                "note": ("Public, real-world information. Cite each item with its link and date. Client positions and "
                         "valuations come only from the portfolio system, never from web prices."),
            })

    @tool(name=READ_TOOL, approval_mode="never_require")
    async def read_public_web_page(
        url: Annotated[str, "A public http(s) link returned by search_public_web, such as a news article or a "
                            "company's investor-relations page."],
    ) -> str:
        """Web IQ: reads one public web page and returns its text. Only public links; never Microsoft 365 or
        Azure links, which a guard blocks."""
        with tracer.start_as_current_span("advisor.webiq.browse") as span:
            reason = guard.check_url(url)
            if reason:
                span.set_attribute("advisor.webiq.blocked", reason)
                logger.warning("Web IQ page read blocked at the boundary: %s", reason)
                return blocked(reason, "link")
            text, error = await call("browse", {"url": url, "maxLength": 12_000})
            if error:
                span.set_attribute("advisor.webiq.failed", error[:200])
                return unavailable(error)
            try:
                data = json.loads(text or "")
            except ValueError:
                data = {"content": text}
            if isinstance(data, dict):
                page = next((v for v in data.values() if isinstance(v, dict) and ("content" in v or "url" in v)), data)
            else:
                page = {"content": data}
            return json.dumps({
                "source": SOURCE, "url": page.get("url") or url, "title": page.get("title"),
                "crawled_at": page.get("crawledAt"), "retrieved_at": datetime.now(UTC).isoformat(timespec="seconds"),
                "content": trimmed(page.get("content") or page.get("text") or page, 10_000),
            })

    return [search_public_web, read_public_web_page]
