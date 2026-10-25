"""The team's activity record: every call each agent made in a turn, grouped into lanes.

Specialists run inside a tool call of the lead (a direct ask, a background task or a workflow), so their calls never
appear as the lead's own output items. ``LaneRecorder`` (a function middleware on every specialist) records each call
with its lane, timing and outcome; ``TeamActivityOutput`` (an agent middleware on the lead) attaches the records that
haven't been shown yet to the lead's next streamed tool result, in an envelope only the app reads (the model and the
history never see it). The app's X-ray and the rehearsal runner both read the same records.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from copy import copy
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

from agent_framework import AgentContext, AgentMiddleware, AgentResponseUpdate, FunctionInvocationContext, FunctionMiddleware

from .execution import tool_output_failed

logger = logging.getLogger(__name__)
TEAM_KEY = "__team_activity__"
MAX_TEXT = 4_000

# Tools of the lead whose results carry the team's records to the app.
DELEGATION_TOOLS = frozenset({
    "ask_client_context", "ask_portfolio", "ask_firm_policy", "ask_market",
    "prepare_client_brief", "review_packs_status", "run_what_if", "convene_investment_committee",
    "background_agents_wait_for_first_completion", "background_agents_get_task_results",
    "background_agents_get_all_tasks",
})


@dataclass
class CallRecord:
    lane: str
    tool: str
    call_id: str
    started_at: str
    duration_ms: int
    failed: bool
    arguments: str = ""
    output: str = ""
    parent: str = ""


@dataclass
class StepRecord:
    """A workflow step (executor) or a lane's start and end, for the graph and the lanes' time spans."""

    lane: str
    step: str
    status: str
    at: str
    detail: str = ""


@dataclass
class TurnActivity:
    calls: list[CallRecord] = field(default_factory=list)
    steps: list[StepRecord] = field(default_factory=list)
    shown_calls: int = 0
    shown_steps: int = 0
    verdict: dict[str, Any] | None = None
    references: list[dict[str, Any]] = field(default_factory=list)
    # The Microsoft 365 items the specialists' tools returned this turn, read from their full output (a call record
    # keeps only MAX_TEXT characters of it): what a cited item's IDs are checked against (ui/grounding.Evidence).
    evidence: Any = None

    def observe(self, output: Any) -> None:
        # Imported here: the app's grounding module reads this module.
        from .ui.grounding import Evidence

        if self.evidence is None:
            self.evidence = Evidence()
        self.evidence.record(output)


_turn: ContextVar[TurnActivity | None] = ContextVar("team_turn_activity", default=None)
_lane: ContextVar[str] = ContextVar("team_lane", default="lead")


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def _clip(value: Any) -> str:
    if value is None:
        return ""
    text = value if isinstance(value, str) else json.dumps(value, default=str, ensure_ascii=False)
    return text if len(text) <= MAX_TEXT else text[:MAX_TEXT] + " …"


class ActivityRecorder:
    """Owns the turn's record. One recorder serves one hosted request (the agent factory builds one per request)."""

    def __init__(self) -> None:
        self.turn = TurnActivity()
        _turn.set(self.turn)

    @staticmethod
    def current() -> TurnActivity | None:
        return _turn.get()

    @contextmanager
    def lane(self, name: str) -> Iterator[None]:
        token = _lane.set(name)
        self.step(name, "lane", "started")
        try:
            yield
        finally:
            self.step(name, "lane", "finished")
            _lane.reset(token)

    def step(self, lane: str, step: str, status: str, detail: str = "") -> None:
        self.turn.steps.append(StepRecord(lane=lane, step=step, status=status, at=now_iso(), detail=_clip(detail)))

    def set_verdict(self, verdict: dict[str, Any]) -> None:
        self.turn.verdict = verdict

    def add_references(self, references: list[dict[str, Any]], lane: str = "lead") -> None:
        seen = {(r.get("entity_url") or r.get("url")) for r in self.turn.references}
        for ref in references:
            ref = self.grounded(ref, lane)
            key = ref.get("entity_url") or ref.get("url")
            if key and key not in seen:
                self.turn.references.append(ref)
                seen.add(key)

    def grounded(self, ref: dict[str, Any], lane: str = "lead") -> dict[str, Any]:
        """The reference with an item ID the specialist mis-copied replaced by the one its tools returned: the app's
        repair (ui/grounding.py), here with the specialists' full output. An item the tools returned is kept as cited."""
        evidence = self.turn.evidence
        if evidence is None:
            return ref
        from pydantic import ValidationError

        from .ui.models import Reference

        url = ref.get("url")
        try:
            reference = Reference.model_validate({
                # The link isn't repaired, and a missing one mustn't stop the IDs from being checked.
                "title": ref.get("title") or "Item",
                "url": url if isinstance(url, str) and url.startswith("https://") else "https://outlook.office.com/",
                "kind": ref.get("kind") or "file",
                **{name: ref.get(name) for name in ("entity_url", "drive_id", "item_id") if ref.get(name)},
            })
        except ValidationError:
            return ref
        if evidence.holds(reference):
            return ref
        repaired = evidence.repair(reference)
        changed = {name: getattr(repaired, name) for name in ("entity_url", "drive_id", "item_id")
                   if getattr(repaired, name) != getattr(reference, name)}
        if not changed:
            return ref
        before = str(reference.entity_url or reference.item_id or "").rsplit("/", 1)[-1]
        after = str(repaired.entity_url or repaired.item_id or "").rsplit("/", 1)[-1]
        at = next((n for n, (old, new) in enumerate(zip(before, after)) if old != new), min(len(before), len(after)))
        logger.info("Repaired the IDs of a cited item (%s) from the specialists' tool output.", ", ".join(changed))
        self.step(lane, "references", "repaired", f"{str(ref.get('title') or '')[:80]}: from character {at + 1}, "
                  f"...{before[max(0, at - 8):at + 8]} -> ...{after[max(0, at - 8):at + 8]}")
        return {**ref, **changed}

    def take_unshown(self) -> dict[str, Any] | None:
        calls = self.turn.calls[self.turn.shown_calls:]
        steps = self.turn.steps[self.turn.shown_steps:]
        self.turn.shown_calls = len(self.turn.calls)
        self.turn.shown_steps = len(self.turn.steps)
        if not calls and not steps and self.turn.verdict is None:
            return None
        envelope: dict[str, Any] = {
            "version": 1,
            "calls": [asdict(c) for c in calls],
            "steps": [asdict(s) for s in steps],
        }
        if self.turn.verdict is not None:
            envelope["verdict"] = self.turn.verdict
        if self.turn.references:
            envelope["references"] = self.turn.references
        return envelope

    def lanes_summary(self) -> dict[str, dict[str, Any]]:
        """Per lane: calls, first start and last end (the rehearsal runner checks overlap with these)."""
        summary: dict[str, dict[str, Any]] = {}
        for call in self.turn.calls:
            lane = summary.setdefault(call.lane, {"calls": [], "start": call.started_at, "end": call.started_at})
            lane["calls"].append(call.tool)
            lane["start"] = min(lane["start"], call.started_at)
            end = datetime.fromisoformat(call.started_at).timestamp() + call.duration_ms / 1000
            lane["end"] = max(lane["end"], datetime.fromtimestamp(end, UTC).isoformat(timespec="milliseconds"))
        return summary


