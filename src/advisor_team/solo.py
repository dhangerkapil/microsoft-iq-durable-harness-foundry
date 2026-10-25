from __future__ import annotations

import asyncio
import logging
import os
from contextlib import AsyncExitStack
from types import TracebackType

from agent_framework import (
    Agent,
    AgentLoopMiddleware,
    HistoryProvider,
    InMemoryHistoryProvider,
    SkillsProvider,
    create_harness_agent,
)
from agent_framework_azure_cosmos import CosmosHistoryProvider
from agent_framework_foundry import FoundryChatClient
from agent_framework_foundry_hosting import ResponsesHostServer
from azure.ai.agentserver.core import AgentConfig, get_request_context
from azure.cosmos.aio import CosmosClient
from azure.identity.aio import AzureCliCredential, ManagedIdentityCredential

from .agent_config import load_agent_settings
from .advisor import (
    PREFERENCES_STORE_NAME,
    AdvisorPreferences,
    ClientMemory,
    EvidenceCheck,
    FaultSwitch,
    ResilienceMiddleware,
    TurnLedger,
    clock_note,
    get_memory_store,
    get_state_store,
    liquidity_check,
    rebalancing_check,
    to_eastern_time,
)
from .ask import AskSessionContext
from .durable import DURABLE_STORE_NAME, DurableMeetingPack
from .execution import ModelTimingLogger, ToolTimingOutput, ToolTimingRecorder
from .history import HISTORY_STORE_NAME, ScopedCosmosHistoryProvider, StateStoreHistoryProvider
from .inventory import StateStoreInventory, framework_store_names, framework_stores, harness_stores
from .knowledge import knowledge_tools
from .portfolio import portfolio_tool
from .progress import SourceProgressOutput, SourceSearchProgress
from .retrieval import ParallelWorkIQToolbox
from .drafts import draft_tool
from .webiq import web_tools
from .workiq_chat import chat_tools
from .workiq_tools import client_documents_tool, inspect_document_tool, probe_tool, read_chart_tool


def pack_runners(portfolio, knowledge: list, web: list) -> dict:
    """Wave 3 of the durable meeting pack: the other Microsoft IQ sources this deployment has."""

    def runner(function, arguments):
        async def run(client: str, topics: list[str]):
            return await function.invoke(arguments=arguments(client, topics))
        return run

    runners = {}
    if portfolio is not None:
        runners["portfolio"] = runner(portfolio, lambda client, _: {"question": (
            f"{client}: positions above their single-stock or sector limits, the largest positions with weights, and "
            "cash and equivalents against the liquidity reserve, on the latest date")})
    if knowledge:
        runners["firm_policy"] = runner(knowledge[0], lambda *_: {"question": (
            "What do the firm's concentrated position policy, large withdrawal and wire procedure and client "
            "communication standards require of the advisor?")})
    if web:
        runners["market_news"] = runner(web[0], lambda _, topics: {"query": topics[0], "kind": "news", "max_results": 4})
    return runners


