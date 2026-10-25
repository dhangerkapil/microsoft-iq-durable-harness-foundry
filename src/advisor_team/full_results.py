"""Complete the lead's answer in code: the advisor sees only the lead's answer, never a tool's result.

Two things are added at the end of the answer, in the stream and in the final response, rather than retyped by a model:

- A brief, what-if or committee result the answer left out. The lead answers a brief with one line naming the agents, and
  is told to show run_what_if's and convene_investment_committee's results in full; a result runs to several thousand
  characters, and a model retyping one changed a link in it, or answered with one line about it instead. It is added
  exactly as the tool returned it.
- The ```workiq_references block: the Microsoft 365 items the specialists cited this turn, exactly as recorded. The app
  turns it into source links. A model retyping it once fell into a repetition loop on an Outlook item ID and wrote
  15,000 characters of it; the lead is told never to write the block.

An answer that already holds them passes through unchanged.
"""

from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

from agent_framework import (
    AgentContext,
    AgentMiddleware,
    AgentResponse,
    AgentResponseUpdate,
    Content,
    Message,
    ResponseStream,
)

from .activity import ActivityRecorder
from .execution import ToolTimingRecorder

SHOWN_IN_FULL = frozenset({"prepare_client_brief", "run_what_if", "convene_investment_committee"})
REFERENCES_FENCE = "```workiq_references"
_MARKUP = re.compile(r"[#*_`>|]+")


def _flat(text: str) -> str:
    return " ".join(_MARKUP.sub(" ", text).split()).lower()


def result_text(result: Any) -> str:
    """A tool result as text: a string, a list of contents or strings, or a content with text."""
    if isinstance(result, str):
        return result
    if isinstance(result, (list, tuple)):
        return "\n".join(part for part in (result_text(item) for item in result) if part)
    text = getattr(result, "text", None)
    return text if isinstance(text, str) else ""


def missing_from(answer: str, result: str) -> bool:
    """True when the answer leaves the result out: its first or its last line isn't in the answer (markup aside)."""
    lines = [line for line in (result or "").strip().splitlines() if _flat(line)]
    if not lines:
        return False
    shown = _flat(answer)
    return any(_flat(line) not in shown for line in (lines[0], lines[-1]))


class _Turn:
    """What a turn produced: the results that must be shown, the lead's own text and its message ID."""

    def __init__(self, timing: ToolTimingRecorder) -> None:
        self.timing = timing
        self.results: list[str] = []
        self.answer: list[str] = []
        self.message_id: str | None = None

    def observe(self, contents: Sequence[Content], role: Any, message_id: str | None = None) -> None:
        for content in contents:
            if content.type == "function_result":
                timing = self.timing.timings.get(content.call_id or "")
                text = result_text(content.result) if timing is not None and timing.name in SHOWN_IN_FULL else ""
                if text.strip():
                    self.results.append(text.strip())
            elif content.type == "text" and str(role or "assistant") == "assistant" and content.text:
                self.answer.append(content.text)
                self.message_id = message_id or self.message_id

    def missing(self) -> list[str]:
        answer = "".join(self.answer)
        return [result for result in self.results if missing_from(answer, result)]


class ShowResultInFull(AgentMiddleware):
    """Adds what the lead's answer must end with, in code: a brief, what-if or committee result it left out, verbatim,
    and the turn's workiq_references block from the activity record.

    Register it inside SourceProgressOutput (after it in the lead's middleware), so that wrapper's producer task owns
    this stream's pulls and cleanup.
    """

    def __init__(self, timing: ToolTimingRecorder, recorder: ActivityRecorder | None = None) -> None:
        self.timing = timing
        self.recorder = recorder

    async def process(self, context: AgentContext, call_next: Callable[[], Awaitable[None]]) -> None:
        await call_next()
        if context.stream and isinstance(context.result, ResponseStream):
            context.result = self.wrap(context.result)
        elif isinstance(context.result, AgentResponse):
            self.complete(context.result)

    def addition(self, turn: _Turn) -> str:
        """What the answer still needs: missing results, then the references block unless the answer has one."""
        missing = turn.missing()
        text = _appended(missing) if missing else ""
        references = self.recorder.turn.references if self.recorder is not None else []
        if references and REFERENCES_FENCE not in "".join(turn.answer) + text:
            text += f"\n\n{REFERENCES_FENCE}\n{json.dumps(references, ensure_ascii=False)}\n```"
        return text

    def complete(self, response: AgentResponse) -> AgentResponse:
        turn = _Turn(self.timing)
        for message in response.messages:
            turn.observe(message.contents, message.role, message.message_id)
        text = self.addition(turn)
        if text:
            response.messages.append(Message(role="assistant", contents=[Content.from_text(text)]))
        return response

    def wrap(self, original: ResponseStream) -> ResponseStream:
        turn = _Turn(self.timing)
        added: list[str] = []
        final: AgentResponse | None = None

        async def updates():
            nonlocal final
            async with original:
                async for update in original:
                    turn.observe(update.contents, update.role, update.message_id)
                    yield update
                final = await original.get_final_response()
            text = self.addition(turn)
            if text:
                added.append(text)
                yield AgentResponseUpdate(role="assistant", contents=[Content.from_text(text)],
                                          message_id=turn.message_id)

        async def finalize(_: Sequence[AgentResponseUpdate]) -> AgentResponse:
            if final is None:
                raise RuntimeError("The answer stream ended without an agent response.")
            if added:
                final.messages.append(Message(role="assistant", contents=[Content.from_text(added[0])]))
            return final

        return ResponseStream(updates(), finalizer=finalize)


def _appended(results: list[str]) -> str:
    return "\n\n" + "\n\n".join(results)
