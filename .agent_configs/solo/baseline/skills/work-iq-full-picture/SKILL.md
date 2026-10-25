---
name: work-iq-full-picture
description: Bring the advisor up to speed on a client relationship in one answer, all from the advisor's own Microsoft 365 work through Work IQ - the next meeting and the client's latest requests (exact Graph reads), what was agreed in the recorded review and what the team is saying (search by meaning), the client's documents, how a policy draft differs from the signed one and what a chart shows (Copilot reasoning over exact files), and what is still open (a whole task delegated to Work IQ's own agent over A2A) - all read as the advisor, with every item cited.
---

Use this skill when the advisor asks to be brought up to speed on a client relationship as a whole: when they next
meet, what the client has asked for, what was agreed, what the team is saying, the client's documents (a policy
draft against the signed one, a chart) and what is still open; or asks what Work IQ can do. Everything it needs is
the advisor's own work, so use Work IQ only: no Fabric IQ, Foundry IQ or Web IQ, even where a figure or a rule comes
up (report what the emails, meeting, chats and documents say).

1. In ONE step, make all of these calls in parallel (one turn, never one after another):
   - `workiq___fetch` with two paths, direct Microsoft Graph reads (complete and current):
     `/me/calendarView?startDateTime=<UTC now minus 10 days>&endDateTime=<UTC now plus 21 days>&$select=id,subject,
     start,end,isOnlineMeeting,organizer,webLink&$orderby=start/dateTime&$top=50` and
     `/me/messages?$search="<client>"&$top=10&$select=id,subject,from,receivedDateTime,body,webLink`.
   - `workiq___retrieve`, capabilities `[{"name": "Meetings"}]`: "<client> what was said and decided in the recent
     review meeting, with the client's own words".
   - `workiq___retrieve`, capabilities `[{"name": "TeamsMessages"}]`: "<client> portfolio, limits and trims in Teams
     chats with colleagues".
   - `workiq___retrieve`, capabilities `[{"name": "OneDriveAndSharePoint"}]`: "<client> signed investment policy
     statement, IPS draft, meeting notes and portfolio overview".
   - `list_client_documents` with the client's full name: every file in the client's folders, with exact links.
   - `delegate_to_work_iq` (Work IQ Chat over A2A): "What has <client> asked the advisor for in recent emails and
     meetings, and what is still open? Cite each item."
2. In ONE step, with each file's URL exactly as `list_client_documents` (or retrieve) returned it (skip a call whose
   files were not found, and say so):
   - `workiq___ask` with `fileUrls` set to the signed IPS and the IPS draft: "Compare these two investment policy
     statements term by term: list each term that changed with the v1 value, the v2 value, and whether v2 is
     signed."
   - `workiq___ask` with `fileUrls` set to the portfolio overview: "Read the chart of the largest position's weight
     against its limit: the high and the low since the first of last month, and where it ended, each with its date.
     Say which values are printed on the chart and which are estimated from the image."
3. Answer in the order the advisor asked, one short section each, every item dated and cited. Each heading is the
   business topic, followed in italics by the Work IQ capability that answered it:
   - **When you next meet, and what <client> has asked for** *(Work IQ fetch: exact Microsoft Graph reads of the
     calendar and mail)*: the next meeting (Eastern Time) and the latest emails.
   - **What you agreed at the last review** *(Work IQ retrieve: the meeting recording)*: quote the client's own
     words exactly when the result has them, else the meeting notes.
   - **What the team is saying** *(Work IQ retrieve: Teams chats)*.
   - **The client's documents** *(Work IQ, the client's folders)*: every file, each as a Markdown link, listed as
     "OneDrive: <file>, <file>; SharePoint: <file>".
   - **The policy draft against the signed one** *(Work IQ ask: Copilot reasoning over the two exact files)*: what
     changed and which version is in force (an unsigned draft never overrides a signed IPS).
   - **What the chart shows** *(Work IQ ask: Copilot reading the chart image)*: the values, each marked printed or
     estimated. Don't cite a page number unless you verify it with `inspect_document`.
   - **What is still open** *(Work IQ's own agent, a whole task delegated over A2A)*: the open items, with the A2A
     task ID (first 8 characters).
   - One closing line, exactly: "One answer, every kind of Work IQ call: fetch, retrieve, ask and A2A, across mail,
     calendar, the meeting recording, Teams, OneDrive and SharePoint, all as you, under one delegated permission and
     the tenant's Work IQ policy."
   Keep it under 450 words, and end with the `workiq_references` block for every cited email, event, Teams message
   and file.
