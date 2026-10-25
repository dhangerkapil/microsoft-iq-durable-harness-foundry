"""Durable, long-running meeting research: progress markers and checkpoints in the Foundry state store.

Follows Foundry's "Manage state for long-running agents": every finished phase becomes an append-only checkpoint
(deterministic key, SHA-256), written before the progress marker moves, and the marker moves only with the ETag of
the last read. If the hosting process stops between those writes (the presenter's crash switch does exactly that),
the next attempt finds the checkpoint by its key, verifies it, reuses it and moves the marker on, so no finished
phase runs twice and nothing is written twice. The platform keeps the store and the session; this harness decides
what to checkpoint and how to resume.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import sys
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from html import unescape
from typing import Annotated, Any, Literal, Protocol
from uuid import uuid4

from agent_framework import tool
from azure.ai.agentserver.core.storage import FoundryStorageConflictError, FoundryStoragePreconditionError

from .advisor import (
    PROCESS,
    ItemStore,
    annotate,
    client_key,
    now_iso,
    process_identity,
    result_text,
    story_now,
    tracer,
)
from .naming import store_name

logger = logging.getLogger(__name__)
DURABLE_STORE_NAME = store_name("durable-runs")
RESUME_WINDOW = timedelta(hours=3)
MAX_TEXT = 9_000
CRASH_KEY = "demo--crash"

class WorkIQCaller(Protocol):
    async def call_tool(self, tool_name: str, **kwargs: Any) -> Any: ...


@dataclass(frozen=True)
class Phase:
    name: str
    title: str
    tool: str
    wave: int
    source: str = "Work IQ"


# Wave 1: the slow semantic searches. Wave 2: exact Graph reads. Wave 3: the other Microsoft IQ sources, when this
# deployment has them (Fabric IQ, Foundry IQ, Web IQ).
PHASES = (
    Phase("documents", "IPS, meeting notes and portfolio overview (Work IQ retrieve, OneDrive and SharePoint)",
          "workiq___retrieve", 1),
    Phase("meetings", "Meeting topics, decisions and actions (Work IQ retrieve, Meetings)", "workiq___retrieve", 1),
    Phase("calendar", "Meetings with the client (Graph calendar read through Work IQ fetch)", "workiq___fetch", 2),
    Phase("email", "Email about the client (Graph mail search through Work IQ fetch)", "workiq___fetch", 2),
)
EXTRA_PHASES = (
    Phase("portfolio", "Positions, limits and cash (Fabric IQ portfolio data agent)", "query_portfolio_data", 3,
          "Fabric IQ"),
    Phase("firm_policy", "Concentrated positions, large withdrawals and client communications (Foundry IQ firm "
          "knowledge base)", "search_firm_knowledge", 3, "Foundry IQ"),
    Phase("market_news", "Public news on the client's largest holdings (Web IQ)", "search_public_web", 3, "Web IQ"),
)
RETRIEVAL_KEY = "application/vnd.ms-workiq.retrieval"


def phase_arguments(phase: Phase, client: str, now: datetime) -> dict[str, Any]:
    if phase.name == "calendar":
        start = (now - timedelta(days=10)).strftime("%Y-%m-%dT%H:%M:%SZ")
        end = (now + timedelta(days=21)).strftime("%Y-%m-%dT%H:%M:%SZ")
        return {"entityUrls": [
            f"/me/calendarView?startDateTime={start}&endDateTime={end}"
            "&$select=subject,start,end,isOnlineMeeting,organizer&$orderby=start/dateTime&$top=50"]}
    if phase.name == "email":
        name = client.replace('"', "")
        return {"entityUrls": [f'/me/messages?$search="{name}"&$top=10&$select=subject,from,receivedDateTime,body']}
    queries = {
        "documents": (f"{client} signed investment policy statement limits and tax budget, draft IPS, meeting notes "
                      "and portfolio overview", "OneDriveAndSharePoint"),
        "meetings": (f"{client} meeting topics, decisions and action items", "Meetings"),
    }
    query, source = queries[phase.name]
    return {"query": query, "capabilities": [{"name": source}]}


def plain_text(html: str) -> str:
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    return " ".join(unescape(text).split())


def compact(phase: Phase, client: str, raw: Any) -> dict[str, Any]:
    """Keep what the brief needs and stay far below the state store's 1 MB item limit."""
    text = result_text(raw)
    try:
        payload = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return {"text": text[:MAX_TEXT], "truncated": len(text) > MAX_TEXT}
    entries = [
        entry for result in (payload.get("results", []) if isinstance(payload, dict) else [])
        if isinstance(result, dict) for entry in (result.get("data") or {}).get("value", [])
        if isinstance(entry, dict)
    ]
    if phase.name == "calendar":
        names = {part.lower() for part in client.split() if len(part) > 2}
        events = [{key: event.get(key) for key in ("subject", "start", "end", "isOnlineMeeting")}
                  for event in entries if any(name in str(event.get("subject", "")).lower() for name in names)]
        return {"events": events}
    if phase.name == "email":
        messages = []
        for message in entries[:10]:
            sender = (message.get("from") or {}).get("emailAddress") or {}
            body = plain_text(str((message.get("body") or {}).get("content", "")))
            messages.append({"subject": message.get("subject"), "from": sender.get("name") or sender.get("address"),
                             "receivedDateTime": message.get("receivedDateTime"), "body": body[:1500]})
        return {"messages": messages}
    if isinstance(payload, dict) and isinstance(payload.get(RETRIEVAL_KEY), dict):
        payload = payload[RETRIEVAL_KEY]
    if isinstance(payload, dict):
        hits = []
        for hit in payload.get("retrievalHits", [])[:12]:
            if isinstance(hit, dict):
                hits.append({key: hit.get(key) for key in (
                    "webUrl", "entityUrl", "entityType", "driveId", "itemId", "sensitivityLabel", "relevanceScore")
                    if hit.get(key) is not None})
        markdown = str(payload.get("markdown", ""))
        return {"markdown": markdown[:MAX_TEXT], "truncated": len(markdown) > MAX_TEXT, "hits": hits,
                "resultCount": payload.get("resultCount")}
    return {"text": text[:MAX_TEXT], "truncated": len(text) > MAX_TEXT}


