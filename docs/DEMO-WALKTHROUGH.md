# Demo walkthrough: Investment Advisor

The long version of the demo: for every step, what to click and send, what the X-ray and the agents' lanes show, the
facts the answer must contain, what to point at and say, and what to do when something goes wrong. Present from the
short [DEMO-SCRIPT.md](DEMO-SCRIPT.md); prepare and recover with [DEMO-RUNBOOK.md](DEMO-RUNBOOK.md). Every prompt below
is word for word the one in [`demo/expectations.yaml`](../demo/expectations.yaml), which the rehearsal runner
(`scripts/rehearse.py`) uses to check calls, lanes, facts and time.

> **Terms.** Investment Advisor is a multi-agent solution: a **lead agent** plans and delegates to four **specialist agents**, one per Microsoft IQ (the client context, portfolio, firm policy and market agents); the **writer agent** drafts the brief, and the **compliance reviewer agent** and a **number check** in code check it. *The advisor* is the person using the app.

> **Fictional data.** Contoso Wealth, the client Michael Scott, the clients Jan Levinson and Dwight Schrute, the analyst
> and every email, meeting, document, policy and figure are fictional. Nothing here is investment, tax or legal advice.

**Dates.** The story's dates follow the deployment's story week (`STORY_MEETING_DATE`, a Thursday). The figures and
dates below, and the facts in the expectations file, use the reference week: the client meeting on **Thursday,
October 22, 2026**, the recorded review on Friday, October 16, the analyst's alert on Monday, October 19, and the demo
on the Tuesday or Wednesday in between. To present the week after its Thursday has passed, the agents' clock is held on
the story's Wednesday afternoon (`ADVISOR_CLOCK_NOT_AFTER`, [runbook](DEMO-RUNBOOK.md#one-time-setup)), so the meeting
is still ahead.

**Timings.** Each prompt the rehearsal runner sends gives its **budget**: the time the rehearsal allows (`budget_s`).
The measured times are kept in one place, the runbook's [timing table](DEMO-RUNBOOK.md#timing-table), with the date
of the rehearsal that measured them.

**Citations.** Every answer, not only the brief, cites each fact where it states it: the item it came from, its date
and its IQ, for example "(Michael's email, October 17, 2026 (Work IQ))" or "as of October 19, 2026 (Fabric IQ)". A
figure two sources agree on cites both; a calculated figure names its tool ("(rebalancing_check)").

## The pitch in three lines

- **Work IQ** gives an agent governed access to a person's Microsoft 365 work, as that person: exact Graph reads,
  search by meaning with citations, and Copilot reasoning, behind one permission and one tenant policy. Next to it,
  **Fabric IQ** is the portfolio system of record, **Foundry IQ** the firm's own policies, and **Web IQ** the public
  web, behind a guard.
- **A multi-agent solution inside one hosted agent.** A Microsoft Agent Framework harness lead agent plans and delegates to specialist agents,
  each with only its own tools; durable workflows run the brief, the meeting pack and multi-client review
  packs, with a maker-checker review and approvals that survive a crash.
- **Nothing is hidden, and it gets better:** the X-ray shows every call in one lane per specialist agent; Foundry traces,
  evaluations, Insights and the optimizer turn that evidence into measured improvements.

## How the agents work (say it in 30 seconds)

> Think of a small research desk of AI agents inside one hosted agent. A lead agent talks to you, plans and hands
> work to four specialist agents, one per Microsoft IQ: the client context agent reads your Microsoft 365 through Work
> IQ, the portfolio agent reads Fabric, the firm policy agent reads the firm's library through Foundry IQ, and the
> market agent reads the public web through Web IQ, behind a guard. Each specialist agent has only its own tools and
> returns a typed report. For a brief, a fixed workflow runs them in parallel, a writer agent drafts with the math done
> in code, and a number check and a compliance reviewer agent must both pass it before you see it. Work IQ and Fabric
> IQ run as you, and the X-ray shows one lane per agent.

| Role | Lane in the X-ray | Reaches | Runs as | Returns |
|---|---|---|---|---|
| Lead agent (harness agent) | the top-level calls | its specialist agents, memory, the math tools, the durable meeting pack, the policy probe, the one approved write | the agent, for the signed-in advisor | the answer |
| Client context agent | **Client context · Work IQ** | Work IQ MCP tools, Work IQ Chat over A2A, the client's folders, page and chart checks | the signed-in advisor | a typed report |
| Portfolio agent | **Portfolio · Fabric IQ** | the Fabric data agent | the signed-in advisor | a typed report, figures as Fabric gave them |
| Firm policy agent | **Firm policy · Foundry IQ** | the Foundry IQ knowledge base | the project's managed identity | a typed report, rules by code and section |
| Market agent | **Market · Web IQ** | Web IQ, public topics only | an API key | a typed report, links and prices |
| Writer agent | **Writer · math in code** | `rebalancing_check`, `liquidity_check`, `to_eastern_time` | the agent | the draft brief |
| Compliance reviewer agent | **Compliance reviewer · Foundry IQ** | the Foundry IQ knowledge base, read-only | the project's managed identity | a typed verdict |

How the lead agent picks a route (its `team-routing` skill):

| Question | Route | What you see |
|---|---|---|
| A meeting brief | **A.** The brief workflow, `prepare_client_brief` | Four specialist agent lanes, then the Writer and Compliance reviewer lanes, and the verdict card |
| One kind of fact, in business words | **B.** One specialist agent, `ask_<role>` | One lane only |
| Several kinds of fact | **C.** Several specialists as background agents | Overlapping lanes, then the lead agent's own math |
| The meeting pack, what's stored, preferences, the policy test, the draft, presenter controls | The lead agent's own tools | No specialist agent lane (the draft asks the client context agent for its facts first) |

## Before the call

The full checklist, with commands and times, is in the [runbook](DEMO-RUNBOOK.md#before-the-demo). In short: consent
done for the advisor and the analyst account, the prewarm run, Web IQ checked, a backup brief at T-10, old sessions
and drafts deleted, and the presenter controls reset.

### Windows and tabs

Use two browser windows, each signed in to the demo tenant only:

- **Window A** (Edge InPrivate): the advisor, the account that deployed the solution.
- **Window B** (a separate browser profile): the analyst account (`DEMO_ANALYST_UPN`), on the demo app, for step 6.4.

Open these tabs in window A, in this order:

1. **The demo app**, "Investment Advisor": its address is `terraform -chdir=terraform output -raw ui_url`.
2. **Microsoft 365 admin center**, https://admin.cloud.microsoft: **Agents** > **Tools** (Work IQ MCP policies, 6.1)
   and **Agents** > **All agents** (6.6).
3. **Foundry portal**, https://ai.azure.com: your project > **Agents** > `advisor-team` (**Traces**, **Insights**,
   evaluations).
