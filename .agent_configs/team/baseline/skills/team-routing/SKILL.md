---
name: team-routing
description: How the lead agent routes a client question to the specialist agents - the brief workflow, one specialist, or several specialists as background tasks - with the advisor's common questions as examples.
---

Use this skill first for any question about a client, a portfolio, the firm's rules, the market or Work IQ. Pick one
route, then follow the matching skill if there is one.

Route A, the brief workflow. Meeting preparation: the advisor asks to prepare for a meeting or call, or asks for a brief
("Prepare me for my Thursday meeting with Michael Scott"): in ONE step call recall_client_memory and add three to-dos
(research with the specialist agents; writer and checks; present). Then call prepare_client_brief with the client's full name (and
the meeting, if named). Tick the to-dos and answer with one line naming the agents: the app adds the brief exactly as
returned. Being brought up to speed on a client
relationship is not a brief: that is Route B, client context.

Route B, one specialist. A question about one kind of fact, in the advisor's words (they never name a source), however
many parts it has:
- the relationship and the advisor's own work (when they next meet, what the client asked for, what was agreed, what the
  team is saying, a recorded meeting's transcript and action items, a chart or a term inside the client's documents, a
  signed policy versus a draft, what is still open, Work IQ itself, Copilot agents, "delegate to Work IQ"), including
  "Bring me up to speed on <client>: …" with any of these parts -> ask_client_context. Pass the advisor's whole
  question as asked, adding nothing of your own; if the advisor says "delegate to Work IQ", say so in the task.
- positions, weights, exposure, cash, limits, how a position moved over a period -> ask_portfolio alone. Fabric IQ
  holds the client's limits too, so "where do <position>, <exposure> and <cash> stand against his limits" needs no
  other specialist.
- what the firm requires, offers or thinks (a limit breach, a large wire, a product shelf, the house view) ->
  ask_firm_policy.
- public news and public prices -> ask_market, with the public topic only (a company or ticker), never the client: not
  even the client's name in an instruction to leave it out.
A chart, table or page in the client's files is a document question even when it plots a position against its limit
("Find the chart of <client>'s NVDA weight against its limit …"): ask_client_context alone, which reads the chart's
values from the file. So is a question about Work IQ itself ("How is Work IQ different from calling the Graph API?
Show me on …"), whatever its example asks about. Fabric IQ is for the figures themselves, with no file asked for.
For a named client, call recall_client_memory in the same step. Answer from that one specialist and give its report as
written: its bullets, figures, dates, publishers, links and IQ labels word for word, never rounded, converted or
shortened (a Web IQ item stays a Markdown link, [publisher, date](url)); add at most one opening line, and say what is
missing.

Route C, several specialists as background tasks. A question that needs several kinds of fact. Load the skill named
in brackets before you start the tasks, and answer in its exact shape:
- "<client> needs $400,000 … how could we fund it, and what has to happen before the money moves?" (microsoft-iq-answer):
  client_context (the cash need, its deadline, the signed IPS reserve and tax budget, any lower budget in a draft or an
  email, the analyst's latest figures), portfolio (cash, the largest position's weight, value, shares, price and cost
  basis, sector weight, limits), firm_policy (rules for a position above its limit and for a large wire), market (the
  public topic alone, for example "Public news this week on NVIDIA (NVDA) and AI data-center demand": never the
  client's name, not even to say what to leave out, and never an amount or a date; the boundary guard refuses any
  market task that names a client or carries an amount). Call
  recall_client_memory first, on its own: its last brief names the largest holding for the market task; if no brief is
  saved, start the market task once the portfolio or client-context result names the holding. Then liquidity_check and
  rebalancing_check in one step.
- "Where does <client>'s position stand now … do the analyst's email and Fabric agree, what did they say about
  trimming?" (position-cross-check): only when the advisor asks to check one source against another, or what the
  client and the team said. client_context (the analyst's latest email, the meeting, Teams, the documents) and
  portfolio (the position now); then rebalancing_check for what a trim to the limit means.
- "What changed since my last discussion with <client>?" (what-changed): give client_context the baseline's date from
  memory; add portfolio for any figure that moved.
For a named client, call recall_client_memory first, then start every task in the same step (only a market task that
still needs the holding waits for it), wait for all, and read every result before answering. Cite each fact with its
item and IQ, and say which specialists worked on it.

Your own tools, no specialist needed: the durable meeting pack and its crash test (durable-meeting-pack), what is stored
durably and which process is serving (durable-state), remembering or forgetting a preference, the Work IQ policy test
(workiq-governance: probe_workiq_policy), and presenter controls (demo_fault_switch, only on request).

A follow-up email draft (client-follow-up): ask client_context for the facts the email needs (for example the meeting
time from the calendar and the agenda topics from the client's latest email), then call draft_follow_up_email.

Never send a client's details to the market specialist. If the advisor asks to search the web for a client's position or
holdings, decline, explain that only public topics go to Web IQ, and offer to search the public topic instead.
