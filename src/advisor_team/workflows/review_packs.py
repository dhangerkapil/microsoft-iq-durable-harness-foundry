"""W3, review packs for several clients, run by the hosted agent, advisor-team.

Durable steps with checkpoints in the Foundry state store: the pattern of the durable meeting pack (W2). The app (or
scripts/review_packs.py) sends one step per request as a review-packs command, and ReviewPacksGate answers it in code,
without the lead agent:

    start    the plan: the clients to confirm (a card)
    answer   the user's answer to the pending card: the clients, then each follow-up draft, one at a time (an approval
             applies only to the exact text, by its SHA-256)
    next     the next client's pack: the brief workflow (W1) for that client, then a short follow-up draft. Each
             finished pack is a create-only checkpoint in the state store, and no request runs longer than one pack
    view     the run as it stands

The crash test stops the agent process once, before the last pack, after the others are checkpointed. That request
fails; the app sends "next" again, and the new process restores the finished packs from the state store and runs only
the last one: nothing finished is redone. In a tenant with Microsoft 365 history for only some clients, a client without
any is a defined degraded pack: portfolio, firm policy and market, and "no Microsoft 365 history".

State, in the user-isolated durable store (team-durable-runs):
    packs/<run>/run       the run: the clients, its status, the pending card, the decisions and the timeline of steps
    packs/<run>/pack-<n>  one create-only checkpoint per finished pack, with its SHA-256
    packs/<run>/crash     the crash test's create-only marker, so it fires once per run
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import asdict, dataclass, field
from typing import Any

from agent_framework import AgentContext, AgentMiddleware, AgentResponse, AgentResponseUpdate, Content, Message, ResponseStream
from azure.ai.agentserver.core.storage import FoundryStorageConflictError

from ..advisor import PROCESS, ItemStore, now_iso
from ..durable import ProcessStopped, digest, flush_logs
from ..packs_protocol import COMMAND, view_text
from .brief import BriefRequest, run_brief

logger = logging.getLogger(__name__)
DEFAULT_CLIENTS = ("Michael Scott", "Jan Levinson", "Dwight Schrute")
DEFAULT_CLIENT_EMAIL = "michael.scott@scott-family.example"  # the hydrated story's client (data/hydration)
CRASH_CONTROL = "packs_crash"
CRASH_EXIT_CODE = 75
PROGRESS_SECONDS = 15.0
MAX_CLIENTS = 6
RUN_ID = re.compile(r"^[0-9a-f]{32}$")
# The run's own states, and what the app shows for each.
SHOWN = {"confirm": "waiting", "packing": "working", "drafts": "waiting", "done": "completed", "cancelled": "completed"}
CLIENTS_QUESTION = ("Prepare review packs for these {count} clients, one after another? Reply approve, or send the client "
                    "list to use.")
DRAFT_QUESTION = "Approve this follow-up draft? It is shown, not sent."


@dataclass
class PacksSummary:
    text: str
    packs: list[dict] = field(default_factory=list)
    drafts: list[dict] = field(default_factory=list)
    attempts: list[dict] = field(default_factory=list)


def draft_hash(to: str, subject: str, body: str) -> str:
    return hashlib.sha256(json.dumps([to, subject, body], ensure_ascii=False).encode("utf-8")).hexdigest()


def clients_from(text: str) -> list[str]:
    """The advisor's clients (TEAM_REVIEW_CLIENTS, or the story's three); only those the request names, if it names any."""
    configured = [c.strip() for c in os.environ.get("TEAM_REVIEW_CLIENTS", "").split(",") if c.strip()]
    clients = configured or list(DEFAULT_CLIENTS)
    lowered = text.lower()
    named = [c for c in clients if c.lower() in lowered or c.split()[-1].lower() in lowered]
    return named or clients


def chosen_clients(value: Any) -> list[str] | None:
    """A client list the user sent instead of the proposed one: 1-6 names of at most 80 characters, else None."""
    if not isinstance(value, list) or not 1 <= len(value) <= MAX_CLIENTS:
        return None
    names = [str(v).strip() for v in value]
    return names if all(0 < len(n) <= 80 for n in names) else None


async def make_pack(kit: Any, client: str) -> dict:
    """The brief workflow for one client, then a short follow-up draft. Degraded when Work IQ has nothing on them."""
    outcome = await run_brief(BriefRequest(client, "quarterly review pack", ""), kit.team, kit.writer, kit.reviewer,
                              kit.recorder)
    last = client.split()[-1].lower()
    references = [r for r in kit.recorder.turn.references
                  if last in json.dumps(r, ensure_ascii=False).lower()]
    degraded = not references
    response = await kit.writer.run(
        f"Write a short follow-up email to {client} proposing a time next week for their quarterly review. No advice, "
        "no figures, no placeholders such as [Advisor Name]; sign off as 'Your advisor'. Answer exactly as two lines: "
        "'Subject: ...' then 'Body: ...' (the body on one line).")
    text = (response.text or "").strip()
    subject = next((line[8:].strip() for line in text.splitlines() if line.lower().startswith("subject:")),
                   f"Quarterly review next week, {client}")
    body = next((line[5:].strip() for line in text.splitlines() if line.lower().startswith("body:")), text[:600])
    to = ((os.environ.get("DEMO_CLIENT_EMAIL") or DEFAULT_CLIENT_EMAIL) if client == "Michael Scott"
          else f"{client.lower().replace(' ', '.')}@example.com")
    return {"client": client, "cleared": outcome.cleared, "attempts": outcome.attempts, "degraded": degraded,
            "brief": outcome.brief, "references": references[:20], "lanes": outcome.lanes,
            "findings": [{"issue": str(f.get("issue", ""))[:240], "sentence": str(f.get("sentence", ""))[:160]}
                         for f in outcome.findings][:5],
            "draft_to": to, "draft_subject": subject, "draft_body": body,
            "draft_sha256": draft_hash(to, subject, body), "finished_at": now_iso(), "process": PROCESS["id"]}


def summarize(packs: list[dict], decisions: list[dict], attempts: list[dict]) -> PacksSummary:
    processes = []
    for a in attempts:
        if not processes or processes[-1]["process"] != a["process"]:
            processes.append({"process": a["process"], "steps": []})
        processes[-1]["steps"].append(a["step"])
    lines = [f"## Review packs: {len(packs)} clients", ""]
    for p in packs:
        note = " (degraded: no Microsoft 365 history found for this client as the advisor)" if p["degraded"] else ""
        state = ("cleared" + (" after one revision" if p.get("attempts", 1) > 1 else "") if p["cleared"]
                 else "NOT CLEARED: held back by the checks")
        lines.append(f"- **{p['client']}**: {state}{note}; finished {p['finished_at']} by process {p['process'][-8:]}")
        for finding in ([] if p["cleared"] else p.get("findings") or []):
            quoted = f' ("{finding["sentence"]}")' if finding.get("sentence") else ""
            lines.append(f"  - {finding['issue']}{quoted}")
    lines += ["", "### Follow-up drafts"]
    lines += [f"- {d['client']}: {d['result']}" for d in decisions]
    lines += ["", "### Durability timeline"]
    for i, proc in enumerate(processes, 1):
        lines.append(f"- Process {i} ({proc['process'][-8:]}): " + "; ".join(proc["steps"]))
    if len(processes) > 1:
        lines.append("- The packs finished before the stop were restored from the Foundry state store, not redone.")
    lines += ["", "### Packs", ""]
    for p in packs:
        lines += [f"#### {p['client']}", p["brief"], ""]
    return PacksSummary("\n".join(lines), packs, decisions, attempts)


def summary_json(summary: PacksSummary) -> str:
    return json.dumps(asdict(summary), ensure_ascii=False)


def record_step(run: dict, step: str) -> None:
    run.setdefault("attempts", []).append({"at": now_iso(), "process": PROCESS["id"], "step": step})


def valid_checkpoint(value: Any) -> bool:
    """A pack checkpoint whose pack still matches the SHA-256 written with it."""
    return isinstance(value, dict) and isinstance(value.get("pack"), dict) and digest(value["pack"]) == value.get("sha256")


def draft_card(run_id: str, index: int, pack: dict) -> dict:
    return {"call_id": f"{run_id}-draft-{index + 1}", "kind": "draft", "question": DRAFT_QUESTION,
            "client": pack["client"], "to": pack["draft_to"], "subject": pack["draft_subject"],
            "body": pack["draft_body"], "sha256": pack["draft_sha256"]}


def view_of(run: dict, packs: list[dict] | None = None, error: str | None = None) -> dict:
    """What the caller gets back from every step: the run's status, its pending card and, when done, the summary."""
    clients = run.get("clients") or []
    done = int(run.get("packs_done", 0))
    text = ""
    if run.get("status") == "cancelled":
        text = "Review packs cancelled; nothing was prepared."
    elif run.get("status") == "done" and packs is not None:
        text = summarize(packs, run.get("decisions") or [], run.get("attempts") or []).text
    return {"run": run.get("run", ""), "status": SHOWN.get(run.get("status", ""), "failed"),
            "cards": [run["pending"]] if run.get("pending") else [], "text": text,
            "progress": {"done": done, "total": len(clients),
                         "next": clients[done] if run.get("status") == "packing" and done < len(clients) else ""},
            "error": error}


