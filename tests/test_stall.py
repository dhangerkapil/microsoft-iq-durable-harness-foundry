"""The stalled-model-call bound: one resend after the bound, streaming untouched."""

from __future__ import annotations

import asyncio

import pytest

from advisor_team.stall import ModelStallGuard


class Context:
    def __init__(self, stream: bool = False) -> None:
        self.stream = stream


@pytest.mark.asyncio
async def test_a_stalled_call_is_sent_once_more():
    calls = []

    async def call_next():
        calls.append(1)
        if len(calls) == 1:
            await asyncio.sleep(5)

    await ModelStallGuard("writer", seconds=0.05).process(Context(), call_next)
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_a_second_stall_fails_instead_of_waiting_forever():
    async def call_next():
        await asyncio.sleep(5)

    with pytest.raises(TimeoutError):
        await ModelStallGuard("writer", seconds=0.05).process(Context(), call_next)


@pytest.mark.asyncio
async def test_streaming_calls_pass_through_without_a_bound():
    calls = []

    async def call_next():
        calls.append(1)
        await asyncio.sleep(0.1)

    await ModelStallGuard("lead", seconds=0.01).process(Context(stream=True), call_next)
    assert calls == [1]


def test_the_bound_comes_from_the_environment(monkeypatch):
    monkeypatch.setenv("TEAM_MODEL_STALL_SECONDS", "45")
    assert ModelStallGuard("x").seconds == 45
    monkeypatch.setenv("TEAM_MODEL_STALL_SECONDS", "not-a-number")
    assert ModelStallGuard("x").seconds == 120
