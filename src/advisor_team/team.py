"""The team: one hosted agent, a lead and its specialists.

``create_team_agent()`` is the shared factory (the series' Part 4 pattern) that the hosted, console and evaluation
hosts use. Per request it builds:

- the **lead advisor**, a harness agent (``create_harness_agent``) with visible to-dos and plan mode, skills, memory,
  exact arithmetic, the durable meeting pack, the governance probe and the approval-gated draft;
- four **specialists**, Agent Framework agents with one IQ each (client context: Work IQ; portfolio: Fabric IQ;
  firm policy: Foundry IQ; market: Web IQ), answering in a typed report. The lead reaches them three ways: one
  directly (``ask_*``) for a one-IQ question, several at once as **background agents** for an open question, and all
  four through the **brief workflow** (W1) for a client brief;
- a **writer** and a **compliance reviewer** for the brief's maker-checker loop.

Every agent runs inside the advisor's request, so Work IQ and Fabric see the advisor exactly as in the single agent.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from contextlib import AsyncExitStack
from pathlib import Path
from types import TracebackType
from typing import Annotated, Any

from agent_framework import (
    Agent,
    AgentModeProvider,
    AgentResponse,
    Content,
    FileSkillsSource,
    FilteringSkillsSource,
    HistoryProvider,
    InMemoryHistoryProvider,
    Message,
    SkillsProvider,
    create_harness_agent,
    tool,
)
from agent_framework_foundry import FoundryChatClient
from azure.ai.agentserver.core import AgentConfig, get_request_context
from azure.identity.aio import AzureCliCredential, ManagedIdentityCredential

from .activity import ActivityRecorder, LaneRecorder, TeamActivityOutput
from .advisor import (
    PREFERENCES_STORE_NAME,
    AdvisorPreferences,
    ClientMemory,
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
from .agent_config import load_agent_settings
from .ask import AskSessionContext
from .committee import committee_tool
from .contracts import ReviewVerdict, SpecialistReport, parse_report, unavailable
from .drafts import draft_tool
from .durable import DURABLE_STORE_NAME, DurableMeetingPack
from .execution import ModelTimingLogger, ToolTimingOutput, ToolTimingRecorder
from .full_results import ShowResultInFull
from .history import HISTORY_STORE_NAME, StateStoreHistoryProvider
from .inventory import StateStoreInventory, framework_store_names, framework_stores, harness_stores
from .knowledge import knowledge_tools
from .portfolio import portfolio_tool
from .progress import SourceProgressOutput, SourceSearchProgress
from .purview import purview_middleware
from .retrieval import ParallelWorkIQToolbox
from .roles import CLIENT_CONTEXT, FIRM_POLICY, LEAD_SKILLS, MARKET, PORTFOLIO, REVIEWER, SPECIALISTS, WRITER, Role
from .solo import flag, pack_runners, resolver_credential
from .stall import ModelStallGuard
from .webiq import BoundaryGuard, web_tools
from .whatif import what_if_tool
from .workflows.brief import BriefRequest, run_brief
from .workflows.review_packs import ReviewPacks, ReviewPacksGate
from .workiq_chat import chat_tools
from .workiq_tools import client_documents_tool, inspect_document_tool, probe_tool, read_chart_tool

logger = logging.getLogger(__name__)
REASONING = {"store": False, "reasoning": {"effort": "low"}}
SPECIALIST_MAX_RETRIES = 5
SEEDED_ADVICE = "We recommend Michael sell 2,000 NVDA shares this week to fund the closing."
TEAM_CONTROLS = {
    "reviewer_test": "the next client brief's writer adds one sentence that reads like a trade recommendation, so the "
                     "compliance reviewer can catch it (clears itself after one brief)",
    "packs_crash": "the next review-packs run stops the agent process once before the last client's pack; the app "
                   "sends the step again and the run resumes from the state store (clears itself after one run)",
    **{f"specialist_{role.key}": f"take the {role.title.lower()} specialist ({role.iq}) offline for the next "
                                 f"question (clears itself after one use)"
       for role in SPECIALISTS},
}


# --- Skills per role -----------------------------------------------------------------------------------------------
def _skill_name(skill: Any) -> str:
    return str(getattr(skill, "name", None) or getattr(getattr(skill, "frontmatter", None), "name", "") or "")


def skills_provider(skills_dir: Path, names: tuple[str, ...]) -> SkillsProvider | None:
    if not names:
        return None
    allowed = set(names)
    source = FilteringSkillsSource(FileSkillsSource(skills_dir), lambda skill, _context: _skill_name(skill) in allowed)
    return SkillsProvider(source, disable_load_skill_approval=True, disable_read_skill_resource_approval=True,
                          disable_run_skill_script_approval=True)


def role_instructions(skills_dir: Path, role: Role) -> str:
    """A role's instructions are its own skill, so the optimizer can propose changes to them."""
    path = skills_dir / role.skill / "SKILL.md"
    text = path.read_text(encoding="utf-8")
    if text.startswith("---"):
        text = text.split("---", 2)[2]
    return text.strip()


