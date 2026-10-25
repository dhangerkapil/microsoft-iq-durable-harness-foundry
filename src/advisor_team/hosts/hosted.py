"""The Foundry hosted agent: one Responses server, in team mode (the lead and its specialists) or solo mode.

TEAM_MODE picks the build: ``team`` (default), the hosted agent ``advisor-team``, which also answers the review-packs
steps (workflow W3) in code; or ``solo``, the single agent with every tool, which is also the demo's fallback agent
``advisor-team-solo``. Both come from the same image.
"""

from __future__ import annotations

import asyncio
import logging
import os

from agent_framework_foundry_hosting import ResponsesHostServer
from azure.ai.agentserver.core import AgentConfig

MODES = ("team", "solo")


def team_mode() -> str:
    mode = os.environ.get("TEAM_MODE", "team").strip().lower()
    if mode not in MODES:
        raise RuntimeError("TEAM_MODE must be team or solo.")
    return mode


def align_trace_identity() -> None:
    """Stamp the container's spans with the agent identity the platform uses (name:version), as Insights expects."""
    name, version = os.environ.get("FOUNDRY_AGENT_NAME"), os.environ.get("FOUNDRY_AGENT_VERSION")
    if name and version:
        from azure.ai.agentserver.core import _config as host_config

        host_config.resolve_agent_id = lambda: f"{name}:{version}"


def main() -> None:
    if not AgentConfig.from_env().is_hosted:
        raise RuntimeError("Use advisor-team-console locally; the Responses server requires Foundry hosting.")
    logging.basicConfig(level=logging.INFO)
    align_trace_identity()
    mode = team_mode()
    logging.getLogger(__name__).info("Advisor host starting in %s mode.", mode)
    if mode == "solo":
        from ..solo import create_agent
    else:
        from ..team import create_team_agent as create_agent
    asyncio.run(ResponsesHostServer(agent=create_agent, history_source="agent").run_async())


if __name__ == "__main__":
    main()
