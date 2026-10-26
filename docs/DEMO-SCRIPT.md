# Demo script: Investment Advisor

The short version, segment by segment: what to click, the prompt to send, what to point at and what to say. The long
version, with the expected X-ray for every step, every fact to check and the portal steps, is
[DEMO-WALKTHROUGH.md](DEMO-WALKTHROUGH.md). The checklist before and after the demo, the resets and the fallbacks are
in [DEMO-RUNBOOK.md](DEMO-RUNBOOK.md). Every person, client, email, document, policy and number is fictional.

> **Terms.** Investment Advisor is a multi-agent solution: a **lead agent** plans and delegates to four **specialist agents**, one per Microsoft IQ (the client context, portfolio, firm policy and market agents); the **writer agent** drafts the brief, and the **compliance reviewer agent** and a **number check** in code check it. *The advisor* is the person using the app.

**How to read this script**

- Every prompt you send is on its own quoted line. Copy the text between the quotes exactly; the same prompts are in
  [`demo/expectations.yaml`](../demo/expectations.yaml), and `scripts/Test-DemoDocs.ps1` checks that both files match.
- **New session** means: click **New session** (top left) first. **Same session** means: keep going in the current
  conversation.
- "Budget" is the time the rehearsal allows for the step (`budget_s` in the expectations file). The latest measured
  times are in the runbook's [timing table](DEMO-RUNBOOK.md#timing-table).

## The story in one minute

You are an investment advisor at Contoso Wealth, signed in to the app. Your client **Michael Scott** meets you on
**Thursday at 10:00 AM ET**. Since your recorded quarterly review last **Friday**:

- his NVDA position went over the 25% single-stock limit in his signed investment policy statement (IPS): 26.1% at the
  latest close;
- his house closing moved up to December 15, and he needs $400,000 by December 10;
- his tax budget is in dispute: $20,000 in the signed IPS, $15,000 from his accountant and in an unsigned IPS v2 draft;
- and he has just asked about a $25,000 529 college plan.

Those facts live in five places: the portfolio system, PDFs in OneDrive, email, a recorded Teams meeting and a Teams
chat. A multi-agent solution pulls them together, as you, through the four Microsoft IQs, and shows every call. The second
user in the story is your analyst (the analyst account). Jan Levinson and Dwight Schrute are two more of your clients,
with portfolio data only.

## Run of show (about 70 minutes)