# --- Specialists as the lead sees them ------------------------------------------------------------------------------
class Specialist:
    """Runs a specialist agent in its lane, validates its report (one repair attempt) and records its references.

    Implements the agent protocol, so the lead's harness can also run it as a background agent.
    """

    def __init__(self, role: Role, agent: Agent, recorder: ActivityRecorder, guard: BoundaryGuard | None = None,
                 faults: FaultSwitch | None = None) -> None:
        self.role, self.agent, self.recorder, self.guard, self.faults = role, agent, recorder, guard, faults
        self.id = role.key
        self.name = role.key
        self.description = role.description

    def create_session(self, *, session_id: str | None = None):
        return self.agent.create_session(session_id=session_id)

    def get_session(self, service_session_id: Any, *, session_id: str | None = None):
        return self.agent.get_session(service_session_id, session_id=session_id)

    async def report(self, task: str, session: Any = None) -> SpecialistReport:
        control = f"specialist_{self.role.key}"
        if self.faults is not None and await self.faults.active(control):
            self.recorder.step(self.role.lane, "fault", "offline", "demo fault")
            try:
                await self.faults.clear(control)  # offline for the next question only, as the presenter asked
            except Exception:
                logger.warning("Could not clear the %s demo fault; clear it with the presenter control.", control)
            return unavailable(f"{self.role.iq} not checked: the {self.role.title.lower()} specialist is offline "
                               "(simulated outage, demo fault switch).")
        if self.guard is not None:
            # The boundary guard at the hand-off: client details never reach the market specialist.
            blocked = self.guard.check_text(task)
            if blocked:
                report = unavailable(f"WEB_TASK_BLOCKED: the task named a client detail ({blocked}), so it never reached "
                                     "the market specialist. Start the market task once more with the public topic "
                                     "alone, for example 'Public news this week on NVIDIA (NVDA)': no client name, "
                                     "amount or date, not even in an explanation.")
                self.recorder.step(self.role.lane, "guard", "blocked", blocked)
                return report
        session = session or self.agent.create_session()
        with self.recorder.lane(self.role.lane):
            try:
                response = await self.agent.run(task, session=session)
                try:
                    report = parse_report(response.text or "")
                except ValueError as exc:
                    retry = await self.agent.run(
                        f"That answer was not a valid report ({exc}). Return the same report again as the JSON object only.",
                        session=session)
                    report = parse_report(retry.text or "")
            except ValueError as exc:
                report = unavailable(f"The {self.role.title.lower()} specialist returned no valid report: {exc}")
            except Exception as exc:
                logger.warning("Specialist %s failed: %s", self.role.key, exc, exc_info=True)
                report = unavailable(f"The {self.role.title.lower()} specialist failed: {type(exc).__name__}: {exc}"[:500])
        self.recorder.add_references([r.model_dump() for r in report.references], lane=self.role.lane)
        return report

    async def run(self, messages: Any = None, *, stream: bool = False, session: Any = None, **kwargs: Any):
        if stream:
            raise NotImplementedError("Specialists run as background tasks, not streams.")
        task = messages if isinstance(messages, str) else getattr(messages, "text", None) or (
            messages[-1].text if isinstance(messages, list) and messages else str(messages or ""))
        report = await self.report(task, session=session)
        return AgentResponse(messages=[Message(role="assistant", contents=[Content.from_text(render(self.role, report))])])


