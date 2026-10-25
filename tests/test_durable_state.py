import asyncio
import json
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from agent_framework import Message
from azure.ai.agentserver.core.storage import (
    FoundryStateStore,
    FoundryStorageConflictError,
    FoundryStorageNotFoundError,
    FoundryStoragePreconditionError,
)

import advisor_team.advisor as advisor
from advisor_team.advisor import (
    MAX_PREFERENCES,
    PREFERENCES_KEY,
    PROCESS,
    AdvisorPreferences,
    ClientMemory,
    FaultSwitch,
    InMemoryItemStore,
    TurnLedger,
)
from advisor_team.durable import DurableMeetingPack
from advisor_team.history import StateStoreHistoryProvider
from advisor_team.inventory import (
    ARTIFACTS,
    FRAMEWORK,
    HISTORY,
    PROFILE,
    PROGRESS,
    StateStoreInventory,
    StoreSpec,
    framework_store_names,
    framework_stores,
    harness_stores,
    list_all_keys,
)

SKILLS = Path(advisor.__file__).resolve().parents[2] / ".agent_configs" / "solo" / "baseline" / "skills"


def text_of(result) -> str:
    if isinstance(result, str):
        return result
    if isinstance(result, list):
        return "".join(getattr(item, "text", "") or "" for item in result)
    return str(result)


def factory_for(store):
    async def factory():
        return store

    return factory


@pytest.fixture(params=["in-memory", "sdk-local"])
def make_store(request, tmp_path, monkeypatch):
    """The harness's stand-in and the SDK's own FoundryStateStore (its local file backend) behind one factory."""
    if request.param == "in-memory":
        async def make(name: str):
            return InMemoryItemStore(name)
    else:
        monkeypatch.setenv("AGENTSERVER_STATE_ROOT", str(tmp_path))
        monkeypatch.delenv("FOUNDRY_HOSTING_ENVIRONMENT", raising=False)

        async def make(name: str):
            return await FoundryStateStore.get_or_create(name, user_isolation=True)
    return make


async def test_hedged_store_retries_held_reads_and_upserts_but_not_conditional_writes(make_store, monkeypatch):
    store = await make_store("hedged")
    hedged = advisor.HedgedStore(store)
    await hedged.create_item("a", {"n": 1}, tags={"kind": "x"})
    await hedged.set_item("b", {"n": 2})
    assert (await hedged.get_item("a")).value == {"n": 1}
    assert sorted(key.key for key in (await hedged.list_keys(limit=10)).keys) == ["a", "b"]
    assert await hedged.get() is not None
    get_item, set_item, calls = store.get_item, store.set_item, []

    async def held_once(operation, name, *args, **kwargs):
        calls.append(name)
        if calls.count(name) == 1:
            await asyncio.sleep(30)
        return await operation(*args, **kwargs)

    monkeypatch.setattr(store, "get_item", lambda *a, **k: held_once(get_item, "get", *a, **k))
    monkeypatch.setattr(store, "set_item", lambda *a, **k: held_once(set_item, "set", *a, **k))
    monkeypatch.setattr(advisor, "HEDGE_SECONDS", 0.05)
    started = time.monotonic()
    assert (await hedged.get_item("b")).value == {"n": 2}
    await hedged.set_item("b", {"n": 3}, tags={"kind": "y"})
    assert calls == ["get", "get", "set", "set"] and time.monotonic() - started < 5
    assert (await get_item("b")).value == {"n": 3}
    calls.clear()
    current = await get_item("b")
    monkeypatch.setattr(store, "set_item", lambda *a, **k: (calls.append("conditional"), set_item(*a, **k))[1])
    await hedged.set_item("b", {"n": 4}, if_match=current.etag)
    assert calls == ["conditional"]


