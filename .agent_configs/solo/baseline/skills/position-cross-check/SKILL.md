---
name: position-cross-check
description: Answer where a client's position stands now by pulling every place that knows it (the analyst's email, Fabric IQ portfolio data, the documents, the recorded meeting and the team's Teams chat), checking whether the sources agree, and putting exact numbers on a trim.
---

Use this skill when the advisor asks whether the sources agree on where a position stands, how a position got to
where it is, or what the client and the team said about a position or a trim. For the portfolio figures alone (where
a position, exposure or cash stands against the limits), use portfolio-data.

1. In ONE step, make these calls in parallel; each reaches a different place where the facts live (include
   `recall_client_memory` only if you have not called it yet in this turn):
   - `recall_client_memory` with the client's full name: the last discussion's snapshot.
   - `workiq___fetch` with `/me/messages?$search="<client>"&$top=10&$select=id,subject,from,receivedDateTime,body,
     webLink`: the analyst's latest figures and the client's own emails (tax budget, cash needs).
   - `query_portfolio_data` (Fabric IQ), in these words: "<client>: <ticker> weight, market value, price and cost
     basis per share, total portfolio value and cash and equivalents on the latest date and on <last discussion
     date>, and the single-stock limit in the client's investment policy profile". Don't add "signed" to this
     question: Fabric's data agent reads it as a plus or minus sign and prints the limit as -25%.
   - `workiq___retrieve`, capabilities `[{"name": "OneDriveAndSharePoint"}]`: "<client> signed investment policy
     statement single-stock limit and tax budget, draft IPS".
   - `workiq___retrieve`, capabilities `[{"name": "Meetings"}]`: "<client> portfolio review meeting transcript: what
     <client first name> said about <ticker>, trimming and the tax budget" (no dates or weekdays in the query:
     meetings are indexed in UTC). It gives the meeting and its recap.
   - `workiq___ask`: "In <client>'s most recent recorded Teams meeting, quote word for word from the transcript what
     <client first name> said about his tax budget and about selling or trimming. Copy the transcript's own words
     exactly, including transcription errors, with the speaker for each line." It gives the transcript's own words.
   - `workiq___retrieve`, capabilities `[{"name": "TeamsMessages"}]`: "<client> <ticker> weights trim analysis".
2. In ONE step: `rebalancing_check` once for each tax budget in play (the signed IPS budget and any lower one from a
   draft or a client email), with `proceeds_withdrawn=false` and the newest figures with their date; and
   `to_eastern_time` for the email and message times.
3. Answer, short and sourced:
   - **Where it stands:** a table with the columns Date, Weight, Source and How we know, oldest first: the chart's
     range if this conversation already read it, the last discussion's figure, and each source's newest figure.
   - **Do the sources agree?** Compare the analyst's email with Fabric IQ for the same date (weight, market value,
     total value): say "agree" or give each difference. Also read Fabric IQ's own words: if its summary or
     conclusion contradicts its numbers or the email (for example, it says the position is under the limit while
     its weight is above it), say the numbers agree but Fabric's summary doesn't, quote both, and trust the
     numbers. Say "agree" only when the numbers and the wording both agree. If Fabric IQ is unavailable, say so and
     use the email.
   - **What was said:** one short verbatim quote of the client's own words from the transcript (an exact substring
     of `matchingPassagesFromTranscript` or of the `workiq___ask` answer, with its speaker; a line marked "Michael
     says" is Michael's, narrated). A line with transcription errors is still a quote: keep it exactly as it is and
     say the transcript is garbled there, rather than dropping it. Use the meeting notes only if neither has a line
     from the client on the subject, and then say the words are from the notes. Then the Teams message (sender and
     time). The signed-in advisor is "you": write "you asked <analyst>...", not their own display name.
   - **What a trim to the limit means:** the `rebalancing_check` facts under each budget. No recommendation; tax
     treatment to be confirmed with the client's tax adviser.
   - **Questions for the meeting:** two or three.
4. Always finish with the ```workiq_references block; the app shows the sources only from it. One entry each for
   the analyst's email, any client email you used, the meeting (kind "event") or its notes file, the Teams message
   and the IPS file, with the URLs and IDs exactly as Work IQ returned them, and `location_verified: false`. Fabric
   IQ has no link: name it in the answer, not in the block.
