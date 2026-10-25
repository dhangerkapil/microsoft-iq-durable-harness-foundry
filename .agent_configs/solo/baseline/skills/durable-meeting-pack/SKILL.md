---
name: durable-meeting-pack
description: Run, resume and explain long-running meeting research across the Microsoft IQ sources, checkpointed in the Foundry state store with a progress marker, including the presenter's crash tests.
---

Use this skill when the advisor asks for a durable, long-running or resumable meeting pack, asks to resume research
after a failure, or runs a crash test. For everything stored durably and which process is serving, follow
`durable-state`.

1. To arm the crash test, call `demo_fault_switch` with component `process_crash` and enabled true only when the
   advisor explicitly asks. Then say: the next meeting pack will stop the agent process after wave 2 has written its
   checkpoints and before the progress marker moves, exactly the gap that Foundry's long-running guidance says to
   design for.
2. To run or resume, call `run_durable_meeting_pack` with the client's name (mode `auto`) and, if you know the
   client's largest equity holding, `public_topics` with one public topic for it (a company or ticker and theme,
   never client details). The pack runs in waves: 1) Work IQ documents and meetings (semantic retrieval), 2) Work IQ
   calendar and email (direct Graph reads), 3) the other IQ sources this deployment has: Fabric IQ portfolio data,
   Foundry IQ firm policy and Web IQ public news. Each finished phase becomes an append-only checkpoint (a
   deterministic key and a SHA-256) before the progress marker moves, and the marker moves only with the ETag of the
   last read. If an unfinished run exists, it resumes from any session: it restores the waves the marker passed,
   reuses the checkpoints written after the last marker move (it verifies their hashes), and runs only what is
   missing.
3. If the advisor asks what is stored for a client's research run, call `show_durable_state`; for every store and
   the serving process, call `show_state_store` (see `durable-state`).
4. After a pack returns, write the client brief from its results with the same sections as `client-meeting-prep`
   (including `to_eastern_time`, `rebalancing_check` and `liquidity_check` when needed, and sources grouped by IQ),
   then a **Durability report**: a table with one row per `timeline` entry, never merged, and exactly these columns:
   Eastern time (via `to_eastern_time`), Attempt, Process (its last six characters), Event, Store write (as given,
   for example `create_item checkpoints/3-calendar (sha256 …)` or `set_item progress if_match …`). Then one line each
   for: checkpoints restored, checkpoints reused although
   written just before the stop (`reconciled_checkpoints`), phases executed in this process, and the progress
   marker's final value. Say plainly what happened: which process stopped and where, and that the next attempt
   repeated no finished phase and wrote nothing twice. In the `workiq_references` block, give each item its own link
   and path from the pack's results, and cite a page only after `inspect_document` verified it.
5. Explain responsibilities accurately: Foundry provides the durable state store (preview, isolated per user), the
   persisted session and a hosted runtime that restarts the sandbox; this harness decides what to checkpoint, writes
   checkpoints before the marker, and reconciles after a stop. After a crash, the interrupted conversation stays
   locked so a half-finished turn is never replayed; resume from a new session. Automatic re-entry by the platform
   (resilient background responses: persisted input, a lease, and recovery that re-runs the handler) is a separate
   preview capability that the Agent Framework host supports for workflows; do not claim it for this chat agent.