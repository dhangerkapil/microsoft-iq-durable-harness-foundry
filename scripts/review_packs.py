"""Review packs (W3) from the command line, on the hosted agent advisor-team: start a run, answer each card, run the packs.

    python scripts/review_packs.py ["Prepare review packs for my three clients for next week."]
        [--crash-test] [--approve | --reject] [--clients "Michael Scott,Jan Levinson"] [--agent advisor-team]

Each step is a review-packs command sent to the agent in the run's own conversation: a streamed response whose last line
is the run's view (src/advisor_team/packs_protocol.py). "next" runs one client's pack. If the agent process stops during
a step (the crash test does so once, before the last pack), the step is sent again in a new conversation (Foundry keeps
the interrupted one's turn in flight) until a new process answers, and the run resumes from the Foundry state store.
Without --approve or --reject, the script asks.
Writes $TEAM_WORK_DIR/review-packs/<time>/transcript.json.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import httpx

from advisor_team.packs_protocol import command_text, view_in

ROOT = Path(__file__).resolve().parents[1]
# A connection that never opened (DNS, refused, connect timeout) or a token that couldn't be read means the request never
# reached the agent, so sending it again is safe. A failure after that (a read timeout) may have been processed.
SAFE_TO_RESEND = (httpx.ConnectError, httpx.ConnectTimeout, subprocess.CalledProcessError)
# After a stop, the run's session needs a new process: send the step again every RETRY_SECONDS, up to MAX_RETRIES times.
RETRY_SECONDS = 10
MAX_RETRIES = 24


class StepFailed(RuntimeError):
    """The step didn't come back with the run's view: the process stopped, or the connection broke."""


def resilient(call, attempts: int = 4):
    for attempt in range(attempts):
        try:
            return call()
        except SAFE_TO_RESEND as exc:
            if attempt == attempts - 1:
                raise
            wait = 10 * (attempt + 1)
            print(f"  ... {type(exc).__name__} before the request reached the agent; retrying in {wait} s", flush=True)
            time.sleep(wait)
    raise RuntimeError("unreachable")


def tf_output(name: str) -> str:
    return subprocess.run(["terraform", f"-chdir={ROOT / 'terraform'}", "output", "-raw", "-no-color", name],
                          capture_output=True, text=True, check=True, shell=os.name == "nt").stdout.strip()


def token() -> str:
    return subprocess.run(["az", "account", "get-access-token", "--resource", "https://ai.azure.com", "--query",
                           "accessToken", "-o", "tsv"], capture_output=True, text=True, check=True,
                          shell=os.name == "nt").stdout.strip()


class PacksClient:
    """The review-packs steps against one hosted agent, in one conversation per run."""

    def __init__(self, agent: str) -> None:
        project = os.environ.get("TEAM_PROJECT_ENDPOINT") or tf_output("foundry_project_endpoint")
        self.url = f"{project}/agents/{agent}/endpoint/protocols/openai"
        self.client = httpx.Client(timeout=httpx.Timeout(300, connect=30))
        self.conversation = ""
        self.transcript: list[dict] = []

    def _headers(self) -> dict:
        return {"Authorization": "Bearer " + token(), "Content-Type": "application/json"}

    def start_conversation(self) -> None:
        r = resilient(lambda: self.client.post(f"{self.url}/conversations?api-version=v1", headers=self._headers(),
                                               json={}))
        r.raise_for_status()
        self.conversation = r.json()["id"]

    def step(self, command: dict) -> dict:
        """One step: the streamed answer's last line is the run's view. Raises StepFailed when it doesn't come."""
        body = {"input": command_text(command), "conversation": self.conversation, "store": True, "stream": True}
        text: list[str] = []
        try:
            with self.client.stream("POST", f"{self.url}/responses?api-version=v1", headers=self._headers(),
                                    json=body) as r:
                if r.status_code >= 400:
                    r.read()
                    if r.status_code in (401, 403, 404, 422):
                        raise SystemExit(f"The agent refused the step ({r.status_code}): {r.text[:800]}")
                    raise StepFailed(f"HTTP {r.status_code}")
                for line in r.iter_lines():
                    if not line.startswith("data:"):
                        continue
                    try:
                        event = json.loads(line[5:].strip())
                    except json.JSONDecodeError:
                        continue
                    if event.get("type") == "response.output_text.delta" and isinstance(event.get("delta"), str):
                        text.append(event["delta"])
        except (httpx.TransportError, subprocess.CalledProcessError) as exc:
            raise StepFailed(type(exc).__name__) from exc
        view = view_in("".join(text))
        if view is None:
            raise StepFailed("the response ended without the run's view")
        self.transcript.append({"at": datetime.now().astimezone().isoformat(), "command": command, "view": view})
        return view

    def step_until_answered(self, command: dict) -> dict:
        """The step, sent again after a stop until a new process answers it. Foundry keeps an interrupted conversation's
        turn in flight, so each retry goes to a new conversation: a new session and process, the same user's run."""
        for attempt in range(MAX_RETRIES + 1):
            try:
                return self.step(command)
            except StepFailed as exc:
                if attempt == MAX_RETRIES:
                    raise
                print(f"  ... the step didn't finish ({exc}); the agent process may have stopped. Sending it again in "
                      f"a new conversation in {RETRY_SECONDS} s (attempt {attempt + 2}).", flush=True)
                time.sleep(RETRY_SECONDS)
                self.start_conversation()
        raise RuntimeError("unreachable")

    def run_packs(self, view: dict) -> dict:
        """While packs remain: the next pack, one per request."""
        while view.get("status") == "working":
            progress = view.get("progress") or {}
            print(f"  ... pack {progress.get('done', 0) + 1} of {progress.get('total', '?')}: {progress.get('next', '')}",
                  flush=True)
            started = time.monotonic()
            view = self.step_until_answered({"op": "next", "run": view["run"]})
            print(f"  ... {int(time.monotonic() - started)} s", flush=True)
        return view