class LaneRecorder(FunctionMiddleware):
    """Records every tool call a specialist makes, in its lane, under the lead's tool call that started it."""

    def __init__(self, lane: str, parent_call: ContextVar[str | None] | None = None) -> None:
        self.lane_name = lane
        self.parent_call = parent_call

    async def process(self, context: FunctionInvocationContext, call_next: Callable[[], Awaitable[None]]) -> None:
        started_at = now_iso()
        started = time.perf_counter_ns()
        failed = True
        error: BaseException | None = None
        try:
            await call_next()
            failed = tool_output_failed(context.result, skill=context.function.name == "load_skill")
        except BaseException as exc:
            error = exc
            raise
        finally:
            turn = _turn.get()
            if turn is not None:
                # A call that raised has no result: record why (its result is still the unawaited handler).
                result = f"Error: {type(error).__name__}: {error}" if error is not None else _result_text(context.result)
                turn.calls.append(CallRecord(
                    lane=self.lane_name,
                    tool=context.function.name,
                    call_id=str(context.metadata.get("call_id") or ""),
                    started_at=started_at,
                    duration_ms=(time.perf_counter_ns() - started) // 1_000_000,
                    failed=failed,
                    arguments=_clip(getattr(context, "arguments", None) and _arguments(context)),
                    output=_clip(result),
                    parent=(self.parent_call.get() if self.parent_call else None) or "",
                ))
                # A failed call can echo the ID it was asked for, and a skill's text is no item evidence.
                if not failed and context.function.name != "load_skill" and not _error_text(result):
                    try:
                        turn.observe(result)
                    except Exception:
                        logger.warning("Could not read item IDs from %s.", context.function.name, exc_info=True)


def _arguments(context: FunctionInvocationContext) -> Any:
    args = context.arguments
    if hasattr(args, "model_dump"):
        return args.model_dump(mode="json")
    if isinstance(args, dict):
        return args
    return str(args)


def _result_text(result: Any) -> Any:
    if result is None:
        return ""
    if hasattr(result, "text") and isinstance(getattr(result, "text"), str):
        return result.text
    if isinstance(result, list):
        return [_result_text(item) for item in result]
    return result


def _error_text(value: Any) -> bool:
    """An error message rather than a tool's result (the framework reports a raised exception as "Error: ...")."""
    if isinstance(value, str):
        return value.lstrip()[:5].lower() == "error"
    return isinstance(value, list) and any(_error_text(item) for item in value)


class TeamActivityOutput(AgentMiddleware):
    """Attaches the turn's unshown team records to the lead's next delegation result in the outgoing stream only.

    Register it after ToolTimingOutput in the lead's middleware, so the timing envelope stays outermost.
    """

    def __init__(self, recorder: ActivityRecorder, timing: Any = None) -> None:
        self.recorder = recorder
        self.timing = timing

    async def process(self, context: AgentContext, call_next: Callable[[], Awaitable[None]]) -> None:
        if context.stream:
            context.stream_update_transforms.append(self.annotate)
        await call_next()

    def annotate(self, update: AgentResponseUpdate) -> AgentResponseUpdate:
        contents = []
        changed = False
        for content in update.contents:
            if content.type == "function_result" and self._is_delegation(content):
                envelope = self.recorder.take_unshown()
                if envelope is not None:
                    content = copy(content)
                    content.result = {TEAM_KEY: envelope, "output": content.result}
                    changed = True
            contents.append(content)
        if not changed:
            return update
        outgoing = copy(update)
        outgoing.contents = contents
        return outgoing

    def _is_delegation(self, content: Any) -> bool:
        timing = self.timing.timings.get(content.call_id or "") if self.timing is not None else None
        name = timing.name if timing is not None else getattr(content, "name", None)
        return name in DELEGATION_TOOLS


def unwrap_team_output(output: Any) -> tuple[Any, dict[str, Any] | None]:
    """The app's side: split a tool output into the original output and the team records, if any."""
    value = output
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return output, None
    if isinstance(value, dict) and TEAM_KEY in value and set(value) == {TEAM_KEY, "output"}:
        return value["output"], value[TEAM_KEY]
    return output, None