def flush_logs() -> None:
    for handler in logging.getLogger().handlers:
        try:
            handler.flush()
        except Exception:
            pass
    sys.stdout.flush()
    sys.stderr.flush()


PhaseRunner = Callable[[str, list[str]], Awaitable[Any]]
PREFIX = "pack"


class ProcessStopped(BaseException):
    """Raised after the crash switch stops the process; only reachable when exit_process returns (tests)."""


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def flatten(raw: Any) -> Any:
    """A runner's answer, parsed when it is JSON, and cut to the size every checkpoint keeps."""
    text = result_text(raw)
    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return {"text": text[:MAX_TEXT], "truncated": len(text) > MAX_TEXT}
    if len(json.dumps(parsed)) <= MAX_TEXT * 2:
        return parsed
    return {"text": text[:MAX_TEXT], "truncated": True}


class DurableMeetingPack:
    """Long-running meeting research with the state pattern from Foundry's "Manage state for long-running agents".

    State for one run lives in the Foundry state store under pack/<client>/<run>/:
      run                      what the run is (client, phases, public topics) and its status
      progress                 the progress marker: the next wave to run. It moves only with the ETag of the last
                               read (if_match), and it keeps the history of every move.
      checkpoints/<n>-<phase>  one append-only checkpoint per finished phase: the result and its SHA-256, written with
                               create_item under a deterministic key, always before the marker moves
      attempts/<n>, <n>-end    one record per attempt (the process that ran it) and its end; an attempt with no end
                               record was interrupted
    When the process stops between a wave's checkpoints and the marker, the next attempt finds the checkpoints by key,
    verifies their hashes, reuses them, moves the marker with its ETag and continues: no finished phase runs twice and
    nothing is written twice. The presenter's crash switch stops the process at exactly that point (between_writes) or
    in the middle of a wave (mid_wave).
    """

    def __init__(
        self, store_factory: Callable[[], Awaitable[ItemStore]], memory_store_factory: Callable[[], Awaitable[ItemStore]],
        workiq: WorkIQCaller, *, runners: dict[str, PhaseRunner] | None = None,
        exit_process: Callable[[int], None] = os._exit, clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.store_factory = store_factory
        self.memory_store_factory = memory_store_factory
        self.workiq = workiq
        self.runners = dict(runners or {})
        self.phases = PHASES + tuple(phase for phase in EXTRA_PHASES if phase.name in self.runners)
        self.numbers = {phase.name: index for index, phase in enumerate(self.phases, start=1)}
        self.waves = sorted({phase.wave for phase in self.phases})
        self.exit_process = exit_process
        self.clock = clock

    # --- crash switch (presenter control, one shot) ---------------------------------------------------
    async def arm_crash(self, enabled: bool, mode: Literal["between_writes", "mid_wave"] = "between_writes",
                        wave: int = 2) -> dict:
        store = await self.memory_store_factory()
        value = {"armed": enabled, "mode": mode, "wave": wave, "armed_at": now_iso(), "written_by": PROCESS["id"]}
        await store.set_item(CRASH_KEY, value, tags={"kind": "demo-faults"})
        return value

    async def crash_plan(self) -> dict | None:
        item = await (await self.memory_store_factory()).get_item(CRASH_KEY)
        return dict(item.value) if item is not None and item.value.get("armed") else None

    async def crash_armed(self) -> bool:
        return await self.crash_plan() is not None

    async def _fire_crash(self, plan: dict, base: str, wave: int, detail: str) -> None:
        # The switch records why the process stopped; the run itself records nothing, as with a real crash.
        await (await self.memory_store_factory()).set_item(CRASH_KEY, {
            **plan, "armed": False, "fired_at": now_iso(), "fired_in": base, "fired_in_wave": wave, "detail": detail,
            "process": PROCESS["id"]}, tags={"kind": "demo-faults"})
        logger.error("DEMO CRASH: stopping process %s in %s (%s).", PROCESS["id"], base, detail)
        annotate(**{"durable.crash": True, "durable.crash_mode": plan.get("mode")})
        flush_logs()
        self.exit_process(75)
        raise ProcessStopped(detail)

    # --- keys ------------------------------------------------------------------------------------------
    @staticmethod
    def current_key(client: str) -> str:
        return f"{PREFIX}/{client_key(client)}/current"

    @staticmethod
    def base(client: str, run: str) -> str:
        return f"{PREFIX}/{client_key(client)}/{run}"

    def checkpoint_name(self, phase: Phase) -> str:
        return f"{self.numbers[phase.name]}-{phase.name}"

    @staticmethod
    def tags(client: str, run: str, kind: str) -> dict[str, str]:
        return {"client": client_key(client), "run": run, "kind": kind}

    # --- reads and writes ------------------------------------------------------------------------------
    async def latest_run(self, client: str) -> dict | None:
        store = await self.store_factory()
        pointer = await store.get_item(self.current_key(client))
        if pointer is None:
            return None
        item = await store.get_item(f"{self.base(client, pointer.value['run'])}/run")
        return dict(item.value) if item is not None else None

    def resumable(self, run: dict | None) -> bool:
        if not run or run.get("status") != "running":
            return False
        return self.clock() - datetime.fromisoformat(run["created_at"]) <= RESUME_WINDOW

    async def _new_attempt(self, store: ItemStore, client: str, run: str) -> int:
        base = self.base(client, run)
        page = await store.list_keys(tags=self.tags(client, run, "pack-attempt"), limit=100)
        number = 1 + sum(1 for key in page.keys if not key.key.endswith("-end"))
        while True:
            try:
                await store.create_item(f"{base}/attempts/{number}", {
                    "attempt": number, "process": PROCESS["id"], "process_started_at": PROCESS["started_at"],
                    "started_at": now_iso()}, tags=self.tags(client, run, "pack-attempt"))
                return number
            except FoundryStorageConflictError:
                number += 1  # another process started an attempt at the same moment

    async def _checkpoint(self, store: ItemStore, client: str, run: str, phase: Phase, attempt: int,
                          result: Any) -> tuple[dict, bool]:
        """Create the phase's checkpoint; if another attempt already created it, keep and return that one."""
        key = f"{self.base(client, run)}/checkpoints/{self.checkpoint_name(phase)}"
        value = {"phase": phase.name, "number": self.numbers[phase.name], "wave": phase.wave, "source": phase.source,
                 "result": result, "sha256": digest(result), "saved_at": now_iso(), "process": PROCESS["id"],
                 "attempt": attempt}
        try:
            await store.create_item(key, value, tags={**self.tags(client, run, "pack-checkpoint"), "phase": phase.name})
            return value, True
        except FoundryStorageConflictError:
            existing = await store.get_item(key)
            return dict(existing.value), False

    async def _advance(self, store: ItemStore, client: str, run: str, progress: Any, to_wave: int | str,
                       event: dict) -> Any:
        """Move the progress marker with the ETag of the last read. On a precondition failure another attempt moved
        it; reread and keep whichever marker is further ahead."""
        key = f"{self.base(client, run)}/progress"
        for _ in range(3):
            value = dict(progress.value)
            entry = {**event, "from": value.get("next_wave"), "to": to_wave, "at": now_iso(), "process": PROCESS["id"],
                     "etag_before": getattr(progress, "etag", None)}
            value.update(next_wave=to_wave, updated_at=entry["at"], written_by=PROCESS["id"],
                         history=[*value.get("history", []), entry])
            try:
                await store.set_item(key, value, tags=self.tags(client, run, "pack-progress"), if_match=progress.etag)
                return await store.get_item(key)
            except FoundryStoragePreconditionError:
                progress = await store.get_item(key)
                ahead = progress.value.get("next_wave")
                if ahead == "done" or (isinstance(ahead, int) and isinstance(to_wave, int) and ahead >= to_wave):
                    return progress
        raise RuntimeError("The progress marker kept changing; another attempt is running this pack.")

    # --- the run ----------------------------------------------------------------------------------------
    async def _execute(self, phase: Phase, client: str, topics: list[str], now: datetime) -> Any:
        with tracer.start_as_current_span("advisor.durable.phase") as span:
            span.set_attribute("advisor.durable.phase", phase.name)
            span.set_attribute("advisor.durable.source", phase.source)
            if phase.source == "Work IQ":
                return compact(phase, client, await self.workiq.call_tool(phase.tool, **phase_arguments(phase, client, now)))
            if phase.name == "market_news" and not topics:
                return {"skipped": "No public topics were given (tickers or companies, never client details)."}
            return flatten(await self.runners[phase.name](client, topics))

    async def run(self, client: str, mode: Literal["auto", "fresh"] = "auto",
                  public_topics: list[str] | None = None) -> dict:
        store = await self.store_factory()
        topics = [t.strip() for t in (public_topics or []) if t and t.strip()][:3]
        existing = await self.latest_run(client)
        resumed = mode == "auto" and self.resumable(existing)
        if resumed:
            assert existing is not None
            run_value, run_id = existing, existing["run"]
            topics = run_value.get("public_topics") or topics
        else:
            run_id = uuid4().hex[:12]
            run_value = {"run": run_id, "client": client, "status": "running", "created_at": now_iso(),
                         "public_topics": topics, "written_by": PROCESS["id"],
                         "phases": [{"checkpoint": self.checkpoint_name(p), "wave": p.wave, "source": p.source,
                                     "title": p.title} for p in self.phases]}
            await store.set_item(f"{self.base(client, run_id)}/run", run_value, tags=self.tags(client, run_id, "pack-run"))
            await store.create_item(f"{self.base(client, run_id)}/progress", {
                "next_wave": self.waves[0], "history": [], "updated_at": now_iso(), "written_by": PROCESS["id"]},
                tags=self.tags(client, run_id, "pack-progress"))
            await store.set_item(self.current_key(client), {"run": run_id, "updated_at": now_iso()},
                                 tags={"client": client_key(client), "kind": "pack-current"})
        base = self.base(client, run_id)
        attempt = await self._new_attempt(store, client, run_id)
        progress = await store.get_item(f"{base}/progress")
        plan = await self.crash_plan()
        # The calendar window follows the story clock; resuming (resumable) stays on real time.
        now = story_now(self.clock())
        restored, reconciled, executed, results = [], [], [], {}

        for wave in self.waves:
            wave_phases = [phase for phase in self.phases if phase.wave == wave]
            found = {}
            for phase in wave_phases:
                item = await store.get_item(f"{base}/checkpoints/{self.checkpoint_name(phase)}")
                if item is not None and item.value.get("sha256") == digest(item.value.get("result")):
                    found[phase.name] = item.value
            marker = progress.value.get("next_wave")
            if marker == "done" or (isinstance(marker, int) and wave < marker):
                for phase in wave_phases:
                    if phase.name in found:
                        results[phase.name] = found[phase.name]["result"]
                        restored.append(self.checkpoint_name(phase))
                if len(found) == len(wave_phases):
                    continue
            # The marker says this wave is pending: reuse checkpoints written before a crash, run the rest.
            for phase in wave_phases:
                if phase.name in found and phase.name not in results:
                    results[phase.name] = found[phase.name]["result"]
                    reconciled.append(self.checkpoint_name(phase))
            pending = [phase for phase in wave_phases if phase.name not in results]
            crash_mid_wave = bool(plan and plan.get("mode") == "mid_wave" and plan.get("wave") == wave and pending)

            async def execute(phase: Phase) -> None:
                result = await self._execute(phase, client, topics, now)
                value, created = await self._checkpoint(store, client, run_id, phase, attempt, result)
                results[phase.name] = value["result"]
                (executed if created else reconciled).append(self.checkpoint_name(phase))
                if crash_mid_wave and created:
                    await self._fire_crash(plan, base, wave, f"mid_wave: after checkpoint {self.checkpoint_name(phase)}, "
                                                             f"before the rest of wave {wave} finished")

            # A failure waits for the rest of its wave, so every phase that finishes is checkpointed. A stopped
            # process loses whatever was still in flight, so a simulated stop cancels the wave's other phases.
            tasks = [asyncio.create_task(execute(phase)) for phase in pending]
            failure: BaseException | None = None
            for finished in asyncio.as_completed(tasks):
                try:
                    await finished
                except ProcessStopped:
                    for task in tasks:
                        task.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)
                    raise
                except Exception as exc:
                    failure = failure or exc
            if failure is not None:
                # Finished phases stay checkpointed; the next attempt resumes the rest.
                await store.create_item(f"{base}/attempts/{attempt}-end", {
                    "attempt": attempt, "outcome": f"failed: {type(failure).__name__}: {str(failure)[:120]}",
                    "ended_at": now_iso(), "process": PROCESS["id"]}, tags=self.tags(client, run_id, "pack-attempt"))
                raise failure
            if plan and plan.get("mode", "between_writes") == "between_writes" and plan.get("wave") == wave and pending:
                await self._fire_crash(plan, base, wave, f"between_writes: wave {wave}'s checkpoints were written, the "
                                                         "progress marker was not moved")
            last = wave == self.waves[-1]
            progress = await self._advance(store, client, run_id, progress, "done" if last else wave + 1, {
                "attempt": attempt, "wave": wave,
                "reconciled": [name for name in reconciled if int(name.split("-", 1)[0]) in
                               {self.numbers[p.name] for p in wave_phases}],
            })

        run_value.update(status="completed", completed_at=now_iso(), written_by=PROCESS["id"])
        await store.set_item(f"{base}/run", run_value, tags=self.tags(client, run_id, "pack-run"))
        await store.create_item(f"{base}/attempts/{attempt}-end", {
            "attempt": attempt, "outcome": "completed", "ended_at": now_iso(), "process": PROCESS["id"]},
            tags=self.tags(client, run_id, "pack-attempt"))
        annotate(**{"durable.resumed": resumed, "durable.restored": len(restored),
                    "durable.reconciled": len(reconciled), "durable.executed": len(executed)})
        state = await self.state(client, run_id)
        return {
            "client": client, "run": run_id, "attempt": attempt,
            "durability": {
                "pattern": ("Checkpoint first (create_item, deterministic key, SHA-256), then move the progress "
                            "marker (set_item with the ETag of the last read)."),
                "store": f"Foundry state store '{DURABLE_STORE_NAME}' (preview), isolated to the signed-in user; "
                         f"keys under {base}/",
                "resumed_unfinished_run": resumed,
                "restored_checkpoints": restored,
                "reconciled_checkpoints": reconciled,
                "executed_in_this_process": executed,
                "this_process": PROCESS["id"],
                "progress_marker": state["progress_marker"],
                "timeline": state["timeline"],
            },
            "sources": {phase.name: phase.source for phase in self.phases},
            "results": results,
        }

    # --- what the store holds ---------------------------------------------------------------------------
    async def state(self, client: str, run: str) -> dict:
        store = await self.store_factory()
        base = self.base(client, run)
        run_item = await store.get_item(f"{base}/run")
        progress = await store.get_item(f"{base}/progress")
        page = await store.list_keys(tags={"client": client_key(client), "run": run}, limit=100)
        checkpoints, attempts, ends = [], {}, {}
        for key in page.keys:
            item = await store.get_item(key.key)
            if item is None:
                continue
            value = item.value
            if "/checkpoints/" in key.key:
                checkpoints.append({"key": key.key.removeprefix(base + "/"), "etag": key.etag, "source": value.get("source"),
                                    "wave": value.get("wave"),
                                    "attempt": value.get("attempt"), "process": value.get("process"),
                                    "saved_at": value.get("saved_at"), "sha256": str(value.get("sha256", ""))[:12],
                                    "verified": value.get("sha256") == digest(value.get("result"))})
            elif "/attempts/" in key.key and key.key.endswith("-end"):
                ends[value["attempt"]] = value
            elif "/attempts/" in key.key:
                attempts[value["attempt"]] = value
        checkpoints.sort(key=lambda c: int(c["key"].split("/")[-1].split("-", 1)[0]))
        crash = (await (await self.memory_store_factory()).get_item(CRASH_KEY))
        crash_value = dict(crash.value) if crash is not None and crash.value.get("fired_in") == base else None

        # Order: time (whole seconds), then attempt, then the order of writes within an attempt: start, each wave's
        # checkpoints, the marker move after that wave, then the attempt's end or stop.
        timeline: list[tuple[tuple, dict]] = []
        last = 1_000

        def add(at: str | None, attempt: int | None, order: tuple[int, int], event: dict) -> None:
            timeline.append(((at or "", attempt or 0, *order), {"at": at, "attempt": attempt, **event}))

        numbers = sorted(attempts)
        for number in numbers:
            a = attempts[number]
            add(a["started_at"], number, (0, 0), {"process": a["process"], "event": f"attempt {number} started",
                                             "write": f"create_item attempts/{number}"})
            if number in ends:
                outcome = ends[number]["outcome"]
                add(ends[number]["ended_at"], number, (last, 1), {
                    "process": a["process"], "event": f"attempt {number} {outcome}",
                    "write": f"create_item attempts/{number}-end"})
            elif number != numbers[-1] or (run_item and run_item.value.get("status") != "completed"):
                stopped_here = bool(crash_value and crash_value.get("process") == a["process"])
                cause = f" ({crash_value['detail']})" if stopped_here else ""
                at = crash_value.get("fired_at") if stopped_here else (attempts.get(number + 1) or {}).get("started_at")
                add(at, number, (last, 0), {"process": a["process"],
                                    "event": f"attempt {number} interrupted: process {a['process']} stopped{cause}",
                                    "write": "none: a stopped process writes nothing"})
        for c in checkpoints:
            add(c["saved_at"], c["attempt"], (int(c.get("wave") or 0), 1), {"process": c["process"],
                                                 "event": f"checkpoint {c['key'].split('/')[-1]} written ({c['source']})",
                                                 "write": f"create_item {c['key']} (sha256 {c['sha256']})"})
        for h in (progress.value.get("history", []) if progress is not None else []):
            note = f"; reused checkpoints written before the stop: {', '.join(h['reconciled'])}" if h.get("reconciled") else ""
            add(h.get("at"), h.get("attempt"), (int(h.get("wave") or 0), 2), {"process": h.get("process"),
                                                   "event": f"progress marker {h.get('from')} -> {h.get('to')}{note}",
                                                   "write": f"set_item progress if_match {h.get('etag_before')}"})
        ordered = [event for _, event in sorted(timeline, key=lambda pair: pair[0])]
        return {
            "run": run, "status": run_item.value.get("status") if run_item is not None else None,
            "progress_marker": {"next_wave": progress.value.get("next_wave"), "etag": getattr(progress, "etag", None)}
            if progress is not None else None,
            "checkpoints": checkpoints,
            "attempts": [{"attempt": n, "process": attempts[n]["process"], "started_at": attempts[n]["started_at"],
                          "outcome": ends[n]["outcome"] if n in ends else "interrupted"} for n in numbers],
            "timeline": ordered,
        }

    async def describe(self, client: str) -> dict:
        latest = await self.latest_run(client)
        memory = await self.memory_store_factory()
        memory_items = {}
        for kind in ("last-discussion", "last-brief"):
            item = await memory.get_item(f"{client_key(client)}--{kind}")
            memory_items[kind] = {"saved_at": item.value.get("saved_at"), "written_by": item.value.get("written_by"),
                                  "etag": getattr(item, "etag", None)} if item else None
        return {
            "client": client,
            "stores": {
                DURABLE_STORE_NAME: "long-running research runs: run, progress marker, checkpoints and attempts",
                store_name("client-memory"): "discussion baselines and briefs (long-term memory)",
                store_name("conversations"): "conversation history for each session",
                store_name("preferences"): "the advisor's preferences for how briefs are written",
            },
            "latest_run": await self.state(client, latest["run"]) if latest else None,
            "memory": memory_items,
            "this_process": process_identity(),
            "isolation": "Items are partitioned by the calling user; another advisor cannot read them.",
        }

    def tools(self) -> list:
        pack = self

        @tool(approval_mode="never_require")
        async def run_durable_meeting_pack(
            client_name: Annotated[str, "Client's full name, for example 'Michael Scott'."],
            mode: Annotated[Literal["auto", "fresh"], "auto resumes an unfinished run; fresh starts over."] = "auto",
            public_topics: Annotated[list[str] | None, "Up to three public topics for the web news phase, such as "
                                                       "tickers or companies ('NVIDIA AI data center outlook'). "
                                                       "Never client names or amounts."] = None,
        ) -> str:
            """Long-running meeting research in checkpointed waves across the Microsoft IQ sources: Work IQ documents
            and meetings, then calendar and email, then portfolio data, firm policy and public news where available.
            Each phase is checkpointed in the Foundry state store before the progress marker moves. After a crash,
            calling it again from any session resumes the run: it reuses every checkpoint, including ones written just
            before the stop, and runs only what is missing. Returns the research, the sources and a durability report
            with a timeline of every attempt, checkpoint and marker move."""
            with tracer.start_as_current_span("advisor.durable.run"):
                return json.dumps(await pack.run(client_name, mode, public_topics))

        @tool(approval_mode="never_require")
        async def show_durable_state(
            client_name: Annotated[str, "Client's full name."],
        ) -> str:
            """Show the latest long-running research run for a client as stored in the Foundry state store: the
            progress marker with its ETag, every checkpoint (key, ETag, writer, SHA-256), every attempt and process,
            and a timeline; plus memory items. For every store and the serving process, call show_state_store."""
            return json.dumps(await pack.describe(client_name))

        return [run_durable_meeting_pack, show_durable_state]
