"""Inventory of what this agent keeps in the Foundry durable state store, for the signed-in caller only.

It lists keys and the writer and time metadata the harness records, never item values that hold message content.
With this process's ID, start time and uptime, a presenter can show that state outlived a crash, a restart or idle
eviction of the hosting process.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import partial
from typing import Any

from agent_framework import tool
from agent_framework_foundry_hosting import FoundryAgentSessionStore, FoundryFunctionApprovalStore, FoundryRequestScope
from azure.ai.agentserver.core.storage import FoundryStorageNotFoundError

from .advisor import (
    MEMORY_STORE_NAME,
    PREFERENCES_KEY,
    PREFERENCES_STORE_NAME,
    PROCESS,
    ItemStore,
    annotate,
    process_identity,
    tracer,
)
from .durable import DURABLE_STORE_NAME
from .history import HISTORY_STORE_NAME

logger = logging.getLogger(__name__)
MAX_KEYS = 500
PAGE_SIZE = 100
SAMPLE_KEYS = 5
RECENT_ITEMS = 5
RESTART_MARGIN_SECONDS = 60

# What the Learn article "Durable state store for hosted agents" lists as typical contents.
FRAMEWORK = "Framework checkpoints"
HISTORY = "Conversation history the agent manages itself"
ARTIFACTS = "Generated artifacts and intermediate work a later turn needs"
PROFILE = "Per-user profiles, preferences and long-lived application data"
# From "Manage state for long-running agents".
PROGRESS = "Checkpoints and progress markers (long-running agent pattern)"
MAF_HOSTING = "MAF hosting library (agent_framework_foundry_hosting)"

Inspector = Callable[[ItemStore, list[Any]], Awaitable[dict]]


@dataclass(frozen=True)
class StoreSpec:
    name: str
    category: str
    holds: str
    factory: Callable[[], Awaitable[ItemStore | None]]
    inspect: Inspector | None = None
    writer: str = "this harness"
    label: str | None = None
    session_scoped: bool = False


def iso(timestamp: Any) -> str | None:
    """Store timestamps are Unix seconds."""
    if isinstance(timestamp, bool) or not isinstance(timestamp, int | float) or timestamp <= 0:
        return None
    seconds = timestamp / 1000 if timestamp > 100_000_000_000 else timestamp
    return datetime.fromtimestamp(seconds, UTC).replace(microsecond=0).isoformat()


def stamp(key: Any, name: str) -> int:
    value = getattr(key, name, None)
    return value if isinstance(value, int | float) and not isinstance(value, bool) else 0


def expiry(ttl: Any) -> str | None:
    if ttl == -1:
        return "never"
    if not isinstance(ttl, int) or ttl <= 0:
        return None
    return f"{ttl // 86400} days after the last write" if ttl % 86400 == 0 else f"{ttl} seconds after the last write"


def newest(keys: list[Any], count: int = RECENT_ITEMS) -> list[Any]:
    return sorted(keys, key=lambda key: stamp(key, "updated_at"), reverse=True)[:count]


async def values(store: ItemStore, keys: list[Any]) -> list[dict]:
    items = await asyncio.gather(*(store.get_item(key.key) for key in keys))
    return [dict(item.value) if item is not None else {} for item in items]


async def list_all_keys(store: ItemStore) -> tuple[list[Any], bool]:
    """Every key the caller can see, newest first, up to MAX_KEYS; True when more exist."""
    keys: list[Any] = []
    after = None
    while True:
        page = await store.list_keys(limit=PAGE_SIZE, after=after)
        keys.extend(page.keys)
        if len(keys) >= MAX_KEYS:
            return keys[:MAX_KEYS], len(keys) > MAX_KEYS or bool(page.has_more)
        if not page.has_more or not page.last_id:
            return keys, False
        after = page.last_id


# Each inspector reads a few small items and returns writer and time metadata only, never their content.
async def inspect_memory(store: ItemStore, keys: list[Any]) -> dict:
    recent = newest(keys)
    items = [{"key": key.key, "saved_at": value.get("saved_at") or value.get("armed_at"),
              "written_by": value.get("written_by")} for key, value in zip(recent, await values(store, recent))]
    return {"recent_items": items, "last_written_by": items[0]["written_by"] if items else None}


async def inspect_history(store: ItemStore, keys: list[Any]) -> dict:
    indexes = [key for key in keys if key.key.endswith("--index")]
    recent = newest(indexes)
    conversations = [{
        "conversation": key.key.removesuffix("--index"), "chunks": value.get("chunks"),
        "first_saved_at": iso(stamp(key, "created_at")), "last_saved_at": iso(stamp(key, "updated_at")),
        "written_by": value.get("written_by"),
    } for key, value in zip(recent, await values(store, recent))]
    return {"conversations": len(indexes), "recent_conversations": conversations,
            "last_written_by": conversations[0]["written_by"] if conversations else None}


async def inspect_runs(store: ItemStore, keys: list[Any]) -> dict:
    # Long-running runs: pack/<client>/<run>/{run, progress, checkpoints/<n>-<phase>, attempts/<n>[-end]}.
    run_keys = newest([key for key in keys if key.key.startswith("pack/") and key.key.endswith("/run")])
    runs = []
    for key, value in zip(run_keys, await values(store, run_keys)):
        base = key.key.removesuffix("/run")
        attempt_keys = [k.key for k in keys if k.key.startswith(base + "/attempts/")]
        started = [k for k in attempt_keys if not k.endswith("-end")]
        ended = {k.removesuffix("-end") for k in attempt_keys if k.endswith("-end")}
        open_attempts = [k for k in started if k not in ended]
        runs.append({
            "client": value.get("client"), "run_id": value.get("run"), "status": value.get("status"),
            "checkpoints": sum(k.key.startswith(base + "/checkpoints/") for k in keys),
            "attempts": len(started),
            # A completed run's unfinished attempts were interrupted; a running run's newest one may still be going.
            "interrupted_attempts": len(open_attempts) - (1 if value.get("status") == "running" and open_attempts else 0),
            "updated_at": iso(stamp(key, "updated_at")), "written_by": value.get("written_by"),
        })
    return {"runs": runs, "phase_checkpoints": sum("/checkpoints/" in key.key for key in keys if key.key.startswith("pack/")),
            "last_written_by": runs[0]["written_by"] if runs else None}


async def inspect_preferences(store: ItemStore, keys: list[Any]) -> dict:
    item = await store.get_item(PREFERENCES_KEY)
    value = item.value if item is not None else {}
    return {"preferences_saved": len(value.get("preferences") or []), "updated_at": value.get("updated_at"),
            "last_written_by": value.get("written_by")}


async def describe_store(spec: StoreSpec) -> dict:
    report: dict[str, Any] = {"store": spec.label or spec.name, "category": spec.category, "holds": spec.holds,
                              "writer": spec.writer}
    try:
        store = await spec.factory()
        if store is None:
            return {**report, "status": "not used in this environment"}
        try:
            descriptor = await store.get()
        except FoundryStorageNotFoundError:
            return {**report, "status": "not created yet", "items": 0}
        # Only stores that partition items per end user are listed, so the caller sees only their own keys.
        if not getattr(descriptor, "user_isolation", False):
            return {**report, "status": "not listed: the store is not user-isolated"}
        keys, capped = await list_all_keys(store)
        report.update({
            "status": "ok", "user_isolated": True, "idle_expiry": expiry(getattr(descriptor, "item_ttl_seconds", None)),
            "items": len(keys), "items_capped": capped, "sample_keys": [key.key for key in keys[:SAMPLE_KEYS]],
            "first_write": iso(min((stamp(key, "created_at") for key in keys), default=0)),
            "last_write": iso(max((stamp(key, "updated_at") for key in keys), default=0)),
        })
        if spec.inspect is not None and keys:
            report.update(await spec.inspect(store, keys))
            report["last_written_by_this_process"] = report.get("last_written_by") == PROCESS["id"]
    except Exception as exc:
        logger.warning("State store inventory failed for %s.", spec.label or spec.name, exc_info=True)
        report.update(status="error", error=f"{type(exc).__name__}: {str(exc)[:200]}")
    return report


def harness_stores(
    *, memory: Callable[[], Awaitable[ItemStore]], history: Callable[[], Awaitable[ItemStore]] | None,
    durable: Callable[[], Awaitable[ItemStore]], preferences: Callable[[], Awaitable[ItemStore]],
) -> list[StoreSpec]:
    """The stores this harness writes itself (history only when it keeps conversation history in the store)."""
    specs = [StoreSpec(
        MEMORY_STORE_NAME, ARTIFACTS, "Client discussion baselines and the last brief with its portfolio snapshot "
        "(what-changed and the stale fallback use them in later turns), plus the presenter's demo switches.",
        memory, inspect_memory)]
    if history is not None:
        specs.append(StoreSpec(
            HISTORY_STORE_NAME, HISTORY, "Each conversation's messages in chunk items plus a small index item; the "
            "next turn reads them back, on whichever process serves it.", history, inspect_history))
    specs += [
        StoreSpec(DURABLE_STORE_NAME, PROGRESS, "Long-running meeting-pack runs: per run, a progress marker moved only "
                  "with its ETag, one append-only checkpoint per finished phase (SHA-256), and one record per attempt "
                  "and process.", durable, inspect_runs),
        StoreSpec(PREFERENCES_STORE_NAME, PROFILE, "The advisor's preferences for how briefs are written: one item, "
                  "changed with ETag (If-Match) preconditions.", preferences, inspect_preferences),
    ]
    return specs


def framework_store_names(config: Any, context: Any) -> dict[str, str]:
    """The stores the MAF hosting library writes for this user and platform session, named as the library names
    them: '<root>/v2/<FoundryRequestScope.storage_key>' (a hash of the user and session IDs)."""
    if not config.is_hosted:
        return {}
    try:
        key = FoundryRequestScope.from_context(config, context).storage_key
    except Exception:
        logger.warning("Cannot resolve the MAF hosting state-store scope.", exc_info=True)
        return {}
    return {root: f"{root}/v2/{key}" for root in (
        FoundryAgentSessionStore.DEFAULT_ROOT_SCOPE, FoundryFunctionApprovalStore.DEFAULT_ROOT_SCOPE)}


def framework_stores(names: dict[str, str], bind: Callable[[str], Awaitable[ItemStore]]) -> list[StoreSpec]:
    holds = {
        FoundryAgentSessionStore.DEFAULT_ROOT_SCOPE: (
            "MAF agent session state (the serialized AgentSession, keyed by conversation and response IDs) for this "
            "platform session; the hosting library saves it on every stored turn so a new process can continue."),
        FoundryFunctionApprovalStore.DEFAULT_ROOT_SCOPE: (
            "Tool calls waiting for the advisor's approval (such as a draft email), so the approval can complete on "
            "another process."),
    }
    return [StoreSpec(name, FRAMEWORK, holds.get(root, "MAF hosting state."), partial(bind, name), writer=MAF_HOSTING,
                      label=f"{root}/v2/{name.rsplit('/', 1)[-1][:12]}…",
                      session_scoped=root == FoundryAgentSessionStore.DEFAULT_ROOT_SCOPE)
            for root, name in names.items()]


class StateStoreInventory:
    def __init__(self, specs: list[StoreSpec], *, session_id: str | None = None) -> None:
        self.specs = specs
        self.session_id = session_id

    async def describe(self) -> dict:
        reports = await asyncio.gather(*(describe_store(spec) for spec in self.specs))
        result: dict[str, Any] = {
            "this_process": process_identity(),
            "stores": list(reports),
            "isolation": "Counts and keys are only the signed-in advisor's: every listed store is user-isolated, and "
                         "the platform resolves the caller from the request, not from anything the agent passes.",
            "item_values_shown": False,
        }
        if self.session_id:
            first = min((report["first_write"] for spec, report in zip(self.specs, reports, strict=True)
                         if spec.session_scoped and report.get("first_write")), default=None)
            session: dict[str, Any] = {"platform_session_id": self.session_id, "state_first_saved_at": first}
            if first is not None:
                gap = datetime.fromisoformat(PROCESS["started_at"]) - datetime.fromisoformat(first)
                session["process_started_after_session_state"] = gap.total_seconds() > RESTART_MARGIN_SECONDS
            result["this_session"] = session
        return result

    def tool(self):
        inventory = self

        @tool(approval_mode="never_require")
        async def show_state_store() -> str:
            """Inventory of what this agent keeps durably in the Foundry state store for the signed-in advisor: each
            store, what it holds, item count, sample keys, last write and which process wrote it, plus this process
            (ID, start time, uptime) to show a restart or idle eviction. Never returns item contents."""
            with tracer.start_as_current_span("advisor.state_store.inventory"):
                result = await inventory.describe()
                annotate(**{"inventory.stores": len(result["stores"]),
                            "inventory.items": sum(report.get("items") or 0 for report in result["stores"])})
                return json.dumps(result)

        return show_state_store
