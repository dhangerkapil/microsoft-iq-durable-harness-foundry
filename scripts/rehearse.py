"""Demo rehearsal runner: every example in demo/expectations.yaml against the hosted agent, with checks.

    python scripts/rehearse.py --segments prep fouriqs eachiq ... [--agent advisor-team] [--have webiq fabric ...]

Each prompt goes to the agent as the signed-in user (the isolated Azure CLI profile). The runner reads the lead's calls
and the specialists' lanes from the response (the activity envelope), checks the facts, calls, lanes and time budget,
opens nothing it shouldn't, and writes work/rehearsal/<time>/report.md and results.json. Examples whose prerequisites
are missing (--have) are skipped, not failed.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import httpx
import yaml

ROOT = Path(__file__).resolve().parents[1]
TEAM_KEY, TIMING_KEY = "__team_activity__", "__workiq_tool_execution__"
LANES = ("client-context", "portfolio", "firm-policy", "market", "writer", "reviewer")
sys.path.insert(0, str(Path(__file__).resolve().parent))
from review_packs import resilient  # noqa: E402


def az(*args: str) -> str:
    return subprocess.run(["az", *args], capture_output=True, text=True, check=True, shell=os.name == "nt").stdout.strip()


def tf_output(name: str) -> str:
    return subprocess.run(["terraform", f"-chdir={ROOT / 'terraform'}", "output", "-raw", "-no-color", name],
                          capture_output=True, text=True, check=True, shell=os.name == "nt").stdout.strip()


def unwrap(output):
    """Split a tool output into its text and the team's records, whatever the envelopes' order."""
    team = None
    value = output
    for _ in range(4):
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except json.JSONDecodeError:
                break
        if isinstance(value, dict) and TEAM_KEY in value and "output" in value:
            team = team or value[TEAM_KEY]
            value = value["output"]
        elif isinstance(value, dict) and TIMING_KEY in value and "output" in value:
            value = value["output"]
        else:
            break
    return value, team


class Agent:
    def __init__(self, agent: str) -> None:
        project = os.environ.get("TEAM_PROJECT_ENDPOINT") or tf_output("foundry_project_endpoint")
        self.url = f"{project}/agents/{agent}/endpoint/protocols/openai"
        self.client = httpx.Client(timeout=600)
        self.conversation: str | None = None

    def headers(self) -> dict:
        token = az("account", "get-access-token", "--resource", "https://ai.azure.com", "--query", "accessToken", "-o", "tsv")
        return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    def new_conversation(self) -> None:
        r = resilient(lambda: self.client.post(f"{self.url}/conversations?api-version=v1", headers=self.headers(),
                                               json={}))
        r.raise_for_status()
        self.conversation = r.json()["id"]

    def send(self, input_value) -> dict:
        if not self.conversation:
            self.new_conversation()
        body = {"input": input_value, "conversation": self.conversation, "store": True}
        try:
            r = resilient(lambda: self.client.post(f"{self.url}/responses?api-version=v1", headers=self.headers(),
                                                   json=body))
        except httpx.TransportError as exc:  # possibly processed: not resent; this step fails, the run goes on
            return {"status": "transport_error", "error": f"{type(exc).__name__}: {exc}", "output": []}
        if r.status_code >= 400:
            return {"status": "http_error", "error": f"{r.status_code}: {r.text[:500]}", "output": []}
        return r.json()


def analyse(response: dict) -> dict:
    calls, nested, answer, consent, approvals = [], [], [], [], []
    for item in response.get("output") or []:
        kind = item.get("type")
        if kind == "function_call":
            calls.append(item.get("name", ""))
        elif kind == "function_call_output":
            _, team = unwrap(item.get("output"))
            if team:
                nested.extend(team.get("calls") or [])
        elif kind == "message":
            answer.extend(c.get("text", "") for c in item.get("content") or [] if c.get("text"))
        elif kind == "oauth_consent_request":
            consent.append(item.get("consent_link", ""))
        elif kind == "mcp_approval_request":
            approvals.append(item.get("id"))
    lanes: dict[str, dict] = {}
    for call in nested:
        lane = lanes.setdefault(call["lane"], {"calls": [], "start": call["started_at"], "end": call["started_at"]})
        lane["calls"].append(call["tool"])
        start = datetime.fromisoformat(call["started_at"]).timestamp()
        lane["start"] = min(lane["start"], call["started_at"])
        end = datetime.fromtimestamp(start + call.get("duration_ms", 0) / 1000).astimezone().isoformat()
        lane["end"] = max(lane["end"], end)
    return {"calls": calls, "nested": nested, "lanes": lanes, "answer": "\n".join(answer), "consent": consent,
            "approvals": approvals, "status": response.get("status")}


def check(example: dict, result: dict, elapsed: float) -> list[str]:
    budget = example.get("budget_s")
    over_budget = [f"over budget: {elapsed:.0f}s > {budget}s"] if budget and elapsed > budget else []
    if example.get("expect_process_stop"):
        # The crash test stops the process mid-turn on purpose, so the turn ends without output to check; the next
        # steps check what it saved and that it resumes. A turn that completes means the crash test didn't fire.
        if result["status"] != "completed":
            return over_budget
        return ["the process did not stop: the crash test did not fire", *over_budget]
    problems = []
    all_calls = result["calls"] + [c["tool"] for c in result["nested"]]
    for pattern in example.get("calls") or []:
        if not any(re.search(pattern, c) for c in all_calls):
            problems.append(f"missing call {pattern}")
    for pattern in example.get("never") or []:
        hits = [c for c in all_calls if re.search(pattern, c)]
        if hits:
            problems.append(f"unexpected call {hits[0]}")
    for lane in example.get("lanes") or []:
        if lane not in result["lanes"]:
            problems.append(f"lane {lane} did not run")
    if example.get("only_lanes"):
        extra = [l for l in result["lanes"] if l not in (example.get("lanes") or [])]
        if extra:
            problems.append(f"other lanes ran: {', '.join(extra)}")
    for pattern in example.get("facts") or []:
        if not re.search(pattern, result["answer"], re.I | re.M):
            problems.append(f"fact not found: {pattern}")
    if example.get("no_client_detail_to_web"):
        terms = [t.strip() for t in os.environ.get("WEB_BOUNDARY_TERMS", "").split(",") if t.strip()]
        for call in result["nested"]:
            if call["lane"] == "market" and any(t.lower() in (call.get("arguments") or "").lower() for t in terms):
                problems.append("a client detail reached the market specialist")
    if over_budget:
        problems.extend(over_budget)
    if result["status"] not in ("completed",):
        problems.append(f"status {result['status']}")
    return problems


def run_packs(agent_name: str, example: dict) -> tuple[dict, str]:
    """W3 on the hosted agent: review-packs steps, every card answered from the expectation, every pack run."""
    from review_packs import PacksClient
    from review_packs import run as run_review_packs

    packs = PacksClient(agent_name)
    packs.start_conversation()
    approve = example.get("approve", "approve") == "approve"

    def decide(card: dict) -> dict:
        if card.get("kind") == "draft":
            return {"approve": approve, "sha256": card.get("sha256", "") if approve else ""}
        return {"approve": True}

    view = run_review_packs(packs, example["prompt"], bool(example.get("crash_test")), decide)
    return view, "\n".join(t for t in (view.get("text"), view.get("error")) if t)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--segments", nargs="*", default=None)
    parser.add_argument("--agent", default="advisor-team")
    parser.add_argument("--have", nargs="*", default=[],
                        help="prerequisites in place: workiq-consent fabric webiq second-user committee morning-note purview")
    args = parser.parse_args()
    spec = yaml.safe_load((ROOT / "demo" / "expectations.yaml").read_text(encoding="utf-8"))["segments"]
    segments = args.segments or list(spec)
    out = Path(os.environ.get("TEAM_WORK_DIR", ROOT / "work")) / "rehearsal" / time.strftime("%Y%m%d-%H%M%S")
    out.mkdir(parents=True, exist_ok=True)
    agent = Agent(args.agent)
    rows, results = [], []
    for segment in segments:
        for example in spec[segment]:
            missing = [n for n in example.get("needs") or [] if n not in args.have]
            if missing or example.get("user") == "analyst" and "second-user" not in args.have:
                rows.append((example["id"], "SKIP", f"needs {', '.join(missing) or 'second-user'}", "", ""))
                continue
            if example.get("agent") == "packs":
                started = time.perf_counter()
                response, answer = run_packs(args.agent, example)
                elapsed = time.perf_counter() - started
                (out / f"{example['id']}.json").write_text(json.dumps(response, indent=1), encoding="utf-8")
                result = {"calls": [], "nested": [], "lanes": {}, "answer": answer, "consent": [], "approvals": [],
                          "status": response.get("status")}
                problems = check(example, result, elapsed)
                rows.append((example["id"], "PASS" if not problems else "FAIL", "; ".join(problems)[:300],
                             f"{elapsed:.0f}s/{example.get('budget_s', '-')}s", "review packs"))
                results.append({"id": example["id"], "elapsed_s": round(elapsed, 1), "problems": problems,
                                "answer": answer})
                print(f"{example['id']:<14} {rows[-1][1]:<7} {rows[-1][3]:<10} {rows[-1][2]}", flush=True)
                continue
            if example.get("new_session"):
                agent.new_conversation()
            if example.get("restart_before"):
                subprocess.run(["pwsh", "-NoProfile", "-File", str(ROOT / "scripts" / "Restart-AgentSession.ps1"),
                                "-AgentName", args.agent], capture_output=True, text=True)
            started = time.perf_counter()
            response = agent.send(example["prompt"])
            result = analyse(response)
            if result["approvals"] and example.get("approve"):
                decision = example["approve"] == "approve"
                response = agent.send([{"type": "mcp_approval_response", "approval_request_id": result["approvals"][0],
                                        "approve": decision}])
                later = analyse(response)
                result["answer"] += "\n" + later["answer"]
                result["calls"] += later["calls"]
            elapsed = time.perf_counter() - started
            (out / f"{example['id']}.json").write_text(json.dumps(response, indent=1), encoding="utf-8")
            if result["consent"]:
                rows.append((example["id"], "CONSENT", "consent needed: " + result["consent"][0][:60], f"{elapsed:.0f}s", ""))
                continue
            problems = check(example, result, elapsed)
            lanes = ", ".join(f"{k}({len(v['calls'])})" for k, v in result["lanes"].items())
            rows.append((example["id"], "PASS" if not problems else "FAIL", "; ".join(problems)[:300],
                         f"{elapsed:.0f}s/{example.get('budget_s', '-')}s", lanes))
            results.append({"id": example["id"], "elapsed_s": round(elapsed, 1), "problems": problems,
                            "calls": result["calls"], "lanes": result["lanes"], "answer": result["answer"]})
            print(f"{example['id']:<14} {rows[-1][1]:<7} {rows[-1][3]:<10} {lanes} {rows[-1][2]}", flush=True)
    report = ["| Step | Result | Time/budget | Lanes | Problems |", "|---|---|---|---|---|"]
    report += [f"| {r[0]} | {r[1]} | {r[3]} | {r[4]} | {r[2]} |" for r in rows]
    (out / "report.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    (out / "results.json").write_text(json.dumps(results, indent=1), encoding="utf-8")
    print(f"\nReport: {out / 'report.md'}")
    return 1 if any(r[1] == "FAIL" for r in rows) else 0


if __name__ == "__main__":
    sys.exit(main())
