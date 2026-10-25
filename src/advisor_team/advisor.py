"""Advisor harness features: client memory in the Foundry durable state store, deterministic
portfolio arithmetic, source resilience with a demo fault switch, and the evidence loop."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import socket
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal, Protocol
from uuid import uuid4

from agent_framework import Content, FunctionInvocationContext, FunctionMiddleware, tool
from azure.ai.agentserver.core.storage import FoundryStorageConflictError, FoundryStoragePreconditionError
from opentelemetry import trace

from .naming import store_name

logger = logging.getLogger(__name__)
tracer = trace.get_tracer("advisor-team")
FABRIC_PREFIX = "fabric_portfolio___"
FABRIC_TOOLS = ("query_portfolio_data",)
WORKIQ_PREFIXES = ("workiq", "work_iq")
# Harness tools whose evidence comes from Work IQ calls they make themselves.
WORKIQ_TOOLS = ("run_durable_meeting_pack", "inspect_document", "read_chart", "probe_workiq_policy",
                "list_client_documents")
BRIEF_TOOL = "record_brief"
BRIEF_SKILL = "client-meeting-prep"
BASELINE_TOOL = "record_discussion_baseline"


def skill_name(arguments: Any) -> str | None:
    values = arguments.model_dump() if hasattr(arguments, "model_dump") else dict(arguments or {})
    return values.get("skill_name")
MEMORY_STORE_NAME = store_name("client-memory")
NOT_ADVICE = "Arithmetic on the supplied figures only; not investment, tax or legal advice."


def client_key(client_name: str) -> str:
    key = re.sub(r"[^a-z0-9]+", "-", client_name.strip().lower()).strip("-")
    if not key:
        raise ValueError("Provide the client's name.")
    return key[:80]


def now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


# The one identity every durable writer records, so stored items show which process wrote them.
PROCESS = {"id": f"{socket.gethostname()}-{os.getpid()}-{uuid4().hex[:6]}", "started_at": now_iso()}
PROCESS_CLOCK_START = time.monotonic()


def process_identity() -> dict:
    return {**PROCESS, "uptime_seconds": int(time.monotonic() - PROCESS_CLOCK_START)}


def annotate(**attributes: Any) -> None:
    span = trace.get_current_span()
    for name, value in attributes.items():
        if value is not None:
            span.set_attribute(f"advisor.{name}", value if isinstance(value, (bool, int, float, str)) else str(value))


# --- Durable client memory ------------------------------------------------------------------------
class ItemStore(Protocol):
    """The FoundryStateStore item API this harness uses (azure-ai-agentserver-core storage)."""

    async def get_item(self, key: str) -> Any: ...
    async def set_item(self, key: str, value: dict, *, tags: dict[str, str] | None = None,
                       if_match: str | None = None) -> Any: ...
    async def create_item(self, key: str, value: dict, *, tags: dict[str, str] | None = None) -> Any: ...
    async def list_keys(self, *, tags: dict[str, str] | None = None, limit: int | None = None,
                        after: str | None = None) -> Any: ...
    async def get(self) -> Any: ...


MAX_ITEM_BYTES = 1024 * 1024


class InMemoryItemStore:
    """Local and test stand-in for FoundryStateStore. It mirrors the SDK's item API and the service rules: an ETag
    that changes on every write, If-Match preconditions (412), create-only writes (409), 1-128 character keys, 1 MB
    values, and newest-first key pages of 1-100 filtered by tags (AND). It reports the descriptor of a store created
    with user isolation but keeps a single partition: local runs have no platform caller identity."""

    @dataclass
    class Item:
        key: str
        value: dict
        etag: str = "local"
        tags: dict[str, str] | None = None
        created_at: int = 0
        updated_at: int = 0
        id: str = ""

    @dataclass
    class Key:
        key: str
        etag: str
        tags: dict[str, str] | None
        created_at: int
        updated_at: int
        id: str

    @dataclass
    class KeyPage:
        keys: list[InMemoryItemStore.Key]
        first_id: str | None = None
        last_id: str | None = None
        has_more: bool = False

    @dataclass
    class Store:
        name: str
        user_isolation: bool = True
        item_ttl_seconds: int = 30 * 24 * 60 * 60

    @dataclass
    class Deleted:
        key: str
        id: str | None
        deleted: bool = True

    def __init__(self, name: str = "local") -> None:
        self.name = name
        self.items: dict[str, InMemoryItemStore.Item] = {}
        self.writes = 0

    async def get(self) -> Any:
        return InMemoryItemStore.Store(self.name)

    async def get_item(self, key: str, *, call_id: str | None = None) -> Any:
        item = self.items.get(key)
        return None if item is None else replace(item, value=json.loads(json.dumps(item.value)))

    async def set_item(self, key: str, value: dict, *, tags: dict[str, str] | None = None, if_match: str | None = None,
                       require_exists: bool = False, call_id: str | None = None) -> Any:
        if if_match is not None and require_exists:
            raise ValueError("if_match and require_exists are mutually exclusive")
        self._check_precondition(key, "*" if require_exists else if_match)
        return self._write(key, value, tags)

    async def create_item(self, key: str, value: dict, *, tags: dict[str, str] | None = None,
                          call_id: str | None = None) -> Any:
        if key in self.items:
            raise FoundryStorageConflictError(f"State store item {key!r} already exists.", status_code=409)
        return self._write(key, value, tags)

    async def delete_item(self, key: str, *, if_match: str | None = None, call_id: str | None = None) -> Any:
        self._check_precondition(key, if_match)
        item = self.items.pop(key, None)
        return InMemoryItemStore.Deleted(key, item.id if item is not None else None)

    async def list_keys(self, *, tags: dict[str, str] | None = None, limit: int | None = None, after: str | None = None,
                        before: str | None = None, order: Literal["asc", "desc"] = "desc",
                        call_id: str | None = None) -> Any:
        if after is not None and before is not None:
            raise ValueError("after and before are mutually exclusive")
        size = 20 if limit is None else limit
        if not 1 <= size <= 100:
            raise ValueError("limit must be between 1 and 100")
        items = sorted((item for item in self.items.values()
                        if all((item.tags or {}).get(name) == value for name, value in (tags or {}).items())),
                       key=lambda item: (item.created_at, item.id), reverse=order == "desc")
        ids = [item.id for item in items]
        if after is not None:
            items = items[ids.index(after) + 1:] if after in ids else []
        elif before is not None:
            items = items[:ids.index(before)] if before in ids else []
        keys = [self._key(item) for item in items[:size]]
        return InMemoryItemStore.KeyPage(keys, keys[0].id if keys else None, keys[-1].id if keys else None,
                                         len(items) > size)

    def _check_precondition(self, key: str, if_match: str | None) -> None:
        current = self.items.get(key)
        if if_match is not None and (current is None or if_match not in ("*", current.etag)):
            raise FoundryStoragePreconditionError(
                f"ETag precondition failed for state store item {key!r}.", status_code=412,
                current_etag=current.etag if current is not None else None)

    def _write(self, key: str, value: dict, tags: dict[str, str] | None) -> Any:
        if not 1 <= len(key) <= 128:
            raise ValueError("An item key must be 1-128 characters.")
        payload = json.dumps(value)
        if len(payload.encode()) > MAX_ITEM_BYTES:
            raise ValueError("An item value is limited to 1 MB of serialized JSON.")
        self.writes += 1
        current = self.items.get(key)
        now = int(time.time())
        self.items[key] = InMemoryItemStore.Item(
            key, json.loads(payload), f'"mem-{self.writes}"', dict(tags) if tags else None,
            current.created_at if current is not None else now, now,
            current.id if current is not None else f"it_{self.writes:08d}")
        return self._key(self.items[key])

    @staticmethod
    def _key(item: InMemoryItemStore.Item) -> InMemoryItemStore.Key:
        return InMemoryItemStore.Key(item.key, item.etag, item.tags, item.created_at, item.updated_at, item.id)


_store_lock = asyncio.Lock()
_stores: dict[str, ItemStore] = {}
_store_credential: Any = None


async def get_state_store(name: str, project_endpoint: str, hosted: bool, description: str = "", *,
                          create: bool = True) -> ItemStore:
    """User-isolated Foundry state stores, one client per name for the process lifetime. create=False binds to a
    store another component owns (the MAF hosting library) without creating it; reads then report it missing."""
    global _store_credential
    async with _store_lock:
        if name not in _stores:
            if hosted:
                from azure.ai.agentserver.core.storage import FoundryStateStore
                from azure.identity.aio import ManagedIdentityCredential

                # A process-lifetime credential: per-request credentials close after each request.
                if _store_credential is None:
                    _store_credential = ManagedIdentityCredential(
                        client_id=os.environ["FOUNDRY_AGENT_INSTANCE_CLIENT_ID"])
                if create:
                    _stores[name] = HedgedStore(await FoundryStateStore.get_or_create(
                        name, _store_credential, project_endpoint, user_isolation=True, description=description))
                else:
                    _stores[name] = HedgedStore(
                        FoundryStateStore(name, _store_credential, project_endpoint, user_isolation=True))
            else:
                _stores[name] = InMemoryItemStore(name)
        return _stores[name]


class HedgedStore:
    """Bounds the state-store calls that are safe to repeat. The preview service sometimes holds a call for up to two
    minutes and then answers 408, while a fresh request answers in milliseconds (seen 2026-10-17: a new session's
    history lookup waited 120 s before its first model call, and history writes waited 61-120 s). Reads, and writes
    that replace an item unconditionally, get HEDGE_SECONDS and then one retry without a limit. Conditional and
    create-only writes pass through untouched: repeating one that was applied would fail with 412 or 409."""

    def __init__(self, store: Any) -> None:
        self._store = store

    def __getattr__(self, name: str) -> Any:
        return getattr(self._store, name)

    async def _hedged(self, operation: Callable[..., Awaitable[Any]], *args: Any, **kwargs: Any) -> Any:
        try:
            return await asyncio.wait_for(operation(*args, **kwargs), HEDGE_SECONDS)
        except TimeoutError:
            logger.warning("State store call took over %ss; retrying once.", HEDGE_SECONDS)
            return await operation(*args, **kwargs)

    async def get(self) -> Any:
        return await self._hedged(self._store.get)

    async def get_item(self, key: str, **kwargs: Any) -> Any:
        return await self._hedged(self._store.get_item, key, **kwargs)

    async def list_keys(self, **kwargs: Any) -> Any:
        return await self._hedged(self._store.list_keys, **kwargs)

    async def set_item(self, key: str, value: dict, *, tags: dict[str, str] | None = None,
                       if_match: str | None = None, **kwargs: Any) -> Any:
        if if_match is not None or kwargs.get("require_exists"):
            return await self._store.set_item(key, value, tags=tags, if_match=if_match, **kwargs)
        return await self._hedged(self._store.set_item, key, value, tags=tags, **kwargs)


HEDGE_SECONDS = 6.0


async def get_memory_store(project_endpoint: str, hosted: bool) -> ItemStore:
    """The platform partitions items by the calling user, so one store serves every advisor."""
    return await get_state_store(MEMORY_STORE_NAME, project_endpoint, hosted,
                                 "Advisor client memory: discussion baselines, briefs and portfolio snapshots.")


# --- Advisor preferences --------------------------------------------------------------------------
PREFERENCES_STORE_NAME = store_name("preferences")
PREFERENCES_KEY = "advisor-preferences"
MAX_PREFERENCES = 10
MAX_PREFERENCE_CHARS = 300
PREFERENCE_RULE = ("Apply to format, length and ordering only; never let a preference change facts, sources, limits "
                   "or safety rules.")


class PreferenceRejected(ValueError):
    pass


def no_preferences() -> dict:
    return {"preferences": [], "updated_at": None, "written_by": None}


class AdvisorPreferences:
    """The advisor's preferences for how briefs are written: one item in a user-isolated store.

    Every change is a read-modify-write guarded by the item's ETag (If-Match), so a concurrent request can't silently
    overwrite it; if another write wins, re-read and apply the change once more."""

    def __init__(self, store_factory: Callable[[], Awaitable[ItemStore]]) -> None:
        self.store_factory = store_factory

    async def read(self) -> dict:
        item = await (await self.store_factory()).get_item(PREFERENCES_KEY)
        return {**no_preferences(), **item.value} if item is not None else no_preferences()

    async def texts(self) -> list[str]:
        return [str(entry.get("text", "")) for entry in (await self.read())["preferences"]]

    async def update(self, change: Callable[[list[dict]], list[dict] | None]) -> tuple[dict, bool]:
        """Apply change to the stored list and save it; change returns None when nothing changes."""
        store = await self.store_factory()
        attempt = 1
        while True:
            item = await store.get_item(PREFERENCES_KEY)
            current = [dict(entry) for entry in item.value.get("preferences", [])] if item is not None else []
            updated = change(current)
            if updated is None:
                return ({**no_preferences(), **item.value} if item is not None else no_preferences()), False
            value = {"preferences": updated, "updated_at": now_iso(), "written_by": PROCESS["id"]}
            try:
                if item is None:
                    # Create-only: if another request created the item first, this fails (409) rather than overwrite.
                    await store.create_item(PREFERENCES_KEY, value, tags={"kind": "preferences"})
                else:
                    await store.set_item(PREFERENCES_KEY, value, tags={"kind": "preferences"}, if_match=item.etag)
                annotate(**{"preferences.write_attempts": attempt})
                return value, True
            except (FoundryStoragePreconditionError, FoundryStorageConflictError):
                if attempt == 2:
                    raise
                attempt += 1
                logger.info("Advisor preferences changed concurrently; re-reading and retrying once.")

    @staticmethod
    def added(text: str, current: list[dict]) -> list[dict] | None:
        if any(entry.get("text") == text for entry in current):
            return None
        if len(current) >= MAX_PREFERENCES:
            raise PreferenceRejected(f"{MAX_PREFERENCES} preferences are already saved; clear them first.")
        return [*current, {"text": text, "saved_at": now_iso(), "written_by": PROCESS["id"]}]

    async def apply(self, action: Literal["show", "add", "clear"], preference: str | None = None) -> dict:
        outcome = "cleared" if action == "clear" else "saved"
        result: dict[str, Any] = {"action": action}
        try:
            if action == "show":
                state = await self.read()
            elif action == "clear":
                state, result["cleared"] = await self.update(lambda current: [] if current else None)
                if not result["cleared"]:
                    result["note"] = "Nothing to clear."
            else:
                text = (preference or "").strip()
                if not 1 <= len(text) <= MAX_PREFERENCE_CHARS:
                    raise PreferenceRejected(f"Give one preference of 1-{MAX_PREFERENCE_CHARS} characters.")
                state, result["saved"] = await self.update(lambda current: self.added(text, current))
                if not result["saved"]:
                    result["note"] = "Already saved; nothing changed."
        except PreferenceRejected as exc:
            state = await self.read()
            result.update({outcome: False, "error": str(exc)})
        except (FoundryStoragePreconditionError, FoundryStorageConflictError):
            state = await self.read()
            result.update({outcome: False, "error": "Another request changed the preferences at the same time; "
                                                    "nothing was saved. Try again."})
        entries = state["preferences"]
        return {**result, "preferences": entries, "count": len(entries), "max": MAX_PREFERENCES,
                "updated_at": state["updated_at"], "written_by": state["written_by"],
                "store": f"Foundry state store '{PREFERENCES_STORE_NAME}' (preview), isolated to the signed-in advisor",
                "rule": PREFERENCE_RULE}

    def tool(self):
        preferences = self

        @tool(approval_mode="never_require")
        async def advisor_preferences(
            action: Annotated[Literal["show", "add", "clear"],
                              "show: list the saved preferences. add: save one. clear: remove them all."],
            preference: Annotated[str | None, "For add only: one preference in the advisor's words, up to 300 "
                                              "characters, e.g. 'Put risks and conflicts first.'"] = None,
        ) -> str:
            """The advisor's saved preferences for how you write for them (format, length, ordering), kept durably
            in the Foundry state store for this advisor only. Call add or clear only when the advisor explicitly asks
            to remember or forget a preference."""
            with tracer.start_as_current_span("advisor.preferences"):
                result = await preferences.apply(action, preference)
                annotate(**{"preferences.action": action, "preferences.count": result["count"]})
                return json.dumps(result)

        return advisor_preferences


class ClientMemory:
    def __init__(self, store_factory: Callable[[], Awaitable[ItemStore]],
                 preferences: AdvisorPreferences | None = None) -> None:
        self.store_factory = store_factory
        self.preferences = preferences

    async def read(self, client: str) -> dict:
        store = await self.store_factory()
        key = client_key(client)
        result: dict[str, Any] = {"client": client, "client_key": key}
        for kind in ("last-discussion", "last-brief"):
            item = await store.get_item(f"{key}--{kind}")
            result[kind.replace("-", "_")] = item.value if item is not None else None
        return result

    async def saved_preferences(self) -> list[str] | None:
        if self.preferences is None:
            return []
        try:
            return await self.preferences.texts()
        except Exception:
            logger.warning("Advisor preferences unavailable.", exc_info=True)
            return None

    async def write(self, client: str, kind: str, value: dict) -> dict:
        store = await self.store_factory()
        key = client_key(client)
        value = {**value, "client": client, "saved_at": now_iso(), "written_by": PROCESS["id"]}
        await store.set_item(f"{key}--{kind}", value, tags={"client": key, "kind": kind})
        return value

    async def recall(self, client_name: str) -> dict:
        """This advisor's durable memory for a client with their saved preferences: what recall_client_memory
        returns, as a dict (the brief workflow calls this directly, not through the tool)."""
        with tracer.start_as_current_span("advisor.memory.recall"):
            data, preferences = await asyncio.gather(self.read(client_name), self.saved_preferences())
            data["advisor_preferences"] = preferences
            annotate(**{"memory.client": data["client_key"],
                        "memory.baseline_found": data["last_discussion"] is not None,
                        "memory.brief_found": data["last_brief"] is not None,
                        "memory.preferences": len(preferences or [])})
            if data["last_discussion"] is None:
                data["note"] = ("No discussion baseline is saved. Find the most recent client meeting with Work IQ "
                                "and ask the advisor to confirm it before saving it as the baseline.")
            return data

    def tools(self, ledger: TurnLedger) -> list:
        memory = self

        @tool(approval_mode="never_require")
        async def recall_client_memory(
            client_name: Annotated[str, "Client's full name, for example 'Michael Scott'."],
        ) -> str:
            """Read this advisor's durable memory for a client: the confirmed last-discussion baseline and
            the last brief, including its saved portfolio snapshot, plus the advisor's saved preferences
            (advisor_preferences: format, length and ordering only). Call this first for any client question."""
            return json.dumps(await memory.recall(client_name))

        @tool(approval_mode="never_require")
        async def record_discussion_baseline(
            client_name: Annotated[str, "Client's full name."],
            discussion_at: Annotated[str, "When the discussion happened, ISO 8601 with time zone, e.g. 2026-10-16T11:30:00-04:00."],
            summary: Annotated[str, "Two to five sentences: topics, decisions and open actions."],
            source: Annotated[str, "Where the discussion is recorded, e.g. the meeting title or notes file."],
        ) -> str:
            """Save the confirmed last discussion with a client as the baseline for 'what changed'. Only call this
            after the advisor explicitly confirms which discussion to save."""
            with tracer.start_as_current_span("advisor.memory.save_baseline"):
                datetime.fromisoformat(discussion_at)
                saved = await memory.write(client_name, "last-discussion",
                                           {"discussion_at": discussion_at, "summary": summary, "source": source})
                annotate(**{"memory.client": client_key(client_name), "memory.baseline_at": discussion_at})
                return json.dumps({"saved": True, "baseline": saved})

        @tool(approval_mode="never_require")
        async def record_brief(
            client_name: Annotated[str, "Client's full name."],
            summary: Annotated[str, "Three to six sentences summarizing the brief you produced."],
            portfolio_as_of: Annotated[str, "As-of date of the portfolio figures you used, e.g. 2026-10-19."],
            portfolio_figures: Annotated[str, "Key portfolio figures with units: weights, limits, values, cash, tax budget."],
            sources: Annotated[list[str], "Sources you used: email subjects, meeting titles, file names, data tables."],
        ) -> str:
            """Save the brief you just produced and its portfolio snapshot. The snapshot is the labelled stale
            fallback when Fabric is unavailable next time."""
            with tracer.start_as_current_span("advisor.memory.save_brief"):
                saved = await memory.write(client_name, "last-brief", {
                    "summary": summary, "portfolio_as_of": portfolio_as_of,
                    "portfolio_figures": portfolio_figures, "sources": sources[:20]})
                annotate(**{"memory.client": client_key(client_name), "memory.snapshot_as_of": portfolio_as_of})
                return json.dumps({"saved": True, "saved_at": saved["saved_at"]})

        return [recall_client_memory, record_discussion_baseline, record_brief]


# --- Deterministic portfolio arithmetic -----------------------------------------------------------
def rebalancing_math(
    position_value_usd: float, total_portfolio_value_usd: float, limit_pct: float, price_per_share: float,
    cost_basis_per_share: float, annual_tax_budget_usd: float, realized_gains_ytd_usd: float = 0.0,
    proceeds_withdrawn: bool = False,
) -> dict:
    if min(position_value_usd, total_portfolio_value_usd, price_per_share) <= 0 or not 0 < limit_pct < 100:
        raise ValueError("Values and prices must be positive and the limit between 0 and 100 percent.")
    limit = limit_pct / 100
    weight = position_value_usd / total_portfolio_value_usd
    excess = max(0.0, position_value_usd - limit * total_portfolio_value_usd)
    trim = excess / (1 - limit) if proceeds_withdrawn else excess
    gain_ratio = max(0.0, 1 - cost_basis_per_share / price_per_share)
    realized = trim * gain_ratio
    remaining = max(0.0, annual_tax_budget_usd - realized_gains_ytd_usd)
    max_trim = min(trim, remaining / gain_ratio) if gain_ratio > 0 else trim
    after_total = total_portfolio_value_usd - (max_trim if proceeds_withdrawn else 0.0)
    return {
        "current_weight_pct": round(weight * 100, 2),
        "limit_pct": limit_pct,
        "above_limit": weight > limit,
        "trim_to_limit_usd": round(trim, 2),
        "shares_to_sell": round(trim / price_per_share, 1),
        "realized_gain_on_trim_usd": round(realized, 2),
        "tax_budget_remaining_usd": round(remaining, 2),
        "trim_exceeds_tax_budget": realized > remaining + 0.005,
        "max_trim_within_tax_budget_usd": round(max_trim, 2),
        "weight_after_max_trim_within_budget_pct": round((position_value_usd - max_trim) / after_total * 100, 2),
        "note": NOT_ADVICE,
    }


def liquidity_math(cash_and_equivalents_usd: float, liquidity_reserve_usd: float, needs_usd: list[float],
                   needs_labels: list[str]) -> dict:
    available = cash_and_equivalents_usd - liquidity_reserve_usd
    total_needs = sum(needs_usd)
    labels = needs_labels + [f"need {i + 1}" for i in range(len(needs_labels), len(needs_usd))]
    return {
        "cash_and_equivalents_usd": round(cash_and_equivalents_usd, 2),
        "liquidity_reserve_usd": round(liquidity_reserve_usd, 2),
        "available_above_reserve_usd": round(available, 2),
        "needs": [{"label": lbl, "amount_usd": round(amt, 2)} for lbl, amt in zip(labels, needs_usd, strict=False)],
        "total_needs_usd": round(total_needs, 2),
        "shortfall_usd": round(max(0.0, total_needs - available), 2),
        "note": NOT_ADVICE,
    }


@tool(approval_mode="never_require")
def rebalancing_check(
    position_value_usd: Annotated[float, "Market value of the position."],
    total_portfolio_value_usd: Annotated[float, "Total portfolio value including cash."],
    limit_pct: Annotated[float, "Single-stock or sector limit in percent, e.g. 25."],
    price_per_share: Annotated[float, "Latest price per share."],
    cost_basis_per_share: Annotated[float, "Cost basis per share."],
    annual_tax_budget_usd: Annotated[float, "Annual realized-gain tax budget to test against."],
    realized_gains_ytd_usd: Annotated[float, "Realized gains already taken this year."] = 0.0,
    proceeds_withdrawn: Annotated[bool, "False for a trim back to the limit: the proceeds stay in the portfolio as "
                                        "cash. True only when the advisor asks how much to sell to fund a "
                                        "withdrawal; a cash need is checked separately with liquidity_check."] = False,
) -> str:
    """Exact trim-to-limit arithmetic: amount and shares to sell, realized gain, and the largest trim that fits
    the tax budget. Run once per tax budget you need to compare."""
    result = rebalancing_math(position_value_usd, total_portfolio_value_usd, limit_pct, price_per_share,
                              cost_basis_per_share, annual_tax_budget_usd, realized_gains_ytd_usd, proceeds_withdrawn)
    annotate(**{"guard.above_limit": result["above_limit"], "guard.exceeds_tax_budget": result["trim_exceeds_tax_budget"]})
    return json.dumps(result)


@tool(approval_mode="never_require")
def liquidity_check(
    cash_and_equivalents_usd: Annotated[float, "Cash plus cash equivalents."],
    liquidity_reserve_usd: Annotated[float, "Reserve the policy requires to keep."],
    needs_usd: Annotated[list[float], "Upcoming cash needs in dollars."],
    needs_labels: Annotated[list[str], "Labels for the needs, same order, e.g. 'home down payment by Dec 10'."],
) -> str:
    """Exact liquidity arithmetic: cash available above the reserve against upcoming needs, and the shortfall."""
    result = liquidity_math(cash_and_equivalents_usd, liquidity_reserve_usd, needs_usd, needs_labels)
    annotate(**{"guard.liquidity_shortfall_usd": result["shortfall_usd"]})
    return json.dumps(result)


def us_eastern(moment: datetime) -> tuple[datetime, str]:
    """US Eastern time: DST from the second Sunday in March to the first Sunday in November, 2 AM local."""
    utc = moment.astimezone(UTC)
    year = utc.year
    march = datetime(year, 3, 8, 7, tzinfo=UTC)  # 2 AM EST on the second Sunday in March
    march = march + timedelta(days=(6 - march.weekday()) % 7)
    november = datetime(year, 11, 1, 6, tzinfo=UTC)  # 2 AM EDT on the first Sunday in November
    november = november + timedelta(days=(6 - november.weekday()) % 7)
    daylight = march <= utc < november
    return (utc + timedelta(hours=-4 if daylight else -5)).replace(tzinfo=None), "EDT" if daylight else "EST"


def _clock_setting(name: str) -> datetime | None:
    value = os.environ.get(name, "").strip()
    if not value:
        return None
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        logger.warning("Ignoring %s=%r: not an ISO 8601 time.", name, value)
        return None
    return (moment if moment.tzinfo else moment.replace(tzinfo=UTC)).astimezone(UTC)


def story_clock_floor() -> datetime | None:
    """ADVISOR_CLOCK_NOT_BEFORE: the earliest time the agent treats as now (ISO 8601, UTC). For rehearsing a story
    before its own timeline: on the story's Monday morning, Monday afternoon's email is still in the future. Once real
    time passes the floor, real time applies again, so a floor never needs removing."""
    return _clock_setting("ADVISOR_CLOCK_NOT_BEFORE")


def story_clock_ceiling() -> datetime | None:
    """ADVISOR_CLOCK_NOT_AFTER: the latest time the agent treats as now (ISO 8601, UTC). For presenting a story after
    its own week without moving it (a new week needs new content and a newly recorded review): the agent stays on the
    story's demo day, so Thursday's client meeting is still ahead. Unlike the floor, a ceiling holds until removed."""
    return _clock_setting("ADVISOR_CLOCK_NOT_AFTER")


def story_now(now: datetime | None = None) -> datetime:
    """The time the agent treats as now: real time, kept between the story clock's floor and ceiling."""
    moment = (now or datetime.now(UTC)).astimezone(UTC)
    floor, ceiling = story_clock_floor(), story_clock_ceiling()
    if floor is not None and moment < floor:
        moment = floor
    if ceiling is not None and moment > ceiling:
        moment = ceiling
    return moment