async def test_in_memory_store_mirrors_the_sdk_item_api(make_store):
    store = await make_store("mirror")
    created = await store.create_item("a", {"n": 1}, tags={"kind": "x"})
    with pytest.raises(FoundryStorageConflictError):
        await store.create_item("a", {"n": 2})
    item = await store.get_item("a")
    assert item.value == {"n": 1} and item.etag == created.etag and item.tags == {"kind": "x"}
    replaced = await store.set_item("a", {"n": 2}, if_match=item.etag)
    assert replaced.etag != item.etag
    with pytest.raises(FoundryStoragePreconditionError) as stale:
        await store.set_item("a", {"n": 3}, if_match=item.etag)
    assert stale.value.status_code == 412 and stale.value.current_etag == replaced.etag
    with pytest.raises(FoundryStoragePreconditionError):
        await store.set_item("missing", {"n": 1}, if_match=item.etag)
    for n in range(5):
        await store.set_item(f"k{n}", {"n": n}, tags={"kind": "y", "odd": str(n % 2)})
    tagged = await store.list_keys(tags={"kind": "y", "odd": "1"}, limit=100)
    assert sorted(key.key for key in tagged.keys) == ["k1", "k3"]
    seen, after = [], None
    while True:
        page = await store.list_keys(limit=2, after=after)
        seen += [key.key for key in page.keys]
        if not page.has_more:
            break
        after = page.last_id
    assert sorted(seen) == ["a", "k0", "k1", "k2", "k3", "k4"]
    descriptor = await store.get()
    assert descriptor.user_isolation is True and descriptor.item_ttl_seconds == 30 * 24 * 60 * 60
    deleted = await store.delete_item("k0")
    assert deleted.deleted and await store.get_item("k0") is None


async def test_in_memory_store_enforces_service_limits():
    store = InMemoryItemStore()
    with pytest.raises(ValueError):
        await store.set_item("k" * 129, {"n": 1})
    with pytest.raises(ValueError):
        await store.set_item("big", {"text": "x" * (1024 * 1024)})
    with pytest.raises(ValueError):
        await store.list_keys(limit=101)
    item = await store.set_item("a", {"nested": {"n": 1}})
    copy = await store.get_item("a")
    copy.value["nested"]["n"] = 2
    assert (await store.get_item("a")).value == {"nested": {"n": 1}} and item.etag == copy.etag


async def test_preferences_add_show_clear_limits_and_duplicates(make_store):
    store = await make_store("team-preferences")
    tool = AdvisorPreferences(factory_for(store)).tool()

    async def call(**arguments):
        return json.loads(text_of(await tool.invoke(arguments=arguments)))

    assert (await call(action="show"))["count"] == 0
    added = await call(action="add", preference="  Put risks and conflicts first.  ")
    assert added["saved"] is True and added["count"] == 1
    entry = added["preferences"][0]
    assert entry["text"] == "Put risks and conflicts first." and entry["written_by"] == PROCESS["id"] and entry["saved_at"]
    assert added["written_by"] == PROCESS["id"] and "never" in added["rule"]
    duplicate = await call(action="add", preference="Put risks and conflicts first.")
    assert duplicate["saved"] is False and duplicate["count"] == 1 and "Already saved" in duplicate["note"]
    for invalid in ("   ", "x" * 301):
        rejected = await call(action="add", preference=invalid)
        assert rejected["saved"] is False and "1-300" in rejected["error"] and rejected["count"] == 1
    assert (await call(action="add"))["saved"] is False
    assert (await call(action="add", preference="y" * 300))["saved"] is True
    for n in range(MAX_PREFERENCES - 2):
        assert (await call(action="add", preference=f"Preference {n}"))["saved"] is True
    full = await call(action="add", preference="One too many")
    assert full["saved"] is False and "10 preferences" in full["error"] and full["count"] == MAX_PREFERENCES
    shown = await call(action="show")
    assert [p["text"] for p in shown["preferences"]][:2] == ["Put risks and conflicts first.", "y" * 300]
    item = await store.get_item(PREFERENCES_KEY)
    assert item.tags == {"kind": "preferences"} and item.value["written_by"] == PROCESS["id"]
    cleared = await call(action="clear")
    assert cleared["cleared"] is True and cleared["count"] == 0 and cleared["preferences"] == []
    assert (await store.get_item(PREFERENCES_KEY)).etag != item.etag
    again = await call(action="clear")
    assert again["cleared"] is False and again["note"] == "Nothing to clear."