def render(role: Role, report: SpecialistReport) -> str:
    """A specialist's report for the lead: its answer with provenance; numbers and citations stay exact."""
    lines = [f"[{role.title} ({role.iq}): {report.status}]"]
    if report.status == "ok":
        lines.append(report.answer)
    else:
        lines.append(report.unavailable_reason or report.answer or report.status)
    if report.consent_link:
        lines.append(f"Consent required. The advisor must open this link (show it in full): {report.consent_link}")
    if report.figures:
        lines.append("Figures: " + "; ".join(
            f"{f.label} = {f.value}" + (f" ({f.source}{', ' + f.as_of if f.as_of else ''})" if f.source else "")
            for f in report.figures))
    if report.references:
        titles = "; ".join(dict.fromkeys(r.title for r in report.references if r.title))
        lines.append("Microsoft 365 items cited (the app attaches them to your answer as sources; never write a "
                     f"workiq_references block yourself): {titles}")
    return "\n".join(lines)


def ask_tool(specialist: Specialist):
    role = specialist.role

    @tool(name=f"ask_{role.key}", approval_mode="never_require",
          description=(f"Ask the {role.title} specialist ({role.iq}) one self-contained task and get its report. "
                       f"{role.description}"))
    async def ask(task: Annotated[str, "A self-contained task: the client's full name, what is needed, any dates."]) -> str:
        return render(role, await specialist.report(task))

    return ask


# --- The factory ----------------------------------------------------------------------------------------------------
@dataclass
class TeamKit:
    """The parts of the team for one request (the lead, and the review-packs steps it answers in code, use it)."""

    config: Any
    context: Any
    project_endpoint: Any
    toolbox_endpoint: Any
    lead_model: Any
    settings: Any
    resources: Any
    credential: Any
    headers: Any
    history: Any
    timing: Any
    progress: Any
    recorder: Any
    ledger: Any
    preferences: Any
    memory: Any
    faults: Any
    consent_gate: Any
    workiq: Any
    portfolio: Any
    knowledge: Any
    web: Any
    pack: Any
    inventory: Any
    lead_client: Any
    team: Any
    writer: Any
    reviewer: Any


