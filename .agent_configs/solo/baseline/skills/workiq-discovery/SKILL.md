---
name: workiq-discovery
description: Find a Microsoft Graph path at run time with Work IQ's discovery tools (search_paths, then get_schema), then read it with fetch, without assuming any endpoint.
---

Use this skill when the advisor asks you to discover or look up how to reach some Microsoft 365 data, not to assume
a Graph endpoint, or to use `search_paths` or `get_schema`.

1. Call `workiq___search_paths` once, with a few plain words for the data (for example "files shared with me").
   From the paths it returns, pick the one that best matches; prefer a `/me/...` path, which reads only what the
   advisor can see. Don't search again unless no path matches at all.
2. Call `workiq___get_schema` once for that path. Note the fields you need and whether the path supports `$select`
   and `$top`.
3. Call `workiq___fetch` once on the path, with `$select` of those fields and `$top=50` if supported. Keep the items
   that match what the advisor asked (for example, a client's name in the title or file name).
4. Answer briefly: the path you discovered and why it fits; the fields you read from its schema; the matching items
   (title, type, who shared it, when in Eastern Time through `to_eastern_time`, and its link). Say that no endpoint
   was hard-coded and that every call ran as the advisor, inside the tenant's Work IQ policy.
