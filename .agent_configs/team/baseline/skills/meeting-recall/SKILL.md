---
name: meeting-recall
description: Answer what was said, decided or assigned in a past meeting from its Teams recording transcript through Work IQ, with exact quotes and speakers, action items with owners, and what newer sources changed since.
---

Use this skill when the advisor asks what was said, discussed, decided or agreed in a past meeting, asks for quotes
from a meeting or its transcript, or asks for a meeting's action items.

1. In ONE step, make these calls in parallel (include `recall_client_memory` only if a client is named and you have
   not called it yet in this turn):
   - `recall_client_memory` with the client's full name.
   - `workiq___retrieve`, capabilities `[{"name": "Meetings"}]`, with the meeting's title words and the topics asked
     about, for example "<meeting title words> <client> tax budget house down payment action items". Never put a
     date or a weekday in a retrieve query: meetings are indexed in UTC, so an evening meeting in Eastern Time falls
     on the next UTC day, and a date in the query can exclude it.
   - `workiq___retrieve`, capabilities `[{"name": "OneDriveAndSharePoint"}]`: "<meeting title> meeting notes,
     action items, and documents prepared since, such as a draft policy or an analysis".
   - `workiq___fetch` with two paths: `/me/calendarView?startDateTime=<UTC now minus 10 days>&endDateTime=<UTC now>&
     $select=id,subject,start,end,organizer,webLink&$orderby=start/dateTime&$top=50`, for the meeting's exact time,
     and `/me/messages?$search="<client>"&$top=10&$select=id,subject,from,receivedDateTime,bodyPreview,webLink`,
     for what happened after the meeting.
   - `workiq___ask`: "In the recorded Teams meeting <meeting title words> with <client>, quote word for word from the
     transcript what <client first name> said about <the topics asked about>. Copy the transcript's own words
     exactly, including transcription errors, with the speaker for each line." (no dates or weekdays here either).
2. The transcript's words come back in two places: the Meetings hit's `matchingPassagesFromTranscript`
   (`Speaker: words`), when Work IQ returns passages rather than only the meeting's recap, and the
   `workiq___ask` answer. Quote from either; both are the transcript as Work IQ read it for the advisor. If neither
   has transcript text, say so and use the recap or the notes, labeled as such.
3. In ONE step, call `to_eastern_time` with the meeting's UTC start and end from the calendar. Then answer:
   - **The meeting:** title, date and time (Eastern Time), organizer, and that it was recorded and transcribed.
   - **What was said:** short verbatim quotes in quotation marks, each an exact substring of the transcript text
     Work IQ returned (the passages or the `workiq___ask` answer), with its speaker. A scripted recording can label
     every line with one account and mark the client's lines with
     "Michael says"; then attribute those words to Michael and say the transcript shows them as a narrated line.
     Never fix, complete or join transcript words inside quotation marks. Where a passage is garbled, quote only its
     legible part, or paraphrase it and label it a paraphrase.
   - **Action items:** a table with the columns Owner, Action, Due, Source and Done since?. Source is the
     transcript where the assignment is legible, otherwise the meeting notes; every row has its own Source cell
     (a table without the Source column is incomplete), and list only what a source assigns.
     Done since? is "done", "in progress" or "no sign yet", each with its evidence from a later email or file
     (title and date), for example a draft that now exists or an email that confirms the item. Never mark an item
     done without a source.
   - **Since then:** anything a newer email, file or chat changed (a moved date, a different budget), with both
     values, their sources and dates.
   End with the `workiq_references` block: the meeting itself (kind "event", the link Work IQ returned for it, and
   `/me/events/{id}` from the calendar when it is there) and the notes file.
