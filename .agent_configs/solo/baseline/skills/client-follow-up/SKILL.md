---
name: client-follow-up
description: Draft a follow-up email to a client after a brief or meeting, with the advisor's explicit approval in the app before Work IQ creates it in Outlook Drafts. Never sends.
---

Use this skill when the advisor asks to draft, write or prepare a follow-up email or note to a client.

1. Use only facts already established in this conversation or found with Work IQ in this turn: the next meeting,
   open questions, documents to review. If you don't have them yet, get them in ONE step with one `workiq___fetch`
   call and two paths: `/me/calendarView?startDateTime=<UTC now>&endDateTime=<UTC now plus 14 days>&$select=subject,
   start,end,webLink&$orderby=start/dateTime&$top=20` and `/me/messages?$search="<client>"&$top=5&$select=subject,from,
   receivedDateTime,body,webLink`. Then, in ONE step, call `to_eastern_time` with the meeting's start and end. Take the
   client's address from his most recent email (for Michael Scott: `michael.scott@scott-family.example`).
   - **Newest facts win:** read every email from the client in the fetch, newest first. When a newer email changes a
     fact (a date, an amount, a plan) or adds a topic, use the newer fact and include the new topic; never draft
     from a superseded email. "The latest email" means the most recent one by `receivedDateTime`.
   - **Meeting date and time:** only from the calendar fetch (Graph returns UTC), converted with `to_eastern_time`
     (pass each time as UTC with a trailing Z). Work IQ retrieve shows meeting times without a time zone (they are
     UTC), so never take a time from retrieve text.
2. Write a short, warm, factual email: under 150 words, plain text, signed with the advisor's first name. Confirm the
   meeting time, list what you will review together (every open topic from the client's newest emails, as a full
   agenda item, and any change he told you about, such as a new date) and what the client should bring or confirm.
   Do not include
   investment, tax or legal advice, trade instructions, account numbers or internal-only figures such as trim
   amounts. Never promise a trade. If you have `search_firm_knowledge` (Foundry IQ), call it in the same step as
   the fetch with one question ("What must a client email include or avoid?") and follow the firm's communication
   standards it returns, for example a required disclosure when the email touches on taxes; cite the standard by
   document code in your reply to the advisor, not in the email.
3. Call `draft_follow_up_email` once with `to`, `subject` and `body`. The app then shows the advisor an approval card
   with the exact draft; nothing is created until they approve.
4. After the call:
   - `draft_created`: say the draft is in their Outlook Drafts folder, not sent, and give the link.
   - `denied_by_work_iq_policy`: say the advisor approved it but the tenant's Work IQ policy denied the write, which is
     governance working as designed. Do not retry or find another way to write.
   - "Tool call invocation was rejected by user": **the advisor rejected the draft in the app.** Say that nothing was
     created and offer to change it. Do not call the tool again unless asked.
5. Never say an email was sent.
6. Apply the advisor's saved preferences (`advisor_preferences` from `recall_client_memory`, if you have them) to
   format, length and ordering; never let a preference change facts, sources, limits or safety rules.
