"""The app's review-packs path: steps sent to the hosted agent as commands, its view read back, structured answers,
an interrupted step reported for the browser to send again, signed-in access only."""

import json
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError

from advisor_team.packs_protocol import COMMAND, view_text
from advisor_team.ui.auth import Authenticator
from advisor_team.ui.config import UIConfig
from advisor_team.ui.packs import INTERRUPTED, PacksGateway, PacksNext, PacksReply, PacksStart, packs_view
from advisor_team.ui.server import Services, create_app

TENANT = "11111111-1111-4111-8111-111111111111"
API = "22222222-2222-4222-8222-222222222222"
BROWSER = "33333333-3333-4333-8333-333333333333"
ALICE = "44444444-4444-4444-8444-444444444444"
SHA = "a" * 64
RUN = "0123456789abcdef0123456789abcdef"


def make_config(tmp_path: Path) -> UIConfig:
    return UIConfig(
        tenant_id=TENANT, api_client_id=API, browser_client_id=BROWSER,
        identity_client_id="66666666-6666-4666-8666-666666666666", origin="https://ui.example.com",
        project_endpoint="https://example.services.ai.azure.com/api/projects/team", agent_name="advisor-team",
        cosmos_endpoint="https://example.documents.azure.com", cosmos_database="team", cosmos_container="ui",
        static_directory=tmp_path,
    )


def clients_card(call_id: str = f"{RUN}-clients") -> dict:
    return {"call_id": call_id, "kind": "clients", "question": "Prepare these?", "clients": ["Michael Scott"], "note": "n"}


def sse(*deltas: str, completed: bool = True) -> bytes:
    events = [{"type": "response.output_text.delta", "delta": d} for d in deltas]
    if completed:
        events.append({"type": "response.completed", "response": {"id": "resp_1", "status": "completed"}})
    return "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events).encode()


def test_the_view_keeps_cards_progress_and_text_but_nothing_else():
    view = packs_view({
        "run": RUN, "status": "waiting", "text": "", "progress": {"done": 1, "total": 3, "next": "Jan Levinson"},
        "cards": [clients_card(), {"call_id": "d", "kind": "draft", "question": "Approve?", "client": "Jan",
                                   "to": "jan@example.com", "subject": "S", "body": "B", "sha256": SHA},
                  {"call_id": "bad id!", "kind": "clients"}, {"call_id": "x", "kind": "send_email"}],
        "secret": "dropped"})
    assert [c["kind"] for c in view["cards"]] == ["clients", "draft"]
    assert view["progress"] == {"done": 1, "total": 3, "next": "Jan Levinson"} and "secret" not in view
    assert packs_view({"status": "failed", "error": "boom"})["error"] == "boom"


