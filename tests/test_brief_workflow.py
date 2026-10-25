"""W1, the brief workflow, with fake specialists and agents: routing, the revision loop and the checks."""

from __future__ import annotations

import pytest

from advisor_team.activity import ActivityRecorder
from advisor_team.contracts import Figure, ReviewVerdict, SpecialistReport
from advisor_team.workflows.brief import (STANDARD_DISCLOSURE, BriefRequest, market_task, number_check, only_disclosure,
                                          run_brief)


class FakeSpecialist:
    def __init__(self, report: SpecialistReport) -> None:
        self.reportobj = report
        self.tasks: list[str] = []

    async def report(self, task: str, session=None) -> SpecialistReport:
        self.tasks.append(task)
        return self.reportobj


class Reply:
    def __init__(self, text: str) -> None:
        self.text = text
        self.value = None


class FakeWriter:
    def __init__(self, drafts: list[str]) -> None:
        self.drafts = list(drafts)
        self.prompts: list[str] = []

    async def run(self, prompt, **_):
        self.prompts.append(prompt)
        return Reply(self.drafts.pop(0))


class FakeReviewer:
    def __init__(self, verdicts: list[ReviewVerdict]) -> None:
        self.verdicts = list(verdicts)

    async def run(self, prompt, **_):
        return Reply(self.verdicts.pop(0).model_dump_json())


def specialists(**overrides):
    portfolio = SpecialistReport(answer="NVDA 26.1% of the portfolio; limit 25%; total $4,250,000 (Fabric IQ, Oct 19).",
                                 figures=[Figure(label="largest position ticker", value="NVDA"),
                                          Figure(label="largest position company", value="NVIDIA"),
                                          Figure(label="NVDA weight", value="26.1%")])
    base = {
        "client_context": FakeSpecialist(SpecialistReport(answer="Closing moved to December 15; $400,000 due December 10.")),
        "portfolio": FakeSpecialist(portfolio),
        "firm_policy": FakeSpecialist(SpecialistReport(answer="CP-104 §3 requires a review within 10 business days.")),
        "market": FakeSpecialist(SpecialistReport(answer="NVIDIA news (Web IQ).")),
    }
    base.update(overrides)
    return base


GOOD = "## Snapshot\nNVDA at 26.1% against the 25% limit; $400,000 due December 10.\n## Sources\n- **Work IQ:** email"


@pytest.mark.asyncio
async def test_cleared_first_time_and_market_gets_only_the_public_name():
    team = specialists()
    recorder = ActivityRecorder()
    outcome = await run_brief(BriefRequest("Michael Scott"), team, FakeWriter([GOOD]),
                              FakeReviewer([ReviewVerdict(cleared=True)]), recorder)
    assert outcome.cleared and outcome.attempts == 1 and outcome.brief == GOOD
    market_task_text = team["market"].tasks[0]
    assert "NVIDIA (NVDA)" in market_task_text and "Michael" not in market_task_text
    assert set(outcome.lanes) == {"client-context", "portfolio", "firm-policy", "market"}
    assert recorder.turn.verdict["cleared"] is True


@pytest.mark.asyncio
async def test_a_changed_figure_is_caught_then_revised():
    bad = GOOD.replace("26.1%", "21.6%")
    outcome = await run_brief(BriefRequest("Michael Scott"), specialists(), FakeWriter([bad, GOOD]),
                              FakeReviewer([ReviewVerdict(cleared=True), ReviewVerdict(cleared=True)]), ActivityRecorder())
    assert outcome.cleared and outcome.attempts == 2


@pytest.mark.asyncio
async def test_two_failures_mean_not_cleared_with_findings():
    advice = ReviewVerdict(cleared=False, findings=[{"sentence": "Sell NVDA now.", "issue": "reads like a trade "
                                                     "recommendation", "rule": "COM-310 §2"}])
    outcome = await run_brief(BriefRequest("Michael Scott"), specialists(), FakeWriter([GOOD, GOOD]),
                              FakeReviewer([advice, advice]), ActivityRecorder())
    assert not outcome.cleared and outcome.attempts == 2
    assert outcome.findings and outcome.findings[0]["rule"] == "COM-310 §2"


@pytest.mark.asyncio
async def test_seeded_sentence_reaches_the_reviewer():
    writer = FakeWriter([GOOD, GOOD])
    reviewer = FakeReviewer([ReviewVerdict(cleared=False, findings=[{"sentence": "We recommend", "issue": "advice"}]),
                             ReviewVerdict(cleared=True)])
    outcome = await run_brief(BriefRequest("Michael Scott"), specialists(), writer, reviewer, ActivityRecorder(),
                              seeded_sentence="We recommend selling 2,000 NVDA shares this week.")
    assert outcome.cleared and outcome.attempts == 2


