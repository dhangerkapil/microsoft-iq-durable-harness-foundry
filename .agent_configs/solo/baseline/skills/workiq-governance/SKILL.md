---
name: workiq-governance
description: Show how Work IQ is governed for this agent, with a live probe of the tenant's Work IQ policy, and explain each control layer from Entra consent to the harness runtime.
---

Use this skill when the advisor asks what the agent is allowed to do, how Work IQ is governed, about Work IQ
policies, directory lookups, write access, or how to restrict an agent.

1. Call `probe_workiq_policy`. It asks Work IQ, as the signed-in advisor, to read their own mail and files, look up
   other people in the directory, and update and delete an email (a test item that doesn't exist, so nothing can
   change), and reports each policy decision. Creating content is shown by the approval-gated draft
   (`client-follow-up` skill).
2. Present the results as a table: check, Work IQ tool and request, status, decision. A denied request is a
   governance decision, not an error to retry. Never retry or work around it.
3. Explain the layers, briefly, using the results:
   - Entra ID: the Work IQ permission (WorkIQAgent.Ask) is delegated, admin-consented and per user; no app-only access.
   - The user's own Microsoft 365 permissions: Work IQ returns only what the advisor can already open.
   - Work IQ in the Microsoft 365 admin center: Agents > Tools > MCP servers > **Work IQ MCP** > **Policies**. Two
     policies: **Tenant Default** (applies to agents such as this one) and **Trusted Platform** (trusted Microsoft
     platforms such as Copilot Studio; it overrides Tenant Default for them). Each has Path Access toggles (user's
     own data, directory lookups, SharePoint, OneDrive, Teams, chats, mail search and more), Query Limits (paging,
     page sizes) and Mutations (write actions, create, partial update, replace, delete). Policy is evaluated on every
     request. Example: turn off directory lookups so agents use only the user's own data. Block turns the server off
     entirely, and Reset to recommended restores the defaults.
   - Source-specific Work IQ MCP servers (Outlook Mail, Outlook Calendar, OneDrive, SharePoint, Teams, Word, User,
     Copilot) let a harness get only the workloads it needs.
   - Foundry toolbox: this harness's toolbox exposes only read tools (retrieve, fetch, fetch_blob, call_function,
     get_schema, search_paths, ask); write tools are not even visible to the model. The probe uses a separate,
     demo-only toolbox (fetch, update_entity, delete_entity), and drafting uses one limited to create_entity.
   - Harness runtime: deterministic code, a human approval before any write (the follow-up draft is saved to
     Drafts, never sent), and policy denials surfaced, not retried.
4. Say plainly that per-agent or per-app policy is not in today's tenant-level control. The Work IQ product group
   is working on more granular, agent-level controls; do not promise dates or features. For per-harness limits
   today, use the toolbox allowlist or source-specific servers, an API Management gateway in front of Work IQ, and
   declarative-agent scoping.
