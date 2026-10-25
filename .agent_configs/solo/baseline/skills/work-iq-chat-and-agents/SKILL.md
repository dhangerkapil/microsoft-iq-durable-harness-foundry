---
name: work-iq-chat-and-agents
description: Use Work IQ beyond search - delegate a whole question to Work IQ's own agent (Chat over A2A), find and ask the Copilot agents the advisor can use (list_agents, ask with agentId), and have Copilot reason over exact files (ask with fileUrls), such as comparing a client's signed investment policy statement with a draft.
---

Use this skill when the advisor asks to delegate a question to Work IQ or Copilot, asks which Copilot agents are
available or to ask one of them, or asks to compare or reason over specific documents (for example the signed IPS
and a draft).

1. **Delegate a whole question (Work IQ Chat, A2A).** Call `delegate_to_work_iq` once with a self-contained task in
   the advisor's words: who, what, and which kinds of sources (email, meetings, chats, files). Each call is a new A2A
   task with no memory of earlier ones, so include everything the task needs. Present Work IQ's answer as Work IQ's,
   keeping its links, and give its A2A task ID (first 8 characters). If the advisor follows up on that answer, use
   `workiq___ask` (it keeps a conversation) or the Work IQ tools, not another delegation.
2. **Copilot agents.** Call `workiq___list_agents` (no arguments). Show a table with the columns Agent, Provider and
   Built by (Microsoft, or the organization when the provider is not Microsoft), plus one line: these are the agents
   this advisor can reach through Work IQ, with their own permissions. To ask one, call `workiq___ask` with its exact
   `agentId` and a self-contained question. Each agent keeps its own conversation in this session, so a follow-up to
   the same agent continues it. Never pass `conversationId`.
3. **Exact files.** Find the files with `workiq___retrieve` (capabilities `[{"name": "OneDriveAndSharePoint"}]`) and
   copy their web URLs exactly as returned. Call `workiq___ask` once with `fileUrls` set to those URLs (at most five)
   and a precise question, for example: "Compare these two investment policy statements: list every term that
   changed as a table with the v1 value, the v2 value and whether v2 is signed." Present the table, then say which
   version is in force and why (an unsigned draft never overrides a signed IPS), and list each file under Sources
   with its link.
4. When it helps, explain the three ways to reach Work IQ in one line each: MCP gives this agent Work IQ's tools to
   compose step by step (retrieve, fetch, ask, discovery, governed writes); A2A hands a whole task to Work IQ's own
   agent and returns its finished answer; REST is for an application calling Work IQ directly. Every call runs as the
   signed-in advisor under the tenant's Work IQ policy, and Work IQ logs and governs each one.