class Racing:
    """Before each of the first `rounds` conditional writes, another request saves a preference first."""

    def __init__(self, store, rounds: int = 1) -> None:
        self.store, self.rounds, self.conditional_writes = store, rounds, 0
        self.competitor = AdvisorPreferences(factory_for(store))

    async def race(self) -> None:
        self.conditional_writes += 1
        if self.conditional_writes <= self.rounds:
            await self.competitor.apply("add", f"Competing preference {self.conditional_writes}")

    async def get_item(self, key, **kwargs):
        return await self.store.get_item(key, **kwargs)

    async def create_item(self, key, value, **kwargs):
        await self.race()
        return await self.store.create_item(key, value, **kwargs)

    async def set_item(self, key, value, *, if_match=None, **kwargs):
        if if_match is not None:
            await self.race()
        return await self.store.set_item(key, value, if_match=if_match, **kwargs)


async def test_preferences_retry_once_when_another_write_wins(make_store):
    store = await make_store("team-preferences")
    await AdvisorPreferences(factory_for(store)).apply("add", "First")
    racing = Racing(store)
    result = await AdvisorPreferences(factory_for(racing)).apply("add", "Mine")
    assert result["saved"] is True and racing.conditional_writes == 2
    assert [p["text"] for p in result["preferences"]] == ["First", "Competing preference 1", "Mine"]
    stored = (await store.get_item(PREFERENCES_KEY)).value["preferences"]
    assert [p["text"] for p in stored] == ["First", "Competing preference 1", "Mine"]


async def test_preferences_retry_after_a_concurrent_create(make_store):
    store = await make_store("team-preferences")
    racing = Racing(store)
    result = await AdvisorPreferences(factory_for(racing)).apply("add", "Mine")
    assert result["saved"] is True
    assert [p["text"] for p in result["preferences"]] == ["Competing preference 1", "Mine"]


async def test_preferences_give_up_after_two_lost_writes_without_overwriting(make_store):
    store = await make_store("team-preferences")
    await AdvisorPreferences(factory_for(store)).apply("add", "First")
    result = await AdvisorPreferences(factory_for(Racing(store, rounds=2))).apply("add", "Mine")
    assert result["saved"] is False and "nothing was saved" in result["error"]
    assert [p["text"] for p in result["preferences"]] == ["First", "Competing preference 1", "Competing preference 2"]


async def test_recall_client_memory_includes_saved_preferences():
    preferences = AdvisorPreferences(factory_for(InMemoryItemStore()))
    await preferences.apply("add", "Lead with the risks.")
    memory_store = factory_for(InMemoryItemStore())
    recall = ClientMemory(memory_store, preferences).tools(TurnLedger())[0]
    data = json.loads(text_of(await recall.invoke(arguments={"client_name": "Michael Scott"})))
    assert data["advisor_preferences"] == ["Lead with the risks."] and data["last_discussion"] is None
    recall = ClientMemory(memory_store).tools(TurnLedger())[0]
    assert json.loads(text_of(await recall.invoke(arguments={"client_name": "Michael Scott"})))[
        "advisor_preferences"] == []

    async def broken():
        raise RuntimeError("store down")

    recall = ClientMemory(memory_store, AdvisorPreferences(broken)).tools(TurnLedger())[0]
    data = json.loads(text_of(await recall.invoke(arguments={"client_name": "Michael Scott"})))
    assert data["advisor_preferences"] is None and data["client_key"] == "michael-scott"


async def test_recall_gives_the_brief_workflow_what_the_tool_gives_the_lead():
    # prepare_client_brief once parsed recall_client_memory's invoke() result as a string; invoke() returns a list
    # of contents, so every brief failed. The workflow now calls recall() directly.
    preferences = AdvisorPreferences(factory_for(InMemoryItemStore()))
    await preferences.apply("add", "Lead with the risks.")
    memory = ClientMemory(factory_for(InMemoryItemStore()), preferences)
    direct = await memory.recall("Michael Scott")
    tool = memory.tools(TurnLedger())[0]
    via_tool = json.loads(text_of(await tool.invoke(arguments={"client_name": "Michael Scott"})))
    assert direct == via_tool
    assert direct["advisor_preferences"] == ["Lead with the risks."] and "note" in direct


