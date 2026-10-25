"""The rehearsal scripts send a request again only when it never reached the agent."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import review_packs  # noqa: E402


@pytest.fixture(autouse=True)
def no_waiting(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(review_packs.time, "sleep", lambda _: None)


def test_a_request_that_never_left_is_sent_again() -> None:
    attempts = []

    def call() -> str:
        attempts.append(1)
        if len(attempts) < 3:
            raise httpx.ConnectError("getaddrinfo failed")
        return "sent"

    assert review_packs.resilient(call) == "sent"
    assert len(attempts) == 3


def test_a_request_that_may_have_been_processed_is_not_sent_again() -> None:
    attempts = []

    def call() -> str:
        attempts.append(1)
        raise httpx.ReadTimeout("no answer")

    with pytest.raises(httpx.ReadTimeout):
        review_packs.resilient(call)
    assert len(attempts) == 1


def test_it_gives_up_after_four_tries() -> None:
    attempts = []

    def call() -> str:
        attempts.append(1)
        raise subprocess.CalledProcessError(1, ["az", "account", "get-access-token"])

    with pytest.raises(subprocess.CalledProcessError):
        review_packs.resilient(call)
    assert len(attempts) == 4


def result(status: str = "completed", calls: list[str] | None = None, answer: str = "") -> dict:
    return {"calls": calls or [], "nested": [], "lanes": {}, "answer": answer, "consent": [], "approvals": [],
            "status": status}


def test_a_crash_step_passes_when_the_process_stops_and_fails_when_it_does_not():
    import rehearse

    crash = {"id": "7.2", "budget_s": 65, "calls": ["run_durable_meeting_pack"], "expect_process_stop": True}
    assert rehearse.check(crash, result(status="failed"), 35) == []
    assert rehearse.check(crash, result(status="failed"), 70) == ["over budget: 70s > 65s"]
    assert rehearse.check(crash, result(calls=["run_durable_meeting_pack"]), 30) == [
        "the process did not stop: the crash test did not fire"]


def test_an_ordinary_step_checks_calls_facts_budget_and_status():
    import rehearse

    step = {"id": "2.3", "budget_s": 70, "calls": ["ask_firm_policy"], "facts": ["OPS-220"]}
    assert rehearse.check(step, result(calls=["ask_firm_policy"], answer="OPS-220 §2 (Foundry IQ)"), 33) == []
    assert rehearse.check(step, result(status="failed"), 80) == [
        "missing call ask_firm_policy", "fact not found: OPS-220", "over budget: 80s > 70s", "status failed"]