def clock_note(now: datetime | None = None) -> str:
    """The current time for the instructions: the model must never guess today's date for a Graph date range."""
    moment = story_now(now).replace(microsecond=0)
    local, zone = us_eastern(moment)
    eastern = f"{local:%A, %B} {local.day}, {local.year}, {local:%I:%M %p}".replace(", 0", ", ")
    return (
        f"Current time: {moment:%Y-%m-%dT%H:%M:%S}Z ({eastern} {zone}). Use it for every date range, such as "
        "'UTC now minus 10 days', and to resolve words such as today, tomorrow or Thursday."
    )


@tool(approval_mode="never_require")
def to_eastern_time(
    timestamps: Annotated[list[str], "ISO 8601 timestamps with a UTC offset, e.g. 2026-10-22T14:00:00+00:00."],
) -> str:
    """Convert timestamps exactly to US Eastern Time for display. Use it for every meeting or message time."""
    results = []
    for value in timestamps[:40]:
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                raise ValueError("missing UTC offset")
            local, zone = us_eastern(parsed)
            results.append({"input": value, "eastern": f"{local:%a %b} {local.day}, {local.year} "
                                                       f"{local:%I:%M %p}".replace(" 0", " ") + f" {zone}"})
        except ValueError as exc:
            results.append({"input": value, "error": f"not converted: {exc}"})
    return json.dumps(results)


