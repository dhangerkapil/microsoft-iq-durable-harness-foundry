# Patterns: the Agent Framework harness and the "claw" series, in this code

This solution is built from the patterns in the Microsoft Agent Framework harness release and its "Build your own claw
and agent harness" series, plus Agent Framework workflows. This page maps each pattern to the file that implements it,
and says what is on, off or changed, and why.

> **Terms.** Investment Advisor is a multi-agent solution: a **lead agent** plans and delegates to four **specialist agents**, one per Microsoft IQ (the client context, portfolio, firm policy and market agents); the **writer agent** drafts the brief, and the **compliance reviewer agent** and a **number check** in code check it. *The advisor* is the person using the app.

The sources:

- [The Microsoft Agent Framework Harness is now released](https://devblogs.microsoft.com/agent-framework/the-microsoft-agent-framework-harness-is-now-released/)
- [Build your own claw and agent harness](https://devblogs.microsoft.com/agent-framework/build-your-own-claw-and-agent-harness-with-microsoft-agent-framework/), and its parts:
  [1. Meet your harness and claw](https://devblogs.microsoft.com/agent-framework/meet-your-agent-harness-and-claw/),
  [2. Working with your data, safely](https://devblogs.microsoft.com/agent-framework/agent-harness-working-with-your-data-safely/),
  [3. Scaling the claw or harness capabilities](https://devblogs.microsoft.com/agent-framework/agent-harness-scaling-the-claw-or-harness-capabilities/),
  [4. Making your claw production-ready](https://devblogs.microsoft.com/agent-framework/agent-harness-making-your-claw-production-ready/)
- [Durable workflows in Microsoft Agent Framework](https://devblogs.microsoft.com/dotnet/durable-workflows-in-microsoft-agent-framework/)
- Foundry: [durable state store](https://learn.microsoft.com/en-us/azure/foundry/agents/concepts/agent-state-store),
  [managing state for long-running agents](https://learn.microsoft.com/en-us/azure/foundry/agents/how-to/manage-task-state)

## The map

| Pattern | From | In this code | Notes |
|---|---|---|---|
| Harness lead agent | Harness release; Part 1 | `team.py`, `create_team_agent()`: `create_harness_agent(...)` | Skills, to-dos, plan and execute modes, history, approvals and telemetry on; file memory and built-in web search off |
| Planning: to-dos and modes | Part 1 | The harness's to-do list (`disable_todo` off unless `TEAM_TODOS` is off) and `AgentModeProvider(default_mode="execute")` (`TEAM_PLAN_MODE`) | Execute by default, like a chat agent; the routing skill adds to-dos for multi-step work |
| Custom tools | Part 1 | `@tool` functions: `ask_*`, `prepare_client_brief`, the IQ tools, the math tools | Read-only tools are `approval_mode="never_require"` |
| Skills from files | Part 3 | `SkillsProvider(FilteringSkillsSource(FileSkillsSource(...)))` per role, in `team.py`; files in `.agent_configs/team/baseline/skills` | Each role sees only its own skills; a role's instructions are a skill too (`role-*`) |
| Background agents | Part 3 | `background_agents=list(team.values())`; `Specialist` in `team.py` implements the agent protocol | Specialist agents run concurrently as asyncio tasks inside the request |
| Structured outputs | Harness release | `response_format=SpecialistReport` and `ReviewVerdict` (`contracts.py`); `parse_report()` with one repair | Background results come back as text and are validated in code |
| Approvals | Part 2 | `draft_follow_up_email`: `@tool(approval_mode="always_require")` (`drafts.py`); the app's approval card | The only write; everything else is read-only |
| Durable memory | Part 2 | `ClientMemory`, `AdvisorPreferences`, `StateStoreHistoryProvider` on Foundry state stores (`advisor.py`, `history.py`) | User-isolated stores, prefix `team` |
| Workflows: executors, edges, fan-out and fan-in | Durable workflows | `workflows/brief.py` (W1): `WorkflowBuilder`, `Executor`, `@handler` | W1 has a loop edge (decide back to the writer agent) for one revision |
| Request ports (human in the loop) | Durable workflows | Add-on: the morning note (`functions/morning_note/`): `ctx.request_info(...)` and `@response_handler` for the head of research's approval | W3's clients card and draft approvals are durable decisions in the state store instead ([below](#human-in-the-loop-durably)) |
| Checkpoints | Durable workflows | W3: one create-only checkpoint per pack, with its SHA-256 (`workflows/review_packs.py`); W2: its own checkpoints and progress marker (`durable.py`) | Both on the user-isolated Foundry state store; a new process continues from them after a crash |
| Durable state stores | Foundry | `FoundryStateStore(name, …, user_isolation=True)` (`advisor.py`); inventory in `inventory.py` | ETags, create-only writes, conditional updates |
| Middleware | Harness release; Part 4 | Function: `LaneRecorder`, `ResilienceMiddleware`, `ToolTimingRecorder`. Chat: `ModelStallGuard`. Agent: `ReviewPacksGate`, `TeamActivityOutput`, `ToolTimingOutput`, `SourceProgressOutput`, `ShowResultInFull`, Purview (add-on) | See [Middleware](#middleware) |
| One factory, thin hosts | Part 4 | `build_kit()` and `create_team_agent()` in `team.py`; hosts `hosts/hosted.py`, `hosts/console.py` | The same factory serves the hosted lead agent, the review-packs steps and the local console |
| Hosted agent | Part 4 | `ResponsesHostServer(agent=…)` (`hosts/hosted.py`) | One hosted agent; `TEAM_MODE` picks the team or the single-agent build |
| Observability | Part 4 | OpenTelemetry to Application Insights; spans stamped `name:version` (`hosts/hosted.py`) | Plus the activity record for the app |
| Evaluations and tuning | Part 4 | `evals/` (Foundry evaluations, Insights, the optimizer) and `scripts/rehearse.py` | [QUALITY-LOOP.md](QUALITY-LOOP.md) |
| CodeAct | Part 3 | Add-on: `whatif.py`, `MontyCodeActProvider` | The what-if analyst computes scenarios in a sandbox |
| Governance middleware | Part 4 | Add-on: `purview.py` (Agent Framework's `PurviewPolicyMiddleware`, wrapped by `PurviewGate`) | Off by default; on for the demo, where a Purview DLP policy blocks a prompt with a U.S. Social Security number |
| Group chat orchestration | Agent Framework orchestrations | Add-on: `committee.py`, `GroupChatBuilder` | Experimental; off by default |
| Durable workflows on Azure Functions | Durable workflows | Add-on: `functions/morning_note/`, `AgentFunctionApp(workflow=…)` on the Durable Task Scheduler; an approval over HTTP (`request_info`) | The same workflow model as W1 on a second runtime; off by default |

## The harness lead

`create_team_agent()` builds the lead agent for every request:

```python
agent = create_harness_agent(
    client=lead_client,                                   # team-lead
    name="advisor-team",
    harness_instructions="Verify facts through your specialists. State brief progress, not private reasoning.",
    agent_instructions=settings.instructions,             # .agent_configs/team/baseline/instructions.md
    tools=tools,                                          # ask_*, prepare_client_brief, memory, math, W2, probe, draft, ...
    skills_provider=skills_provider(skills_dir, LEAD_SKILLS),
    background_agents=list(team.values()),                # the four specialists
    background_agents_instructions="... start one task per specialist in the same step, then wait ...",
    middleware=[timing, ResilienceMiddleware(...), ModelTimingLogger()],
    history_provider=history,                             # StateStoreHistoryProvider when hosted
    mode_provider=AgentModeProvider(default_mode="execute"),
    disable_web_search=True,                              # Web IQ, behind the guard, replaces it
    disable_file_memory=True,                             # Foundry state stores hold memory across sessions
    ...
)
```

What's on and off, and why:

| Harness capability | Here | Why |
|---|---|---|
| Function invocation, history, context window | On; 128K-token window, 8K output | The lead agent holds the conversation |
| To-dos | On (`TEAM_TODOS`) | The routing skill adds to-dos for multi-step work |
| Modes | On, **execute** by default (`TEAM_PLAN_MODE`) | Plan mode first would ask for approval before every chat answer |
| Skills | On, filtered per role | Each agent loads only the playbooks for its job |
| Tool approval | On; `always_require` for the draft | The one write needs a person |
| Background agents | On: the four specialist agents | Parallel lanes for open questions |
| File memory | **Off** | No local files in a production container; memory lives in Foundry stores |
| Built-in web search | **Off** | Only the market agent reaches the web, through Web IQ and the guard |
| Shell | Not used | A production security decision (Part 4) |

## Skills and the role instructions

The skills live in `.agent_configs/team/baseline/skills/<name>/SKILL.md`, the layout the Foundry agent optimizer reads
and writes (`agent_config.py` loads it through `azure-ai-agentserver-optimization`, or a candidate the optimizer
supplies). `skills_provider()` wraps a `FileSkillsSource` in a `FilteringSkillsSource`, so the lead agent sees `LEAD_SKILLS`
and each specialist agent sees its own domain skills (`roles.py`). A role's own instructions are a skill as well
(`role-client-context`, `role-portfolio`, `role-firm-policy`, `role-market`, `role-writer`, `role-reviewer`), read by
`role_instructions()`: the optimizer can propose changes to a specialist agent's instructions the same way as to the lead agent's.
`team-routing` tells the lead agent which of the three routes to take.

## Background agents

`Specialist` (in `team.py`) wraps an Agent Framework `Agent` and implements the agent protocol (`create_session`,
`get_session`, `run`), so the harness can run it as a background agent. Its `report()` method:

1. returns `unavailable` at once if the presenter took the specialist agent offline (`specialist_<role>`);
2. for the market agent, checks the task with the boundary guard before anything runs (`WEB_TASK_BLOCKED`);
3. runs the agent in its lane (`ActivityRecorder.lane`), parses the `SpecialistReport`, and asks once for a valid
   report if the first answer doesn't parse;
4. records the Microsoft 365 items it cited, so the app can show them as source chips. An ID the specialist agent mis-copied
   is repaired there, against everything the specialist agents' tools returned this turn (the conversation keeps only the
   first 4,000 characters of each call, too little to repair safely); an ID the tools returned is never changed.

The lead agent calls background agents with `background_agents_start_task` (one per specialist agent, in one step),
`background_agents_wait_for_first_completion` and `background_agents_get_task_results`. They run as asyncio tasks, which
inherit the request's context: the advisor's identity for the toolboxes, and the turn's activity record. Background
tasks aren't durable; they finish inside the request.

## Structured outputs

Every specialist agent's `default_options` include `response_format=SpecialistReport`; the reviewer agent's include
`ReviewVerdict`. Because a background agent's result reaches the lead agent as text, `parse_report()` re-validates it (a fenced
or bare JSON object), moves any references block into the structured list, and fails cleanly. `render()` turns a
report into what the lead agent reads: a status line naming the role and its IQ, the answer, the figures and the cited items.
`evidence_block()` wraps a report as quoted evidence with its trust level for the writer agent.

## Workflows

**W1** (`workflows/brief.py`) is a graph of executors, each with a `@handler`:

```python
WorkflowBuilder(start_executor=start, max_iterations=24, name="client-brief")
    .add_fan_out_edges(start, [context, portfolio, policy])
    .add_edge(portfolio, market)                 # market waits for the largest holding's public name
    .add_fan_in_edges([context, market, policy], writer)
    .add_fan_out_edges(writer, [numbers, review])
    .add_fan_in_edges([numbers, review], decide)
    .add_edge(decide, writer)                    # one revision with the findings
    .build()
```

`Decide` yields the outcome (`ctx.yield_output`) or sends a `Revision` back to the writer agent. The workflow runs inside the
lead agent's `prepare_client_brief` call, so it inherits the request's identity and activity record.

**W3** (`workflows/review_packs.py`) is durable steps rather than a graph. `ReviewPacks` keeps the run in the state
store (`packs/<run>/run`: the clients, the pending card, the decisions and the attempts timeline) and answers one step
per request: `start` (the plan and the clients card), `answer` (a card's decision) and `next` (one client's pack: W1 for
that client, then a follow-up draft, written create-only to `packs/<run>/pack-<n>` with its SHA-256). Every step reads
the run from the store, never from memory, so any process can take the next one. `ReviewPacksGate`, the lead agent's
outermost middleware, recognises the app's `review-packs:` command and answers it in code, streaming a progress line
every 15 seconds while a pack runs.

## Human in the loop, durably

W3's decisions are cards the app shows: the clients to confirm, then one follow-up draft at a time. The pending card is
saved in the run item, so it survives a crash, and an answer counts only if it carries the pending card's `call_id`;
anything else gets "That card is no longer pending". A draft's approval must carry the SHA-256 of the exact text it
approves, or it's refused ("approval refused: the text changed").

The morning-note add-on uses the Agent Framework request ports for its approval:

```python
await ctx.request_info(approval_request(draft), ApprovalDecision)

@response_handler
async def decided(self, original: dict, decision: ApprovalDecision, ctx) -> None: ...
```

On Azure Functions, the Durable Task Scheduler holds the pending request, and the answer comes over HTTP.

## Checkpoints, two crash tests

| | W3: review packs | W2: durable meeting pack |
|---|---|---|
| Who writes checkpoints | `ReviewPacks`, after every pack | The meeting pack, after every phase |
| Where | `team-durable-runs`: `packs/<run>/…` | `team-durable-runs` |
| The crash test | Stops the process before the last pack (once per run: a create-only marker) | Stops the process between two writes |
| After a crash | The stopped conversation stays locked, so the app sends the step again in a new one: a new session and process read the run and restore the finished packs | The next call reconciles: restores what the marker passed, verifies and reuses newer checkpoints |
| Who resumes | Nobody: the app sends the step again by itself | The advisor, by asking again |
| Integrity | Create-only packs with a SHA-256; a checkpoint that fails its hash is rewritten | JSON items with a SHA-256 |

The lead agent is hosted with `agent=`, and the platform doesn't re-enter in-process work after a crash (its automatic
re-entry, `resilient_background`, is for workflow-hosted agents). So both workflows keep their progress in the state
store, and a new process continues from it.

## Durable state stores

`get_state_store()` (`advisor.py`) opens a `FoundryStateStore` with `user_isolation=True` for each store (memory,
preferences, history, durable runs). Writes use the store's primitives: `create_item` for create-only checkpoints and
the crash marker, `set_item` with `if_match` for the progress marker and preferences. `show_state_store` lists every
store the advisor can see, with counts and sample keys but never contents, plus the serving process, to make restarts
visible. Names come from `ADVISOR_STORE_PREFIX` (`naming.py`).

## Middleware

| Middleware | Kind | On | Does |
|---|---|---|---|
| `LaneRecorder` (`activity.py`) | Function | Specialist agents, writer agent, reviewer agent | Records every tool call in the role's lane, and the item IDs in each full result (what a cited item's IDs are checked against) |
| `ResilienceMiddleware` (`advisor.py`) | Function | Lead agent, specialist agents | Bounded time, one retry and graceful degradation for the Fabric data agent; the Fabric fault switch; one more try for a Work IQ retrieval the service couldn't complete, and for any tool whose toolbox couldn't resolve its connection (the request never reached the tool) |
| `ToolTimingRecorder` (`execution.py`) | Function | Lead agent | Measures each tool call inside the agent |
| `ModelStallGuard` (`stall.py`) | Chat | Specialist agents, writer agent, reviewer agent | Resends a non-streaming model call once after `TEAM_MODEL_STALL_SECONDS` |
| `ReviewPacksGate` (`workflows/review_packs.py`) | Agent | Lead agent, outermost | Answers the app's `review-packs:` commands in code (W3's steps), streaming progress lines; any other message goes on to the lead agent |
| `TeamActivityOutput` (`activity.py`) | Agent | Lead agent | Attaches the agents' records to delegation results for the app only |
| `ToolTimingOutput`, `SourceProgressOutput` | Agent | Lead agent | The timing envelope and Work IQ search progress for the app |
| `ShowResultInFull` (`full_results.py`) | Agent | Lead agent | Adds, in code, what the answer must end with: the brief (the lead agent answers it with one line), a what-if or committee result the lead agent left out, verbatim, and the turn's cited Microsoft 365 items (the `workiq_references` block the app shows as sources), so no model retypes them |
| `ModelTimingLogger` | Chat | Lead agent | Logs each model call's time |
| Purview (`purview.py`, add-on) | Agent | Lead agent | Evaluates prompts and answers against Purview policies as the signed-in user's activity (`AdvisorUserStamp` adds their object ID); `PurviewGate` answers a blocked prompt with Purview's message, so a streamed run completes instead of failing |

## Hosting, and one factory for every host

`build_kit()` returns a `TeamKit`: the models, the IQ tools, the stores, the specialist agents, the writer agent and the reviewer agent
for one request. `create_team_agent()` adds the lead agent on top, and the review-packs steps use the same kit (W1 per
client); `hosts/console.py` runs the lead agent locally. The hosted agent stamps its spans with the platform's agent
identity (`align_trace_identity()`), so Foundry groups traces, evaluations and Insights per version.

## What the multi-agent solution adds over the single agent

`solo.py` is the single agent with every tool (the fallback build). The multi-agent solution keeps every capability and adds:
parallel lanes; one IQ per role with its own tools; a deterministic brief workflow with a maker-checker review; a
guard at the hand-off as well as at the tool; per-role instructions the optimizer can tune; and a multi-client workflow
that survives a crash without anyone resuming it.