async def build_kit() -> TeamKit:
    """Everything the team needs for one request: models, IQ tools, stores, specialists, writer and reviewer."""
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
    lead_model = os.environ.get("TEAM_LEAD_DEPLOYMENT") or os.environ.get("AZURE_AI_MODEL_DEPLOYMENT_NAME")
    settings = load_agent_settings(lead_model, credential=resolver_credential(config.is_hosted))
    resources = AsyncExitStack()

    class RequestClient(FoundryChatClient):
        async def __aenter__(self) -> RequestClient:
            return self

        async def __aexit__(self, exc_type: type[BaseException] | None, exc_value: BaseException | None,
                            traceback: TracebackType | None) -> None:
            await resources.__aexit__(exc_type, exc_value, traceback)

    try:
        credential = (ManagedIdentityCredential(client_id=os.environ["FOUNDRY_AGENT_INSTANCE_CLIENT_ID"])
                      if config.is_hosted else AzureCliCredential())
        resources.push_async_callback(credential.close)
        headers = context.platform_headers()

        async def history_store():
            return await get_state_store(HISTORY_STORE_NAME, project_endpoint, config.is_hosted,
                                         "Advisor conversation history.")

        history: HistoryProvider
        if config.is_hosted:
            history = StateStoreHistoryProvider(
                history_store, scope=[os.environ["AZURE_TENANT_ID"], project_endpoint, config.session_id])
        else:
            history = InMemoryHistoryProvider()

        timing = ToolTimingRecorder()
        progress = SourceSearchProgress(timing)
        recorder = ActivityRecorder()
        ledger = TurnLedger()

        async def memory_store():
            return await get_memory_store(project_endpoint, config.is_hosted)

        async def preferences_store():
            return await get_state_store(PREFERENCES_STORE_NAME, project_endpoint, config.is_hosted,
                                         "Advisor preferences for how briefs are written.")

        async def durable_store():
            return await get_state_store(DURABLE_STORE_NAME, project_endpoint, config.is_hosted,
                                         "Resumable research runs: manifests and phase checkpoints.")

        async def framework_store(name: str):
            return await get_state_store(name, project_endpoint, True, create=False)

        preferences = AdvisorPreferences(preferences_store)
        memory = ClientMemory(memory_store, preferences)
        faults = FaultSwitch(memory_store)

        # Work IQ. The lead holds a copy with no functions: connecting it at the start of each turn is the consent
        # check (the app's Authorize banner), exactly as in the single agent. Only the client-context specialist
        # can call Work IQ's functions.
        consent_gate = ParallelWorkIQToolbox(credential, url=toolbox_endpoint, progress=progress, timeout=240.0,
                                             allowed_tools=[])
        workiq = ParallelWorkIQToolbox(credential, url=toolbox_endpoint, progress=progress, timeout=240.0)
        portfolio = portfolio_tool(credential, os.environ.get("FABRIC_TOOLBOX_ENDPOINT"), faults)
        knowledge = knowledge_tools(credential, os.environ.get("FOUNDRYIQ_TOOLBOX_ENDPOINT"))
        web = web_tools(credential, os.environ.get("WEBIQ_TOOLBOX_ENDPOINT"))
        documents: dict = {}
        pack = DurableMeetingPack(durable_store, memory_store, workiq, runners=pack_runners(
            portfolio if os.environ.get("FABRIC_TOOLBOX_ENDPOINT") else None, knowledge, web))
        inventory = StateStoreInventory([
            *harness_stores(memory=memory_store, durable=durable_store, preferences=preferences_store,
                            history=history_store if isinstance(history, StateStoreHistoryProvider) else None),
            *framework_stores(framework_store_names(config, context), framework_store),
        ], session_id=config.session_id if config.is_hosted else None)

        lead_client = RequestClient(project_endpoint=project_endpoint, model=settings.model, credential=credential,
                                    default_headers=headers)
        resources.push_async_callback(lead_client.project_client.close)
        resources.push_async_callback(lead_client.client.close)
        specialist_client = _specialist_client(project_endpoint, credential, headers, resources, RequestClient)

        def specialist(role: Role, tools: list, extra_middleware: list | None = None) -> Agent:
            provider = skills_provider(settings.skills_dir, role.domain_skills)
            return Agent(
                client=specialist_client, name=role.key, description=role.description,
                instructions=f"{role_instructions(settings.skills_dir, role)}\n{clock_note()}\n",
                tools=tools, context_providers=[provider] if provider else None,
                middleware=[LaneRecorder(role.lane, timing.active_call_id), ResilienceMiddleware(ledger, faults),
                            ModelStallGuard(role.key), *(extra_middleware or [])],
                default_options={**REASONING, "response_format": SpecialistReport},
            )

        context_tools = [
            workiq, to_eastern_time,
            inspect_document_tool(credential, toolbox_endpoint, documents),
            client_documents_tool(credential, toolbox_endpoint),
            read_chart_tool(credential, toolbox_endpoint, project_endpoint, lead_model or settings.model, headers, documents),
            *chat_tools(credential, os.environ.get("WORKIQ_CHAT_TOOLBOX_ENDPOINT")),
        ]
        ask_context = AskSessionContext() if os.environ.get("WORKIQ_MODE", "mcp").lower() == "mcp" else None
        guard = BoundaryGuard()
        team = {
            CLIENT_CONTEXT.key: Specialist(CLIENT_CONTEXT, specialist(
                CLIENT_CONTEXT, context_tools, [ask_context] if ask_context else None), recorder, faults=faults),
            PORTFOLIO.key: Specialist(PORTFOLIO, specialist(PORTFOLIO, [portfolio, to_eastern_time]), recorder,
                                      faults=faults),
            FIRM_POLICY.key: Specialist(FIRM_POLICY, specialist(FIRM_POLICY, [*knowledge]), recorder, faults=faults),
            MARKET.key: Specialist(MARKET, specialist(MARKET, [*web]), recorder, guard=guard, faults=faults),
        }
        writer = Agent(
            client=lead_client, name=WRITER.key, description=WRITER.description,
            instructions=f"{role_instructions(settings.skills_dir, WRITER)}\n{clock_note()}\n",
            tools=[rebalancing_check, liquidity_check, to_eastern_time],
            middleware=[LaneRecorder(WRITER.lane, timing.active_call_id), ModelStallGuard(WRITER.key)],
            default_options=REASONING,
        )
        reviewer = Agent(
            client=lead_client, name=REVIEWER.key, description=REVIEWER.description,
            instructions=f"{role_instructions(settings.skills_dir, REVIEWER)}\n{clock_note()}\n",
            tools=[*knowledge], middleware=[LaneRecorder(REVIEWER.lane, timing.active_call_id),
                                            ModelStallGuard(REVIEWER.key)],
            default_options={**REASONING, "response_format": ReviewVerdict},
        )

        return TeamKit(config=config, context=context, project_endpoint=project_endpoint, toolbox_endpoint=toolbox_endpoint, lead_model=lead_model, settings=settings, resources=resources, credential=credential, headers=headers, history=history, timing=timing, progress=progress, recorder=recorder, ledger=ledger, preferences=preferences, memory=memory, faults=faults, consent_gate=consent_gate, workiq=workiq, portfolio=portfolio, knowledge=knowledge, web=web, pack=pack, inventory=inventory, lead_client=lead_client, team=team, writer=writer, reviewer=reviewer)
    except BaseException:
        await resources.aclose()
        raise


