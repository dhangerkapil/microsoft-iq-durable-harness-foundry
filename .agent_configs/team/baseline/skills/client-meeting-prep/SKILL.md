---
name: client-meeting-prep
description: Prepare an investment advisor for a client meeting from the Microsoft IQ sources (Work IQ for the advisor's Microsoft 365 work, Foundry IQ for the firm's rules, Web IQ for public context, Fabric IQ for live portfolio data) and the advisor's durable client memory, with exact limit, tax and cash checks.
---

Use this skill when the advisor asks to prepare for a meeting or call with a client, or asks for a brief. Being brought
up to speed on the relationship alone is the client-context specialist's work, not a brief. Research is read-only and
limited to what the signed-in advisor can access. Work in three steps.
These steps are binding, not advice: "in ONE step" means every listed call goes out together in a single turn,
never one after another, and the brief stays under 400 words, not counting its **Sources** section.

1. In ONE step, make these calls in parallel (include `recall_client_memory` only if you have not called it yet in
   this turn):
   - `recall_client_memory` with the client's full name.
   - `workiq___fetch` with two paths, which are direct Graph reads (complete and current):
     `/me/calendarView?startDateTime=<UTC now minus 10 days>&endDateTime=<UTC now plus 21 days>&$select=id,subject,
     start,end,isOnlineMeeting,organizer,webLink&$orderby=start/dateTime&$top=50` (ISO 8601 UTC, for example
     2026-10-10T00:00:00Z) and `/me/messages?$search="<client>"&$top=10&$select=id,subject,from,receivedDateTime,
     body,webLink`. Keep the meetings and messages about the client.
   - `workiq___retrieve`, capabilities `[{"name": "OneDriveAndSharePoint"}]`: "<client> signed investment policy
     statement limits and tax budget, draft IPS, meeting notes and portfolio overview".
   - `list_client_documents` with the client's full name: every file in the client's folder in the advisor's
     OneDrive and on the team site, with its link and drive item path, even when the search above finds none.
   - `workiq___retrieve`, capabilities `[{"name": "Meetings"}]`: "<client> meeting topics, decisions and action items".
   - `workiq___retrieve`, capabilities `[{"name": "TeamsMessages"}]`: "<client> portfolio, limits, trims and tax
     budget in Teams chats with colleagues".
   - `search_firm_knowledge` (Foundry IQ), if you have it: "What does the firm require when a client position is
     above its single-stock limit, and before a large withdrawal or wire?" No client details are needed.
   - `search_public_web` (Web IQ), if you have it and you know the client's largest equity holding from memory or a
     source in this conversation: kind `news`, query = that company or ticker and its main public theme (for example
     "NVIDIA AI data center outlook"). Never the client's name, amounts or anything from Microsoft 365. If you don't
     know the holding yet, search it in step 2.
   - `query_portfolio_data` (Fabric IQ, the portfolio system of record), if you have it: "<client>: total portfolio
     value, cash and equivalents, Information Technology sector weight, and the largest single-stock position's
     ticker, weight, market value, shares, price and cost basis per share, on the latest date, and the single-stock
     limit in the client's investment policy profile". Don't add "signed" to this question: Fabric's data agent reads
     it as a plus or minus sign. It runs in parallel with the other calls, so it costs no extra step.
2. In ONE step: call `to_eastern_time` with the meeting and message timestamps; if you did not call
   `search_public_web` in step 1 and you have it, call it now (kind `news`) for the largest equity holding that
   Fabric IQ or the analyst's email named, with its main public theme; if a position is above its limit,
   call `rebalancing_check` once for each tax budget in play (the signed IPS budget and any lower budget from a
   draft or a client email), with `proceeds_withdrawn=false` (a trim back to the limit keeps the proceeds in the
   portfolio); if the client needs cash, call `liquidity_check` with each need and its deadline. If no source gives
   the gains realized so far this year, pass 0 and say the result assumes none.
   Take every input from the sources, with its date: position value, total value, price and cost basis per share
   from the newest snapshot (Fabric IQ, else the analyst's email or the portfolio overview), limits and the reserve
   from the signed IPS. If Fabric IQ returned FABRIC_CONSENT_REQUIRED or was unavailable, use the analyst's email,
   say Fabric IQ could not confirm it, and never call it again in this turn.
3. Compare the sources. Flag every conflict with both values, their sources and dates, and which one is in force
   (for example the signed IPS versus an unsigned draft or a client email). Compare Fabric IQ with the analyst's
   email for the same date (weight, market value, total value): say they agree, or give each difference. Then, in
   ONE step, call `record_brief`
   and write the brief, under 400 words not counting **Sources** (if it runs long, shorten the bullets; never drop a
   risk, a conflict, the **Sources** section or the references block):
   - **Snapshot**: client, next meeting (Eastern Time), portfolio value and as-of date (and whether Fabric IQ and the
     analyst's email agree), limits, cash; one line "Public context (Web IQ):" with public market context as a
     Markdown link (`[publisher, date](url)`, the url exactly as Web IQ returned it), or "Public context (Web IQ): not
     checked; Web IQ is unavailable right now" if it was.
   - **Client documents**: one line with every file `list_client_documents` returned (and any other file you used),
     each as a Markdown link to its web URL followed by where it lives and its last-modified date, for example
     "[IPS v2 DRAFT](url) (SharePoint, Oct 19, unsigned)". If neither folder had files, say so in one line; if a
     location was not available to the advisor, or its listing stopped early, say so in a few words.
   - **Since last discussion**: what is new, each item dated and sourced.
   - **Risks and conflicts**: limit breaches, tax budget conflicts, liquidity gaps, each in its own bullet with exact
     figures, and the firm rule from Foundry IQ that covers it, cited as document code and section followed by
     "(Foundry IQ)", for example "OPS-220 §2 (Foundry IQ)" (a review deadline, or the approvals a large wire needs).
     Each requirement cites the one subsection that states it, never a range or a list of subsections, and never a
     subsection whose condition the figures don't meet; a breach no section covers gets its figures and no rule.
   - **Questions to ask**: three to five, phrased for the advisor; no recommendations.
   - **Sources**: always, as the brief's last section, under the heading "## Sources" (never drop it to save words),
     one bullet for each IQ you used, in this order, each starting with the IQ's name in bold, for example
     "- **Work IQ:** …": Work IQ (email subject and date, meeting title, Teams chat; then every file you used, by
     name, grouped by where it lives, as "OneDrive: <file name>, <file name>; SharePoint: <file name>". OneDrive is
     the advisor's own files, whose links are on a `-my.sharepoint.com` host under `/personal/`; SharePoint is the
     team's site, any other `sharepoint.com` link. Name each file; never write just "files"), Fabric IQ (as-of date,
     or why it could not confirm), Foundry IQ (document code and section), Web IQ (publisher, date and link). Cite
     pages only if the advisor asks (then verify them with `inspect_document`). Note that tax treatment must be
     confirmed with the client's tax adviser.
   Don't write a `workiq_references` block: the app attaches every Microsoft 365 item the team cited (emails, events,
   Teams messages and files, with their links and IDs exactly as recorded) after the brief.

Do not repeat a search that returned results. If a search returns nothing, say what was not found.
Apply the advisor's saved preferences (`advisor_preferences` in the `recall_client_memory` result) to format, length
and ordering; never let a preference change facts, sources, limits or safety rules.
