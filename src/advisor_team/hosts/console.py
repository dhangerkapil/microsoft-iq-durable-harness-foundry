"""Run the team locally against the deployed resources (no hosting): python -m advisor_team.hosts.console "question".

Uses the Azure CLI sign-in (scripts/Use-IsolatedAzure.ps1) and the Terraform outputs for endpoints. Foundry IQ and Web IQ
work locally; Work IQ and Fabric need the hosted agent's per-user consent, so local runs skip the consent check
(TEAM_CONSENT_GATE=off) and work best with questions for the firm-policy and market specialists.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path


def _tf_outputs() -> dict:
    root = Path(__file__).resolve().parents[3]
    raw = subprocess.run(["terraform", f"-chdir={root / 'terraform'}", "output", "-json"], capture_output=True, text=True,
                         check=True, shell=os.name == "nt").stdout
    return {k: v.get("value") for k, v in json.loads(raw).items()}


def configure() -> None:
    out = _tf_outputs()
    project = out["foundry_project_endpoint"]
    os.environ.setdefault("FOUNDRY_PROJECT_ENDPOINT", project)
    os.environ.setdefault("TOOLBOX_ENDPOINT", out["workiq_toolbox_endpoint"])
    names = {"FOUNDRYIQ_TOOLBOX_ENDPOINT": "team-foundryiq", "WEBIQ_TOOLBOX_ENDPOINT": "team-webiq",
             "WORKIQ_CHAT_TOOLBOX_ENDPOINT": "team-workiq-chat", "GOVERNANCE_TOOLBOX_ENDPOINT": "team-governance",
             "DRAFTS_TOOLBOX_ENDPOINT": "team-drafts", "FABRIC_TOOLBOX_ENDPOINT": "team-fabric"}
    for key, name in names.items():
        os.environ.setdefault(key, f"{project}/toolboxes/{name}/mcp?api-version=v1")
    deployments = out.get("team_deployments") or {}
    os.environ.setdefault("TEAM_LEAD_DEPLOYMENT", deployments.get("lead", "team-lead"))
    os.environ.setdefault("TEAM_SPECIALIST_DEPLOYMENT", deployments.get("specialist", "team-specialist"))
    os.environ.setdefault("TEAM_SPECIALIST_ENDPOINT", out.get("specialist_endpoint") or "")
    os.environ.setdefault("TEAM_MODE", "team")
    os.environ.setdefault("TEAM_CONSENT_GATE", "off")
    os.environ.setdefault("WORKIQ_MODE", "mcp")


async def ask(question: str) -> None:
    from ..team import create_team_agent

    agent = await create_team_agent()
    started = time.perf_counter()
    response = await agent.run(question, session=agent.create_session())
    print(f"--- answer ({time.perf_counter() - started:.0f}s) ---\n{response.text}")
    from ..activity import ActivityRecorder

    turn = ActivityRecorder.current()
    if turn is not None:
        lanes: dict[str, list[str]] = {}
        for call in turn.calls:
            lanes.setdefault(call.lane, []).append(f"{call.tool} {call.duration_ms}ms{' FAILED' if call.failed else ''}")
        print("--- lanes ---")
        for lane, calls in lanes.items():
            print(f"{lane}: {', '.join(calls)}")


def main() -> None:
    configure()
    question = " ".join(sys.argv[1:]) or "What does our firm require before we release a large wire?"
    asyncio.run(ask(question))


if __name__ == "__main__":
    main()