# --- Turn ledger, Fabric resilience and the demo fault switch -------------------------------------
@dataclass
class TurnLedger:
    """Tool outcomes for one user turn (the hosted server creates the agent per request)."""

    client_turn: bool = False
    baseline_turn: bool = False
    outcomes: list[tuple[str, str]] = field(default_factory=list)

    def record(self, name: str, status: str) -> None:
        self.outcomes.append((name, status))

    def succeeded(self, source: Literal["fabric", "workiq"]) -> bool:
        return any(status == "ok" and is_source(name, source) for name, status in self.outcomes)

    def attempted(self, source: Literal["fabric", "workiq"]) -> bool:
        return any(is_source(name, source) for name, _ in self.outcomes)


def is_source(name: str, source: Literal["fabric", "workiq"]) -> bool:
    lowered = name.lower()
    if source == "fabric":
        return lowered.startswith(FABRIC_PREFIX) or lowered in FABRIC_TOOLS
    if lowered in WORKIQ_TOOLS:
        return True
    # The A2A Work IQ tool's toolbox name is not fixed (for example workiq___chat or work_iq_preview).
    return lowered.startswith(WORKIQ_PREFIXES) or "workiq" in lowered.replace("_", "").replace("-", "")


def result_text(result: object) -> str:
    if isinstance(result, Content):
        return result.text or ""
    if isinstance(result, list):
        return "\n".join(result_text(item) for item in result)
    return result if isinstance(result, str) else json.dumps(result, default=str)