def error_view(run_id: str, message: str) -> dict:
    return {"run": run_id, "status": "failed", "cards": [], "text": "", "progress": None, "error": message}


class ReviewPacks:
    """One user's review-packs runs, step by step, in the Foundry state store."""

    def __init__(self, store_factory: Callable[[], Awaitable[ItemStore]], kit: Any = None, faults: Any = None, *,
                 exit_process: Callable[[int], None] = os._exit,
                 pack_maker: Callable[[Any, str], Awaitable[dict]] | None = None) -> None:
        self.store_factory = store_factory
        self.kit = kit
        self.faults = faults if faults is not None else getattr(kit, "faults", None)
        self.exit_process = exit_process
        self.pack_maker = pack_maker or make_pack
        self.working_on = ""
        self.started = time.monotonic()

    @staticmethod
    def key(run_id: str, name: str) -> str:
        return f"packs/{run_id}/{name}"

    @staticmethod
    def tags(run_id: str, kind: str) -> dict[str, str]:
        return {"kind": kind, "run": run_id}

    def progress(self) -> str:
        elapsed = int(time.monotonic() - self.started)
        return f"Working on {self.working_on}: {elapsed} s" if self.working_on else f"Working: {elapsed} s"

    async def handle(self, command: dict) -> dict:
        op = command.get("op")
        if op == "start":
            return await self.start(str(command.get("request") or "")[:2000], command.get("crash_test") is True)
        run_id = str(command.get("run") or "")
        if not RUN_ID.match(run_id):
            return error_view(run_id, "Unknown review-packs run.")
        if op == "answer":
            return await self.answer(run_id, str(command.get("call_id") or ""), command.get("answer"))
        if op == "next":
            return await self.next(run_id)
        if op == "view":
            return await self.view(run_id)
        return error_view(run_id, "Unknown review-packs step.")

    # --- the steps ---------------------------------------------------------------------------------
    async def start(self, request: str, crash_test: bool = False) -> dict:
        store = await self.store_factory()
        run_id = uuid.uuid4().hex
        clients = clients_from(request)
        crash = crash_test or "crash test" in request.lower()
        note = ("Each pack is checkpointed in the Foundry state store when it finishes; follow-up drafts need your "
                "approval one by one.")
        if crash:
            note += (" Crash test armed: the agent process stops once before the last pack, and the run resumes from "
                     "the state store in a new process.")
        run = {"run": run_id, "request": request, "clients": clients, "crash_test": crash, "status": "confirm",
               "created_at": now_iso(), "packs_done": 0, "decisions": [], "attempts": [],
               "pending": {"call_id": f"{run_id}-clients", "kind": "clients",
                           "question": CLIENTS_QUESTION.format(count=len(clients)), "clients": clients, "note": note}}
        record_step(run, "plan")
        await store.create_item(self.key(run_id, "run"), run, tags=self.tags(run_id, "packs-run"))
        return view_of(run)

    async def answer(self, run_id: str, call_id: str, answer: Any) -> dict:
        store = await self.store_factory()
        run = await self._load(store, run_id)
        if run is None:
            return error_view(run_id, "Unknown review-packs run.")
        pending = run.get("pending")
        if not pending or pending.get("call_id") != call_id or not isinstance(answer, dict):
            return view_of(run, await self._packs_if_done(store, run),
                           error="That card is no longer pending: the run has moved on.")
        approve = answer.get("approve") is True
        if pending["kind"] == "clients":
            if not approve:
                run.update(status="cancelled", pending=None)
            else:
                clients = pending["clients"]
                if answer.get("clients"):
                    clients = chosen_clients(answer.get("clients"))
                    if clients is None:
                        return view_of(run, error="Send 1 to 6 client names of at most 80 characters.")
                run.update(clients=clients, status="packing", pending=None, packs_done=0)
            await self._save(store, run)
            return view_of(run)
        sha256 = str(answer.get("sha256") or "")
        bound = approve and sha256 == pending["sha256"]
        mode = os.environ.get("DRAFT_MODE", "preview").lower()
        result = ("previewed (DRAFT_MODE=preview: shown, not saved)" if bound and mode == "preview"
                  else "approval refused: the text changed" if approve and not bound
                  else "approved" if bound else "rejected; nothing was created")
        run.setdefault("decisions", []).append({"client": pending["client"], "approved": approve, "bound_to_text": bound,
                                                "sha256": pending["sha256"], "result": result})
        packs = await self._packs(store, run)
        decided = len(run["decisions"])
        if decided < len(packs):
            run["pending"] = draft_card(run_id, decided, packs[decided])
        else:
            run.update(pending=None, status="done")
            record_step(run, "summary")
        await self._save(store, run)
        return view_of(run, packs)

    async def next(self, run_id: str) -> dict:
        """The next client's pack. Idempotent: the finished packs come from the state store, never from memory."""
        store = await self.store_factory()
        run = await self._load(store, run_id)
        if run is None:
            return error_view(run_id, "Unknown review-packs run.")
        if run.get("status") != "packing":
            return view_of(run, await self._packs_if_done(store, run))
        clients = run["clients"]
        packs = await self._packs(store, run)
        index = len(packs)
        if index < len(clients):
            attempts = run.get("attempts") or []
            if packs and attempts and attempts[-1]["process"] != PROCESS["id"]:
                record_step(run, f"restored packs 1-{index} from the state store")
            await self._crash_test(store, run, index)
            client = clients[index]
            record_step(run, f"pack {index + 1}: {client}")
            await self._save(store, run)
            self.working_on, self.started = f"pack {index + 1} of {len(clients)} ({client})", time.monotonic()
            pack = await self.pack_maker(self.kit, client)
            key = self.key(run_id, f"pack-{index + 1}")
            checkpoint = {"pack": pack, "sha256": digest(pack), "saved_at": now_iso(), "process": PROCESS["id"]}
            try:
                await store.create_item(key, checkpoint, tags=self.tags(run_id, "packs-checkpoint"))
            except FoundryStorageConflictError:
                existing = await store.get_item(key)
                if existing is not None and valid_checkpoint(existing.value):
                    logger.info("Review packs: pack %d of run %s was already checkpointed; keeping that one.",
                                index + 1, run_id)
                else:  # the checkpoint there failed its hash check: replace it with this one
                    await store.set_item(key, checkpoint, tags=self.tags(run_id, "packs-checkpoint"))
            packs = await self._packs(store, run)
            run["packs_done"] = len(packs)
        if len(packs) >= len(clients):
            run.update(status="drafts", pending=draft_card(run_id, 0, packs[0]))
        await self._save(store, run)
        return view_of(run, packs)

    async def view(self, run_id: str) -> dict:
        store = await self.store_factory()
        run = await self._load(store, run_id)
        if run is None:
            return error_view(run_id, "Unknown review-packs run.")
        return view_of(run, await self._packs_if_done(store, run))

    # --- the crash test ------------------------------------------------------------------------------
    async def _crash_test(self, store: ItemStore, run: dict, index: int) -> None:
        """The demo's crash test: stop the process once, before the last pack, after the others are checkpointed.

        Armed by "crash test" in the request or the packs_crash presenter control. A create-only marker in the state
        store makes it fire once per run, so the next process carries on instead of stopping again; if the store can't
        be reached, the test is skipped rather than risk a stop on every attempt."""
        if index != min(2, len(run["clients"]) - 1):
            return
        by_control = False
        if self.faults is not None:
            try:
                by_control = await self.faults.active(CRASH_CONTROL)
            except Exception:
                logger.warning("Crash test: the presenter controls are unavailable.", exc_info=True)
        if not (run.get("crash_test") or by_control):
            return
        try:
            await store.create_item(self.key(run["run"], "crash"), {
                "stopped_at": now_iso(), "process": PROCESS["id"], "before": index + 1},
                tags=self.tags(run["run"], "packs-crash"))
        except FoundryStorageConflictError:
            return  # it already fired for this run
        except Exception:
            logger.warning("Crash test skipped: the state store is unavailable.", exc_info=True)
            return
        if by_control:
            try:
                await self.faults.clear(CRASH_CONTROL)
            except Exception:
                logger.warning("Crash test: couldn't clear the presenter control.", exc_info=True)
        record_step(run, f"stopped before pack {index + 1} (crash test)")
        await self._save(store, run)
        logger.warning("DEMO CRASH TEST: stopping the agent process before pack %d; packs 1-%d are in the state store.",
                       index + 1, index)
        flush_logs()
        self.exit_process(CRASH_EXIT_CODE)
        raise ProcessStopped(f"stopped before pack {index + 1}")

    # --- reads and writes --------------------------------------------------------------------------
    async def _load(self, store: ItemStore, run_id: str) -> dict | None:
        item = await store.get_item(self.key(run_id, "run"))
        return dict(item.value) if item is not None else None

    async def _save(self, store: ItemStore, run: dict) -> None:
        await store.set_item(self.key(run["run"], "run"), run, tags=self.tags(run["run"], "packs-run"))

    async def _packs(self, store: ItemStore, run: dict) -> list[dict]:
        """The finished packs, in order, from their checkpoints; a checkpoint whose hash doesn't match is not used."""
        packs = []
        for number in range(1, len(run.get("clients") or []) + 1):
            item = await store.get_item(self.key(run["run"], f"pack-{number}"))
            if item is None:
                break
            if not valid_checkpoint(item.value):
                logger.warning("Review packs: pack %d of run %s failed its hash check; it will be prepared again.",
                               number, run["run"])
                break
            packs.append(item.value["pack"])
        return packs

    async def _packs_if_done(self, store: ItemStore, run: dict) -> list[dict] | None:
        return await self._packs(store, run) if run.get("status") == "done" else None