async def create_team_agent() -> Agent:
    """The lead advisor for one request, with the team's specialists, workflows and tools."""
    kit = await build_kit()
    (config, context, project_endpoint, toolbox_endpoint, lead_model, settings, resources, credential, headers, history, timing, progress, recorder, ledger, preferences, memory, faults, consent_gate, workiq, portfolio, knowledge, web, pack, inventory, lead_client, team, writer, reviewer) = (kit.config, kit.context, kit.project_endpoint, kit.toolbox_endpoint, kit.lead_model, kit.settings, kit.resources, kit.credential, kit.headers, kit.history, kit.timing, kit.progress, kit.recorder, kit.ledger, kit.preferences, kit.memory, kit.faults, kit.consent_gate, kit.workiq, kit.portfolio, kit.knowledge, kit.web, kit.pack, kit.inventory, kit.lead_client, kit.team, kit.writer, kit.reviewer)
    try:
        @tool(name="prepare_client_brief", approval_mode="never_require")
        async def prepare_client_brief(
            client_name: Annotated[str, "The client's full name, for example 'Michael Scott'."],
            meeting_focus: Annotated[str, "Optional: the meeting or question the brief is for."] = "",
        ) -> str:
            """Prepare a client meeting brief with the whole team (workflow W1): the four specialists research in
            parallel (Work IQ, Fabric IQ, Foundry IQ, Web IQ), the writer drafts the brief with exact arithmetic, and
            a number check and the compliance reviewer must both pass it (one revision; otherwise Not cleared).
            Returns the finished brief; the app shows it to the advisor exactly as returned, below your one line."""
            recalled = await memory.recall(client_name)
            seeded = SEEDED_ADVICE if await faults.armed("reviewer_test") else ""
            outcome = await run_brief(BriefRequest(client_name, meeting_focus, json.dumps(recalled)), team,
                                      writer, reviewer, recorder, seeded_sentence=seeded)
            if seeded:
                await faults.clear("reviewer_test")
            await _remember_brief(memory, client_name, outcome.brief, recorder)
            header = "" if outcome.cleared else (
                "**Not cleared.** The checks found these problems twice; the brief is shown for reference only and its "
                "follow-up draft is blocked:\n" + "\n".join(f"- {f.get('issue')}: {f.get('sentence')}"
                                                            for f in outcome.findings) + "\n\n")
            # The workiq_references block is added after the lead's answer in code (ShowResultInFull), not copied.
            return (f"{header}{outcome.brief}\n\n<!-- verdict: {'cleared' if outcome.cleared else 'not cleared'};"
                    f" attempts: {outcome.attempts} -->")

        tools: list = [
            *([consent_gate] if flag("TEAM_CONSENT_GATE", True) else []),
            *(ask_tool(s) for s in team.values()), prepare_client_brief,
            *memory.tools(ledger), preferences.tool(), rebalancing_check, liquidity_check, to_eastern_time,
            faults.tool(crash_toggle=pack.arm_crash, extra_components=TEAM_CONTROLS), *pack.tools(), inventory.tool(),
        ]
        if os.environ.get("GOVERNANCE_TOOLBOX_ENDPOINT"):
            tools.append(probe_tool(credential, os.environ["GOVERNANCE_TOOLBOX_ENDPOINT"]))
        if os.environ.get("DRAFTS_TOOLBOX_ENDPOINT"):
            tools.append(draft_tool(credential, os.environ["DRAFTS_TOOLBOX_ENDPOINT"]))
        if flag("TEAM_COMMITTEE", False):  # add-on O4, experimental
            tools.append(committee_tool(kit))
        if flag("TEAM_WHAT_IF", True):  # add-on O3
            tools.append(what_if_tool(kit))

        agent = create_harness_agent(
            client=lead_client,
            name=os.environ.get("FOUNDRY_AGENT_NAME") or "advisor-team",
            harness_instructions="Verify facts through your specialists. State brief progress, not private reasoning.",
            agent_instructions=f"{settings.instructions}\n{clock_note()}\n",
            tools=tools,
            skills_provider=skills_provider(settings.skills_dir, LEAD_SKILLS),
            background_agents=list(team.values()),
            background_agents_instructions=(
                "Your specialists can run as background agents. For a question that needs several of them, start one "
                "task per specialist in the same step, then wait for them and read every result before you answer."),
            middleware=[*purview_middleware(credential), timing, ResilienceMiddleware(ledger, faults), ModelTimingLogger()],
            history_provider=history,
            max_context_window_tokens=128_000,
            max_output_tokens=8_192,
            disable_mode=not flag("TEAM_PLAN_MODE", True),
            # Execute by default, as the single agent does; the lead switches to plan mode for multi-client work.
            mode_provider=AgentModeProvider(default_mode="execute"),
            disable_todo=not flag("TEAM_TODOS", True),
            disable_web_search=True,
            disable_file_memory=True,
            default_options=REASONING,
        )
        # The team's records go inside the timing envelope: TeamActivityOutput first, ToolTimingOutput outermost.
        # ShowResultInFull sits inside SourceProgressOutput, whose producer task then owns its stream. Review-packs
        # steps (the app's commands) are answered in code before any of them, without the lead.
        agent.middleware = [
            ReviewPacksGate(ReviewPacks(pack.store_factory, kit)),
            TeamActivityOutput(recorder, timing), ToolTimingOutput(timing), SourceProgressOutput(progress),
            ShowResultInFull(timing, recorder), *(agent.middleware or []),
        ]
        return agent
    except BaseException:
        await resources.aclose()
        raise