def looks_failed(result: object) -> bool:
    text = result_text(result)[:600].lower()
    return (text.startswith("error") or '"iserror": true' in text or "portfolio_data_unavailable" in text
            or "consent" in text and "required" in text)


NO_RETRY_MARKERS = ("polic", "not allowed", "not permitted", "disabled", "blocked", "consent", "forbidden",
                    "unauthorized", "access denied")
# A Foundry toolbox that couldn't resolve its project connection fails before the request reaches the tool, so one
# more try is safe for any tool, a write included. It happens in bursts (Work IQ's connection, 2026-10-22).
CONNECTION_FAILURE = "error resolving connection"
CONNECTION_RETRY_DELAY_SECONDS = 2.0


def transient_workiq_failure(text: str) -> bool:
    """A Work IQ retrieval that the service couldn't complete ("stoppedReason": "error"), as opposed to a policy,
    permission or consent denial, which is never retried."""
    lowered = text.lower()
    return '"stoppedreason": "error"' in lowered and not any(marker in lowered for marker in NO_RETRY_MARKERS)


class FaultSwitch:
    """Demo faults: DEMO_FAULTS=fabric (environment), or toggled per advisor in the state store."""

    KEY = "demo--faults"

    def __init__(self, store_factory: Callable[[], Awaitable[ItemStore]]) -> None:
        self.store_factory = store_factory
        self.cached: dict | None = None

    async def active(self, component: str) -> bool:
        if component in {f.strip() for f in os.environ.get("DEMO_FAULTS", "").split(",")}:
            return True
        if self.cached is None:
            try:
                item = await (await self.store_factory()).get_item(self.KEY)
                self.cached = item.value if item is not None else {}
            except Exception:
                logger.warning("Demo fault switch unavailable; assuming no faults.", exc_info=True)
                self.cached = {}
        return bool(self.cached.get(component))

    async def armed(self, component: str) -> bool:
        return await self.active(component)

    async def clear(self, component: str) -> None:
        store = await self.store_factory()
        item = await store.get_item(self.KEY)
        value = dict(item.value) if item is not None else {}
        value[component] = False
        value.update(saved_at=now_iso(), written_by=PROCESS["id"])
        await store.set_item(self.KEY, value, tags={"kind": "demo-faults"})
        self.cached = value

    def tool(self, crash_toggle: Callable[[bool], Awaitable[dict]] | None = None,
             extra_components: dict[str, str] | None = None):
        switch = self
        components = {"fabric": "Fabric IQ outage",
                      "process_crash": "stop the agent process once, halfway through the next durable meeting pack",
                      **(extra_components or {})}
        described = "; ".join(f"{name}: {what}" for name, what in components.items())

        @tool(approval_mode="never_require", description=(
            "Presenter control: simulate or clear a demo fault for this advisor. Call only when the user explicitly asks "
            f"to simulate or clear a demo fault, a crash, a specialist outage or the reviewer test. Controls: {described}."))
        async def demo_fault_switch(
            component: Annotated[str, "One of the presenter controls named in this tool's description."],
            enabled: Annotated[bool, "True to simulate, False to clear."],
        ) -> str:
            if component not in components:
                return json.dumps({"component": component, "error": f"unknown control; use one of {sorted(components)}"})
            if component == "process_crash":
                if crash_toggle is None:
                    return json.dumps({"component": component, "error": "crash simulation is not available"})
                state = await crash_toggle(enabled)
                annotate(**{"fault.component": component, "fault.enabled": enabled})
                if not enabled:
                    effect = "Crash switch cleared."
                elif state.get("mode") == "mid_wave":
                    effect = (f"The next durable meeting pack stops the agent process in the middle of wave "
                              f"{state.get('wave', 2)}, right after its first new checkpoint is written.")
                else:
                    effect = (f"The next durable meeting pack stops the agent process after wave {state.get('wave', 2)} "
                              "has written its checkpoints and before the progress marker moves.")
                return json.dumps({"component": component, "armed": state["armed"], "effect": effect})
            store = await switch.store_factory()
            item = await store.get_item(switch.KEY)
            value = dict(item.value) if item is not None else {}
            value[component] = enabled
            value.update(saved_at=now_iso(), written_by=PROCESS["id"])
            await store.set_item(switch.KEY, value, tags={"kind": "demo-faults"})
            switch.cached = value
            annotate(**{"fault.component": component, "fault.enabled": enabled})
            return json.dumps({"component": component, "simulated_outage": enabled})

        return demo_fault_switch