def test_no_code_parses_a_tool_invoke_result_as_a_string():
    import re
    from pathlib import Path

    source = Path(__file__).resolve().parents[1] / "src" / "advisor_team"
    offenders = [str(path.relative_to(source)) for path in source.rglob("*.py")
                 if re.search(r"json\.loads\(\s*await\s[^\n]*\.invoke\(", path.read_text(encoding="utf-8"))]
    assert offenders == []


async def test_memory_items_record_the_writing_process():
    store = InMemoryItemStore()
    memory = ClientMemory(factory_for(store))
    await memory.write("Michael Scott", "last-brief", {"summary": "Brief."})
    await FaultSwitch(factory_for(store)).tool().invoke(arguments={"component": "fabric", "enabled": True})
    assert store.items["michael-scott--last-brief"].value["written_by"] == PROCESS["id"]
    assert store.items["demo--faults"].value["written_by"] == PROCESS["id"]
    assert await FaultSwitch(factory_for(store)).active("fabric")


async def test_history_index_records_the_writing_process():
    store = InMemoryItemStore()
    provider = StateStoreHistoryProvider(factory_for(store), scope=["tenant", "project", "platform-session"])
    await provider.save_messages("conv-1", [Message(role="user", contents=["Prepare me for Michael Scott."])])
    index = store.items[f"{provider.key('conv-1')}--index"]
    assert index.value["written_by"] == PROCESS["id"] and index.value["chunks"] == 1
    assert index.tags == {"kind": "history-index"}


class FakeWorkIQ:
    async def call_tool(self, tool_name, **kwargs):
        if tool_name == "workiq___fetch":
            return json.dumps({"results": [{"statusCode": 200, "data": {"value": []}}]})
        return json.dumps({"application/vnd.ms-workiq.retrieval": {"markdown": "passage", "retrievalHits": []}})


class MissingStore:
    async def get(self):
        raise FoundryStorageNotFoundError("State store does not exist.", status_code=404)


class SharedStore(InMemoryItemStore):
    async def get(self):
        return InMemoryItemStore.Store(self.name, user_isolation=False)


async def harness_inventory(session_offset_seconds: int):
    memory, history, durable, preferences = (InMemoryItemStore(name) for name in (
        "team-client-memory", "team-conversations", "team-durable-runs", "team-preferences"))
    client_memory = ClientMemory(factory_for(memory))
    await client_memory.write("Michael Scott", "last-discussion", {"summary": "SECRET-SUMMARY"})
    await client_memory.write("Michael Scott", "last-brief", {"summary": "SECRET-BRIEF"})
    await FaultSwitch(factory_for(memory)).tool().invoke(arguments={"component": "fabric", "enabled": False})
    provider = StateStoreHistoryProvider(factory_for(history), scope=["tenant", "project", "platform-session"])
    await provider.save_messages("conv-1", [Message(role="user", contents=["SECRET-MESSAGE"])])
    await provider.save_messages("conv-2", [Message(role="user", contents=["Another conversation"])])
    await DurableMeetingPack(factory_for(durable), factory_for(memory), FakeWorkIQ()).run("Michael Scott")
    await AdvisorPreferences(factory_for(preferences)).apply("add", "SECRET-PREFERENCE")
    sessions = InMemoryItemStore("agent_sessions/v2/" + "a" * 64)
    for key in ("conv_123", "caresp_456"):
        await sessions.set_item(key, {"state": "SECRET-SESSION"})
        sessions.items[key].created_at = int(time.time()) + session_offset_seconds
    stores = {"agent_sessions/v2/" + "a" * 64: sessions, "function_approvals/v2/" + "a" * 64: MissingStore()}

    async def bind(name):
        return stores[name]

    specs = [
        *harness_stores(memory=factory_for(memory), history=factory_for(history), durable=factory_for(durable),
                        preferences=factory_for(preferences)),
        *framework_stores({root: f"{root}/v2/{'a' * 64}" for root in ("agent_sessions", "function_approvals")}, bind),
        StoreSpec("shared-store", PROFILE, "Not user-isolated.", factory_for(SharedStore("shared-store"))),
    ]
    return StateStoreInventory(specs, session_id="platform-session")