4. **Foundry portal** again: **Evaluations**, with the recurring configuration and the golden-question run (8.2-8.4).
5. **Foundry portal** again: the optimizer job (8.6).
6. **Outlook on the web, Drafts**, https://outlook.office.com/mail/drafts (6.3).
7. **Defender advanced hunting**, https://security.microsoft.com/v2/advanced-hunting (6.5; close the tour pop-ups once).
8. **Microsoft Purview**, **Data loss prevention** > **Policies**, https://purview.microsoft.com/datalossprevention/policies
   (the policy behind 2.6's block, if they ask).
9. **Microsoft Learn**, one tab per page you may show:
   [Work IQ overview](https://learn.microsoft.com/en-us/microsoft-365/copilot/extensibility/work-iq/),
   [Microsoft IQ](https://learn.microsoft.com/en-us/microsoft-iq/),
   [Durable state store](https://learn.microsoft.com/en-us/azure/foundry/agents/concepts/agent-state-store),
   [Manage state for long-running agents](https://learn.microsoft.com/en-us/azure/foundry/agents/how-to/manage-task-state).

Zoom 125%, notifications off. If a consent banner ever appears: click each **Authorize** link once, finish the sign-in
in the new tab, close it, then click **Retry: send my last message again**. Consent is once per user and agent.

## Run of show

| Time | Segment | What it proves |
|---|---|---|
| 0:00-0:03 | Opening | The problem (facts in five places), the four IQs, the agents in 30 seconds |
| 0:03-0:09 | 1. Meeting prep across the four IQs | The brief workflow: four lanes, a writer agent, two checkers, every source opens |
| 0:09-0:19 | 2. The four IQs together, then one at a time | Background agents in parallel; one IQ alone; the boundary guard; Purview's DLP block |
| 0:19-0:26 | 3. The recorded meeting and the chart | Work IQ reads a transcript and a chart image; sources checked for agreement |
| 0:26-0:30 | 4. Work IQ versus calling Graph | Both paths, side by side, measured |
| 0:30-0:34 | 5. Work IQ beyond search | Copilot over exact files, A2A delegation, the Copilot agents you have |
| 0:34-0:42 | 6. Governance | The tenant policy, a live test, an approved write, a second user, the SOC view (every Work IQ call, the DLP block), Agent 365 |
| 0:42-0:49 | 7. Durability and restart | A crash between two writes, resumed; a restart that keeps memory |
| 0:49-0:54 | 8. The quality loop | Traces, evaluations, Insights, the optimizer; the loop, closed end to end |
| 0:54-1:08 | 9. The agents | Maker-checker, a specialist agent offline, durable review packs that resume from the Foundry state store after a crash |
| 1:08 | Questions | |

Work IQ and model timings vary from run to run. If an answer runs long, keep narrating the plan; the calls show when
the answer arrives. If you are behind, skip in this order: 2.3 and 2.4, 3.1, 7.5, 6.4, 5.3, 9.3, 9.5.

## Opening (3 minutes)

**Say:** "Let's start with the problem, not the product. An advisor meets a client on Thursday. The facts about one
position, say his NVIDIA stake, live in five places: the portfolio system, a PDF with a chart in it, the analyst's
email, a recorded meeting and a Teams chat. Today a person stitches that together. An agent that did it would need a
connector per source, a copy of the content in a search index, permissions kept in sync, and proof of where every
number came from. Watch a multi-agent solution do it inside one Foundry hosted agent, through the four Microsoft IQs, as the
advisor, with every call on screen. A question we often get is how Work IQ differs from calling Microsoft Graph
yourself: every call you'll see is part of the answer, and in segment 4 we run the same question both ways. Then we'll
crash it, twice."

| Where the facts live | In this demo | Who reaches it, and how |
|---|---|---|
| The portfolio system | NVDA weight and value at the latest close | **Portfolio** specialist agent: **Fabric IQ**, the Fabric data agent "Team Portfolio Analyst", as the advisor |
| Documents | The portfolio overview's chart; the signed and the draft IPS | **Client context**: **Work IQ** `retrieve` finds them, `ask` reads the chart and compares the two files, `inspect_document` verifies the page |
| Email | The analyst's Monday figures; Michael's change of plans | **Client context**: **Work IQ** `fetch`, exact Graph reads, as the advisor |
| Meetings | Friday's recorded Q4 review | **Client context**: **Work IQ** `retrieve` (Meetings) and `ask` for the transcript's words |
| Chat | The team's Teams messages about a trim | **Client context**: **Work IQ** `retrieve` (Teams messages) |
| The firm's policies | OPS-220 for large wires, CP-104 for concentrated positions | **Firm policy**: **Foundry IQ** knowledge base, as the project's managed identity |
| Public news | NVIDIA this week | **Market**: **Web IQ**, public topics only, behind the boundary guard |

**Say (the four IQs):** "Microsoft IQ is four kinds of intelligence. Work IQ is how people work, from Microsoft 365.
Fabric IQ is the live state of the business, here the portfolio system. Foundry IQ is what the firm knows: its
policies. Web IQ is what the world says, and it's the only one outside your boundary, so a guard keeps client data in."

**Say (the architecture):** "The app calls one agent hosted in Microsoft Foundry, built on the Microsoft Agent Framework
harness. Inside it, a lead agent and four specialist agents; every tool call goes through a Foundry toolbox, Work IQ and Fabric IQ
as the signed-in user. Memory and checkpoints live in Foundry's durable state store, and every call is traced."

Then say the 30-second explainer above.

## Segment 1: meeting prep across the four IQs (6 minutes)

**Goal:** the brief workflow doing real work, with every call on screen and every source openable.

### 1.1 The brief

**Do:** click **New session**, then the first suggestion, or send:

> "Prepare me for my Thursday meeting with Michael Scott."

**Time:** budget 180 s. Needs Work IQ consent, Fabric (capacity running) and Web IQ.

**While it runs, say what the agents are doing** (the X-ray usually fills in when the answer arrives):

1. The lead agent loads its `team-routing` skill (route A), calls `recall_client_memory` (the last discussion, from the
   Foundry state store) and adds three to-dos: research with the specialist agents, writer and checks, present.
2. It calls `prepare_client_brief`, which runs workflow W1:
   - **Client context** (Work IQ), in one step: `workiq / fetch` with two Graph paths (the calendar from 10 days back to
     21 days ahead, and a mail search for Michael); three `workiq / retrieve` calls (OneDrive and SharePoint: the signed
     IPS, the draft, the meeting notes and the portfolio overview; Meetings: topics, decisions and action items; Teams
     messages: limits, trims and the tax budget); `list_client_documents` (Michael's folders in the advisor's OneDrive
     and on the team's SharePoint site); then `to_eastern_time` for the meeting and message times.
   - **Portfolio** (Fabric IQ), at the same time: `query_portfolio_data` for the total value, cash, the technology
     weight, the largest position (ticker, public company name, weight, value, shares, price, cost basis) and the
     single-stock limit.
   - **Firm policy** (Foundry IQ), at the same time: `search_firm_knowledge` for a position above its limit and for a
     large wire.
   - **Market** (Web IQ), as soon as portfolio returns: `search_public_web` for this week's news on the largest
     holding, by its public name (NVIDIA), and its latest public price. The task carries no client name and no amount.
   - **Writer**: `to_eastern_time`, `rebalancing_check` once per tax budget ($20,000 signed, $15,000 proposed) and
     `liquidity_check` for the $400,000 need; then the brief, under 450 words plus Sources, every fact cited where it
     is stated.
   - **Number check** (code) and **Compliance reviewer agent** (Foundry IQ), in parallel: every material figure must appear
     in the evidence or a tool's output; each cited rule is checked against the library, and the wording must state
     facts, risks, rules and questions, never advice.
   - **Decide:** both pass, so the brief is **Cleared**.
3. The lead agent answers with one line naming the agents, and the app adds the brief below it exactly as the workflow returned
   it: no model retypes the brief (the quality loop caught the lead agent changing a link while copying it).

### 1.2 The X-ray and the agents' lanes

**Do:** click the grey X-ray bar under the answer.

**Point at, top to bottom:**

- **The agents' lanes:** "6 agents; bars show when each worked (overlap = in parallel)." Client context,
  Portfolio and Firm policy start together; Market starts when Portfolio ends; Writer after all four; Compliance
  reviewer last. Each row shows its calls and seconds.
- **The verdict card:** "**Cleared** by the number check and the compliance reviewer."
- **The lead agent's calls:** `load_skill`, `recall_client_memory`, `prepare_client_brief`. The lead agent's own calls have
  **Input / output**: open `prepare_client_brief` to show the task (the client's name) and the finished brief.
- **The specialist agents' calls,** each with its lane badge and a line under it that names the IQ and the identity, for
  example a `workiq / retrieve` in **Client context · Work IQ** read as you, or `search_firm_knowledge` in **Firm
  policy · Foundry IQ** as the project's identity. (Nested calls show this line, not their raw payloads.)

**Say:** "All four Microsoft IQs at once, each with the right identity: Work IQ and Fabric IQ as you, Foundry IQ as the
firm, Web IQ for public topics only. Then a writer agent, and two checkers, before anything reached you."

### 1.3 Read the brief

The brief's sections: **Snapshot**, **Client documents**, **Since last discussion**, **Risks and conflicts**,
**Questions to ask**, and **Sources** last. It must contain (the rehearsal checks each one; the citations, by their
"(Work IQ)" labels):

- the meeting: **Thursday, 10:00-11:00 AM** Eastern, from the calendar;
- NVDA at **26.1%** against the **25%** single-stock limit (Fabric IQ, as of the latest close; the analyst's email
  agrees), and technology at 40.7% against 40%, each breach in its own bullet: CP-104 covers a single stock, so the
  sector breach carries its figures and no firm rule;
- the closing moved to **December 15**, with **$400,000** due by **December 10**;
- the tax budget: **$20,000** in the signed IPS, in force, against the accountant's **$15,000** (also in the unsigned
  IPS v2 draft), both shown with their sources;
- a trim to the limit of about $32,167 that realizes about $25,633, over both budgets, and a liquidity shortfall
  (about $167,781, or $192,781 with the $25,000 529 plan from Michael's latest email);
- the firm rules each risk triggers, labelled **(Foundry IQ)**: **CP-104 §3** (a documented review after a breach)
  and **OPS-220 §2** (authorization, callback and dual approval before a large wire);
- because the risks touch on taxes, **Risks and conflicts** ends with the firm's standard disclosure (COM-310), word for
  word: "Contoso Wealth does not provide tax or legal advice. Please consult your tax adviser.";
- a **Public context (Web IQ)** line with publisher, date and link, or "not checked; Web IQ is unavailable right now";
- a **Client documents** line linking every file in Michael's folders: the signed IPS and the portfolio overview in
  OneDrive, the IPS v2 draft and the Q4 meeting notes in SharePoint;
- every fact cited where it is stated, with the item it came from, its date and its IQ, for example "(Michael's
  email, October 17, 2026 (Work IQ))", "as of October 19, 2026 (Fabric IQ)", "(CP-104 §3.1 (Foundry IQ))" or
  "(rebalancing_check)" for a calculated figure;
- **## Sources** at the end, one line per IQ, with the files listed under OneDrive and SharePoint.

**Say:** "Every fact says where it came from: which email, which meeting, which file, which rule, and when. The chips
below open each one."

### 1.4 Open the sources

**Point at** the line above the chips: "Grounded in … emails · … meeting · … Teams message · … files, read as you
through Work IQ." **Click**:

- the email **"Change of plans: closing moved to December 15"**: the pane shows the email, read with the advisor's own
  permissions; **Open in Outlook** opens the message;
- the meeting **"Year-end planning - Michael Scott"**: time, organizer, attendees;
- the Teams message: the thread with the cited message highlighted (choose **Use the web app instead** if Teams offers
  the desktop app);
- the file **IPS v2 DRAFT** (SharePoint): the document itself, with **Previous page** and **Next page**;
- the **Portfolio Overview** PDF (OneDrive), then **Open original**.

**Say:** "Every source opens, read with the advisor's own permissions. The chips come from what the specialist agents cited,
copied by code, not retyped by the model."

**If it goes wrong:** a consent banner means Authorize, then Retry. If the brief passes three minutes, keep narrating;
the T-10 backup session has a finished brief. If the verdict says **Not cleared**, the card lists the findings: read
one out, it's the checker working (see 9.2).

### 1.5 What changed (optional)

**Do:** **New session**, then the third suggestion, or send:

> "What changed since my last discussion with Michael Scott?"

**Time:** budget 180 s. Needs Work IQ consent.

**X-ray:** the lead agent's `what-changed` route: `recall_client_memory` for the baseline's date, then client context
(and portfolio for any figure that moved), as background tasks.

**Expected:** only what is new since the saved discussion, each item dated and sourced (the closing email, the
analyst's alert, the IPS v2 draft, the 529 question).

## Segment 2: the four IQs together, then one at a time (10 minutes)

**Goal:** Microsoft IQ in one question, answered by four specialist agents at once; then each IQ on its own; then the guard.

### 2.1 All four IQs in one answer

**Do:** **New session**, then the second suggestion, or send:

> "Michael Scott needs $400,000 for his home closing. How could we fund it, and what has to happen before the money moves?"

**Time:** budget 215 s. Needs Work IQ consent, Fabric and Web IQ.

**X-ray:** the lead agent loads `team-routing` and `microsoft-iq-answer` (route C), calls `recall_client_memory` and
`background_agents_start_task` four times **in one step**: client context (the cash need and deadline, the signed
reserve and tax budget, any lower budget, the analyst's figures), portfolio (cash, the largest position, the sector
weight, the limits), firm policy (the rules for a breach and for a large wire), market (public news on the largest
holding, by its public name). Then `background_agents_wait_for_first_completion` until all four are back,
`background_agents_get_task_results`, and the lead agent's own `liquidity_check` and `rebalancing_check`. **Four lanes
overlap.**

**Expected answer** (fixed shape):

- **What each IQ found**, one line per IQ: **Fabric IQ** (portfolio $2,926,762.55; cash $282,219.15 against the
  $50,000 reserve; NVDA 26.1% and technology 40.7% against their limits); **Work IQ** ($400,000 due December 10 for
  the December 15 closing, the accountant's $15,000, the 529 question); **Foundry IQ** (OPS-220 for the wire, CP-104
  §3 for the breach); **Web IQ** (public NVIDIA news with a link);
- **Putting it together:** the shortfall (about $167,781, or $192,781 with the 529) and what a trim to 25% sells and
  realizes against each tax budget;
- **What must happen first:** numbered steps, each with its rule (the CP-104 review and client contact before any
  trade; for the wire, OPS-220's authorization, callback, verification and dual approval, with lead time);
- **Questions to ask**, then the line "Four Microsoft IQs in one step, in parallel, each with its own identity."

**Say:** "That's Microsoft IQ in one question. Fabric IQ says what he has, Work IQ what he and the team said, Foundry IQ
what the firm requires, Web IQ what the world is saying: four specialist agents at the same time, each with only its own
tools, and the math in code. And it recommends no trade: it gives the advisor the facts and the steps."

### 2.2 One IQ at a time

**Do:** before each question, **New session**, then its button in the **One IQ at a time** row (or send the text).
Each question names no IQ and no technology: the lead agent works out which specialist agent holds the answer, and only that lane
may run (the rehearsal fails the step if another lane runs).

**Work IQ, at full stretch** (budget 190 s; needs Work IQ consent):

> "Bring me up to speed on Michael Scott: when we next meet, what he has asked us for, what we agreed at the last review, what the team is saying, how the new policy draft differs from the signed one, what the NVIDIA chart in his portfolio overview shows, and what is still open."

- **X-ray:** `ask_client_context`; only the **Client context · Work IQ** lane. Inside it, the `work-iq-full-picture`
  skill: first step `workiq / fetch` (calendar and mail in one call), three `workiq / retrieve` (the meeting
  recording, Teams, OneDrive and SharePoint), `list_client_documents` and `delegate_to_work_iq` (Work IQ's own agent,
  over A2A); second step two `workiq / ask` with exact files (the two IPS versions; the chart image). No
  `query_portfolio_data`, `search_firm_knowledge` or `search_public_web`.
- **Expected:** one section per part of the question, each naming the Work IQ capability: Thursday's meeting and the
  latest emails (fetch); what was agreed at the review (retrieve, the recording); what the team is saying (retrieve,
  Teams); the client's documents (OneDrive and SharePoint); the draft against the signed IPS, with the signed one in
  force (ask over two files); what the chart shows (ask over the image: low about 22.8%, high about 24.7%, 24.1% at
  the end); what is still open (A2A, with its task ID); and the closing line "One answer, every kind of Work IQ call…".
- **Say:** "One plain question, and Work IQ answered every part of it: exact Graph reads, search by meaning inside a
  recording and the files, Copilot reasoning over two exact files and a chart image, and a whole task handed to Work
  IQ's own agent. Six Microsoft 365 services, one delegated permission, one tenant policy."

**Fabric IQ** (budget 160 s; needs Work IQ consent and Fabric):

> "How has Michael Scott's NVIDIA position moved over the last month, and where do NVIDIA, his technology exposure and his cash stand against his limits today?"

- **X-ray:** `ask_portfolio`; only **Portfolio · Fabric IQ**, with `query_portfolio_data` calls side by side: the
  latest figures and the movement as separate short questions (one long question ran close to the 100-second limit
  on a tool call).
- **Expected:** a table as of the latest close (October 19 in the reference week): NVDA 26.1% against 25% (over by
  1.1 points), technology 40.7% against 40%, cash $282,219.15 against the $50,000 reserve; then the month's
  movement from the stored daily weights, ending on the same 26.1% (23.8% on September 21, a low of 23.0% on
  October 6, 24.1% on October 16), each figure labelled (Fabric IQ). If the movement ends on a different weight than
  the table, the data agent computed it itself; ask again in a new session.
- **Say:** "Fabric IQ is the system of record: a Fabric data agent over the portfolio lakehouse, run as the advisor."

**Foundry IQ** (budget 55 s):

> "What does our firm require when a client's single stock goes above its limit, and before we release a large wire?"

- **X-ray:** `ask_firm_policy`; only **Firm policy · Foundry IQ**, with `search_firm_knowledge` (once per rule).
- **Expected:** CP-104 (a documented concentration review after a breach, client contact before any trade, a
  supervisory sign-off for larger breaches) and OPS-220 (above its threshold and for real-estate closings: a signed
  authorization, a callback on a number on file, verification with the title company, dual approval, lead time),
  each by section and labelled **(Foundry IQ)**.
- **Say:** "Foundry IQ is the firm's own library in an Azure AI Search knowledge base, read as the project's identity,
  so every advisor gets the same rules, cited by document and section."

**Web IQ** (budget 60 s):

> "What's moving NVIDIA this week, and where is the stock trading now?"

- **X-ray:** `ask_market`; only **Market · Web IQ**, with `search_public_web` (news, then a public quote).
- **Expected:** each item with publisher, date and link, the public price with its time, and the closing line that
  public news doesn't reprice any client position.
- **Say:** "Web IQ is fresh public information, the only IQ outside your Microsoft 365 and Azure boundary. The query
  carried no client name and no amount."

### 2.3 The wire rules (skip if you ran 2.2)

**Do:** **New session**, then send:

> "Michael wants to wire $400,000 to his title company for the closing. What does our firm policy require before we send it?"

**Time:** budget 70 s. **X-ray:** `ask_firm_policy` and `search_firm_knowledge`.
**Expected:** OPS-220 by section: when it applies (large amounts and real-estate closings); a signed authorization
letter, a **callback** on a number already on file, phone verification of the title company's instructions and dual
approval; lead time, settlement and the cut-off; each labelled **(Foundry IQ)**.

**Say:** "A firm rule says what must happen; it isn't a fact about the client. That's why it comes from Foundry IQ,
and the client's facts come from Work IQ and Fabric IQ."

### 2.4 Public news (skip if you ran 2.2)

**Do:** **New session**, then send:

> "What is the public news on NVIDIA this week? Three bullets with links."

**Time:** budget 45 s. **X-ray:** `ask_market` and `search_public_web`; one query may show as blocked at the boundary
(`WEB_QUERY_BLOCKED`) if a draft query carried an amount, followed by a rephrased one: the guard is conservative by
design. **Expected:** three items, each with publisher, date and link, and the line "Public news doesn't reprice any
client position: positions and valuations come only from the portfolio system."

### 2.5 The boundary guard

**Do:** stay in the session of the question just before it (2.4; in the 30-minute route, 2.2's Fabric IQ question),
then send:

> "Search the web for Michael Scott's NVDA position and his stake."

**Time:** budget 70 s; it declines in seconds, with no tool call. **X-ray:** no `ask_market`, no Market lane.
**Expected:** the lead agent declines: only public topics go to Web IQ; it offers to search the public topic instead.

**Say:** "Web IQ is outside your boundary, so there are three layers. The lead agent's instructions never send client data
to the market agent. If a model ever tried, a guard in code checks the hand-off to the specialist agent and blocks
client and staff names, email addresses, amounts and account-like numbers, and the same guard checks every query
at the tool. Without a Web IQ key, the agents have no web tools at all."

### 2.6 Purview's DLP block

**Do:** stay in the same session, then send:

> "Michael Scott's Social Security number is 524-77-3612. Add it to his file."

**Time:** budget 40 s; the answer comes back in about ten seconds. **X-ray:** no lanes and no tool calls.
**Expected:** "**Blocked by Microsoft Purview.** Your organization's data loss prevention (DLP) policy doesn't allow this
prompt, because it contains sensitive information. No agent saw it." The number is fictional.

**Say:** "Agent Framework's Purview middleware sends every prompt, and every answer, to Microsoft Purview as the
signed-in user's activity. The tenant's DLP policy for this agent blocks Social Security numbers in a prompt, so this
one never reached an agent. Purview recorded the match and raised an alert; we'll find both in Defender in 6.5."

**If it goes wrong:** an ordinary answer means Purview isn't on for this agent, or a new policy hasn't synced yet (allow
up to an hour after `Set-PurviewDlpForAgent.ps1`); say what the block shows and move on. The conversation keeps the
prompt, so the next segment starts a new session.

## Segment 3: the recorded meeting and the chart (7 minutes)

**Goal:** one position in five places: the meeting's own words, the chart's numbers read from an image, then every
source checked for agreement.

### 3.1 The recorded meeting, in its own words (skip if late)

**Do:** **New session**, then send:

> "In Friday's Q4 portfolio review, what exactly did Michael say about his tax budget and his house? Quote the meeting transcript, and list the action items with their owners and whether each has been done since."

**Time:** budget 140 s. Needs Work IQ consent.

**X-ray:** `recall_client_memory` and `ask_client_context`; in the Client context lane the `meeting-recall` skill:
`workiq / fetch` (the calendar for the exact time, the inbox since), `workiq / retrieve` (Meetings: the meeting and
its recap; files: the notes and documents since) and `workiq / ask` (Copilot over the recording, for the words).

**Expected:**

- the meeting: **Friday, October 16**, recorded and transcribed, organized by the analyst;
- Michael's words quoted exactly, such as "My accountant thinks we should lower the budget to $15,000 for this year"
  and "We will need about $400,000", with transcription errors kept and marked rather than corrected;
- the **action items** with owners, and a **Done since?** column with evidence for each: the accountant's $15,000 in
  Michael's email, the IPS v2 draft (exists, unsigned), the analyst's figures, the funding plan still open;
- under **Since then**, the closing moved to December 15 with $400,000 due December 10.

**Say:** "That was a Teams recording. Work IQ read it as the advisor, and the agent quoted it word for word, including
its mistakes. Then it did the analyst's job: it checked every action item against what has happened since."

If asked why the lines say "Michael says": the recording is a scripted reading by one person, who said "Michael says"
before each of the client's lines.

### 3.2 The chart's numbers, read by Work IQ

**Do:** **New session**, then send:

> "Find the chart of Michael Scott's NVDA weight against its limit. What were the high and the low since September 1, how close did it come to the limit, and where did it end?"

**Time:** budget 110 s. Needs Work IQ consent.

**X-ray:** `ask_client_context`; in the Client context lane the `document-finder` skill: `workiq / retrieve` (finds
the file by meaning) and `workiq / ask` (Copilot reads the chart image, as the advisor) side by side, then
`inspect_document`, whose line lists the Work IQ calls it made inside (`fetch`, `fetch_blob`, as you) before it
matched the page. `read_chart` appears only if Work IQ couldn't read the values; the answer then says so.

**Expected:**

- the file and page: **"Michael Scott - Portfolio Overview (Oct 2026).pdf", page 3, Figure 2**;
- the low: about **22.8%**, early September (the data says September 7);
- the high: about **24.7%**, mid-October (the data says October 14); the answer may name the late-September peak
  (about 24.6%) too and say the image can't rank them, which is also right;
- closest to the limit: about **0.3 points** under 25%;
- the end: **24.1% on October 16**, printed on the chart;
- under the answer, **the chart itself**: page 3, "verified".

**Say:** "The chart is an image: its numbers are pixels, and no search index can read them. Work IQ found the file by
meaning and its Copilot reasoning read the values off the image, as the advisor. The harness downloaded the PDF through
Work IQ and verified the page. A printed label is exact; a value read from gridlines is 'about'."

### 3.3 The evidence

**Do:** click the chart under the answer: the pane opens at page 3 ("verified"), rendered from the file read with the
advisor's permissions. Click **Open original**: the PDF opens in OneDrive.

### 3.4 Every place the position lives, checked for agreement

**Do:** same session as 3.2, then send:

> "Since that chart, where does Michael's NVDA position stand now? Check the analyst's latest email against the portfolio data in Fabric, tell me whether they agree, what Michael and the team said about trimming it, and what a trim to the limit would mean."

**Time:** budget 180 s (the Fabric data agent answers for two dates). Needs Work IQ consent and Fabric.

**X-ray:** the lead agent's `position-cross-check` route: `recall_client_memory`, then client context (the analyst's email,
the meeting and its transcript, Teams, the IPS) and portfolio (the position now and at the last discussion) as
**two overlapping lanes**; then the lead agent's `rebalancing_check` under each tax budget.

**Expected:**

- **Where it stands:** 22.8-24.7% from September to mid-October (the chart), 24.1% on October 16, **26.1%** on
  October 19: from inside the limit to 1.1 points above it;
- **Do the sources agree? Yes:** the analyst's email and Fabric IQ both give 26.1%, $763,857.78, a portfolio of
  $2,926,762.55, a price of $202.83 and a cost basis of $41.20, each cited to both: the analyst's email with its date
  **(Work IQ)** and the portfolio as of its date **(Fabric IQ)**;
- **What was said:** Michael's own words from the transcript, then the Teams request for a trim analysis and the
  analyst's reply;
- **What a trim means:** a trim to 25% sells about **$32,167** and realizes about **$25,633**, over both the signed
  $20,000 and the accountant's $15,000, from `rebalancing_check`, with no recommendation.

**Say:** "Five places, one question: no connector per source, no index copy, no permission sync. Work IQ read four of
them as the advisor, Fabric answered from the system of record, and the lead agent checked that they agree and did the math
in code. If the email and Fabric disagreed, you'd see both numbers."

If asked "why Fabric, if the email has the number?": the email is what someone typed; Fabric is the book of record.
Checking one against the other is the control a risk team wants.

**If it goes wrong:** if Fabric is paused or slow, the Portfolio lane reports Fabric IQ unavailable and the answer
uses the analyst's email and says so: the designed fallback. Resume the capacity before the next Fabric step
(runbook).

## Segment 4: Work IQ versus calling Graph (4 minutes)

**Goal:** answer "how is Work IQ different from calling Graph?" live, on the same question, both ways.

### 4.1 Side by side

**Do:** **New session**, then send:

> "How is Work IQ different from just calling the Graph API? Show me side by side on this question: where is the chart of Michael Scott's NVDA position against its limit, and what did Michael ask about it by email?"

**Time:** budget 90 s. Needs Work IQ consent.

**X-ray:** `ask_client_context`; in the Client context lane the `workiq-vs-graph` skill runs both paths at once:

- **Graph only:** `workiq / call_function` (a OneDrive keyword search) and `workiq / fetch` (a message keyword search);
- **Work IQ:** `workiq / retrieve` across OneDrive and email, then `inspect_document` for the page.

**Expected:** the result (the portfolio overview, **page 3**, and Michael's email asking whether he is "getting close
to the 25% limit"), then a side-by-side table that starts with what this turn measured: calls made, items returned,
whether each part of the question was answered (Graph found the file but not the page, and only the email headers;
Work IQ found both), and the permissions a custom app would need (`Files.Read` and `Mail.Read`, against one delegated
Work IQ permission plus the tenant policy). Point at the X-ray for the times: the Graph keyword calls take about a
second each, Work IQ's retrieval several seconds, and only one of them answers the question.

**Say (five differences):**

1. **What comes back:** Graph returns lists that match keywords; Work IQ returns ranked passages from inside the
   content, with citations and sensitivity labels.
2. **Meaning:** Graph matches words; Work IQ matches the question. "How close to the limit" found the right chart.
3. **Work for the developer:** with Graph you call each workload, download, chunk, rank and summarize yourself; Work IQ
   is one call across sources, plus Copilot reasoning (`ask`) when you need it.
4. **Governance:** a Graph app needs a permission per workload; Work IQ uses one delegated permission, the user's own
   access, and a tenant policy checked on every call.
5. **Not either-or:** Work IQ's `fetch` *is* Graph, through the same governed door, for exact current reads.

| | Microsoft Graph | Work IQ |
|---|---|---|
| You send | A path, such as `/me/messages?$search=...` | A question, or a Graph path (`fetch`) |
| You get back | Records that match the path or keywords | Ranked passages with citations and sensitivity labels, or Copilot's finished answer (`ask`) |
| Search by meaning | You build it: download, chunk, embed, index, rank, keep in sync | Built in, across mail, files, meetings, Teams and people |
| Permissions | A scope per workload; app-only (no user) is possible | One delegated permission: always the user's own access |
| Policy | App consent and Conditional Access | Also a tenant Work IQ policy, checked on every call |

**When Graph is still the right tool** (say it before they do): background jobs with no signed-in user (Work IQ acts
only for a person), bulk export or sync, and the fastest exact lookups.

## Segment 5: Work IQ beyond search (4 minutes)

**Goal:** three things a platform team would otherwise build: Copilot reasoning over exact files, a whole task handed
to Work IQ's own agent, and the Copilot agents they already govern. If you ran 2.2's Work IQ question, it already did
the first two: say so and go to 5.3.

### 5.1 Copilot over two exact files

**Do:** **New session**, then send:

> "Compare Michael Scott's signed IPS with the IPS v2 draft. Which terms changed, and which version is in force?"

**Time:** budget 120 s. Needs Work IQ consent.

**X-ray:** `ask_client_context`; in the Client context lane `workiq / retrieve` (finds both files) and `workiq / ask`
with the two files' URLs: Copilot reasons over exactly those files, as the advisor.

**Expected:** a term-by-term table: the realized-gain tax budget ($20,000 signed, $15,000 in the draft), an ESG tilt
for new money, a longer horizon, the review date and the effective date ("effective only upon client signature");
the unchanged terms (the 25% single-stock and 40% sector limits, the $50,000 reserve); and **the signed IPS is in
force**, the draft is unsigned.

**Say:** "That's Work IQ's context: we pinned two files and Copilot compared them as the advisor. No download, no
parsing, no index."

### 5.2 A whole task over A2A

**Do:** same session, then send:

> "Delegate to Work IQ: what did Michael Scott ask me for in his recent emails, and what is still open?"

**Time:** budget 180 s (the A2A task alone can take up to two minutes). Needs Work IQ consent.

**X-ray:** `ask_client_context`; in the Client context lane `delegate_to_work_iq` ("Work IQ Chat over A2A: task …
completed").

**Expected:** Michael's emails (the review agenda, the change of plans, the 529 question), what has been done since,
and what is still open (the $400,000 funding source, the 529 contribution, the limit breach).

**Say:** "That was A2A. MCP gives our specialist agent Work IQ's tools to compose step by step, and you saw every call. A2A
hands a whole task to Work IQ's own agent, as a peer, and gets the finished answer back. Same user, same policy."

### 5.3 The Copilot agents you already have (skip if late)

**Do:** same session, then send:

> "Which Copilot agents can you work with through Work IQ?"

**Time:** budget 60 s. Needs Work IQ consent.

**X-ray:** `ask_client_context`; `workiq / list_agents` in the Client context lane.

**Expected:** the Microsoft and organization Copilot agents this user can reach, each with its agent ID. The list
depends on the tenant.

**Say:** "The Copilot agents you already govern become tools: the specialist agent can ask any of them with `ask` and its
agent ID, and Work IQ enforces each one's own data scope."

## Segment 6: governance (8 minutes)

**Goal:** the Work IQ tenant policy as it is today, a live decision under it, the one write behind an approval, a second
user, what the SOC sees, and where an admin governs the agent.

### 6.1 The Work IQ MCP policy (portal; view only)

**Do:** in the admin center tab, go to **Agents** > **Tools**. Open the **MCP servers** card (or the registry filtered
to type **MCP Server**), search for **Work IQ MCP**, open it, then the **Policies** tab, **Tenant Default**.

**Point at:**

- **Path access:** User's own data on; **Directory lookups off**; SharePoint, OneDrive and the other sources on.
- **Limits:** page sizes.
- **Mutations:** **Allow write actions** and **Allow create** on; partial update, replace and delete off.
- **Block** would turn the server off for the whole tenant; **Reset to recommended** restores the defaults.
- Back in the list, the source-specific servers (Outlook Mail, Calendar, OneDrive, SharePoint, Teams and others): a
  harness can be given only the workloads it needs.

**Never click a toggle, Block or Save.**

**Say:** "Today this policy is tenant-wide. Microsoft is working on finer, agent-level controls; we'll share details
when the product group does." Don't promise features or dates. For per-agent limits today: the toolbox allowlist (the
client context agent's Work IQ toolbox has no write tools; the probe and the draft use separate, narrow toolboxes), the source-specific
servers and a gateway in front of Work IQ.

### 6.2 A live policy test

**Do:** back in the app, **New session**, then send:

> "What is this agent allowed to do with Work IQ? Test it."

**Time:** budget 90 s. Needs Work IQ consent.

**X-ray:** the lead agent's own `probe_workiq_policy` (its `workiq-governance` skill), through the separate governance
toolbox. No specialist agent lane.

**Expected** (with the recommended Tenant Default):

| Check | Decision |
|---|---|
| Read the advisor's own mail | Allowed |
| Read the advisor's own OneDrive | Allowed |
| Directory lookup: other people in the organization (`/users`) | **Denied** by the Work IQ tenant policy |
| Partial update of an email (a test item that doesn't exist) | **Denied** by the Work IQ tenant policy |
| Delete an email (a test item that doesn't exist) | **Denied** by the Work IQ tenant policy |

**Say:** "That was a live test, and nothing could change: the update and the delete target an email that doesn't
exist. Our code surfaces a policy denial and never retries it."

### 6.3 The one write, behind an approval

**Do:** same session, then send:

> "Draft a short follow-up email to Michael Scott confirming our Thursday meeting and listing the three agenda topics from his latest email."

**Time:** budget 110 s to the card. Needs Work IQ consent.

**X-ray:** the lead agent asks the client context agent for the facts (the meeting time from the calendar, the topics from Michael's
latest email), then proposes `draft_follow_up_email`, which always needs approval.

**Expected:** an **APPROVAL NEEDED** card with the exact recipient (`michael.scott@scott-family.example`), subject and
body: Thursday **10:00-11:00 AM** Eastern and his three topics (the NVDA position and the 25% limit, the $400,000
down payment, ESG). The card says Work IQ will create the draft as you, in your Outlook Drafts folder, and nothing is
sent.

**Do:** click **Approve: create draft**, wait about 15 seconds for the answer, then show the draft in the Outlook
Drafts tab: created, not sent. Delete it after the demo. Or click **Reject**: the answer says nothing was created (the
rehearsal rejects).

**Say:** "Two locks on that write. The harness asked a person first: the tool can't run until you approve, and it can
only create a draft. Then Work IQ's tenant policy allowed create, while update and delete stay denied."

**If it goes wrong:** if the time isn't 10:00-11:00 AM, click **Reject** and ask again in a new session. If the draft
is denied by policy, say so: governance working as designed. If no card appears (for example after a reload), start
a new session and send the prompt again; typing "approve" doesn't answer a card.

### 6.4 A second user (skip if late)

**Do:** switch to window B, signed in as the analyst account. **New session**, then send:

> "Prepare me for my Thursday meeting with Michael Scott."

**Time:** budget 180 s. Needs the analyst's own consent (Work IQ, then Fabric IQ) and a second signed-in user.

**X-ray:** the same route and lanes as 1.1, every call made **as the analyst account**.

**Expected** (verify at rehearsal and adjust this table):

| In the advisor's brief | In the analyst's brief |
|---|---|
| Michael's emails to the advisor: the closing, the accountant's $15,000, the 529 plan | **Missing:** they're in the advisor's mailbox |
| The files in the advisor's OneDrive (the signed IPS, the portfolio overview) | **Missing:** the **Client documents** line says that folder isn't available to this user |
| The team's SharePoint files (meeting notes, IPS v2 draft), the recorded meeting, the Teams chat | **The same:** the analyst can open these too |
| The saved discussion from memory | **Missing:** memory is per user |
| Fabric IQ figures | Whatever the analyst's own Fabric access allows; a denial shows as a permission boundary, not a failure |

**Say:** "Same agent, same prompt, a different person. Nobody wrote a permission check: every specialist agent runs as the
signed-in user, so Microsoft 365 permissions apply automatically, and the durable state store isolates memory per
user."

### 6.5 The SOC view (Defender advanced hunting)

**Do:** in the Defender tab, paste the first query from [Appendix A](#appendix-a-defender-advanced-hunting-queries) ("One
user's Work IQ calls, in four logs"), set `user` to the advisor's address and click **Run query**. Then select each row
to open **Inspect record**:

1. **Agent 365**: `called advisor-team`: each call to the agent, from the Agent 365 audit
   (`InvokeAgent`, with the user, the agent's Entra agent ID and the conversation).
2. **Graph activity logs**: each Microsoft Graph request Work IQ made for the user (`GET /v1.0/me/messages`,
   `/me/calendarView`, the user's OneDrive), under their object ID.
3. **Purview Audit**: each file, email and meeting Work IQ read for them (`CopilotInteraction`; the same records carry
   Microsoft 365 Copilot's jailbreak and cross-prompt-injection detections).
4. **Purview DLP**: 2.6's blocked prompt (`DLPRuleMatch`: the policy, the rule, the U.S. Social Security number) and the
   alert it raised.

Optional: the other queries in Appendix A (the agents in Defender's inventory, a detection candidate for any write Work
IQ makes).

**Say:** "Your SOC needs no new tool. Every Work IQ call is in the logs your analysts already query, under the person it
acted for, next to the prompt the policy blocked."

**Per call:** `.\scripts\Get-WorkIqAuditTrail.ps1 -Minutes 60 -UserPrincipalName <the advisor>` prints each burst of the
agents' Work IQ calls from the Foundry traces, with the Graph requests and the Purview audit records it caused, and the
DLP matches and alerts (it signs you in to Microsoft Graph with ThreatHunting.Read.All).

**If it goes wrong:** a query can return "An unexpected error occurred" for a minute; run it again. Graph activity logs
arrive within minutes and 2.6's DLP match within about fifteen; Purview Audit records can take up to an hour, and the
Agent 365 records more than two hours, so run 1.1 and 2.6 the evening before as well as at T-45 (the query reads one day
back).

### 6.6 Agent 365: where an admin governs the agent (skip if late; view only)

**Do:** in the admin center tab, go to **Agents** > **All agents** and find `advisor-team` (platform **Microsoft
Foundry**), next to the tenant's other agents. Open it.

**Point at:** the agent's details (platform, publisher, status, users and risks as the list shows them), where an admin
reviews the permissions and data access the agent has, where those are changed, and the action that **blocks** the
agent for the tenant. **Don't click Block or change anything.**

**Say:** "All the agents are one hosted agent here: one Entra agent identity and one entry, governed like every other agent in
your tenant. The specialist agents are roles in code inside it; if you wanted a separate identity per role, each role would
become its own hosted agent."

## Segment 7: durability and restart (7 minutes)

**Goal:** make durability visible at the hardest moment, after the work is saved but before the record of progress
moves, and describe precisely what the platform and the harness each do. Workflow W2, the durable meeting pack,
follows Foundry's [Manage state for long-running agents](https://learn.microsoft.com/en-us/azure/foundry/agents/how-to/manage-task-state).

### 7.1 Arm the crash test

**Do:** **New session**, then send:

> "Arm the crash test for the durable meeting pack."

**Time:** budget 75 s. **X-ray:** the lead agent's `demo_fault_switch` (`process_crash`).

**Expected:** "The next durable meeting pack stops the agent process after wave 2 has written its checkpoints and
before the progress marker moves." The switch is one-shot, stored per advisor in the state store.

**Say:** "That's the worst moment to crash: the work is saved, but the record of progress isn't."

### 7.2 Run until the stop

**Do:** same session, then send:

> "Run the durable meeting pack for Michael Scott."

**Time:** budget 65 s. Needs Work IQ consent.

**What happens:** `run_durable_meeting_pack` runs in waves and keeps two kinds of record in the state store: a
**checkpoint** per finished phase, written create-only with a SHA-256 of its result, and a **progress marker**, moved
with its ETag only after the wave's checkpoints exist. Wave 1 is Work IQ documents and meetings; wave 2 is calendar
and email; wave 3 is Fabric IQ, Foundry IQ and Web IQ. After wave 2 writes its checkpoints, **the agent process
exits** (exit code 75) before the marker moves: a real process exit, not an exception.

**In the app:** the X-ray says **process stopped**, the calls that were cut off say **Interrupted**, and a note says
the finished phases are checkpointed and a new session can resume them.

**Say:** "We just killed the agent's process between two writes. Let's see what survived." Don't wait for the crashed
request; go straight to 7.3.

### 7.3 What survived

**Do:** **New session** (the interrupted conversation stays locked by design, so a half-finished turn is never
replayed), then send:

> "What is stored durably for Michael Scott right now?"

**Time:** budget 50 s. **X-ray:** `show_durable_state` or `show_state_store`.

**Expected:** Michael's run is still open, with **4 checkpoints** written by an earlier process; the marker still
points at wave 2; attempt 1 has no end record (interrupted); **this process** started seconds ago.

### 7.4 Resume

**Do:** same session, then send:

> "Resume the durable meeting pack for Michael Scott."

**Time:** budget 105 s. Needs Work IQ consent.

**Expected:** the research, then a **durability report**, a timeline of every attempt, process and store write:

| Attempt | Process | What happened |
|---|---|---|
| 1 | A | Started; documents and meetings checkpoints; marker 1 to 2; calendar and email checkpoints; **process stopped before the marker moved** |
| 2 | B | Started; documents and meetings **restored**; calendar and email **reconciled and reused** (hash-checked, not run again); marker 2 to 3; portfolio, firm policy and news run now; marker to done |

**Say (what is the platform, what is the harness):**

| Foundry provides | This harness adds |
|---|---|
| The **durable state store**: items isolated per user, ETags, create-only writes, conditional updates, expiry | What to checkpoint: one hashed checkpoint per finished phase, written before the progress marker moves |
| **Persisted sessions** and a hosted runtime that starts a new process | Reconciliation on resume: checkpoints written after the last marker are verified and reused, never run twice |
| The hosting library's **conversation lock**: no half-finished turn is replayed | A timeline of attempts and processes, so an interrupted attempt is visible |

"This pack resumes when you ask. In segment 9 you'll see the other kind: a workflow that Foundry itself resumes in a
new process, with nobody asking."

### 7.5 What the state store holds, and a restart (skip if late)

**Do:** **New session**, then send:

> "Remember my preference: in client briefs, put Risks and conflicts first and keep them under 250 words."

**Time:** budget 30 s. **X-ray:** `advisor_preferences`. **Expected:** "saved".

**Do:** same session, then send:

> "What is stored durably for me right now, and which process is serving you?"

**Time:** budget 35 s. **X-ray:** `show_state_store`. **Expected:** a table of every store, with item counts, last
write and who wrote it; never the contents:

| Store | What it holds |
|---|---|
| `team-client-memory` | Discussion baselines, the last brief, and the presenter's demo switches |
| `team-conversations` | Each conversation's messages, so any process can continue it |
| `team-durable-runs` | Meeting-pack runs: progress markers, checkpoints, attempts |
| `team-preferences` | The advisor's preferences for how briefs are written |
| The hosting library's agent-session and function-approval stores | The serialized session and pending approvals, written by Microsoft Agent Framework hosting |

Then the serving process's ID and uptime.

**Do:** in PowerShell (the isolated profile), stop this session's compute, as idle eviction would:

```powershell
.\scripts\Restart-AgentSession.ps1
```

**Do:** back in the app, same session, send:

> "Which process is serving you now, how long has it been running, and what preference did I ask you to remember?"

**Time:** budget 35 s. **Expected:** a **new process** about a minute old, and "Risks and conflicts first, under 250
words".

**Say:** "Crash, restart, eviction after idle: three ways a container goes away. The work, the conversation and the
preferences are in the state store, not in the container."

**Afterwards:** clear the preference before segment 9, so the briefs there are full length: the runbook's
[reset command](DEMO-RUNBOOK.md#reset-the-presenter-controls) does it.

**If it goes wrong:**

- The pack completes without stopping: the one-shot switch was used up (perhaps in a rehearsal). Arm it again.
- The new session fails right after the crash: wait 30 seconds and send again.
- The resume says a fresh run started: the unfinished run is older than the 3-hour resume window. Show 7.3's
  checkpoints and run a fresh pack.

## Segment 8: the quality loop (5 minutes)

**Goal:** how the agents get measurably better: traces, evaluations with built-in judges and your own rubric, Insights
and the optimizer. Say once that Insights, rubric generation, the recurring evaluation and the optimizer are preview
features. Each item below exists once you've run the matching command in [QUALITY-LOOP.md](QUALITY-LOOP.md); judges run
on `team-judge`, never on the agents' own model deployments.

1. **8.1 Traces.** Foundry portal > your project > **Agents** > `advisor-team` > **Traces**; open the brief from 1.1.
   Show the lead agent's run, the tool calls with their durations, and the specialist agents' runs and their tool calls
   inside the `prepare_client_brief` call; each model call with its duration and tokens. Every span carries the agent
   and version (`advisor-team:<version>`).
2. **8.2 Evaluate in production.** **Evaluations** > **Recurring configs** > `advisor-team-production-traces` and its
   evaluation "advisor-team - production traces (prompt injection, every agent step)": every hour, Foundry checks that
   hour's real traces, step by step, for prompt injection hidden in what the agent read. Nothing is replayed, and the
   judge never slows the agent. A flagged step goes to a person. Present it as an audit after the fact, not the
   protection: prompt injection is handled as each request comes in, by the agents' deterministic controls and the
   guardrail policies on the model deployments (Prompt Shields), and 2.6 showed Purview's check on each prompt. Quality
   is scored on whole answers in 8.3: a step's trace ends in a progress note, not an answer, so quality judges would
   misread it.
3. **8.3 Golden questions.** **Evaluations** > "advisor-team - golden questions (answer judges + rubric)": the golden
   questions sent to the live agents, each answer scored by intent resolution, coherence, fluency, the rubric evaluator
   ("Advisor team quality (rubric)", generated by Foundry from the agent and a description of a good answer) and the
   prompt-injection judge. Open one answer with its per-dimension reasons. These judges read the answer itself:
   Foundry can hand them a hosted agent's tool calls only together with its reasoning steps, which they reject, so the
   judges that check an answer against its sources (groundedness, task adherence and completion) run in 8.4.
4. **8.4 Before and after** (skip if late). "Advisor team - demo turns (built-in + rubric)": two saved-turn runs,
   before and after a change, with every tool call and result, so groundedness and task adherence are judged against
   what the specialist agents returned. **Say:** "A release has to win on this comparison, not on hope."
5. **8.5 Insights.** `advisor-team` > **Insights**: the latest **Succeeded** scan, one finding with its evidence traces
   and a suggested fix. **Don't click Run now** during the demo.
6. **8.6 The optimizer.** The completed job: on the brief-writer prompt agent (`advisor-brief-writer` > **Optimize**),
   or on the hosted agent itself through the candidate agent `advisor-team-candidate` (instructions, the role skills and
   the model). Open the best candidate to compare the instructions. **Promote** creates a new version; a person decides,
   and only after the candidate wins on `advisor-team-candidate`, which has its own state stores. A leakage guard
   refuses candidates that copy test answers, dates or names.

**Say:** "These are the agents' real turns, scored by Foundry's judges and by a rubric in our own words, with a reason
for every finding; the optimizer proposes, the comparison decides, and a person promotes."

**Then close the loop. Say:** "That closes the loop, end to end: your admins set the policy, in Agent 365 and Purview;
the agents enforce it at run time, as the user; Defender and Purview Audit keep the evidence, with alerts; and the same
traces feed Foundry's evaluations and optimizer, so the next version is measured before it ships, and runs as the same
agent, under the same policy."

### 8.7 Fabric IQ right now (optional)

**Do:** in the app, **New session**, then send:

> "Where do Michael Scott's NVIDIA weight, his technology exposure and his cash stand right now, and as of when?"

**Time:** budget 105 s. Needs Work IQ consent and Fabric. **X-ray:** `ask_portfolio`; only **Portfolio · Fabric IQ**.
**Expected:** the three figures and the as-of date.

## Segment 9: the agents (14 minutes)

**Goal:** what several agents add over a single agent: maker-checker, graceful degradation, and durable multi-client work that
a new process resumes from the Foundry state store after a crash, with human approvals bound to each draft.

### 9.1 The brief, agent by agent

**Do:** select 1.1's session in the sidebar (or the T-10 backup session) and open its X-ray. No new prompt.

**Point at:** the six lanes and their overlap; the Market lane starting when Portfolio ends (so the web query carries
only a public company name); the Writer lane's `rebalancing_check`, `liquidity_check` and `to_eastern_time`; the
Compliance reviewer lane's `search_firm_knowledge`; the verdict card.

**Say** the 30-second explainer, then: "Three ways the lead agent uses the specialist agents: one specialist agent for a one-IQ question,
several as background agents for an open question, and a fixed workflow for a brief, where the order and the checks
are code, not the model's choice."

### 9.2 The reviewer test

**Do:** **New session**, then send:

> "Arm the reviewer test."

**Time:** budget 40 s. **X-ray:** `demo_fault_switch` (`reviewer_test`). **Expected:** the reviewer test is armed for
the next brief.

**Do:** same session, then send:

> "Prepare me for my Thursday meeting with Michael Scott."

**Time:** budget 240 s. Needs Work IQ consent, Fabric and Web IQ.

**What happens:** the writer agent's first draft gets one seeded sentence under **Risks and conflicts**: "We recommend
Michael sell 2,000 NVDA shares this week to fund the closing." The compliance reviewer agent fails it as advice, citing the
firm's client communication standard (COM-310). The number check lets "2,000" through (it reads a number from 1900 to
2100 as a year), so this is the reviewer agent's catch. The workflow sends the finding back to the writer agent once; the revision
passes. The switch clears itself after this brief.

**X-ray:** the Writer lane with more calls (two drafts) and the Compliance reviewer lane checking twice. The verdict
card: "**Cleared** by the number check and the compliance reviewer **after 1 revision**." Under it, **Held back from
the first draft** lists what the checks caught in the first draft: the sentence in quotes, the reason and the rule. (A
**Not cleared** card lists the findings on the final draft instead.) The revised brief contains no
recommendation; **Risks and conflicts** still ends with the tax disclosure, "Contoso Wealth does not provide tax or
legal advice. Please consult your tax adviser.", and **## Sources** is the last section, with nothing after it.

**Say:** "Maker and checker, enforced by the workflow: here is the sentence it held back, and the advisor never saw
it. If the revision had failed too, the brief would be marked **Not cleared** with the findings, instead of being shown
as if it were fine."

**If Held back lists more than the seeded sentence:** the reviewer agent also caught a real slip in the first draft (in
one recording, client approval before any trade, which CP-104 §3.2 sets only for non-discretionary accounts). Point at
it: the checker found a mistake nobody seeded, and the revision fixed it.

**If it comes back Not cleared after 1 revision:** it happens now and then, when the reviewer agent finds a second problem in
the revision (in one recording, a firm rule stated without its condition: client approval before any trade, which
CP-104 sets for non-discretionary accounts). Read the finding aloud: the checker caught the revision too, and nothing
unchecked reached the advisor. Move on; don't rerun it live.

### 9.3 A specialist offline (skip if late)

**Do:** **New session**, then send:

> "Take the portfolio specialist offline for the next question."

**Time:** budget 40 s. **X-ray:** `demo_fault_switch` (`specialist_portfolio`). **Expected:** a simulated outage of
the portfolio agent (Fabric IQ).

**Do:** same session, then send:

> "Michael Scott needs $400,000 for his home closing. How could we fund it, and what has to happen before the money moves?"

**Time:** budget 215 s, as for 2.1. Needs Work IQ consent and Web IQ.

**X-ray:** three lanes: Client context, Firm policy and Market. There is **no Portfolio lane**: the specialist agent
reported "Fabric IQ not checked: the portfolio specialist is offline" without making a call.

**Expected:** the Fabric IQ line says it was **not checked** (offline); the other three IQs answer; no portfolio figure
is filled in from another source.

**Say:** "An outage of one IQ never blocks the answer, and never turns into a made-up number."

The outage was for this one question: the switch cleared itself when the portfolio agent reported, so 9.4 starts
with every specialist agent back online.

### 9.4 Review packs: durable, multi-client work with approvals

**Do:**

1. Click **Review packs** in the sidebar. The panel says **DURABLE WORKFLOW · ADVISOR-TEAM**: workflow W3 runs as
   durable steps on the same hosted agent, `advisor-team`, and the panel sends it one step at a time. The prompt box
   already holds:

> "Prepare review packs for my three clients for next week."

2. Tick **Crash test: stop the agent process once before the last pack; the run resumes from the state store in a new
   process**.
3. Click **Start review packs**.
4. The **CONFIRM THE CLIENTS** card asks "Prepare review packs for these 3 clients, one after another?" and lists
   Michael Scott, Jan Levinson and Dwight Schrute, with a note that each pack is checkpointed in the Foundry state store
   and that the crash test is armed. Click **Prepare the packs**. (Only clients named in the request are proposed;
   otherwise the configured three.)
5. **Keep the panel open** and narrate while it works: the status reads "Working on pack 1 of 3 (Michael Scott): N s.
   Each pack is checkpointed in the Foundry state store; a crash resumes from the last one." The panel drives the run,
   so closing it stops it after the step in flight. Use the wait for 9.5 in the Foundry tab, then come back.
6. One **FOLLOW-UP DRAFT** card per client arrives, one at a time: "Approve this follow-up draft? It is shown, not
   sent," with To (`michael.scott@scott-family.example`, then `jan.levinson@example.com`, then
   `dwight.schrute@example.com`), Subject and body, and "Approval applies to this exact text only (SHA-256 …)". Click
   **Approve** on each.

**Time:** budget 600 s for the whole run, with the crash test (307 s in the rehearsal runner, which approves at once).

**What happens, and what to say while it runs:**

- **Plan:** the run is saved in the user's Foundry state store, waiting on the clients card. Every step after it reads
  the run from the store, so no step depends on the process that ran the one before.
- **Packs:** one client per step: the brief workflow (W1) for that client, with the four specialist agents, the writer
  agent and the checkers, then a short follow-up draft. Each finished pack is written to the state store as its own
  checkpoint, with its SHA-256. Michael Scott gets the full four-IQ brief (with Work IQ consent and Fabric); Jan
  Levinson and Dwight Schrute have portfolio data only, so their packs are labelled **degraded: no Microsoft 365 history
  found for this client as the advisor**. A pack's source list keeps only items that name its client; a pack with none
  is the degraded one.
- **The crash:** before pack 3 the crash test writes "stopped before pack 3 (crash test)" to the run's timeline and ends
  the process with no cleanup (exit code 75), as a real crash would. It fires once per run (a create-only marker in the
  state store). The interrupted conversation stays locked, so the panel shows "The agent process stopped. Sending the
  step again in a new session" and sends the same step in a new conversation: a new session and a new process, which
  restores packs 1 and 2 from the state store (each checked against its SHA-256) and writes pack 3. **Nobody presses
  resume**, and packs 1 and 2 are restored, not redone.
- **Drafts:** one approval at a time, each bound to the draft's SHA-256: an approval applies only to the exact text it
  showed. With `DRAFT_MODE=preview` (the default) drafts are shown, never sent or saved.

**Expected summary** (as a message in the panel):

- "## Review packs: 3 clients", with each client cleared (or "NOT CLEARED: held back by the checks") and the degraded
  label where it applies;
- **Follow-up drafts:** "Michael Scott: previewed (DRAFT_MODE=preview: shown, not saved)", and the same for the
  others;
- **Durability timeline:** "Process 1 (…): plan; pack 1: Michael Scott; pack 2: Jan Levinson; stopped before pack 3
  (crash test)", "Process 2 (…): restored packs 1-2 from the state store; pack 3: Dwight Schrute; summary", and "The
  packs finished before the stop were restored from the Foundry state store, not redone.";
- **Packs:** each client's brief.

**Say:** "Two processes, one run, on the same hosted agent. Every finished pack is a checkpoint in the Foundry durable
state store, isolated per user; after the crash, a new process reads the run back and carries on from the last
checkpoint, and each approval is bound to the exact draft it showed. That's what makes multi-agent work durable without
anyone babysitting it."

**For the technical audience (the wire protocol):** each step is an ordinary Responses API request to `advisor-team`,
as the signed-in user. The app sends a one-line command (`review-packs: {"op": "start" | "answer" | "next", ...}`), and
the agent's middleware answers it in code instead of passing it to the lead agent's model: progress lines while a pack
runs (every 15 seconds, which also keeps the connection alive), then one `review-packs-view:` line with the run's
status, cards and progress. An answer carries the card's `call_id` and a JSON answer (`{"approve": true}` for the
clients card, optionally with a `clients` list; `{"approve": true, "sha256": "…"}` for a draft).

**If it goes wrong:**

- The status says "The agent process stopped. Sending the step again in a new session (attempt N)": that's the crash
  test working; keep the panel open. It sends the step again 5 seconds after each stop, up to 36 times; the new process
  usually answers within a minute.
- The panel says "The agent didn't come back", or the run ends with an error: start another run without the crash test
  (about 4 minutes) if there is time, or show the transcript of the last rehearsal of
  `python scripts/review_packs.py --crash-test --approve`.
- All three packs are degraded: Work IQ consent or Fabric is missing for this user; the durability story still holds.

### 9.5 Each role measured on its own (while 9.4 runs, or skip)

**Do:** in the Foundry tab, open the trace of the brief from 1.1 or 9.2 and expand one specialist agent's run: its tool
calls, durations and tokens. Then show the latest rehearsal report (`report.md` from `python scripts/rehearse.py`),
whose **Lanes** column lists, for every step, which lanes ran and how many calls each made.

**Expected:** per role, the calls it made, its time, and whether its facts held; the reviewer agent's catch in 9.2 is the
reviewer agent's own test.

**Say:** "Several agents are easier to improve than one big prompt: each role has its own instructions, as a skill the
optimizer can tune, its own tools and its own measures." A Foundry evaluation per role, as its own evaluator, isn't
part of this build yet: say so if asked.

## Optional add-ons

Not in the run of show. Each is a Terraform toggle ([ARCHITECTURE.md](ARCHITECTURE.md#optional-add-ons)); the
rehearsal runner checks them (segment `addons`; O1 only with `--have morning-note`, O4 only with `--have committee`).

### O1: the morning note (off by default)

Needs `enable_morning_note = true` in `terraform/terraform.tfvars`, the Platform step and
`.\scripts\Publish-MorningNote.ps1` ([DEPLOYMENT.md](DEPLOYMENT.md#the-morning-note-add-on)). It handles public news
and firm policy only, so it is safe for any audience.

**Do:** in a PowerShell window (the isolated profile), run:

```powershell
.\scripts\Invoke-MorningNote.ps1 -Tickers NVDA
```

Then open the Durable Task Scheduler dashboard (https://dashboard.durabletask.io/, with the endpoint the script prints
and the task hub `advisor-team`) and the run it started.

**Time:** a target of 150 s from start to published with one ticker (the script's run, not a rehearsal budget). With
more tickers the research steps share one model deployment and can wait out rate limits (once 282 s for NVDA and
MSFT).

**X-ray** (the dashboard): `watch-list`, then `market-research` (Web IQ) and `policy-check` (Foundry IQ) running at the
same time, then `writer`, then `approval`, where the run waits, and after the answer `publish`. Each step is a durable
activity; the orchestration replays from the scheduler's history, so a restart resumes after the last finished step.

**Expected:** the script prints "[Approval] Publish the morning note for <date> to the firm library?" and the draft:
a two-sentence summary, then "## NVDA" with what moved (dated items with links and a quote with its time), the firm's
rules with their codes (the house view **IC-HV-2026-10** and **CP-104**), and a "Talking point:". It never names a
client and never cites an earlier morning note. Answer `y`; the run completes with `published: true`, the code
**IC-MN-<YYYYMMDD>** and two sections (the summary and NVDA).

**Say:** "Same workflow model as the review packs, different runtime: Azure Functions and the Durable Task Scheduler.
The approval is a durable wait, so nothing runs while the head of research reads, and the approved text is exactly
what gets published."

**Do:** in the app, **New session**, then send:

> "What is the house view in this morning's note on NVIDIA? Cite the note."

**Time:** budget 90 s.

**X-ray:** the lead agent's `ask_firm_policy`, the Firm policy lane only (Foundry IQ).

**Expected:** the house view the note states for NVIDIA (in the verified run, neutral weight under IC-HV-2026-10 §1),
cited as the morning note **IC-MN-<YYYYMMDD>**, §NVDA (Foundry IQ), with no recommendation.

**Say:** "Approved this morning, cited this morning. The note went into the same firm library as the policies, so the
firm policy agent reads it through Foundry IQ like any other firm document."

### O3: the what-if analyst (on by default)

**Do:** **New session**, then send:

> "What if NVIDIA falls 20% before Michael Scott's closing? Assume a $4,250,000 portfolio with NVDA at 26.1% against his 25% limit, and $520,000 cash against a $450,000 reserve."

**Time:** budget 150 s. Needs nothing beyond the deployment: the prompt carries its own assumptions.

**X-ray:** the lead agent's `run_what_if`. Inside it, the portfolio agent is asked for the current figures (a Portfolio
lane; with Fabric unavailable it reports that, and the assumptions stand), then the **What-if analyst · code in a
sandbox** lane: the analyst writes Python and runs it in a Monty sandbox (CodeAct) with no network, no file access and
limits on time and memory.

**Expected:** the scenario in one line; a before-and-after table with percentages to two decimals: NVDA from 26.10% to
**22.03%** (a 20% fall in one position also shrinks the whole portfolio, so the weight falls less than 20% of 26.1),
back under the 25% limit, with the values and cash against the reserve; a line per limit crossed (none); and the last
line "**Computed in code** (what-if analyst); …", saying whether the figures came from Fabric IQ or are the advisor's
assumptions. No recommendation.

**Say:** "Scenario math is where models make mistakes. Here the model writes the code and a sandbox runs it, so every
number on screen was printed by code, and every assumption is labelled."

### O4: the investment committee (experimental)

Needs `enable_committee = true` in `terraform/terraform.tfvars` and the Agent step. Don't show it to customers: a
discussion isn't repeatable.

**Do:** **New session**, then send:

> "Convene the investment committee on Michael Scott's NVIDIA concentration against his 25% limit."

**Time:** budget 300 s.

**X-ray:** the lead agent's `convene_investment_committee`, a group chat (Agent Framework's `GroupChatBuilder`): the
portfolio member (Fabric IQ), the policy member (Foundry IQ) and the market member (Web IQ) each research with only
their own tools, in that order, then the risk officer (no tools) challenges them, and each challenged member replies
once. The speaking order is code, so every member is heard even when a source is down. Their calls show in the
Portfolio, Firm policy and Market lanes.

**Expected:** "## Investment committee: …", then **Minutes** (facts with each member and source, the firm's
requirements with **CP-104**, challenges and answers, open questions) and **The meeting**, every member's own words,
including the **Risk officer**. If the chair's minutes add a rule code or a figure no member said, they are withheld
and the meeting is shown in the members' own words instead.

**Say:** "W1 is a fixed graph; this is a conversation, where members react to each other. The guard on the minutes is
the same idea as the number check: nothing in the summary that nobody said."

## Story figures (for checking answers)

Reference story week; the numbers come from `data/fabric/portfolio-story.json` and the hydrated Microsoft 365 story.

| Figure | Value | Where it lives |
|---|---|---|
| Next meeting | Thu Oct 22, 10:00-11:00 AM ET, "Year-end planning - Michael Scott" | Calendar (Work IQ) |
| Q4 review (recorded) | Fri Oct 16, 10:30-11:00 PM ET, organized by the analyst; Michael: the accountant suggests $15,000; the house in spring 2027, about $400,000 down | Teams recording and transcript; meeting notes in SharePoint |
| NVDA weight | 24.1% on Oct 16; 26.1% on Oct 19 (limit 25%) | Portfolio overview PDF; the analyst's Oct 19 email; Fabric IQ |
| Technology sector weight | 39.1% on Oct 16; 40.7% on Oct 19 (limit 40%) | Same |
| Portfolio value | $2,849,919.50 on Oct 16; $2,926,762.55 on Oct 19 | Same |
| NVDA position | 3,766 shares, $763,857.78 at $202.83; cost basis $41.20 a share | The analyst's email; Fabric IQ |
| Cash and equivalents | $282,219.15 (reserve $50,000) | Fabric IQ; the signed IPS |
| Tax budget | $20,000 signed (IPS v1, in force); $15,000 proposed (the accountant's email, the IPS v2 draft) | Signed IPS PDF (OneDrive); Oct 17 email; IPS v2 draft (SharePoint) |
| Trim to 25% | $32,167.14, realizing $25,633.17: over both budgets | `rebalancing_check` |
| Down payment | $400,000 by Dec 10 for the Dec 15 closing; shortfall about $167,781 ($192,781 with the $25,000 529 plan) | Michael's emails; `liquidity_check` |
| The chart | Low 22.8% (Sep 7), peaks 24.6% (Sep 25) and 24.7% (Oct 14), 24.1% on Oct 16 | Portfolio overview, page 3, Figure 2 (an image) |
| Pages | Overview: Figure 1 and Table 1 on page 2, Figure 2 on page 3. Signed IPS: Table 1 (limits) on page 2, signatures on page 3 | OneDrive, Client Files/Michael Scott |
| Firm policy | OPS-220: large wires and real-estate closings need a signed authorization, a callback on a number on file, verification with the title company, dual approval and lead time. CP-104 §3: a documented review after a breach | Foundry IQ knowledge base (six fictional documents) |
| Follow-up draft (6.3) | To michael.scott@scott-family.example; Thursday 10:00-11:00 AM ET; three topics: NVDA and the 25% limit, the $400,000 down payment, ESG | Outlook Drafts, after approval; never sent |
| Review packs (9.4) | Michael Scott (full), Jan Levinson and Dwight Schrute (degraded: portfolio data only) | Workflow W3 on advisor-team; checkpoints in the Foundry state store |

## Quick fixes

| Symptom | Cause | Fix |
|---|---|---|
| Orange consent banner | First use of a connection by this user and agent | Click each **Authorize** once, finish sign-in, close the tab, **Retry: send my last message again** |
| A request fails at once with "in-flight turn" | That conversation was interrupted by a crash test | **New session** |
| An answer runs over its budget | Work IQ or model variance; a stalled model call is resent once after 120 s | Keep narrating; use the T-10 backup session if needed |
| A lane says Fabric IQ unavailable | The Fabric capacity is paused | The designed fallback: the answer uses the email and says so. Resume the capacity before the next Fabric step |
| "Web IQ is unavailable right now" | Web IQ (preview) outage | Say so; the agents carried on with the other IQs |
| A query shows as blocked at the boundary | The guard blocked a query that carried an amount or a name; the specialist agent rephrased it | Point at it: conservative by design |
| A one-IQ question ran another lane | The model strayed (about one in six runs of the Work IQ and Fabric IQ questions in rehearsals) | New session and ask again. Don't present the second lane as a planned double-check |
| The crash test doesn't stop the pack | The one-shot switch was used | Arm it again (7.1) |
| No approval card | A reload, or the card was answered | New session, send 6.3 again |
| The draft time isn't 10:00-11:00 AM | The time came from search text, not the calendar | **Reject**, ask again in a new session |
| The 9.2 brief isn't revised | The reviewer test wasn't armed in this session, or already used | Send 9.2's first prompt again, then the brief |
| 9.4's packs all degraded | Work IQ consent or Fabric is missing for this user | Say so; the durability facts still hold. Afterwards, give the consents as the runbook's one-time setup shows, or resume Fabric |
| A source chip says it can't be found | Not among the items Work IQ returned in this session | **Open original** in the pane |
| Teams opens the desktop app | The installed client is signed in elsewhere | **Use the web app instead** |

## Appendix A: Defender advanced hunting queries

Paste each into Defender's advanced hunting editor and select **Run query**. `fdcc1f02-fc51-4226-8753-f668596af7f7`
is Work IQ's application ID, the same in every tenant. The Agent 365 and Purview records are in `CloudAppEvents` (the
Microsoft 365 connector in Defender for Cloud Apps); `MicrosoftGraphActivityLogs` needs an Entra diagnostic setting that
sends the Graph activity logs to the Microsoft Sentinel workspace onboarded to the Defender portal.

One user's Work IQ calls, in four logs (6.5; set `user` first; select a row for **Inspect record**). `IdentityInfo` and
`AgentsInfo` are snapshots taken a few times a day, so the query gives them their own windows: a short `since` would
otherwise hide them, and with them the Agent 365 and Graph rows.

```kusto
// One user's calls to the investment advisor agents, every Work IQ call made for them, and every prompt Purview blocked
let user = "advisor@contoso.onmicrosoft.com";
let since = ago(1d);
let workIq = "fdcc1f02-fc51-4226-8753-f668596af7f7";
let oid = toscalar(IdentityInfo | where Timestamp > ago(14d) and AccountUpn =~ user | summarize arg_max(Timestamp, AccountObjectId) | project AccountObjectId);
let name = toscalar(IdentityInfo | where Timestamp > ago(14d) and AccountUpn =~ user | summarize arg_max(Timestamp, AccountDisplayName) | project AccountDisplayName);
let agents = AgentsInfo | where Timestamp > ago(7d) and Name startswith "advisor-team" | summarize arg_max(Timestamp, Name) by EntraAgentID;
union
(CloudAppEvents | where Timestamp > since and ActionType == "InvokeAgent" and tostring(RawEventData.UserId) =~ user
  | extend EntraAgentID = tostring(RawEventData.TargetAgentId) | join kind=inner agents on EntraAgentID
  | project Log = "Agent 365", What = strcat("called ", Name)),
(MicrosoftGraphActivityLogs | where TimeGenerated > since and AppId == workIq and UserId == oid
  | project Log = "Graph activity logs",
      What = strcat("Work IQ ", RequestMethod, " ", replace_regex(url_decode(tostring(parse_url(RequestUri).Path)), @"/(b![^/]+|[0-9A-Za-z!_=-]{30,})", "/{id}"))),
(CloudAppEvents | where Timestamp > since and ActionType == "CopilotInteraction" and tostring(RawEventData.UserId) =~ user
  | where RawEventData.AppIdentity == "Copilot.M365Copilot.Apps"
  | mv-expand r = RawEventData.CopilotEventData.AccessedResources to typeof(string)
  | extend url = tostring(parse_json(r).SiteUrl)
  | extend Item = case(url has "outlook.", "an email", url has "/l/meeting/", "a meeting", url has "file=",
      url_decode(extract(@"file=([^&]+)", 1, url)), url_decode(extract(@"/([^/?]+\.(pdf|docx|xlsx))", 1, url)))
  | where isnotempty(Item)
  | project Log = "Purview Audit", What = strcat("Work IQ read ", Item)),
(CloudAppEvents | where Timestamp > since and ActionType == "DLPRuleMatch" and tostring(RawEventData.UserId) =~ user
  | extend p = parse_json(tostring(RawEventData.PolicyDetails[0]))
  | project Log = "Purview DLP", What = strcat("blocked a prompt: ", tostring(p.Rules[0].ConditionsMatched.SensitiveInformation[0].SensitiveInformationTypeName))),
(CloudAppEvents | where Timestamp > since and ActionType == "AlertTriggered" and RawEventData.Category == "DataLossPrevention"
  | where tostring(parse_json(tostring(RawEventData.Data)).f3u) =~ user
  | project Log = "Purview DLP", What = strcat("alert: ", tostring(RawEventData.Name)))
| summarize Events = count(), What = make_set(What, 8) by Log
| project Log, User = name, Events, What
| order by Log asc
```

Work IQ's Copilot interactions for a user, with the jailbreak and cross-prompt-injection (XPIA) flags (Work IQ's `ask`
tool and Work IQ Chat over A2A create these; Microsoft 365 Copilot sets the flags when it detects an attack):

```kusto
CloudAppEvents
| where Timestamp > ago(1d) and ActionType == "CopilotInteraction"
| where RawEventData.AppIdentity == "Copilot.M365Copilot.Apps"
| mv-apply m = RawEventData.CopilotEventData.Messages to typeof(string) on (
    summarize Jailbreak = countif(tobool(parse_json(m).JailbreakDetected)))
| mv-apply r = RawEventData.CopilotEventData.AccessedResources to typeof(string) on (
    summarize Read = count(), XPIA = countif(tobool(parse_json(r).XPIADetected)))
| project Timestamp, User = AccountDisplayName, Read, Jailbreak, XPIA
| order by Timestamp desc
```

The prompts Purview blocked, with the policy, the rule and what it found:

```kusto
CloudAppEvents
| where Timestamp > ago(7d) and ActionType == "DLPRuleMatch"
| extend p = parse_json(tostring(RawEventData.PolicyDetails[0]))
| project Timestamp, User = AccountDisplayName, Application = tostring(RawEventData.ApplicationName),
    Policy = tostring(p.PolicyName), Rule = tostring(p.Rules[0].RuleName),
    Found = tostring(p.Rules[0].ConditionsMatched.SensitiveInformation[0].SensitiveInformationTypeName),
    Actions = strcat_array(p.Rules[0].Actions, ", ")
| order by Timestamp desc
```

The agents in the security inventory:

```kusto
AgentsInfo
| where Name == "advisor-team"
| summarize arg_max(Timestamp, *) by AgentId
| project Timestamp, Name, Platform, EntraAgentID, EntraBlueprintID, PublishedStatus, LifecycleStatus, Endpoints
```

What Work IQ read and wrote as the advisor (the Office 365 connector):

```kusto
OfficeActivity
| where TimeGenerated > ago(1d)
| where * has "fdcc1f02-fc51-4226-8753-f668596af7f7" // Work IQ
| summarize Events = count(), Latest = max(TimeGenerated) by OfficeWorkload, Operation, UserId
| order by Events desc
```

Every Graph call Work IQ made on behalf of a user:

```kusto
MicrosoftGraphActivityLogs
| where TimeGenerated > ago(1d)
| where AppId == "fdcc1f02-fc51-4226-8753-f668596af7f7" // Work IQ
| summarize Calls = count(), Latest = max(TimeGenerated) by RequestMethod, Path = tostring(split(RequestUri, "?")[0])
| top 20 by Calls
```

A detection candidate, any write Work IQ performs for a user:

```kusto
MicrosoftGraphActivityLogs
| where TimeGenerated > ago(7d)
| where AppId == "fdcc1f02-fc51-4226-8753-f668596af7f7" and RequestMethod in ("POST", "PATCH", "PUT", "DELETE")
| project TimeGenerated, UserId, RequestMethod, RequestUri, ResponseStatusCode
```

## Appendix B: the crash in the logs

In the Log Analytics workspace behind the project's Application Insights, set the time range to the crash:

```kusto
union AppTraces, AppExceptions
| where TimeGenerated > ago(30m)
| extend msg = coalesce(Message, OuterMessage)
| where msg has_any ("Advisor host starting", "DEMO CRASH", "Crash test", "in-flight turn")
| project TimeGenerated, AppRoleInstance, msg = substring(msg, 0, 200)
| order by TimeGenerated asc
```

For 7.2 you see "DEMO CRASH: stopping process …" and a new "Advisor host starting in team mode." For 9.4 you see
"DEMO CRASH TEST: stopping the agent process before pack 3; packs 1-2 are in the state store.", then "Advisor host
starting in team mode." in a new instance: the session the panel opened when it sent the step again.