# --- The gate: review-packs commands go to ReviewPacks, everything else to the lead agent -----------------------------
def command_in(messages: list[Any]) -> dict | None:
    """The review-packs command in the run's last user message, if it is one."""
    for message in reversed(messages or []):
        role = getattr(message, "role", None)
        if str(getattr(role, "value", role)) != "user":
            continue
        text = (getattr(message, "text", "") or "").strip()
        if not text.startswith(COMMAND):
            return None
        try:
            payload = json.loads(text[len(COMMAND):])
        except json.JSONDecodeError:
            return {"op": "invalid"}
        return payload if isinstance(payload, dict) else {"op": "invalid"}
    return None


def stream_with_progress(step: Awaitable[dict], progress: Callable[[], str]) -> ResponseStream:
    """The step as a stream: a progress line every PROGRESS_SECONDS while it runs, then the view on its own line."""
    message_id = f"msg_packs_{uuid.uuid4().hex[:12]}"
    texts: list[str] = []

    async def updates() -> AsyncIterator[AgentResponseUpdate]:
        task = asyncio.ensure_future(step)
        try:
            while True:
                done, _ = await asyncio.wait({task}, timeout=PROGRESS_SECONDS)
                if done:
                    break
                line = f"{progress()}\n"
                texts.append(line)
                yield AgentResponseUpdate(role="assistant", contents=[Content.from_text(line)], message_id=message_id)
            final = view_text(task.result())
            texts.append(final)
            yield AgentResponseUpdate(role="assistant", contents=[Content.from_text(final)], message_id=message_id)
        finally:
            if not task.done():
                task.cancel()

    return ResponseStream(updates(), finalizer=lambda _: AgentResponse(
        messages=[Message(role="assistant", contents=["".join(texts)])]))


class ReviewPacksGate(AgentMiddleware):
    """Answers review-packs commands in code, one step per request, without running the lead agent. Any other message
    goes on to the lead. A failed step comes back as a view with an error, so the caller can send it again."""

    def __init__(self, packs: ReviewPacks) -> None:
        self.packs = packs

    async def process(self, context: AgentContext, call_next: Callable[[], Awaitable[None]]) -> None:
        command = command_in(list(context.messages or []))
        if command is None:
            await call_next()
            return
        step = self._step(command)
        if getattr(context, "stream", False):
            context.result = stream_with_progress(step, self.packs.progress)
        else:
            context.result = AgentResponse(messages=[Message(role="assistant", contents=[view_text(await step)])])

    async def _step(self, command: dict) -> dict:
        try:
            return await self.packs.handle(command)
        except Exception:
            logger.exception("Review packs: the %s step failed.", command.get("op"))
            return error_view(str(command.get("run") or ""), "The review-packs step failed; send it again.")