async def test_show_state_store_reports_categories_counts_writers_and_this_process(monkeypatch):
    monkeypatch.setattr(advisor, "PROCESS_CLOCK_START", time.monotonic() - 42)
    inventory = await harness_inventory(session_offset_seconds=-3600)
    raw = text_of(await inventory.tool().invoke(arguments={}))
    for secret in ("SECRET-SUMMARY", "SECRET-BRIEF", "SECRET-MESSAGE", "SECRET-PREFERENCE", "SECRET-SESSION"):
        assert secret not in raw
    result = json.loads(raw)
    process = result["this_process"]
    assert process["id"] == PROCESS["id"] and process["started_at"] == PROCESS["started_at"]
    assert isinstance(process["uptime_seconds"], int) and process["uptime_seconds"] >= 42
    stores = {report["store"]: report for report in result["stores"]}
    assert [stores[name]["category"] for name in (
        "team-client-memory", "team-conversations", "team-durable-runs", "team-preferences",
    )] == [ARTIFACTS, HISTORY, PROGRESS, PROFILE]
    assert {name: stores[name]["items"] for name in stores if stores[name].get("items") is not None} == {
        "team-client-memory": 3, "team-conversations": 4, "team-durable-runs": 9, "team-preferences": 1,
        "agent_sessions/v2/aaaaaaaaaaaa…": 2, "function_approvals/v2/aaaaaaaaaaaa…": 0,
    }
    for name in ("team-client-memory", "team-conversations", "team-durable-runs", "team-preferences"):
        report = stores[name]
        assert report["status"] == "ok" and report["user_isolated"]
        assert report["idle_expiry"] == "30 days after the last write"
        assert report["last_written_by"] == PROCESS["id"] and report["last_written_by_this_process"] is True
        assert 1 <= len(report["sample_keys"]) <= 5 and report["last_write"] and report["items_capped"] is False
    assert stores["team-conversations"]["conversations"] == 2
    run = stores["team-durable-runs"]["runs"][0]
    assert run["client"] == "Michael Scott" and run["status"] == "completed" and run["checkpoints"] == 4
    assert run["attempts"] == 1 and run["interrupted_attempts"] == 0 and run["written_by"] == PROCESS["id"]
    assert stores["team-durable-runs"]["phase_checkpoints"] == 4
    assert stores["team-preferences"]["preferences_saved"] == 1
    framework = stores["agent_sessions/v2/aaaaaaaaaaaa…"]
    assert framework["category"] == FRAMEWORK and "MAF hosting" in framework["writer"]
    assert sorted(framework["sample_keys"]) == ["caresp_456", "conv_123"] and "last_written_by" not in framework
    assert stores["function_approvals/v2/aaaaaaaaaaaa…"]["status"] == "not created yet"
    assert stores["shared-store"]["status"].startswith("not listed") and "sample_keys" not in stores["shared-store"]
    session = result["this_session"]
    assert session["platform_session_id"] == "platform-session"
    assert session["state_first_saved_at"] == framework["first_write"]
    assert session["process_started_after_session_state"] is True
    assert result["item_values_shown"] is False


async def test_a_session_that_began_in_this_process_is_not_reported_as_a_restart():
    result = await (await harness_inventory(session_offset_seconds=0)).describe()
    assert result["this_session"]["process_started_after_session_state"] is False


async def test_inventory_lists_at_most_500_keys_across_pages():
    store = InMemoryItemStore()
    for n in range(500):
        await store.set_item(f"k{n:04d}", {"n": n})
    keys, capped = await list_all_keys(store)
    assert len({key.key for key in keys}) == 500 and capped is False
    await store.set_item("k0500", {"n": 500})
    keys, capped = await list_all_keys(store)
    assert len(keys) == 500 and capped is True