async def test_each_step_is_a_command_to_the_hosted_agent_in_the_runs_conversation(tmp_path: Path):
    requests: list[httpx.Request] = []
    views = [
        {"run": RUN, "status": "waiting", "cards": [clients_card()], "progress": {"done": 0, "total": 1, "next": ""}},
        {"run": RUN, "status": "working", "cards": [], "progress": {"done": 0, "total": 1, "next": "Michael Scott"}},
        {"run": RUN, "status": "waiting", "cards": [], "progress": {"done": 1, "total": 1, "next": ""}},
    ]

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert "/agents/advisor-team/endpoint/protocols/openai/" in request.url.path
        assert request.url.params["api-version"] == "v1" and request.headers["Authorization"] == "Bearer delegated"
        if request.url.path.endswith("/conversations"):
            return httpx.Response(200, json={"id": "conv_1"})
        view = views.pop(0)
        return httpx.Response(200, content=sse("Working on pack 1 of 1 (Michael Scott): 15 s\n", view_text(view)),
                              headers={"content-type": "text/event-stream"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        gateway = PacksGateway(make_config(tmp_path), client)
        started = await gateway.start("delegated", PacksStart(prompt="Prepare review packs", crash_test=True))
        reply = PacksReply.model_validate({"run": RUN, "call_id": f"{RUN}-clients",
                                           "answer": {"kind": "clients", "approve": True, "clients": [" Jan Levinson "]}})
        answered = await gateway.reply("delegated", started["conversation_id"], reply)
        packed = await gateway.next("delegated", "conv_1", PacksNext(run=RUN))
    assert started["conversation_id"] == "conv_1" and started["cards"][0]["kind"] == "clients"
    assert answered["status"] == "working" and packed["progress"]["done"] == 1
    bodies = [json.loads(r.content) for r in requests[1:]]
    assert all(b["conversation"] == "conv_1" and b["stream"] is True and b["store"] is True for b in bodies)
    commands = [json.loads(b["input"][len(COMMAND):]) for b in bodies]
    assert commands[0] == {"op": "start", "request": "Prepare review packs", "crash_test": True}
    assert commands[1] == {"op": "answer", "run": RUN, "call_id": f"{RUN}-clients",
                           "answer": {"approve": True, "clients": ["Jan Levinson"]}}
    assert commands[2] == {"op": "next", "run": RUN}


@pytest.mark.parametrize("respond", [
    lambda request: httpx.Response(200, content=sse("Working on pack 3 of 3: 1 s\n", completed=False),
                                   headers={"content-type": "text/event-stream"}),
    lambda request: httpx.Response(200, content=b'data: {"type": "response.failed", "response": {"error": '
                                   b'{"message": "A service-backed conversation already has an in-flight turn."}}}\n\n',
                                   headers={"content-type": "text/event-stream"}),
    lambda request: httpx.Response(502, json={"error": "the session restarted"}),
    lambda request: (_ for _ in ()).throw(httpx.RemoteProtocolError("peer closed the connection")),
])
async def test_a_pack_step_that_stops_comes_back_interrupted_with_a_new_conversation(tmp_path: Path, respond):
    def route(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/conversations"):
            return httpx.Response(200, json={"id": "conv_2"})
        return respond(request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(route)) as client:
        view = await PacksGateway(make_config(tmp_path), client).next("delegated", "conv_1", PacksNext(run=RUN))
    assert view == {"run": RUN, "status": "interrupted", "cards": [], "text": "", "progress": None,
                    "error": INTERRUPTED, "conversation_id": "conv_2"}


async def test_an_interrupted_step_without_a_new_conversation_is_still_interrupted(tmp_path: Path):
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(503, json={}))) as client:
        view = await PacksGateway(make_config(tmp_path), client).next("delegated", "conv_1", PacksNext(run=RUN))
    assert view["status"] == "interrupted" and "conversation_id" not in view


async def test_a_missing_conversation_is_an_error_not_a_retry(tmp_path: Path):
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(404, json={}))) as client:
        with pytest.raises(HTTPException) as error:
            await PacksGateway(make_config(tmp_path), client).next("delegated", "conv_1", PacksNext(run=RUN))
    assert error.value.status_code == 404


@pytest.mark.parametrize("body", [
    {"run": RUN, "call_id": "c", "answer": {"kind": "draft", "approve": True, "sha256": "not-a-hash"}},
    {"run": RUN, "call_id": "c", "answer": {"kind": "clients", "approve": True, "clients": [f"C {i}" for i in range(7)]}},
    {"run": RUN, "call_id": "c", "answer": {"kind": "clients", "approve": True, "clients": [""]}},
    {"run": RUN, "call_id": "c", "answer": {"kind": "send_email", "approve": True}},
    {"run": RUN, "call_id": "c", "answer": {"approve": True}},
    {"run": "not-a-run", "call_id": "c", "answer": {"kind": "clients", "approve": True}},
    {"call_id": "c", "answer": {"kind": "clients", "approve": True}},
])
def test_answers_outside_the_card_shapes_are_refused(body: dict):
    with pytest.raises(ValidationError):
        PacksReply.model_validate(body)


