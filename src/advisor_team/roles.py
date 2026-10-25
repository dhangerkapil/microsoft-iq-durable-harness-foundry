"""The team's roles: who each specialist is, which IQ it owns and which skills it may load."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Role:
    key: str  # agent name (unique; background agents are addressed by it)
    lane: str  # X-ray lane
    title: str
    iq: str
    description: str
    skill: str  # the role's own instructions, a skill the optimizer can tune
    domain_skills: tuple[str, ...] = ()


CLIENT_CONTEXT = Role(
    key="client_context", lane="client-context", title="Client context", iq="Work IQ",
    description=("Work IQ specialist: everything about the client in the advisor's Microsoft 365 (email, meetings and "
                 "recordings, Teams chats, OneDrive and SharePoint files, charts inside files, Work IQ's own agent over "
                 "A2A and Copilot agents), always as the signed-in advisor."),
    skill="role-client-context",
    domain_skills=("work-iq-full-picture", "meeting-recall", "document-finder", "workiq-vs-graph",
                   "work-iq-chat-and-agents", "workiq-discovery"),
)
PORTFOLIO = Role(
    key="portfolio", lane="portfolio", title="Portfolio", iq="Fabric IQ",
    description=("Fabric IQ specialist: positions, weights against limits, sector exposure, cash, history and the IPS "
                 "record from the portfolio system of record, as the signed-in advisor."),
    skill="role-portfolio", domain_skills=("portfolio-data",),
)
FIRM_POLICY = Role(
    key="firm_policy", lane="firm-policy", title="Firm policy", iq="Foundry IQ",
    description=("Foundry IQ specialist: the firm's own rules and guidance (policies, procedures, approved products, "
                 "planning guides, the house view), quoted with document codes and sections."),
    skill="role-firm-policy", domain_skills=("firm-knowledge",),
)
MARKET = Role(
    key="market", lane="market", title="Market", iq="Web IQ",
    description=("Web IQ specialist: public news and public prices with links. Receives only public topics (tickers, "
                 "companies, sectors), never client details."),
    skill="role-market", domain_skills=("public-web-research",),
)
REVIEWER = Role(
    key="compliance_reviewer", lane="reviewer", title="Compliance reviewer", iq="Foundry IQ",
    description="Checks a draft brief against the firm's rules and communication standards before it is shown.",
    skill="role-reviewer",
)
WRITER = Role(
    key="writer", lane="writer", title="Writer", iq="",
    description="Writes the client brief from the specialists' evidence, with the arithmetic done by tools.",
    skill="role-writer", domain_skills=("rebalancing-guard",),
)

SPECIALISTS = (CLIENT_CONTEXT, PORTFOLIO, FIRM_POLICY, MARKET)
ALL_ROLES = (*SPECIALISTS, REVIEWER, WRITER)
LEAD_SKILLS = ("team-routing", "client-meeting-prep", "microsoft-iq-answer", "what-changed", "rebalancing-guard",
               "position-cross-check", "client-follow-up", "durable-meeting-pack", "durable-state", "workiq-governance")
