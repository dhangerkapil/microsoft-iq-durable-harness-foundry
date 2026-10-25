---
name: microsoft-iq-answer
description: Answer one client question from all four Microsoft IQs at once - Work IQ (mail, meetings, Teams), Fabric IQ (the portfolio system), Foundry IQ (the firm's rules) and Web IQ (public news) - in one parallel step, with every fact labelled by its IQ and one combined picture, for example how a client could fund a cash need.
---

Use this skill when a client question needs every kind of source together, for example how a client could fund a cash
need or a purchase and what must happen first, or what a position's move means for the client; or the advisor asks
for all four IQs in one answer. Advisors ask this in business terms, without naming any source.

1. Call `recall_client_memory` with the client's full name first, on its own: its last brief names the largest holding
   for the Web IQ item. Then, in ONE step, make all of these calls in parallel (one turn, never one after another):
   - Work IQ: `workiq___fetch` with `/me/messages?$search="<client>"&$top=10&$select=id,subject,from,receivedDateTime,
     body,webLink` and `/me/calendarView?startDateTime=<UTC now minus 10 days>&endDateTime=<UTC now plus 21 days>&
     $select=id,subject,start,end,webLink&$orderby=start/dateTime&$top=50`; `workiq___retrieve`, capabilities
     `[{"name": "Meetings"}]`: "<client> <the topic>, with the client's own words"; `workiq___retrieve`, capabilities
     `[{"name": "TeamsMessages"}]`: "<client> <the topic> in Teams chats with colleagues".
   - Fabric IQ: `query_portfolio_data`: "<client>: total portfolio value, cash and equivalents, Information Technology
     sector weight, and the largest single-stock position's ticker, weight, market value, shares, price and cost
     basis per share, on the latest date, and the single-stock limit in the client's investment policy profile".
   - Foundry IQ: `search_firm_knowledge` once per rule the question touches, for example "What does the firm
     require when a client position is above its single-stock limit?" and "What does the firm require before a
     large withdrawal or wire?".
   - Web IQ: `search_public_web`, kind `news`, for the largest holding and its main public theme (a company or
     ticker and a theme only; never the client's name, an amount or anything from Microsoft 365). If neither memory
     nor the question names the holding, make this call once the Fabric IQ or Work IQ result names it.
2. In ONE step: `to_eastern_time` for the dates you report; `liquidity_check` with each cash need and its deadline;
   `rebalancing_check` once per tax budget in play (the signed IPS budget and any lower budget from a draft or an
   email), following `rebalancing-guard`. Take every input from the sources, with its date, the portfolio figures
   from Fabric IQ.
3. Write the answer in exactly this shape, keeping the headings, after one short line saying what you checked
   (the parts in angle brackets are yours to fill):
     ## What each IQ found
     - **Fabric IQ** (the portfolio system, as of <date>): value, cash above the reserve, the position against its
       limit.
     - **Work IQ** (mail, meetings and Teams, read as you): what the client and the team said, each item cited by what
       it is and its date, for example "the client's email of <date>".
     - **Foundry IQ** (the firm's rules): each rule that applies, as "<document code> §<section>".
     - **Web IQ** (public news): one or two items as [publisher, date](url), or "not checked; Web IQ is unavailable
       right now".
     ## Putting it together
     The calculated figures, each naming the tool that calculated it: cash available against the need and the
     shortfall; what a trim to the limit sells and realizes against each tax budget; any conflict between sources.
     ## What must happen first
     Numbered steps in order, each with the rule behind it.
     ## Questions to ask
     Three, phrased for the advisor.
   After the questions, write this line, exactly, on its own: "Four Microsoft IQs in one step, in parallel, each with
   its own identity." Nothing comes after it: the app attaches the Microsoft 365 sources itself.
   Put each fact on its own IQ's line: emails, calendar events, meetings and Teams chats are always Work IQ; firm
   documents (codes such as CP-104, OPS-220 or EDU-115) are always Foundry IQ; positions, weights, cash and limits
   from `query_portfolio_data` are Fabric IQ; public news is Web IQ. Facts and calculations only, never a
   recommendation; tax treatment must be confirmed with the client's tax adviser. Keep it under 450 words. Don't
   write a `workiq_references` block: the app attaches every cited email, event and Teams message.
4. If one IQ is unavailable or needs consent, say so on its line and carry on with the others; never fill its gap
   from another IQ.