class ResilienceMiddleware(FunctionMiddleware):
    """Bounded time, one retry and graceful degradation for the slow Fabric data agent; records outcomes."""

    def __init__(self, ledger: TurnLedger, faults: FaultSwitch, *, timeout_seconds: float = 180.0) -> None:
        self.ledger = ledger
        self.faults = faults
        self.timeout_seconds = timeout_seconds

    async def process(self, context: FunctionInvocationContext, call_next: Callable[[], Awaitable[None]]) -> None:
        name = context.function.name
        if name == BRIEF_TOOL or (name == "load_skill" and skill_name(context.arguments) == BRIEF_SKILL):
            # Meeting briefs must be backed by both governed data and Microsoft 365 context.
            self.ledger.client_turn = True
        elif name == BASELINE_TOOL:
            # Proposing, confirming or saving a baseline needs neither source.
            self.ledger.baseline_turn = True
        if not name.lower().startswith(FABRIC_PREFIX):
            # One retry for a Work IQ retrieval that failed on the service side; never for a denial (Work IQ guidance).
            retries = 1 if name.lower().endswith("retrieve") and is_source(name, "workiq") else 0
            connection_retries = 1
            while True:
                try:
                    await call_next()
                except Exception as exc:
                    if retries and transient_workiq_failure(str(exc)):
                        retries -= 1
                        logger.warning("Work IQ %s failed on the service side; retrying once.", name)
                        annotate(**{"workiq.retried": name})
                        continue
                    if connection_retries and CONNECTION_FAILURE in str(exc).lower():
                        connection_retries -= 1
                        logger.warning("%s: the toolbox couldn't resolve its connection; retrying once.", name)
                        annotate(**{"toolbox.connection_retried": name})
                        await asyncio.sleep(CONNECTION_RETRY_DELAY_SECONDS)
                        continue
                    self.ledger.record(name, "failed")
                    raise
                if retries and transient_workiq_failure(result_text(context.result)[:4000]):
                    retries -= 1
                    logger.warning("Work IQ %s returned a service error; retrying once.", name)
                    annotate(**{"workiq.retried": name})
                    continue
                break
            self.ledger.record(name, "failed" if looks_failed(context.result) else "ok")
            return
        with tracer.start_as_current_span("advisor.fabric.call") as span:
            if await self.faults.active("fabric"):
                span.set_attribute("advisor.fallback.reason", "demo_fault")
                self.ledger.record(name, "fault")
                context.result = unavailable("simulated outage (demo fault switch)")
                return
            for attempt in (1, 2):
                try:
                    await asyncio.wait_for(call_next(), timeout=self.timeout_seconds)
                    if not looks_failed(context.result):
                        self.ledger.record(name, "ok")
                        span.set_attribute("advisor.fabric.attempts", attempt)
                        return
                    reason = result_text(context.result)[:200]
                except TimeoutError:
                    reason = f"no answer within {int(self.timeout_seconds)} seconds"
                except Exception as exc:
                    reason = f"{type(exc).__name__}: {str(exc)[:160]}"
                logger.warning("Fabric attempt %d failed: %s", attempt, reason)
                if attempt == 2 or "timeout" in reason or "within" in reason:
                    break
            span.set_attribute("advisor.fallback.reason", reason[:200])
            self.ledger.record(name, "failed")
            context.result = unavailable(reason)


