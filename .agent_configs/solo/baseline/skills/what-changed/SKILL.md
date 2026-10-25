---
name: what-changed
description: Show what changed for a client since the last confirmed discussion, using the baseline saved in the Foundry durable state store, direct Microsoft Graph reads through Work IQ for new email and meetings, and Work IQ retrieval for changed documents.
---

Use this skill when the advisor asks what changed, what is new, or what happened since the last conversation
with a client, or asks to save a discussion as the baseline.

1. Call `recall_client_memory`.
   - If a last-discussion baseline exists, use its `discussion_at` time as the cutoff and say that it comes from
     durable memory saved on `saved_at`.
   - If none exists, find the most recent meeting with the client (`workiq___fetch` on `/me/calendarView` for the
     past 30 days), propose it as the baseline and ask the advisor to confirm. Do not save it until they confirm.
2. When the advisor confirms a discussion, call `record_discussion_baseline` with the meeting time (ISO 8601 with
   time zone), a two-to-five sentence summary of topics, decisions and actions, and the source.
3. With a baseline, make these calls in ONE step, in parallel:
   - `workiq___fetch` with two paths: `/me/messages?$filter=receivedDateTime ge <cutoff in UTC>&$orderby=
     receivedDateTime desc&$select=id,subject,from,receivedDateTime,bodyPreview,webLink&$top=25` and
     `/me/calendarView?startDateTime=<cutoff in UTC>&endDateTime=<UTC now plus 21 days>&$select=id,subject,start,
     end,webLink&$top=50` (21 days, three weeks ahead, so the next client meeting is included; not 21 hours). These
     are direct Graph reads: complete and current, with no search-index delay.
   - `workiq___retrieve`, capabilities `[{"name": "TeamsMessages"}]`: "<client> in Teams chats after <cutoff date>".
   - `workiq___retrieve`, capabilities `[{"name": "OneDriveAndSharePoint"}]`: "<client> documents created or changed
     after <cutoff date>, such as an IPS draft, with what changed".
4. Keep only items about the client. Convert the timestamps with `to_eastern_time` (in the same step as
   `liquidity_check` and any `rebalancing_check`), then list the changes newest first, each with its Eastern Time
   date and source. Separate client requests, market and portfolio changes, and document changes. Summarize the
   impact: limit breaches, conflicts between sources, cash needs against cash available, trims against the tax
   budget (follow `rebalancing-guard`).
5. Mention in one line why this used direct Graph reads for mail and meetings (exact date filter, immediately
   current) and semantic retrieval for documents and chats (meaning, not keywords). End with questions for the next
   meeting, call `record_brief`, and finish with the `workiq_references` block for every cited item.
6. Apply the advisor's saved preferences (`advisor_preferences` in the `recall_client_memory` result) to format,
   length and ordering; never let a preference change facts, sources, limits or safety rules.