def test_a_draft_answer_carries_the_exact_text_hash():
    reply = PacksReply.model_validate({"run": RUN, "call_id": "d", "answer": {"kind": "draft", "approve": True,
                                                                              "sha256": SHA}})
    assert reply.answer.model_dump(exclude={"kind"}) == {"approve": True, "sha256": SHA}


@pytest.fixture(scope="module")
def signing_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def bearer(config: UIConfig, key) -> dict:
    claims = {"exp": int(time.time()) + 300, "iat": int(time.time()), "aud": config.api_client_id,
              "iss": f"https://login.microsoftonline.com/{config.tenant_id}/v2.0", "tid": config.tenant_id,
              "oid": ALICE, "azp": config.browser_client_id, "scp": "access_as_user"}
    return {"Authorization": "Bearer " + jwt.encode(claims, key, algorithm="RS256", headers={"kid": "t"})}


def services(config: UIConfig, key, packs) -> Services:
    auth = Authenticator(config)
    auth.keys = MagicMock(get_signing_key_from_jwt=MagicMock(return_value=SimpleNamespace(key=key.public_key())))
    tokens = MagicMock(delegated=AsyncMock(return_value="delegated"))
    return Services(auth, tokens, MagicMock(), MagicMock(), MagicMock(), packs)


def test_endpoints_need_a_signed_in_user_and_answer_with_the_agents_view(tmp_path: Path, signing_key):
    config = make_config(tmp_path)
    with TestClient(create_app(config, services(config, signing_key, None))) as api:
        assert api.post("/api/packs", json={"prompt": "packs"}).status_code == 401
        assert api.post("/api/packs", json={"prompt": "packs"}, headers=bearer(config, signing_key)).status_code == 404
    packs = MagicMock(start=AsyncMock(return_value={"conversation_id": "conv_1", "status": "waiting", "cards": []}),
                      reply=AsyncMock(return_value={"status": "working"}),
                      next=AsyncMock(return_value={"run": RUN, "status": "waiting", "cards": []}))
    with TestClient(create_app(config, services(config, signing_key, packs))) as api:
        headers = bearer(config, signing_key)
        assert api.post("/api/packs", json={"prompt": "packs"}, headers=headers).status_code == 202
        packs.start.assert_awaited_once()
        assert packs.start.await_args.args[0] == "delegated"
        body = {"run": RUN, "call_id": f"{RUN}-draft-1", "answer": {"kind": "draft", "approve": False}}
        assert api.post("/api/packs/conv_1/reply", json=body, headers=headers).status_code == 202
        assert api.post("/api/packs/conv_1/reply", json=body,
                        headers={**headers, "Origin": "https://evil.example.com"}).status_code == 403
        step = api.post("/api/packs/conv_1/next", json={"run": RUN}, headers=headers)
        assert step.status_code == 200 and step.json() == {"run": RUN, "status": "waiting", "cards": []}
        assert api.post("/api/packs/bad%20id/next", json={"run": RUN}, headers=headers).status_code == 422
        assert api.post("/api/packs/conv_1/next", json={"run": "nope"}, headers=headers).status_code == 422
    assert "packsAgentName" not in config.public()


def test_a_long_pack_step_keeps_the_connection_alive_then_sends_its_json(tmp_path: Path, signing_key, monkeypatch):
    import asyncio

    import advisor_team.ui.server as server

    config = make_config(tmp_path)

    async def slow_next(token, conversation, body):
        await asyncio.sleep(0.05)
        return {"run": body.run, "status": "working", "cards": []}

    monkeypatch.setattr(server, "PACKS_KEEPALIVE_SECONDS", 0.01)
    packs = MagicMock(next=slow_next)
    with TestClient(create_app(config, services(config, signing_key, packs))) as api:
        step = api.post("/api/packs/conv_1/next", json={"run": RUN}, headers=bearer(config, signing_key))
    assert step.text.startswith(" ") and step.json() == {"run": RUN, "status": "working", "cards": []}