def unavailable(reason: str) -> str:
    return json.dumps({
        "status": "PORTFOLIO_DATA_UNAVAILABLE",
        "reason": reason,
        "instruction": ("Do not retry now. Call recall_client_memory and use the saved portfolio snapshot, labelling "
                        "every portfolio figure as stale with its as-of date, or say that no snapshot exists."),
    })


# --- Evidence loop ----------------------------------------------------------------------------------
class EvidenceCheck:
    """After a client brief, require Work IQ evidence (and Fabric IQ when configured); allow one extra pass."""

    def __init__(self, ledger: TurnLedger, *, require_fabric: bool = False) -> None:
        self.ledger = ledger
        self.require_fabric = require_fabric
        self.gaps: list[str] = []

    def find_gaps(self) -> list[str]:
        gaps = []
        if not self.ledger.client_turn or self.ledger.baseline_turn:
            return gaps
        if self.require_fabric and not self.ledger.succeeded("fabric"):
            gaps.append("portfolio facts from the Fabric IQ Portfolio Analyst are missing or unavailable"
                        if self.ledger.attempted("fabric") else "the Fabric IQ Portfolio Analyst was not consulted")
        if not self.ledger.succeeded("workiq"):
            gaps.append("Microsoft 365 context from Work IQ is missing or failed"
                        if self.ledger.attempted("workiq") else "Work IQ was not consulted for emails, meetings and files")
        return gaps

    def should_continue(self, *, iteration: int, **_: Any) -> tuple[bool, str | None]:
        with tracer.start_as_current_span("advisor.evidence.check") as span:
            self.gaps = self.find_gaps() if iteration == 1 else []
            span.set_attribute("advisor.evidence.iteration", iteration)
            span.set_attribute("advisor.evidence.gaps", len(self.gaps))
            if self.gaps:
                logger.info("Evidence check requests another pass: %s", "; ".join(self.gaps))
                return True, "; ".join(self.gaps)
            return False, None

    def next_message(self, *, feedback: str | None = None, **_: Any) -> str:
        # Streamed to the advisor between passes, so it doubles as a visible harness note.
        return (
            f"\n\n---\n**Harness evidence check:** {feedback}. Recovery pass: one targeted tool call per gap "
            "(Work IQ retrieve or fetch for Microsoft 365 context). If a portfolio source is unavailable, call "
            "recall_client_memory and use its saved snapshot, labelling each figure \"stale as of <date>\". Reply with "
            "a short **Recovery** section only (do not repeat the brief): what was recovered, what is stale, and what "
            "remains unverified.\n\n"
        )