def _specialist_client(project_endpoint: str, credential, headers, resources: AsyncExitStack, project_client_type):
    """The specialists' model: their own account's endpoint (TEAM_SPECIALIST_ENDPOINT), else the project."""
    deployment = os.environ.get("TEAM_SPECIALIST_DEPLOYMENT") or os.environ.get("TEAM_LEAD_DEPLOYMENT") or \
        os.environ.get("AZURE_AI_MODEL_DEPLOYMENT_NAME")
    endpoint = os.environ.get("TEAM_SPECIALIST_ENDPOINT", "").strip()
    if endpoint:
        from agent_framework_openai import OpenAIChatClient

        client = OpenAIChatClient(model=deployment, credential=credential, base_url=endpoint, default_headers=headers)
        # The specialists share one deployment, and four of them reading Work IQ can use its whole token rate for a
        # minute: wait out a 429 (the service says how long) rather than report the IQ unavailable.
        client.client.max_retries = SPECIALIST_MAX_RETRIES
        return client
    client = project_client_type(project_endpoint=project_endpoint, model=deployment, credential=credential,
                                 default_headers=headers)
    resources.push_async_callback(client.project_client.close)
    resources.push_async_callback(client.client.close)
    return client


async def _remember_brief(memory: ClientMemory, client: str, brief: str, recorder: ActivityRecorder) -> None:
    """Save the brief and its snapshot in durable memory (deterministic, as the single agent's record_brief)."""
    try:
        snapshot = brief.split("## Sources")[0]
        await memory.write(client, "last-brief", {
            "summary": snapshot[:1_500], "portfolio_as_of": "", "portfolio_figures": "",
            "sources": [r.get("title", "") for r in recorder.turn.references][:20], "written_by_team": True})
    except Exception:
        logger.warning("Could not save the brief to memory.", exc_info=True)
