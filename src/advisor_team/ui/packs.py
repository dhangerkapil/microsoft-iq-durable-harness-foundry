"""The app's side of review packs (W3): one step per request to the hosted agent, advisor-team.

The hosted agent runs review packs as durable steps with checkpoints in the Foundry state store
(advisor_team.workflows.review_packs). Each run has its own conversation; the app sends every step there as a
review-packs command, streams the response and reads the step's result, the run's view, from the answer's last line.
The agent checks each answer against the card it answers (the pending card, by ID; a draft by its SHA-256), so the app
never needs to trust the browser's copy of a card. "next" runs one client's pack. If the agent process stops during it
(the crash test does so once, before the last pack), the step comes back "interrupted" with a new conversation, and the
app sends it again there: Foundry keeps the interrupted conversation's turn in flight, and the new session's process
restores the finished packs from the user's state store.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Annotated, Literal

import httpx
from fastapi import HTTPException
from pydantic import BaseModel, Field, StringConstraints

from ..packs_protocol import command_text, view_in
from .config import UIConfig
from .foundry import FoundryGateway

logger = logging.getLogger(__name__)

IDENTIFIER = re.compile(r"^[A-Za-z0-9_-]{1,200}$")
RUN = r"^[0-9a-f]{32}$"
INTERRUPTED = ("The agent process stopped before this step finished. Sending it again in a new session: a new process "
               "resumes the run from the Foundry state store.")


class PacksStart(BaseModel):
    prompt: str = Field(min_length=1, max_length=2000)
    crash_test: bool = False


class ClientsAnswer(BaseModel):
    kind: Literal["clients"]
    approve: bool
    clients: list[Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]] | None = (
        Field(default=None, max_length=6))


class DraftAnswer(BaseModel):
    kind: Literal["draft"]
    approve: bool
    sha256: str = Field(default="", pattern=r"^([0-9a-f]{64})?$")


class PacksReply(BaseModel):
    run: str = Field(pattern=RUN)
    call_id: str = Field(pattern=IDENTIFIER.pattern)
    answer: Annotated[ClientsAnswer | DraftAnswer, Field(discriminator="kind")]


class PacksNext(BaseModel):
    run: str = Field(pattern=RUN)


def identifier(value: str, what: str) -> str:
    if not IDENTIFIER.match(value):
        raise HTTPException(status_code=422, detail=f"Invalid {what}.")
    return value


def card_from(card: object) -> dict | None:
    if not isinstance(card, dict) or not isinstance(card.get("call_id"), str) or not IDENTIFIER.match(card["call_id"]):
        return None
    if card.get("kind") == "draft":
        return {"call_id": card["call_id"], "kind": "draft", "question": str(card.get("question", "")),
                "client": str(card.get("client", "")), "to": str(card.get("to", "")),
                "subject": str(card.get("subject", "")), "body": str(card.get("body", "")),
                "sha256": str(card.get("sha256", ""))}
    if card.get("kind") == "clients":
        return {"call_id": card["call_id"], "kind": "clients", "question": str(card.get("question", "")),
                "clients": [str(c) for c in card.get("clients") or []][:6], "note": str(card.get("note", ""))}
    return None


def packs_view(view: dict) -> dict:
    """What the app shows: the run's status, its pending card, its summary and progress; nothing else from the agent."""
    progress = view.get("progress") if isinstance(view.get("progress"), dict) else None
    error = view.get("error")
    return {
        "run": str(view.get("run") or ""), "status": str(view.get("status") or "failed"),
        "cards": [c for c in (card_from(card) for card in view.get("cards") or []) if c],
        "text": str(view.get("text") or ""),
        "progress": {"done": int(progress.get("done") or 0), "total": int(progress.get("total") or 0),
                     "next": str(progress.get("next") or "")} if progress else None,
        "error": str(error) if error else None,
    }


def interrupted(run: str) -> dict:
    return {"run": run, "status": "interrupted", "cards": [], "text": "", "progress": None, "error": INTERRUPTED}


class PacksGateway:
    """Sends review-packs steps to the hosted agent the app already talks to."""

    def __init__(self, config: UIConfig, client: httpx.AsyncClient) -> None:
        self.foundry = FoundryGateway(config, client)

    async def start(self, token: str, body: PacksStart) -> dict:
        conversation = await self.foundry.create_conversation(token)
        if not IDENTIFIER.match(conversation):
            raise HTTPException(status_code=502, detail="Foundry returned an invalid conversation ID.")
        view = await self._step(token, conversation, {"op": "start", "request": body.prompt.strip(),
                                                      "crash_test": body.crash_test})
        return {"conversation_id": conversation, **view}

    async def reply(self, token: str, conversation_id: str, body: PacksReply) -> dict:
        return await self._step(token, conversation_id, {
            "op": "answer", "run": body.run, "call_id": body.call_id,
            "answer": body.answer.model_dump(exclude={"kind"}, exclude_none=True)})

    async def next(self, token: str, conversation_id: str, body: PacksNext) -> dict:
        """One client's pack. A stopped process (the crash test) or a lost connection comes back as "interrupted",
        with a new conversation for the retry: Foundry keeps an interrupted conversation's turn in flight, so the step
        goes again in a new session, a new process, which finds the run in the user's state store."""
        try:
            return await self._step(token, conversation_id, {"op": "next", "run": body.run})
        except (HTTPException, httpx.HTTPError, json.JSONDecodeError) as error:
            if isinstance(error, HTTPException) and error.status_code in (403, 404, 422):
                raise
            view = interrupted(body.run)
            try:
                view["conversation_id"] = await self.foundry.create_conversation(token)
            except Exception:  # the retry tries again
                logger.warning("Review packs: couldn't open a new conversation for the retry.", exc_info=True)
            return view

    async def _step(self, token: str, conversation_id: str, command: dict) -> dict:
        text, failed = [], None
        async for event in self.foundry.stream(token, conversation_id, command_text(command)):
            kind = event.get("type")
            if kind == "response.output_text.delta" and isinstance(event.get("delta"), str):
                text.append(event["delta"])
            elif kind in ("response.failed", "error"):
                failed = event
        view = view_in("".join(text))
        if view is None:
            raise HTTPException(status_code=502, detail=(
                "The hosted agent ended the review-packs step without a result." if failed is None
                else "The hosted agent couldn't run the review-packs step."))
        return packs_view(view)