async def test_inventory_reads_the_sdk_store_shapes(make_store):
    store = await make_store("team-preferences")
    await AdvisorPreferences(factory_for(store)).apply("add", "Short briefs.")
    spec = harness_stores(memory=factory_for(InMemoryItemStore()), history=None, durable=factory_for(
        InMemoryItemStore()), preferences=factory_for(store))[-1]
    result = await StateStoreInventory([spec]).describe()
    report = result["stores"][0]
    assert report["items"] == 1 and report["sample_keys"] == [PREFERENCES_KEY] and report["preferences_saved"] == 1
    assert report["last_written_by_this_process"] is True and report["first_write"] and "this_session" not in result


def test_framework_store_names_match_the_hosting_library():
    from agent_framework_foundry_hosting import FoundryRequestScope
    from agent_framework_foundry_hosting._state_store import _store_name

    config = SimpleNamespace(is_hosted=True, session_id="platform-session")
    context = SimpleNamespace(call_id="call-1", user_id="user-1", session_id="platform-session")
    names = framework_store_names(config, context)
    scope = FoundryRequestScope.from_context(config, context)
    assert names == {root: _store_name(root, scope) for root in ("agent_sessions", "function_approvals")}
    assert framework_store_names(SimpleNamespace(is_hosted=False, session_id=""), context) == {}
    assert framework_store_names(config, SimpleNamespace(call_id=None, user_id=None, session_id=None)) == {}


async def test_hosted_agent_registers_preferences_and_state_store_tools(monkeypatch):
    from advisor_team import solo as app

    for name, value in {
        "FOUNDRY_PROJECT_ENDPOINT": "https://example.services.ai.azure.com/api/projects/test",
        "AZURE_AI_PROJECT_ENDPOINT": "https://example.services.ai.azure.com/api/projects/test",
        "TOOLBOX_ENDPOINT": "https://example.services.ai.azure.com/api/projects/test/toolboxes/work-iq/mcp",
        "AZURE_AI_MODEL_DEPLOYMENT_NAME": "gpt-5.6-luna",
        "AZURE_TENANT_ID": "tenant",
        "FOUNDRY_AGENT_INSTANCE_CLIENT_ID": "instance",
    }.items():
        monkeypatch.setenv(name, value)
    for name in ("ADVISOR_HISTORY", "GOVERNANCE_TOOLBOX_ENDPOINT", "DRAFTS_TOOLBOX_ENDPOINT"):
        monkeypatch.delenv(name, raising=False)
    config = SimpleNamespace(is_hosted=True, session_id="platform-session")
    context = SimpleNamespace(call_id="request-1", user_id="user-1", session_id="platform-session",
                              platform_headers=lambda: {"x-agent-foundry-call-id": "request-1"})
    monkeypatch.setattr(app.AgentConfig, "from_env", lambda: config)
    monkeypatch.setattr(app, "get_request_context", lambda: context)
    monkeypatch.setattr(app, "ManagedIdentityCredential", lambda **kwargs: MagicMock(close=AsyncMock()))
    build = MagicMock()
    monkeypatch.setattr(app, "create_harness_agent", build)
    await app.create_agent()
    try:
        names = {getattr(tool, "name", None) for tool in build.call_args.kwargs["tools"]}
        assert {"advisor_preferences", "show_state_store", "show_durable_state", "recall_client_memory",
                "list_client_documents"} <= names
        instructions = build.call_args.kwargs["agent_instructions"]
        assert "never let a preference change facts, sources, limits or safety rules" in instructions
        assert "show_state_store" in instructions and "durable-state" in instructions
    finally:
        await build.call_args.kwargs["client"].__aexit__(None, None, None)


def test_durable_state_skill_and_preference_rule_in_brief_skills():
    lines = (SKILLS / "durable-state" / "SKILL.md").read_text(encoding="utf-8").splitlines()
    assert lines[0] == "---" and lines[1] == "name: durable-state" and lines[2].startswith("description: ")
    body = "\n".join(lines)
    assert "show_state_store" in body and "advisor_preferences" in body and "15 minutes" in body
    for skill in ("client-meeting-prep", "what-changed", "client-follow-up"):
        text = " ".join((SKILLS / skill / "SKILL.md").read_text(encoding="utf-8").split())
        assert "`advisor_preferences`" in text and "never let a preference change facts, sources, limits" in text