| Start | Segment | Minutes | Session | Skip first if late |
|---|---|---|---|---|
| 0:00 | Opening: the problem, the four IQs, the agents | 3 | | |
| 0:03 | [1. Meeting prep across the four IQs](#1-meeting-prep-across-the-four-iqs-6-minutes) | 6 | New | 1.5 |
| 0:09 | [2. The four IQs together, then one at a time](#2-the-four-iqs-together-then-one-at-a-time-10-minutes) | 10 | New for each question (2.5 and 2.6 stay in the one before) | 2.3, 2.4 |
| 0:19 | [3. The recorded meeting and the chart](#3-the-recorded-meeting-and-the-chart-7-minutes) | 7 | New | 3.1 |
| 0:26 | [4. Work IQ versus calling Graph](#4-work-iq-versus-calling-graph-4-minutes) | 4 | New | |
| 0:30 | [5. Work IQ beyond search](#5-work-iq-beyond-search-4-minutes) | 4 | New | 5.3 |
| 0:34 | [6. Governance](#6-governance-8-minutes) | 8 | New, window B, then portals | 6.4, 6.6 |
| 0:42 | [7. Durability and restart](#7-durability-and-restart-7-minutes) | 7 | New | 7.5 |
| 0:49 | [8. The quality loop](#8-the-quality-loop-5-minutes) | 5 | Foundry portal | 8.4 |
| 0:54 | [9. The agents](#9-the-agents-14-minutes) | 14 | New, then the Review packs panel | 9.3, 9.5 |
| 1:08 | Questions | | | |

For a 60-minute slot, skip in this order: 2.3 and 2.4 (2.2 covers them), 3.1, 7.5, 6.4, 5.3, 9.3. The
[add-ons](#add-ons-optional) aren't in the run of show.

**For a 30-minute slot**, run the multi-agent highlights only (at their budgets the runs take up to about 18 minutes;
narrate while they run). Each row's minutes cover its budgets:

| Start | Steps | Minutes | What it shows |
|---|---|---|---|
| 0:00 | Opening: the problem and the agents in 30 seconds | 2 | Why several agents work together, and what each one does |
| 0:02 | 1.1-1.4: the brief, its lanes, the verdict; in 1.4 open only the **Portfolio Overview** file, then **Open original** | 7 | The workflow: four specialist agents in parallel, a writer agent, two checkers, cited facts |
| 0:09 | 2.1: funding the closing | 5 | Four background agents at once, then the math in code |
| 0:14 | 2.2, Fabric IQ only; then 2.5 and 2.6 in the same session | 5 | One lane for a one-IQ question; the boundary guard; Purview's DLP block |
| 0:19 | 9.4: review packs with the crash test; 9.5 in the Foundry tab while it runs | 8 | Durable multi-client work: the crash, the resume from the Foundry state store, approvals bound to each draft |
| 0:27 | Questions | 3 | |

6.3 is left out: 9.4's follow-up drafts show the approval step. With three more minutes, add 6.3 in a new session
after 2.5.

## How to drive the app

- **New session** (top left) starts a conversation. **Review packs**, just below it, opens the panel for step 9.4.
- Wait for each answer before you send the next prompt; while a run is going, the send button is a stop button.
- A new session's welcome screen has the prompts of 1.1, 2.1 and 1.5 as suggestions, and a **One IQ at a time** row
  (Work IQ, Fabric IQ, Foundry IQ, Web IQ) that sends the four questions of step 2.2.
- Under each answer: **"Grounded in …"** counts the Microsoft 365 sources, the **source chips** open each source on
  the right (**Open original** opens the real item), and the grey **X-ray** bar ("N skill calls / M tool calls")
  opens every call.
- Inside the X-ray: first **the agents' lanes**, one bar per specialist agent, the writer agent and the reviewer agent (bars that overlap
  ran in parallel), then the **verdict card** for a brief, then every call. The lead agent's own calls come first; each
  specialist agent's calls carry a lane badge, such as **Client context · Work IQ**, and a line naming the IQ it reached and
  as whom.
- The X-ray usually fills in when the answer arrives. While a run is going, say what the agents are doing.

| X-ray entry | Say |
|---|---|
| `load_skill` (team-routing) | "The lead picked a route: the brief workflow, one specialist, or several in parallel." |
| `recall_client_memory` | "Memory from the last discussion, from the Foundry state store." |
| `prepare_client_brief` | "The brief workflow: four specialists, a writer, then a number check and a compliance reviewer." |
| `ask_client_context`, `ask_portfolio`, `ask_firm_policy`, `ask_market` | "One specialist, for a question about one kind of fact." |
| `background_agents_start_task`, `background_agents_wait_for_first_completion`, `background_agents_get_task_results` | "Several specialists at once, as background agents; the lead reads every result before it answers." |
| **Client context · Work IQ**: `workiq / fetch`, `workiq / retrieve`, `workiq / ask`, `workiq / list_agents`, `delegate_to_work_iq` | "Work IQ, as you: exact Graph reads, search by meaning, Copilot reasoning, and Work IQ's own agent over A2A." |
| **Client context · Work IQ**: `list_client_documents`, `inspect_document`, `read_chart` | "Harness tools that use Work IQ as you: the client's folders, a verified page, the chart reader fallback." |
| **Portfolio · Fabric IQ**: `query_portfolio_data` | "The portfolio system of record, as you." |
| **Firm policy · Foundry IQ**: `search_firm_knowledge` | "The firm's policy library, cited by document and section." |
| **Market · Web IQ**: `search_public_web` | "Public news and prices; a guard keeps client data inside the boundary." |
| **Writer · math in code**: `rebalancing_check`, `liquidity_check`, `to_eastern_time` | "The arithmetic and the time zones run in code, never in the model." |
| **Compliance reviewer · Foundry IQ**: `search_firm_knowledge` | "The checker verifies the cited rules and the wording before you see the brief." |

## Opening (3 minutes)

**Say:** "An advisor meets a client on Thursday. The facts about one position live in five places: the portfolio
system, a PDF with a chart in it, the analyst's email, a recorded meeting and a Teams chat. To build an agent for that,
you'd write a connector per source, copy the content into an index, keep permissions in sync, and still have to prove
where every number came from. Watch a multi-agent solution do it inside one Foundry hosted agent, through the four Microsoft
IQs, as the advisor, with every call on screen. Later we'll crash it, twice."

**Say (the four IQs):** "Work IQ is how people work: mail, meetings, chats and files in Microsoft 365, always as the
signed-in user. Fabric IQ is the live state of the business, here the portfolio system. Foundry IQ is what the firm
knows: its policies. Web IQ is what the world is saying, and it's the only one outside your boundary, so a guard keeps
client data in."

**Say (the agents, 30 seconds):** "Think of a small research desk of AI agents inside one hosted agent. A lead agent
talks to you, plans and hands work to specialist agents: the client context agent reads Microsoft 365
through Work IQ, the portfolio agent reads Fabric, the firm policy agent reads the firm's library through Foundry IQ,
and the market agent reads the public web through Web IQ. Each specialist agent has only its own tools and returns a
typed report. For a brief, a fixed workflow runs them in parallel; a writer agent drafts with the math done in code;
and a number check and a compliance reviewer agent must both pass it before you see it. Work IQ and Fabric IQ run as
you, and the X-ray shows one lane per agent."

## 1. Meeting prep across the four IQs (6 minutes)

**1.1** New session. Send (or click the first suggestion):

> "Prepare me for my Thursday meeting with Michael Scott."

Budget 180 s. While it runs, say: "The lead agent recalls our last discussion and starts the brief workflow.
The client context, portfolio and firm policy agents start at once; the market agent starts as soon as the portfolio
agent names the largest holding, so the web query carries only a public company name; then the writer agent drafts,
and the number check and the reviewer agent check."

**1.2** Open the X-ray. Point at the lanes: Client context, Portfolio, Firm policy and Market overlapping, then Writer,
then Compliance reviewer; and the verdict card: **Cleared by the number check and the compliance reviewer.** Point at
the line under a `workiq / retrieve` call in the Client context lane: which IQ, as whom, and what it asked. Open its
**Input / output**: the query and what Work IQ returned (shown during the live run only: the app doesn't store a
specialist agent's payloads). If the card reads **Not cleared**, read one finding aloud: the checkers held the brief
back and say why. Then show the T-10 backup brief.

**Say:** "All four Microsoft IQs, each with the right identity: Work IQ and Fabric IQ as you, Foundry IQ as the firm,
Web IQ for public topics only. And nothing reached you until two checkers passed it."

**1.3** Read the brief. It arrives in one piece under the lead agent's one-line answer, exactly as the checks cleared it: no
model retypes it. Point at: Thursday, 10:00-11:00 AM ET; NVDA at 26.1% against the 25% limit (Fabric IQ, with the
analyst's email agreeing); the closing on December 15 with $400,000 due December 10; the tax budget, $20,000 signed and
in force against the accountant's $15,000; the 529 email; CP-104 §3 and OPS-220 §2, each labelled (Foundry IQ); the
tax disclosure that ends **Risks and conflicts**, word for word: "Contoso Wealth does not provide tax or legal advice.
Please consult your tax adviser."; the **Public context (Web IQ)** line with its link; the **Client documents** line;
and **Sources** last, one line per IQ, with nothing after it.
Every fact is cited where it is stated, with the item, its date and its IQ, such as "(Michael's email, October 17,
2026 (Work IQ))". **Say:** "Every fact says where it came from, and when."

**1.4** Point at the line above the chips ("Grounded in … read as you through Work IQ"), then click: the email
"Change of plans: closing moved to December 15"; the meeting "Year-end planning - Michael Scott"; the Teams message;
the file **IPS v2 DRAFT** (SharePoint); the **Portfolio Overview** PDF (OneDrive), then **Open original**.

**Say:** "Every source opens, read with the advisor's own permissions."

**1.5** (Optional.) New session. Send (or click the third suggestion):

> "What changed since my last discussion with Michael Scott?"

Budget 180 s. Point at: only what is new since the saved discussion, each item with its source and date.

## 2. The four IQs together, then one at a time (10 minutes)

**2.1** New session. Send (or click the second suggestion):

> "Michael Scott needs $400,000 for his home closing. How could we fund it, and what has to happen before the money moves?"

Budget 215 s. In the X-ray: `background_agents_start_task` four times in one step, four overlapping lanes, then
`liquidity_check` and `rebalancing_check` from the lead agent. Point at: what each IQ found (Fabric IQ cash and limits, Work
IQ the deadline and the budgets, Foundry IQ OPS-220 and CP-104, Web IQ the news); the shortfall and the trim from code;
the steps before a wire; and that it recommends no trade.

**Say:** "That's Microsoft IQ in one question, answered by four specialist agents working at the same time, each with its
own identity and only its own tools."

**2.2** One IQ at a time. Before each question: **New session**, then click its button in the **One IQ at a time** row
(or send the text). Only that IQ's lane may run.

Work IQ (budget 190 s):

> "Bring me up to speed on Michael Scott: when we next meet, what he has asked us for, what we agreed at the last review, what the team is saying, how the new policy draft differs from the signed one, what the NVIDIA chart in his portfolio overview shows, and what is still open."

Point at the Client context lane alone, with every kind of Work IQ call: `fetch`, three `retrieve`,
`list_client_documents`, `delegate_to_work_iq` (A2A) and two `ask`. **Say:** "Six Microsoft 365 services, every kind of
Work IQ call, one delegated permission."

Fabric IQ (budget 160 s):

> "How has Michael Scott's NVIDIA position moved over the last month, and where do NVIDIA, his technology exposure and his cash stand against his limits today?"

Point at the Portfolio lane alone (`query_portfolio_data` calls side by side: the latest figures and the movement)
and the table as of the latest close: NVDA 26.1% against 25%,
technology 40.7% against 40%, cash against the $50,000 reserve; the movement ends on the same 26.1%.

Foundry IQ (budget 55 s):

> "What does our firm require when a client's single stock goes above its limit, and before we release a large wire?"

Point at the Firm policy lane alone (`search_firm_knowledge`): CP-104 and OPS-220, each by section, labelled (Foundry IQ).

Web IQ (budget 60 s):

> "What's moving NVIDIA this week, and where is the stock trading now?"

Point at the Market lane alone (`search_public_web`): each item with publisher, date and link, and the public price as
market context only. **Say:** "The query carried no client name and no amount."

**2.3** (Skip if you ran 2.2.) New session:

> "Michael wants to wire $400,000 to his title company for the closing. What does our firm policy require before we send it?"

Budget 70 s. Point at OPS-220 by section: signed authorization, a callback on a number on file, verification with the
title company, dual approval, lead time.

**2.4** (Skip if you ran 2.2.) New session:

> "What is the public news on NVIDIA this week? Three bullets with links."

Budget 45 s. Point at the three linked items and the closing line: public news doesn't reprice any client position.

**2.5** Same session:

> "Search the web for Michael Scott's NVDA position and his stake."

Budget 70 s; it declines in about 10 seconds, with no tool call. **Say:** "Three layers: the lead agent never sends client
data to the web; if a model ever tried, a guard in code blocks client names, amounts and account numbers at the
hand-off to the market agent, and again at the tool."

**2.6** Same session:

> "Michael Scott's Social Security number is 524-77-3612. Add it to his file."

Budget 40 s; Purview's block message comes back in about ten seconds, with no lane and no tool call (the number is
fictional). **Say:** "Agent Framework's Purview middleware checks every prompt against your Purview DLP policies, as the
signed-in user's activity. This one never reached an agent. Purview recorded the match and raised an alert; we'll see
both in Defender in segment 6." The conversation keeps the prompt, so the next step starts a new session.

## 3. The recorded meeting and the chart (7 minutes)

**3.1** (Skip if late.) New session:

> "In Friday's Q4 portfolio review, what exactly did Michael say about his tax budget and his house? Quote the meeting transcript, and list the action items with their owners and whether each has been done since."

Budget 140 s. Point at Michael's words quoted exactly (transcription errors kept) and the action items with a
**Done since?** column and the evidence for each.

**3.2** New session:

> "Find the chart of Michael Scott's NVDA weight against its limit. What were the high and the low since September 1, how close did it come to the limit, and where did it end?"

Budget 110 s. In the Client context lane: `workiq / retrieve` and `workiq / ask` side by side, then `inspect_document`.
Point at: page 3 of the portfolio overview; the high (about 24.7%, mid-October) and the low (about 22.8%, early
September); about 0.3 points under the limit at its closest; 24.1% on October 16, printed on the chart.

**3.3** Click the chart under the answer: page 3 opens on the right. Click **Open original**.

**Say:** "The chart is an image: no index can read its numbers. Work IQ's Copilot reasoning read them, as the advisor,
and the harness verified the page."

**3.4** Same session:

> "Since that chart, where does Michael's NVDA position stand now? Check the analyst's latest email against the portfolio data in Fabric, tell me whether they agree, what Michael and the team said about trimming it, and what a trim to the limit would mean."

Budget 180 s. Two lanes overlap: Client context and Portfolio; then `rebalancing_check`. Point at: 24.1% on October 16,
then 26.1%; the email and Fabric agree (26.1%, $763,857.78), each figure cited to both (Work IQ and Fabric IQ); what
Michael and the team said; a trim to 25% sells about
$32,167 and realizes about $25,633, over both tax budgets.

## 4. Work IQ versus calling Graph (4 minutes)

**4.1** New session:

> "How is Work IQ different from just calling the Graph API? Show me side by side on this question: where is the chart of Michael Scott's NVDA position against its limit, and what did Michael ask about it by email?"

Budget 90 s. In the Client context lane: the Graph path (`workiq / call_function`, `workiq / fetch` keyword searches)
and the Work IQ path (`workiq / retrieve`, then `inspect_document`). Point at the table: Graph found the file but not
the page, and only email headers; Work IQ found page 3 and Michael's question, "getting close to the 25% limit".

**Say:** "Graph matches words and returns lists. Work IQ matches meaning and returns passages with citations, across
sources, through one governed door. And it isn't either-or: Work IQ's `fetch` is Graph, through that same door."

## 5. Work IQ beyond search (4 minutes)

If you ran the Work IQ question in 2.2, it already compared the two IPS files and delegated a task over A2A: say so and
go to 5.3.

**5.1** New session:

> "Compare Michael Scott's signed IPS with the IPS v2 draft. Which terms changed, and which version is in force?"

Budget 120 s. Point at `workiq / ask` with the two exact files, the term-by-term table and "the signed IPS is in force".

**5.2** Same session:

> "Delegate to Work IQ: what did Michael Scott ask me for in his recent emails, and what is still open?"

Budget 180 s. Point at `delegate_to_work_iq` (Work IQ Chat over A2A, task completed) and what is still open.
**Say:** "MCP gives our specialist agent Work IQ's tools, step by step. A2A hands a whole task to Work IQ's own agent."

**5.3** (Skip if late.) Same session:

> "Which Copilot agents can you work with through Work IQ?"

Budget 60 s. Point at `workiq / list_agents`: the Copilot agents this user can reach, each with its agent ID.

## 6. Governance (8 minutes)

**6.1** Admin center tab: **Agents** > **Tools** > **Work IQ MCP** > **Policies** > **Tenant Default**. Show Directory
lookups **off**; Allow write actions and Allow create **on**; partial update, replace and delete **off**. Never click a
toggle or **Save**. **Say:** "The policy is tenant-wide today; finer, agent-level controls are coming." No dates.

**6.2** App, new session:

> "What is this agent allowed to do with Work IQ? Test it."

Budget 90 s. Point at the table: own mail and own OneDrive **Allowed**; directory lookup (`/users`), update and delete
**Denied** by the Work IQ tenant policy. **Say:** "A live test, and nothing changed: the writes targeted an item that
doesn't exist."

**6.3** Same session:

> "Draft a short follow-up email to Michael Scott confirming our Thursday meeting and listing the three agenda topics from his latest email."

Budget 110 s to the **Approval needed** card. Check it says 10:00-11:00 AM and lists three topics. Click **Approve:
create draft** and show it in Outlook Drafts (delete it after the demo), or **Reject** to show nothing is written.
**Say:** "Two locks: a person approves the exact text, and the tenant policy allows create but not update or delete."

**6.4** (Skip if late.) Window B, signed in as the analyst account. New session:

> "Prepare me for my Thursday meeting with Michael Scott."

Budget 180 s. Point at what's missing: the advisor's own emails, the advisor's OneDrive folder and saved memory; the
team's SharePoint files are there. **Say:** "Same agent, same prompt, a different person. Nobody wrote a permission
check: every lane runs as the signed-in user, and memory is per user."

**6.5** Defender tab, **Advanced hunting**: run the walkthrough's one-user query ([Appendix A](DEMO-WALKTHROUGH.md#appendix-a-defender-advanced-hunting-queries),
"One user's Work IQ calls, in four logs") and open each row in **Inspect record**: the agents this user called (Agent
365), each Graph request Work IQ made for them (Graph activity logs), each file, email and meeting it read (Purview
Audit), and 2.6's blocked prompt with its alert (Purview DLP). **Say:** "Your SOC needs no new tool: every Work IQ call
is in the logs they already query, under the user's name." For a per-call view, run
`.\scripts\Get-WorkIqAuditTrail.ps1 -Minutes 60 -UserPrincipalName <the user>`: each burst of the agents' Work IQ calls
from the traces, next to the Graph requests and the Purview audit records it caused.

**6.6** (Skip if late.) Admin center: **Agents** > **All agents** > `advisor-team`. Show where an admin reviews its
permissions, changes them or blocks it. Don't change anything.

## 7. Durability and restart (7 minutes)

**7.1** New session:

> "Arm the crash test for the durable meeting pack."

Budget 75 s. It confirms the stop point: after wave 2 writes its checkpoints, before the progress marker moves.

**7.2** Same session:

> "Run the durable meeting pack for Michael Scott."

Budget 65 s. After wave 2 the agent process really exits: the X-ray says **process stopped**, unfinished calls say
**Interrupted**. **Say:** "We killed the process between two writes. Let's see what survived." Don't wait; go on.

**7.3** New session (the crashed one stays locked on purpose):

> "What is stored durably for Michael Scott right now?"

Budget 50 s. Point at the run: still open, four checkpoints, written by an earlier process.

**7.4** Same session:

> "Resume the durable meeting pack for Michael Scott."

Budget 105 s. Point at the durability report: wave 1 **restored**, wave 2 **reconciled and reused**, only wave 3 run,
two processes in the timeline. **Say:** "Checkpoint first, then move the marker with an ETag, then reconcile after a
crash: Foundry's guidance for long-running agents."

**7.5** (Skip if late.) New session:

> "Remember my preference: in client briefs, put Risks and conflicts first and keep them under 250 words."

Same session:

> "What is stored durably for me right now, and which process is serving you?"

In PowerShell: `.\scripts\Restart-AgentSession.ps1` (stops this session's compute, as idle eviction would). Same
session:

> "Which process is serving you now, how long has it been running, and what preference did I ask you to remember?"

Budgets: 7.5a 30 s, 7.5b 35 s, 7.5c 35 s. Point at the new process (about a minute old) that still knows the preference. Clear the
preference before segment 9 (the runbook's [reset command](DEMO-RUNBOOK.md#reset-the-presenter-controls)).

## 8. The quality loop (5 minutes)

In the Foundry portal, on project items only (the walkthrough lists the clicks):

- **8.1 Traces:** `advisor-team` > **Traces**, the brief from 1.1: the lead agent, the specialist agents and the tool calls nested.
- **8.2 Evaluate in production:** the hourly recurring evaluation of production traces, judged on `team-judge`.
- **8.3 Golden questions:** the agent-target evaluation run: built-in judges and the rubric, with reasons.
- **8.4 Before and after:** (skip if late) two saved-turn runs, compared.
- **8.5 Insights:** the latest succeeded scan and one finding. Don't click **Run now**.
- **8.6 Optimizer:** a completed job and its best candidate; a candidate reaches users only after it wins on
  `advisor-team-candidate`.

**Close the loop.** **Say:** "That closes the loop, end to end: your admins set the policy, in Agent 365 and Purview; the
agents enforce it at run time, as the user; Defender and Purview Audit keep the evidence, with alerts; and the same
traces feed Foundry's evaluations and optimizer, so the next version is measured before it ships, and runs as the same
agent, under the same policy."

**8.7** (Optional, if they want Fabric IQ again.) App, new session:

> "Where do Michael Scott's NVIDIA weight, his technology exposure and his cash stand right now, and as of when?"

Budget 105 s; the Portfolio lane alone, with the as-of date.

## 9. The agents (14 minutes)

**9.1** Go back to 1.1's session (or the T-10 backup). Open the X-ray and narrate the agents: the lanes, the overlap, the
writer agent, the reviewer agent and the verdict card. Say the 30-second explainer if you skipped it in the opening.

**9.2** New session:

> "Arm the reviewer test."

Budget 40 s. Same session:

> "Prepare me for my Thursday meeting with Michael Scott."

Budget 240 s. The writer agent's first draft carries one seeded sentence that reads like a trade recommendation; the reviewer agent
fails it, citing the firm's communication standard, and the writer agent revises once. Point at the verdict card: **Cleared
… after 1 revision**, then at **Held back from the first draft** under it (the sentence, the reason and the rule), and
at the Writer and Compliance reviewer lanes, which ran twice. The revised brief still ends **Risks and conflicts** with
the tax disclosure, and **Sources** is still last. **Say:** "Maker and checker, in code: here is the sentence it held
back; the advisor never saw it." If **Held back** lists a second sentence besides the seeded one, the reviewer agent
caught a real slip in the first draft too (in one recording, client approval before any trade, which CP-104 §3.2 sets
only for non-discretionary accounts): point at it; nobody seeded that one. If it comes back **Not cleared** after 1
revision (now and then the reviewer agent finds a second problem in the revision), read the finding aloud: the checker
caught the revision too. Move on.

**9.3** (Skip if late.) New session:

> "Take the portfolio specialist offline for the next question."

Budget 40 s. Same session:

> "Michael Scott needs $400,000 for his home closing. How could we fund it, and what has to happen before the money moves?"

Budget 215 s. Three lanes run; there is no Portfolio lane. Point at the answer saying Fabric IQ was not checked, and
the absence of invented figures. The outage clears itself after this question, so 9.4 runs with all four specialist agents.

**9.4** Click **Review packs** (sidebar). The prompt box already holds:

> "Prepare review packs for my three clients for next week."

Tick **Crash test**, click **Start review packs**. On the **Confirm the clients** card (Michael Scott, Jan Levinson,
Dwight Schrute), click **Prepare the packs**. Keep the panel open: it sends each step to the agent by itself. About 5
minutes, budget 600 s: packs 1 and 2 finish, the process stops before pack 3, and the panel sends the step again in a
new session, where a new process restores packs 1 and 2 from the Foundry state store and writes pack 3.
Then approve each **Follow-up draft** card, one at a time (each approval is bound to the draft's SHA-256). Point at the
summary's **Durability timeline**: process 1 did the plan and packs 1-2, process 2 restored them and did pack 3 and the
summary, and "restored from the Foundry state store, not redone". **Say:** "Nobody pressed resume. Each pack is
checkpointed in the Foundry state store, isolated per user, so a new process carries on where the old one stopped, and
each approval is bound to the exact draft."

**9.5** (While 9.4 runs, or skip.) Foundry tab: the brief's trace, one specialist agent's spans at a time, and the rehearsal
report's lanes. **Say:** "Each role is measured on its own: its calls, its time, its facts; and the reviewer agent's catch
rate from the reviewer test."

## Add-ons (optional)

Not in the run of show. The what-if analyst is on by default; the investment committee is experimental and
needs `enable_committee`; the morning note needs `enable_morning_note`.

**O1, the morning note** (about 4 minutes). In a PowerShell window:

```powershell
.\scripts\Invoke-MorningNote.ps1 -Tickers NVDA
```

A target of 150 s to published (the script's run, not a rehearsal budget). While it runs, open the Durable Task Scheduler dashboard (the endpoint the script prints,
task hub `advisor-team`): the watch list, then market research and the policy check side by side, then the writer agent.
At the prompt, read the draft: the exact text that gets published, with dated links and the firm's codes. Answer `y`.
**Say:** "The same Agent Framework workflow model on a second runtime: Azure Functions and the Durable Task
Scheduler. While it waits for the head of research it uses no compute, and a restart picks up after the last finished
step." Then, in the app, New session:

> "What is the house view in this morning's note on NVIDIA? Cite the note."

Budget 90 s. In the X-ray: `ask_firm_policy`, the Firm policy lane only. Point at **IC-MN-** and today's date, §NVDA
(Foundry IQ). **Say:** "Approved this morning, cited this morning: the note is firm knowledge now, in the same Foundry
IQ library as the policies."

**O3, the what-if analyst** (2 minutes). New session:

> "What if NVIDIA falls 20% before Michael Scott's closing? Assume a $4,250,000 portfolio with NVDA at 26.1% against his 25% limit, and $520,000 cash against a $450,000 reserve."

Budget 150 s. In the X-ray: `run_what_if`, the **What-if analyst · code in a sandbox** lane (and the Portfolio lane,
which supplies Fabric IQ figures when it can). Point at the before-and-after table: NVDA falls to **22.03%**, back
under the 25% limit, and the last line, "Computed in code (what-if analyst)", with the assumptions labelled.
**Say:** "Every number came from code the analyst wrote and ran in a sandbox with no network and no files, never from
the model's head."

**O4, the investment committee** (experimental; about 5 minutes). New session:

> "Convene the investment committee on Michael Scott's NVIDIA concentration against his 25% limit."

Budget 300 s. In the X-ray: `convene_investment_committee`, with the Portfolio, Firm policy and Market lanes, and the
risk officer's turn. Point at the minutes (facts, the firm's requirements with CP-104, challenges and answers, open
questions) and at **The meeting**, each member's own words, including the **Risk officer**'s challenges. **Say:** "A
conversation, not a fixed graph: members react to each other, and the chair's minutes are refused if they add a rule
or a figure nobody said." Not for customer demos: the discussion isn't repeatable.

## Questions

| If they ask | Answer |
|---|---|
| How is Work IQ different from Graph? | "Graph returns exactly what you ask for, by path; search, reading, permissions and policy are your code. Work IQ adds search by meaning with citations and Copilot reasoning, always as the signed-in user, behind one permission and one tenant policy; its `fetch` is still Graph." |
| Why several agents and not one? | "Separation of duties you can enforce in code: only the market specialist reaches the internet, the reviewer only reads, the lead holds the one write. Parallel lanes cut the wait. And each role can be measured and tuned on its own." |
| Are the specialists separate agents in Foundry? | "No: one hosted agent, one Entra agent identity, one Agent 365 entry. The roles and their limits live in code. Separate identities per role would mean separate hosted agents over A2A." |
| What survives a crash? | "The checkpoints in the Foundry durable state store: finished steps are restored, not redone. A step that was running repeats; reads are harmless, and approvals are bound to the exact text." |
| Can we limit Work IQ per agent? | "The policy is tenant-wide today. Per agent: the toolbox allowlist, source-specific servers and a gateway. Finer controls are coming; no dates." |
| Where is it logged? | "Every Work IQ call, under the user's name: Agent 365 records each call to the agents, the Graph activity logs each request Work IQ made, and Purview Audit what it read. Defender advanced hunting reads them all, next to the Foundry traces." |
| Can we keep sensitive data away from the agents? | "Yes, with Purview DLP. Agent Framework's Purview middleware checks each prompt and answer; a policy scoped to the agent's Entra app blocks the prompt before any agent sees it, and raises an alert. That was 2.6." |
| Does data leave our boundary? | "Only Web IQ is outside it, and it gets public topics only. For the rest, ask your compliance team to read the service terms; don't guess." |
| Can we deploy it ourselves? | "Yes: Terraform and PowerShell, from scratch or into existing shared resources under new names, with guardrails." |

## If something goes wrong

| You see | Do |
|---|---|
| An answer runs long | Keep narrating; the T-10 backup session has a finished brief. |
| An orange consent banner | Click each **Authorize** link once, finish sign-in, then **Retry: send my last message again**. |
| "in-flight turn", or a session that won't answer after 7.2 | That session crashed on purpose: **New session**. |
| No approval card | **New session** and send 6.3 again; typing "approve" does nothing. |
| 2.6 gets an answer instead of Purview's block | Purview isn't on for the agent, or a new DLP policy hasn't synced yet (allow an hour). Say what the block would show and move on; afterwards check `enable_purview` and `Set-PurviewDlpForAgent.ps1`. |
| A lane says "not checked" | Say which IQ wasn't checked and point out the answer carried on: the designed fallback. |
| The packs panel says "The agent process stopped. Sending the step again" | The crash test working: keep the panel open (a closed panel stops driving the run); the new process usually answers within a minute. If it says "The agent didn't come back", close it and show the transcript of the last `review_packs.py --crash-test --approve` rehearsal. |

The full fallback table is in the [runbook](DEMO-RUNBOOK.md#fallbacks).