def flag(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    return default if value is None else value.strip().lower() in ("on", "true", "1", "yes")


def resolver_credential(is_hosted: bool):
    """A synchronous credential for the agent optimizer's candidate resolver, only when a run asks for it."""
    if not (is_hosted and os.environ.get("OPTIMIZATION_CANDIDATE_ID")):
        return None
    from azure.identity import ManagedIdentityCredential as SyncManagedIdentityCredential

    return SyncManagedIdentityCredential(client_id=os.environ["FOUNDRY_AGENT_INSTANCE_CLIENT_ID"])


async def create_agent() -> Agent:
    config = AgentConfig.from_env()
    context = get_request_context()
    if config.is_hosted and (not context.call_id or not context.user_id):
        raise RuntimeError("Work IQ requires a user-invoked request with Foundry caller context.")
    if config.is_hosted and (
        not config.session_id or (context.session_id and context.session_id != config.session_id)
    ):
        raise RuntimeError("Hosted history requires the trusted platform session ID.")
    project_endpoint = os.environ.get("AZURE_AI_PROJECT_ENDPOINT") or os.environ["FOUNDRY_PROJECT_ENDPOINT"]
    toolbox_endpoint = os.environ["TOOLBOX_ENDPOINT"]
    # Instructions, skills and model from .agent_configs: the baseline, or an optimizer candidate during a run.
    settings = load_agent_settings(os.environ.get("AZURE_AI_MODEL_DEPLOYMENT_NAME"),
                                   credential=resolver_credential(config.is_hosted))
    resources = AsyncExitStack()

    class RequestClient(FoundryChatClient):
        async def __aenter__(self) -> RequestClient:
            return self

        async def __aexit__(
            self, exc_type: type[BaseException] | None, exc_value: BaseException | None,
            traceback: TracebackType | None,
        ) -> None:
            await resources.__aexit__(exc_type, exc_value, traceback)

    try:
        credential = (
            ManagedIdentityCredential(client_id=os.environ["FOUNDRY_AGENT_INSTANCE_CLIENT_ID"])
            if config.is_hosted else AzureCliCredential()
        )
        resources.push_async_callback(credential.close)

        async def history_store():
            return await get_state_store(
                HISTORY_STORE_NAME, project_endpoint, config.is_hosted, "Advisor conversation history.")

        history: HistoryProvider
        if config.is_hosted:
            user_id = context.user_id
            if not user_id:
                raise RuntimeError("Work IQ requires trusted Foundry caller context.")
            if os.environ.get("ADVISOR_HISTORY", "state-store").lower() == "cosmos":
                cosmos = CosmosClient(os.environ["COSMOS_ENDPOINT"], credential=credential)
                resources.push_async_callback(cosmos.close)
                container = cosmos.get_database_client(
                    os.environ["COSMOS_DATABASE"]
                ).get_container_client(os.environ["COSMOS_HISTORY_CONTAINER"])
                history = ScopedCosmosHistoryProvider(
                    CosmosHistoryProvider(source_id="workplace", container_client=container),
                    tenant_id=os.environ["AZURE_TENANT_ID"],
                    project_endpoint=project_endpoint,
                    user_id=user_id, platform_session_id=config.session_id,
                )
            else:
                # Durable state store: user-isolated by the platform, survives restarts and idle eviction.
                history = StateStoreHistoryProvider(
                    history_store, scope=[os.environ["AZURE_TENANT_ID"], project_endpoint, config.session_id])
        else:
            logging.getLogger(__name__).info(
                "Foundry model: project=%s deployment=%s; authentication=Azure CLI.",
                project_endpoint, settings.model,
            )
            history = InMemoryHistoryProvider()
        logging.getLogger(__name__).info("Work IQ through Foundry Toolbox: %s", toolbox_endpoint)
        timing = ToolTimingRecorder()
        progress = SourceSearchProgress(timing)
        # The Fabric data agent can take close to a minute, so allow long toolbox calls.
        toolbox = ParallelWorkIQToolbox(credential, url=toolbox_endpoint, progress=progress, timeout=240.0)
        ledger = TurnLedger()

        async def memory_store():
            return await get_memory_store(project_endpoint, config.is_hosted)

        async def preferences_store():
            return await get_state_store(PREFERENCES_STORE_NAME, project_endpoint, config.is_hosted,
                                         "Advisor preferences for how briefs are written.")

        preferences = AdvisorPreferences(preferences_store)
        memory = ClientMemory(memory_store, preferences)
        faults = FaultSwitch(memory_store)

        async def durable_store():
            return await get_state_store(DURABLE_STORE_NAME, project_endpoint, config.is_hosted,
                                         "Resumable research runs: manifests and phase checkpoints.")

        async def framework_store(name: str):
            # Stores the MAF hosting library owns: bind without creating them.
            return await get_state_store(name, project_endpoint, True, create=False)

        portfolio = portfolio_tool(credential, os.environ.get("FABRIC_TOOLBOX_ENDPOINT"), faults)
        knowledge = knowledge_tools(credential, os.environ.get("FOUNDRYIQ_TOOLBOX_ENDPOINT"))
        web = web_tools(credential, os.environ.get("WEBIQ_TOOLBOX_ENDPOINT"))
        pack = DurableMeetingPack(durable_store, memory_store, toolbox, runners=pack_runners(
            portfolio if os.environ.get("FABRIC_TOOLBOX_ENDPOINT") else None, knowledge, web))
        inventory = StateStoreInventory([
            *harness_stores(memory=memory_store, durable=durable_store, preferences=preferences_store,
                            history=history_store if isinstance(history, StateStoreHistoryProvider) else None),
            *framework_stores(framework_store_names(config, context), framework_store),
        ], session_id=config.session_id if config.is_hosted else None)
        evidence = EvidenceCheck(ledger, require_fabric=flag("ADVISOR_REQUIRE_FABRIC", False))
        # PDFs downloaded in this turn (this agent instance serves one request), shared by the two document tools.
        documents: dict = {}
        tools = [
            toolbox, *memory.tools(ledger), preferences.tool(), rebalancing_check, liquidity_check,
            to_eastern_time, faults.tool(crash_toggle=pack.arm_crash), *pack.tools(), inventory.tool(),
            inspect_document_tool(credential, toolbox_endpoint, documents),
            client_documents_tool(credential, toolbox_endpoint),
            read_chart_tool(credential, toolbox_endpoint, project_endpoint,
                            os.environ.get("AZURE_AI_MODEL_DEPLOYMENT_NAME") or settings.model,
                            context.platform_headers(), documents),
            portfolio, *knowledge, *web,
            *chat_tools(credential, os.environ.get("WORKIQ_CHAT_TOOLBOX_ENDPOINT")),
        ]
        if os.environ.get("GOVERNANCE_TOOLBOX_ENDPOINT"):
            tools.append(probe_tool(credential, os.environ["GOVERNANCE_TOOLBOX_ENDPOINT"]))
        if os.environ.get("DRAFTS_TOOLBOX_ENDPOINT"):
            tools.append(draft_tool(credential, os.environ["DRAFTS_TOOLBOX_ENDPOINT"]))
        loop_enabled = flag("ADVISOR_EVIDENCE_LOOP", True)
        client = RequestClient(
            project_endpoint=project_endpoint,
            model=settings.model,
            credential=credential,
            default_headers=context.platform_headers(),
        )
        resources.push_async_callback(client.project_client.close)
        resources.push_async_callback(client.client.close)
        function_middleware: list = [timing, ResilienceMiddleware(ledger, faults)]
        if os.environ.get("WORKIQ_MODE", "a2a").lower() == "mcp":
            function_middleware.append(AskSessionContext())
        agent = create_harness_agent(
            client=client,
            name=os.environ.get("FOUNDRY_AGENT_NAME") or "advisor-team",
            harness_instructions=(
                "Verify facts with tools. State brief search progress, not private reasoning."
            ),
            agent_instructions=f"{settings.instructions}\n{clock_note()}\n",
            tools=tools,
            skills_paths=settings.skills_dir,
            auto_approval_rules=[SkillsProvider.read_only_tools_auto_approval_rule],
            middleware=[*function_middleware, ModelTimingLogger()],
            history_provider=history,
            max_context_window_tokens=128_000,
            max_output_tokens=8_192,
            disable_mode=True,
            disable_todo=True,
            disable_web_search=True,
            disable_file_memory=True,
            loop_should_continue=evidence.should_continue if loop_enabled else None,
            loop_next_message=evidence.next_message if loop_enabled else None,
            loop_max_iterations=2 if loop_enabled else None,
            default_options={"store": False, "reasoning": {"effort": "low"}},
        )
        # Register output hooks before the harness's lazy approval stream starts.
        agent.middleware = [
            ToolTimingOutput(timing), SourceProgressOutput(progress), *(agent.middleware or []),
        ]
        for layer in agent.middleware:
            if isinstance(layer, AgentLoopMiddleware):
                # The session already holds the first pass; re-injecting it as "Progress so far"
                # would stream the whole brief to the advisor a second time.
                layer.inject_progress = False
        return agent
    except BaseException:
        await resources.aclose()
        raise


def align_trace_identity() -> None:
    """Stamp the container's spans with the agent identity the platform uses (name:version).

    The platform labels its spans advisor-team-solo:<version>; the hosting library labels the container's spans
    with the agent instance's client ID. Insights in Foundry rejects traces that carry two identities for one
    agent ("missing or inconsistent agent execution details"), so the container uses name:version too.
    """
    name, version = os.environ.get("FOUNDRY_AGENT_NAME"), os.environ.get("FOUNDRY_AGENT_VERSION")
    if name and version:
        from azure.ai.agentserver.core import _config as host_config

        host_config.resolve_agent_id = lambda: f"{name}:{version}"


def main() -> None:
    if not AgentConfig.from_env().is_hosted:
        raise RuntimeError("Use advisor-team-console locally; the Responses server requires Foundry hosting.")
    logging.basicConfig(level=logging.INFO)
    align_trace_identity()
    asyncio.run(ResponsesHostServer(agent=create_agent, history_source="agent").run_async())


if __name__ == "__main__":
    main()
