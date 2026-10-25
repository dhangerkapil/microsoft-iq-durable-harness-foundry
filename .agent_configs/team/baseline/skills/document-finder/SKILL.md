---
name: document-finder
description: Find a chart, table or passage inside the advisor's OneDrive and SharePoint files with Work IQ, read a chart's values with Work IQ, verify the exact PDF page, and cite it so the app can open the file at that page.
---

Use this skill whenever the advisor asks to find, show or cite a chart, table, figure, page or passage in a file, or
asks for values shown in a chart. Follow every step even if the advisor asks to use Work IQ only: the page check
also runs through Work IQ (the harness downloads the file as the advisor), so say that and do it.

1. In ONE step, make these calls in parallel:
   - `workiq___retrieve`, capabilities `[{"name": "OneDriveAndSharePoint"}]`. Put the meaning of the request in the
     query, not just keywords, for example "chart showing how close Michael Scott's NVDA position is to the
     single-stock limit". Read the returned passages and `retrievalHits` (webUrl, entityUrl).
   - Only if the advisor asks for values shown in a chart (a high, a low, a value on a date, how close it came to a
     limit, where it ended): `workiq___ask`, which reads the chart image itself as the advisor. Ask, in plain words:
     "Look at the chart of <what it shows> in <client>'s files<, in <file name> if the advisor named it>. Reading
     the chart image itself, <what the advisor asked, e.g. what were the highest and lowest values and on which
     dates, how close did it come to the 25% limit, and where did it end>? Give the date of every value. Say which
     values are printed on the chart and which you read from the gridlines, and name the file and the page."
     Always ask for the date of each value, even if the advisor didn't say "date": a high or a low without its date
     is half an answer.
2. Pick the file whose passage matches; prefer the signed or final version over drafts. Verify the page with
   `inspect_document`: pass the hit's entityUrl as `resource_path` (or its webUrl as `web_url`) and the most specific
   literal text near the item, ideally its caption label such as "Figure 2" or "Table 1". Use the page whose text
   matches the caption of the item asked for. If several pages match, cite the page with the caption, not a
   contents line. If nothing matches, try one other short phrase from the passage, then say the page could not be
   verified.
3. Only if `workiq___ask` failed, gave no values, or read a different file than the one you verified: call
   `read_chart` (the harness's own chart reader) with the verified page, the same `resource_path`, and the question
   in plain words.
4. Answer with: the file name, the page, what the chart or table shows (from the passage and the caption), and the
   values with their dates. Say how each value was read: "printed on the chart" or "read from the chart,
   approximately", as Work IQ (or `read_chart`) reported it; never present an estimate as exact. A date read from
   the chart is approximate too, unless it is printed: write "around October 14", not "on October 14". Every high and
   low gets its date; if the chart reading gave none, write "date not given by the chart reading" rather than leaving
   it out. If two highs or
   lows are near-equal, give both with their dates and say the image can't tell which is higher. Check the values
   against any figures printed in the file's own text (the retrieve passage, or `page_text` from `read_chart`), such
   as a stated range, and say whether they agree. Compute differences such as the distance to a limit from those
   values, and say they inherit the estimate. End with how it was found and checked: "Work IQ retrieve found the
   file by meaning; Work IQ ask (Copilot reasoning, as you) read the chart image; the harness verified the page by
   downloading the PDF through Work IQ and matching the caption." If `read_chart` was used instead, say Work IQ
   couldn't read the chart and the harness read it with the agent's own model.
5. Charts are images: their values are pixels, not text, so search and page verification can't read them.
   `workiq___ask` reads the image; `read_chart` is the fallback. If both fail, say so and give values only from text
   in that file or another sourced document.
6. Finish with the ```workiq_references block: one entry per cited file with title, url (the file's webUrl from
   retrieve, not the link inside the ask answer), drive_id and item_id from `inspect_document`, page, slide null,
   and `location_verified: true` only for a page that `inspect_document` matched. The app shows that page under the
   answer, so the chart is the evidence.
