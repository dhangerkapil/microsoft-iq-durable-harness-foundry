---
name: durable-state
description: Show what this agent keeps in the Foundry durable state store and which process is serving, explain how state survives a crash, a restart or idle eviction, and remember, show or forget the advisor's preferences.
---

Use this skill when the advisor asks what is stored durably, to show the state store, which process is serving them
or whether the agent restarted, or to remember, show or forget a preference.

1. For what is stored, the state store, or which process you are: call `show_state_store` (no arguments). Then, in
   ONE step, call `to_eastern_time` with `this_process.started_at` and every store's `last_write`.
2. Present one table: Store | What it holds | Items | Last write (Eastern Time) | Written by.
   - What it holds: the `category` (the Learn article's kind of content), then a short version of `holds`.
   - Items: `items` ("500+" when `items_capped`); otherwise the `status`, such as "not created yet".
   - Written by: `last_written_by`, marked **this process** when `last_written_by_this_process` is true and **an
     earlier process** when it is false; "MAF hosting library" for framework checkpoints.
   Below the table, show this process: ID, start time (Eastern Time) and uptime in minutes. If
   `this_session.process_started_after_session_state` is true, say plainly that this process started after this
   session's state was first saved, so the session outlived a process (a crash, a restart or idle eviction) and
   continued from the state store. If it is false, don't mention it. If a durable meeting pack run lists several
   attempts, name their processes.
3. For an inventory, answer from `show_state_store` alone: don't call `recall_client_memory` for it, and don't quote
   anything stored (client memory, preference text, conversation content), even if it appears earlier in the
   conversation; the tool returns keys and metadata only, and so does the answer. Never say contents are hidden
   and then show some. Counts are the signed-in advisor's only. For one client's research run,
   `show_durable_state` gives its progress marker, checkpoints, attempts and timeline. A question about one preference ("what preference did I ask you to
   remember?") is not an inventory: answer it with `advisor_preferences` action `show`, in the advisor's own words.
4. "Remember that I prefer ...": call `advisor_preferences` with action `add` and the preference in the advisor's
   words. Confirm what was saved: kept durably for this advisor only and applied to format, length and ordering,
   never to facts, sources, limits or safety rules. "What are my preferences": action `show`. "Forget my
   preferences": action `clear`. If the result has an `error` (for example, 10 are already saved), say so.
5. Explain only what was asked, briefly and accurately:
   - Crash: the process exits; the platform restarts the container for the same session. Checkpoints written
     before the crash stay in the store, and the next run resumes from them (see `durable-meeting-pack`).
   - Restart: stopping the session or deploying a new version ends the process the same way; the next request
     starts a new one, which reads the same items.
   - Idle eviction: after the agent version's idle timeout (15 minutes by default, configurable from 2 to 60), the
     platform deprovisions the compute and persists the session's $HOME and files; the next request provisions new
     compute and restores them. State-store items persist independently of compute, so nothing in this table
     depends on the container.
   - Isolation: these stores partition items per end user. The platform resolves the caller from each request (the
     x-agent-foundry-call-id header), not from anything the agent passes, so another advisor can't read them.
   - Concurrency: every item has an ETag. Mutable items such as the preferences and a run's progress marker change
     only with an If-Match precondition (a concurrent change is re-read, never overwritten); checkpoints are
     create-only under deterministic keys, so a second writer gets a conflict instead of overwriting.
   - Expiry: a store's idle window removes items not written for 30 days by default; writes renew it, reads don't.
   - Responsibilities: Foundry provides the durable state store (preview), per-user isolation and the persisted
     session. This harness decides what to save and how to resume, and the MAF hosting library saves its own agent
     session state. Do not claim a fully managed durable harness.
