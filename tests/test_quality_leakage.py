"""The optimizer leakage guard: a candidate that copies test answers, absolute dates or staff names is not applied."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "evals"))
import foundry_quality as quality  # noqa: E402

TRUTH = ["NVDA is 26.1% of the portfolio ($763,857.78) as of October 19; the meeting is at 10:00 AM."]
STAFF = ["Alex Morgan", "Alex"]


def test_the_rejected_overfit_candidate_is_flagged():
    # The shape of the candidate the optimizer actually produced: a fixed date, the test's answers and a name.
    baseline = "Use rebalancing_check for every trim. Limits come from the signed IPS."
    candidate = baseline + "\nCurrent date is 2026-10-18. Michael's NVDA is 26.1% ($763,857.78). Greet Alex."
    findings = quality.leakage_findings(candidate, baseline, TRUTH, STAFF)
    assert any("absolute date" in finding for finding in findings)
    assert any("26.1%" in finding and "$763,857.78" in finding for finding in findings)
    assert any(finding == "names a staff member: Alex" for finding in findings)


def test_general_improvements_and_facts_already_in_the_baseline_pass():
    baseline = "Use rebalancing_check for every trim. The single-stock limit is in the IPS, for example 25%. Alex"
    candidate = baseline + "\nGroup sources by IQ. Keep the brief under 400 words. Quote the limit, for example 25%."
    assert quality.leakage_findings(candidate, baseline, TRUTH + ["The limit is 25%."], STAFF) == []
    assert quality.leakage_findings("Today is a good day to check sources.", "", TRUTH, STAFF) == []


def test_ground_truths_come_from_the_case_files():
    truths = quality.ground_truths()
    assert truths and all(isinstance(truth, str) and truth for truth in truths)


QUESTION = ("How is Work IQ different from just calling the Graph API? Show me side by side on this question: where is "
            "the chart of Michael Scott's NVDA position against its limit, and what did Michael ask about it by email?")


def test_a_candidate_that_copies_a_test_question_is_flagged():
    # The shape of a candidate the hosted optimizer produced: a "special handling" section quoting its test cases.
    baseline = ("A question about Work IQ itself (\"How is Work IQ different from calling the Graph API? Show me on "
                "...\") goes to client context alone.")
    candidate = baseline + ("\nSpecial handling for known recurring advisor tasks:\n- \u201cHow is Work IQ different "
                            "from just calling the Graph API? Show me side by side on this question: where is the chart "
                            "of Michael Scott\u2019s NVDA position against its limit, and what did Michael ask about it "
                            "by email?\u201d\n  - This is primarily a Work IQ question.")
    findings = quality.leakage_findings(candidate, baseline, TRUTH, STAFF, [QUESTION])
    assert any(finding.startswith("copies a test question word for word") for finding in findings)
    # General guidance, and an example the baseline already has, are fine.
    general = baseline + "\nUse claim-level citations: each fact with its source's title and date, and its IQ."
    assert quality.leakage_findings(general, baseline, TRUTH, STAFF, [QUESTION]) == []


def test_the_case_questions_load_and_the_baseline_itself_quotes_none_of_them():
    queries = quality.case_queries()
    assert queries and all(isinstance(query, str) and query for query in queries)
    baseline = "\n".join(path.read_text(encoding="utf-8") for path in
                         [quality.BASELINE / "instructions.md", *sorted((quality.BASELINE / "skills").glob("*/SKILL.md"))])
    assert quality.leakage_findings(baseline, baseline, quality.ground_truths(), [], queries) == []
