"""Source check: does every Microsoft 365 item the team cited open, and is it the item it names?

    python scripts/check_sources.py [<rehearsal run folder> ...]

For each step of a rehearsal run (default: the newest under the work folder's rehearsal folder), reads the answer's
workiq_references block (the app's source chips) and fetches every cited email, meeting, Teams message and file through
the deployment's Work IQ toolbox, as the signed-in user, by the same Graph path the app's source viewer opens. An email
or meeting must come back with the cited title as its subject, or a paraphrase that keeps most of its words; any other
subject is listed for a person to check. Reads only. Writes sources.md in the run folder; exits 1 if a source doesn't
open.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from advisor_team.ui.entities import parse_entity_url  # noqa: E402
from advisor_team.ui.references import references_from_text  # noqa: E402

STOP = {"the", "a", "an", "of", "to", "for", "and", "on", "in", "his", "her", "my", "our", "re", "fw", "fwd"}
BATCH = 8


def az(*args: str) -> str:
    return subprocess.run(["az", *args], capture_output=True, text=True, check=True, shell=os.name == "nt").stdout.strip()


def tf_output(name: str) -> str:
    return subprocess.run(["terraform", f"-chdir={ROOT / 'terraform'}", "output", "-raw", "-no-color", name],
                          capture_output=True, text=True, check=True, shell=os.name == "nt").stdout.strip()


def words(text: str) -> set[str]:
    text = re.sub(r"^\s*(email|meeting|event|re|fw|fwd)\s*:\s*", "", text, flags=re.IGNORECASE)
    return {w for w in re.findall(r"[\w%.+$]+", text.replace("’", "'").casefold()) if w not in STOP}


def same_item(cited: str, actual: str) -> bool:
    """The opened item is the one cited: its title, or a paraphrase keeping most of its words (a specialist sometimes
    writes "Michael Scott: 529 plan" for "One more thing before Thursday: 529 plan")."""
    wanted, found = words(cited), words(actual)
    return not wanted or len(wanted & found) / len(wanted) >= 0.5


def cited_items(answer: str) -> list:
    """The answer's sources with an item path the app can open, once each."""
    seen, items = set(), []
    for reference in references_from_text(answer):
        if reference.entity_url and reference.entity_url not in seen:
            seen.add(reference.entity_url)
            items.append(reference)
    return items


def answer_text(path: Path) -> str:
    data = json.loads(path.read_text(encoding="utf-8"))
    # The sources block is added to the lead's answer message, which isn't always the last message item.
    return "\n".join(part.get("text", "") for item in data.get("output", []) if item.get("type") == "message"
                     for part in item.get("content", []))


class WorkIQ:
    """The deployment's Work IQ toolbox over MCP, as the signed-in user."""

    def __init__(self, endpoint: str) -> None:
        self.url = endpoint if "api-version=" in endpoint else f"{endpoint}?api-version=v1"
        self.headers = {"Authorization": "Bearer " + az("account", "get-access-token", "--resource",
                                                        "https://ai.azure.com", "--query", "accessToken", "-o", "tsv"),
                        "Accept": "application/json, text/event-stream", "Content-Type": "application/json",
                        "Foundry-Features": "Toolboxes=V1Preview"}
        self.http = httpx.Client(timeout=180)
        self.ids = iter(range(1, 1_000_000))
        init = self.call("initialize", {"protocolVersion": "2025-03-26", "capabilities": {},
                                        "clientInfo": {"name": "source-check", "version": "1"}})
        if init.headers.get("mcp-session-id"):
            self.headers["mcp-session-id"] = init.headers["mcp-session-id"]
        self.http.post(self.url, headers=self.headers, json={"jsonrpc": "2.0", "method": "notifications/initialized"})

    def call(self, method: str, params: dict) -> httpx.Response:
        response = self.http.post(self.url, headers=self.headers,
                                  json={"jsonrpc": "2.0", "id": next(self.ids), "method": method, "params": params})
        response.raise_for_status()
        return response

    def fetch(self, paths: list[str]) -> list[dict]:
        response = self.call("tools/call", {"name": "workiq___fetch", "arguments": {"entityUrls": paths}})
        payloads = ([json.loads(line[5:]) for line in response.text.splitlines() if line.startswith("data:")]
                    if "text/event-stream" in response.headers.get("content-type", "") else [response.json()])
        for payload in payloads:
            if "error" in payload:
                raise RuntimeError(f"Work IQ fetch failed: {str(payload['error'])[:300]}")
            result = payload.get("result") or {}
            if isinstance(result.get("structuredContent"), dict):
                return result["structuredContent"].get("results") or []
            for block in result.get("content") or []:
                try:
                    return json.loads(block.get("text") or "").get("results") or []
                except ValueError:
                    continue
        return []


def check(workiq: WorkIQ, folder: Path) -> tuple[dict, list[str]]:
    totals = {"cited": 0, "opened": 0, "not_found": 0, "check_by_hand": 0}
    lines: list[str] = []
    for path in sorted(folder.glob("*.json")):
        if path.name == "results.json":
            continue
        items = cited_items(answer_text(path))
        for start in range(0, len(items), BATCH):
            batch = items[start:start + BATCH]
            kinds = [parse_entity_url(item.entity_url).kind for item in batch]
            paths = [item.entity_url + ("?$select=subject" if kind in ("email", "event") else
                                        "?$select=name" if kind == "file" else "") for item, kind in zip(batch, kinds)]
            for item, kind, result in zip(batch, kinds, workiq.fetch(paths)):
                totals["cited"] += 1
                status = result.get("statusCode")
                if status != 200:
                    totals["not_found"] += 1
                    lines.append(f"| {path.stem} | {kind} | Doesn't open (Graph {status}) | {item.title[:80]} | |")
                    continue
                totals["opened"] += 1
                subject = (result.get("data") or {}).get("subject")
                if kind in ("email", "event") and isinstance(subject, str) and not same_item(item.title, subject):
                    totals["check_by_hand"] += 1
                    lines.append(f"| {path.stem} | {kind} | Opens; check the title | {item.title[:80]} | {subject[:80]} |")
    return totals, lines


def main() -> int:
    folders = [Path(arg) for arg in sys.argv[1:]]
    if not folders:
        runs = Path(os.environ.get("TEAM_WORK_DIR", ROOT / "work")) / "rehearsal"
        folders = sorted((p for p in runs.iterdir() if p.is_dir()), key=lambda p: p.name)[-1:]
    workiq = WorkIQ(tf_output("workiq_toolbox_endpoint"))
    failed = False
    for folder in folders:
        totals, lines = check(workiq, folder)
        summary = (f"{totals['cited']} cited items: {totals['opened']} open, {totals['not_found']} don't, "
                   f"{totals['check_by_hand']} open under another title (check by hand)")
        report = [f"# Source check: {folder.name}", "", summary, ""]
        if lines:
            report += ["| Step | Kind | Result | Cited title | Opened subject |", "|---|---|---|---|---|", *lines]
        (folder / "sources.md").write_text("\n".join(report) + "\n", encoding="utf-8")
        print(f"{folder.name}: {summary}")
        for line in lines:
            print("  " + line)
        failed = failed or totals["not_found"] > 0
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
