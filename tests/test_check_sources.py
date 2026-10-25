"""The source check reads the app's source chips from an answer and tells a paraphrased title from another item."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import check_sources  # noqa: E402

EVENT = "/me/events/AAMkADBjMGU3YTUxLTJmNGItNGQxYS05ZTNjLTZiNWE0ZjNlMmQxYwBGAAAAAAD_c3ludGhldGljRm9sZGVycwDU3lud="


def test_a_paraphrased_title_is_the_same_item_and_another_subject_is_not():
    assert check_sources.same_item("NVDA +11.2% today: Michael Scott over his single-stock limit",
                                   "NVDA +11.2% today: Michael Scott over his single-stock limit")
    assert check_sources.same_item("Email: Michael Scott: 529 plan", "One more thing before Thursday: 529 plan")
    assert check_sources.same_item("Alex Morgan: NVDA +11.2% today",
                                   "NVDA +11.2% today: Michael Scott over his single-stock limit")
    assert not check_sources.same_item("Agenda for tomorrow's review", "Change of plans: closing moved to December 15")


def test_the_sources_are_the_answers_openable_items_once_each():
    references = [{"title": "Year-end planning", "url": "https://outlook.office365.com/owa/?itemid=x", "kind": "event",
                   "entity_url": EVENT},
                  {"title": "Year-end planning (again)", "url": "https://outlook.office365.com/owa/?itemid=y",
                   "kind": "event", "entity_url": EVENT},
                  {"title": "A public page", "url": "https://example.com/news"}]
    answer = "The brief.\n\n```workiq_references\n" + json.dumps(references) + "\n```"
    items = check_sources.cited_items(answer)
    assert [item.entity_url for item in items] == [EVENT]