def decide(prompt: str, default: bool | None) -> bool:
    if default is not None:
        print(f"{prompt} {'yes' if default else 'no'} (from the command line)")
        return default
    return input(f"{prompt} [y/N] ").strip().lower() in ("y", "yes")


def answer(card: dict, args: argparse.Namespace) -> dict:
    if card.get("kind") == "clients":
        print(f"\n[Card] {card.get('question')}\n  Clients: {', '.join(card.get('clients') or [])}")
        if card.get("note"):
            print(f"  {card['note']}")
        clients = [c.strip() for c in (args.clients or "").split(",") if c.strip()]
        approve = decide("Prepare these packs?", True if args.approve or args.reject or clients else None)
        return {"approve": approve, **({"clients": clients} if clients else {})}
    print(f"\n[Card] {card.get('question')}\n  To: {card.get('to')}\n  Subject: {card.get('subject')}\n"
          f"  {card.get('body')}\n  sha256 {str(card.get('sha256'))[:16]}...")
    approve = decide("Approve this draft?", True if args.approve else False if args.reject else None)
    return {"approve": approve, "sha256": card.get("sha256", "") if approve else ""}


def run(packs: PacksClient, prompt: str, crash_test: bool, decider) -> dict:
    """The whole run: start, then each card answered by ``decider(card)``, then every pack, until it completes."""
    view = packs.step_until_answered({"op": "start", "request": prompt, "crash_test": crash_test})
    for _ in range(32):
        view = packs.run_packs(view)
        if view.get("status") != "waiting" or not view.get("cards"):
            return view
        card = view["cards"][0]
        view = packs.step_until_answered({"op": "answer", "run": view["run"], "call_id": card["call_id"],
                                          "answer": decider(card)})
    return view


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("prompt", nargs="?", default="Prepare review packs for my three clients for next week.")
    parser.add_argument("--agent", default=os.environ.get("TEAM_AGENT_NAME", "advisor-team"))
    parser.add_argument("--crash-test", action="store_true", help="Stop the agent process once before the last pack.")
    parser.add_argument("--clients", help="Comma-separated client list to use instead of the proposed one.")
    choice = parser.add_mutually_exclusive_group()
    choice.add_argument("--approve", action="store_true", help="Approve every card without asking.")
    choice.add_argument("--reject", action="store_true", help="Confirm the clients, then reject every draft.")
    args = parser.parse_args()

    packs = PacksClient(args.agent)
    packs.start_conversation()
    print(f"{args.agent}, conversation {packs.conversation}\n> {args.prompt}"
          + (" (crash test)" if args.crash_test else ""))
    started = time.monotonic()
    view = run(packs, args.prompt, args.crash_test, lambda card: answer(card, args))
    if view.get("text"):
        print("\n" + view["text"])
    if view.get("error"):
        print(f"\nThe run ended {view.get('status')}: {view['error']}")
    out = Path(os.environ.get("TEAM_WORK_DIR", ROOT / "work")) / "review-packs" / datetime.now().strftime("%Y%m%d-%H%M%S")
    out.mkdir(parents=True, exist_ok=True)
    (out / "transcript.json").write_text(json.dumps(packs.transcript, indent=2), encoding="utf-8")
    print(f"\n{int(time.monotonic() - started)} s in total; transcript: {out / 'transcript.json'}")
    return 0 if view.get("status") == "completed" else 1


if __name__ == "__main__":
    sys.exit(main())