@pytest.mark.asyncio
async def test_a_cleared_revision_keeps_what_the_checks_held_back():
    advice = {"sentence": "We recommend selling 2,000 NVDA shares this week.", "issue": "reads like a trade "
              "recommendation", "rule": "COM-310 §2"}
    recorder = ActivityRecorder()
    outcome = await run_brief(BriefRequest("Michael Scott"), specialists(), FakeWriter([GOOD, GOOD]),
                              FakeReviewer([ReviewVerdict(cleared=False, findings=[advice]), ReviewVerdict(cleared=True)]),
                              recorder, seeded_sentence=advice["sentence"])
    assert outcome.cleared and outcome.findings == []
    held = [f for f in outcome.held_back if f.get("rule") == "COM-310 §2"]
    assert held and held[0]["sentence"] == advice["sentence"]
    assert recorder.turn.verdict["held_back"] == outcome.held_back
    # A brief cleared on its first draft holds nothing back.
    first_time = await run_brief(BriefRequest("Michael Scott"), specialists(), FakeWriter([GOOD]),
                                 FakeReviewer([ReviewVerdict(cleared=True)]), ActivityRecorder())
    assert first_time.held_back == []


@pytest.mark.asyncio
async def test_a_finding_against_the_standard_disclosure_alone_is_dropped():
    taxed = GOOD.replace("## Sources", f"{STANDARD_DISCLOSURE}\n## Sources")
    disclosure = ReviewVerdict(cleared=False, findings=[{"sentence": STANDARD_DISCLOSURE, "issue": "directs the client "
                                                         "to take a tax-related action", "rule": "COM-310 §1"}])
    outcome = await run_brief(BriefRequest("Michael Scott"), specialists(), FakeWriter([taxed]),
                              FakeReviewer([disclosure]), ActivityRecorder())
    assert outcome.cleared and outcome.attempts == 1
    # Advice next to it still fails the draft, and only the advice is reported.
    both = ReviewVerdict(cleared=False, findings=[
        {"sentence": "Please consult your tax adviser.", "issue": "tax action", "rule": "COM-310 §1"},
        {"sentence": "Sell NVDA now.", "issue": "reads like a trade recommendation", "rule": "COM-310 §2"}])
    outcome = await run_brief(BriefRequest("Michael Scott"), specialists(), FakeWriter([taxed, taxed]),
                              FakeReviewer([both, both]), ActivityRecorder())
    assert not outcome.cleared and [f["sentence"] for f in outcome.findings] == ["Sell NVDA now."]


def test_only_disclosure_matches_the_disclosure_and_nothing_more():
    assert only_disclosure(f'"{STANDARD_DISCLOSURE}"') and only_disclosure("- Please consult your tax adviser")
    assert not only_disclosure(f"{STANDARD_DISCLOSURE} We recommend selling NVDA.")
    assert not only_disclosure("tax adviser") and not only_disclosure("")


def test_number_check_accepts_sourced_and_computed_figures():
    corpus = "NVDA 26.1% limit 25% total $4,250,000; trim 1,840 shares; tax $18,200.50; $400,000 due"
    brief = "NVDA 26.1% vs 25%; trim 1,840 shares; tax $18,200.50; $400K needed in 2026 on December 10."
    assert number_check(brief, corpus) == []


def test_number_check_flags_an_unsourced_figure():
    findings = number_check("NVDA is at 27.3% of the portfolio.", "NVDA 26.1%")
    assert findings and "27.3%" in findings[0]["issue"]


def test_number_check_treats_policy_section_references_as_citations():
    brief = ("- **Foundry IQ:** CP-104 §§3.1–3.3; OPS-220 §§1–4.3; COM-310 §§ 2, 4.3 and sections 3.1 to 3.3.\n"
             "A wire of $400,000 needs OPS-220 §2 controls.")
    assert number_check(brief, "OPS-220 wires above $250,000 need a call-back; $400,000 due") == []
    # A real range of amounts after a dash is still a figure.
    findings = number_check("Tax budget $15,000–$21,000.", "$15,000 from the accountant; $20,000 signed")
    assert findings and "$21,000" in findings[0]["issue"]


def test_number_check_ignores_digits_inside_links():
    url = ("https://contoso-my.sharepoint.com/personal/advisor/Documents/Client%20Files/Michael%20Scott/"
           "Michael%20Scott%20%E2%80%93%20IPS%20v1%20(signed).pdf")
    brief = (f"- OneDrive: [Michael Scott – IPS v1 (signed).pdf]({url}) (modified October 16, 2026)\n"
             "- Public context (Web IQ): [Reuters, Oct 20](https://www.example.com/news/chip%20stocks%20%E2%80%94%20live)")
    assert number_check(brief, "Michael Scott – IPS v1 (signed).pdf, modified 2026-10-16") == []
    # A figure next to a link is still checked.
    findings = number_check(f"NVDA is at 27.3% ([IPS]({url})).", "NVDA 26.1%")
    assert [f["issue"] for f in findings] == ["27.3% does not appear in any source or calculation"]


def test_market_task_without_a_holding_stays_generic():
    assert "US large-cap technology" in market_task("", "")
