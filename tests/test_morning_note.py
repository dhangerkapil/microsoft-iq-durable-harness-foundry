"""Add-on O1, the morning note's pure parts: the watch list, the note's code and the firm library documents."""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "functions" / "morning_note"))

from note import (  # noqa: E402
    APPROVAL_BUDGET_BYTES,
    MAX_TICKERS,
    DraftNote,
    PublishNote,
    approval_answer,
    approval_request,
    doc_code,
    fits_for_approval,
    library_documents,
    note_date,
    sections,
    stale_sections,
    stored_size,
    watch_list,
)


def test_the_watch_list_keeps_public_symbols_only():
    assert watch_list(["nvda", "NVDA", "msft", "Michael Scott", "$400,000", "BRK.B"], "") == ["NVDA", "MSFT", "BRK.B"]
    assert watch_list([], "NVDA, MSFT ,AAPL") == ["NVDA", "MSFT", "AAPL"]
    assert len(watch_list([f"T{chr(65 + i)}" for i in range(12)], "")) == 8


def test_the_note_date_defaults_to_today_and_ignores_garbage():
    today = date(2026, 10, 21)
    assert note_date("", today) == "2026-10-21"
    assert note_date("2026-10-19", today) == "2026-10-19"
    assert note_date("next Tuesday", today) == "2026-10-21"
    assert doc_code("2026-10-21") == "IC-MN-20261021"


def test_the_approved_note_becomes_one_library_section_per_ticker():
    text = "Summary line one. Summary two.\n\n## NVDA\nNVIDIA rose (Reuters, Oct 19).\nTalking point: x\n\n## MSFT\nQuiet week."
    assert [name for name, _ in sections(text, ["NVDA", "MSFT"])] == ["Summary", "NVDA", "MSFT"]
    documents = library_documents(PublishNote("2026-10-21", ["NVDA", "MSFT"], text, "Head of Research"))
    assert [d["section"] for d in documents] == ["§Summary", "§NVDA", "§MSFT"]
    assert {d["doc_code"] for d in documents} == {"IC-MN-20261021"}
    assert len({d["id"] for d in documents}) == 3 and all(d["@search.action"] == "mergeOrUpload" for d in documents)
    assert "IC-MN-20261021 NVDA" in documents[1]["content"] and "Head of Research" in documents[0]["owner"]
    assert set(documents[0]) == {"@search.action", "id", "doc_code", "title", "section", "effective", "owner",
                                 "source_file", "page", "content"}

def test_the_size_check_matches_how_the_framework_stores_a_pending_request():
    encoding = pytest.importorskip("agent_framework._workflows._checkpoint_encoding")
    request = approval_request(DraftNote("2026-10-21", ["NVDA"], "## NVDA\nNVIDIA rose 4% (Reuters, Oct 19). é ✓"))
    stored = json.dumps(encoding.encode_checkpoint_value(request)).encode("utf-16-le")
    assert stored_size(request) == len(stored)


def test_a_concise_note_fits_the_approval_and_a_long_one_does_not():
    # The deployed run failed at 18 KB and 21 KB: the host caps a waiting run's status at 16 KB.
    words = " ".join(["NVIDIA's data-centre revenue rose (Reuters, Oct 19)."] * 50)
    concise = DraftNote("2026-10-21", ["NVDA", "MSFT"], f"Summary.\n\n## NVDA\n{words}\n\n## MSFT\nQuiet week.")
    assert len(concise.text.split()) > 300 and fits_for_approval(concise)
    assert not fits_for_approval(DraftNote("2026-10-21", ["NVDA"], "x" * 9_000))
    assert APPROVAL_BUDGET_BYTES < 16 * 1024
    request = approval_request(concise)
    assert request["note"] == concise.text and request["tickers"] == ["NVDA", "MSFT"]
    assert "2026-10-21" in request["question"] and json.loads(json.dumps(request)) == request

def test_republishing_a_day_removes_sections_the_new_note_no_longer_has():
    deletes = stale_sections("2026-10-21", 2)
    assert [d["id"] for d in deletes] == [f"IC-MN-20261021-{i}" for i in range(3, 10)]
    assert all(d == {"@search.action": "delete", "id": d["id"]} for d in deletes)
    assert stale_sections("2026-10-21", 9) == []

def test_an_approved_note_is_published_in_full_however_long_its_sections():
    # A note the approval budget lets through was cut to 6,000 characters per section when published.
    lines = [f"Item {i:03d}: NVIDIA's data-centre revenue rose (Reuters, Oct 19)." for i in range(105)]
    text = "\n".join(lines)
    assert len(text) > 6_000 and fits_for_approval(DraftNote("2026-10-21", ["NVDA"], text))
    documents = library_documents(PublishNote("2026-10-21", ["NVDA"], text, "Head of Research"))
    published = "\n".join(d["content"] for d in documents)
    assert all(line in published for line in lines)


def test_a_tickers_later_headings_join_its_section_so_stale_deletes_cover_every_section():
    text = ("Summary.\n\n## NVDA\nNVIDIA rose.\n\n## MSFT\nQuiet week.\n\n### NVDA talking point\nSteady.\n\n"
            "### MSFT talking point\nWatch Azure.")
    named = sections(text, ["NVDA", "MSFT"])
    assert [name for name, _ in named] == ["Summary", "NVDA", "MSFT"]
    assert "Steady." in named[1][1] and "Watch Azure." in named[2][1]
    tickers = [f"T{chr(65 + i)}" for i in range(MAX_TICKERS)]
    busy = "\n".join(f"## {t}\nNews.\n### {t} talking point\nPoint." for t in tickers)
    documents = library_documents(PublishNote("2026-10-21", tickers, "Summary.\n" + busy, "Head of Research"))
    assert len(documents) == MAX_TICKERS + 1
    assert stale_sections("2026-10-21", len(documents)) == []


def test_ticker_headings_with_markup_are_recognised():
    text = "Summary.\n## **NVDA**\nA.\n## MSFT:\nB.\n## Apple (AAPL)\nC.\n## Notes on rates\nD."
    named = sections(text, ["NVDA", "MSFT", "AAPL"])
    assert [name for name, _ in named] == ["Summary", "NVDA", "MSFT", "AAPL"]
    assert named[-1][1].endswith("D.")


def test_only_an_explicit_yes_publishes():
    # The respond endpoint builds the answer without checking types: "false" or "no" would have counted as yes.
    assert approval_answer(True) is True and approval_answer("true") is True and approval_answer(" TRUE ") is True
    assert approval_answer(False) is False and approval_answer("false") is False
    for unclear in ("no", "yes", "approved", "", 1, 0, None, [], {}):
        assert approval_answer(unclear) is None

