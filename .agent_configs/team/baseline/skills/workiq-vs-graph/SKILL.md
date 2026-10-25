---
name: workiq-vs-graph
description: Show, on a live question, how Work IQ differs from calling Microsoft Graph directly, by running the Graph-only calls and the Work IQ retrieval side by side and comparing what each returns.
---

Use this skill when the advisor asks how Work IQ is different from, or better than, calling the Graph API, or asks
to compare the two.

1. Pick the question: the advisor's question, or by default "Where is the chart that shows how close Michael
   Scott's NVDA position is to its limit, and what did Michael ask about it by email?"
2. In ONE step, run both paths in parallel:
   - Graph only (what a developer gets from Graph by keyword): `workiq___call_function` with functionUrl
     `/me/drive/root/search(q='<two or three keywords>')`, and `workiq___fetch` with
     `/me/messages?$search="<one or two keywords>"&$select=subject,from,receivedDateTime&$top=5`.
   - Work IQ: `workiq___retrieve` with the full natural-language question and capabilities
     `[{"name": "OneDriveAndSharePoint"}, {"name": "Email"}]`.
3. Answer the question from the Work IQ results (verify a chart page with `inspect_document` if the advisor asks for
   the page).
4. Then compare in one table with the columns Graph only and Work IQ, from what actually came back. Start with these
   measured rows, all from this turn's tool outputs:
   - Calls: how many calls each path made, by name.
   - Items back: how many files and messages the Graph calls listed, versus how many `retrievalHits` Work IQ
     returned, and from which sources.
   - Answer found: for each part of the question (the chart, its page, what the email said), "yes", "no" or
     "partly", and what was missing.
   - Permissions a custom app would need (always include this row): Graph only needs a per-workload scope for each
     source, here `Files.Read` or `Files.Read.All` and `Mail.Read`, consented for your app; Work IQ needs one
     delegated scope, `WorkIQAgent.Ask`, and every request is also checked against the tenant's Work IQ policy.
   Don't report call durations: you can't see them, and the app shows each call's time next to it.
   Then these rows, in a few words each:
   - What returned: file and message lists by keyword, versus ranked passages from inside files and mail, with
     citations, sensitivity labels and entity paths.
   - Work for the developer: one call per workload, then download, chunk, rank and summarize yourself, versus one
     call across sources, ready for grounding.
   - Meaning: keyword match versus semantic match on the question.
   - Reasoning: none, versus `workiq___ask` when Copilot reasoning is needed.
5. Be fair: Graph remains the system of record and Work IQ `fetch` reads Graph paths directly, which is the right
   tool for exact, current reads such as a date-filtered inbox. Work IQ adds semantic retrieval across sources,
   reasoning and a governance layer on top of Graph. Do not claim more than the results show.
