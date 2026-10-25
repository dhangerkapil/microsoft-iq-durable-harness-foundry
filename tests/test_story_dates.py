"""Story dates: STORY_MEETING_DATE moves the demo week by whole weeks; unset changes nothing."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "data" / "hydration"))
import story_dates as sd  # noqa: E402


def test_unset_or_reference_date_changes_nothing(monkeypatch):
    text = (ROOT / "data" / "hydration" / "Publish-DemoContent.ps1").read_text(encoding="utf-8")
    monkeypatch.delenv("STORY_MEETING_DATE", raising=False)
    assert sd.shift_days() == 0
    assert sd.shift_text(text) == text
    monkeypatch.setenv("STORY_MEETING_DATE", "2026-10-22")
    assert sd.shift_text(text) == text


def test_meeting_must_be_a_thursday():
    with pytest.raises(ValueError, match="Thursday"):
        sd.meeting_date("2026-10-21")
    assert sd.shift_days("2027-01-07") == 77


@pytest.mark.parametrize(("days", "text", "expected"), [
    (7, "Thursday October 22, 2026, 10:00 AM", "Thursday October 29, 2026, 10:00 AM"),
    (7, "Meeting notes 2026-10-16 Q4 review.docx", "Meeting notes 2026-10-23 Q4 review.docx"),
    (77, "Meeting notes 2026-10-16 Q4 review.docx", "Meeting notes 2027-01-01 Q1 review.docx"),
    (77, "2026-10-22T10:00:00", "2027-01-07T10:00:00"),
    (77, "wired by December 10", "wired by February 25"),
    (77, "Portfolio Overview (Oct 2026)", "Portfolio Overview (Dec 2026)"),
    (77, "the 2026 realized-gain budget", "the 2027 realized-gain budget"),
    (133, "buy in spring 2027, much sooner than spring", "buy in summer 2027, much sooner than summer"),
    (77, "since September", "since November"),
    (-7, "Friday October 16, 2026", "Friday October 9, 2026"),
    (77, "Private Client since March 2014", "Private Client since March 2014"),
    (77, "api-version=2023-11-01; NVDA could fall 10%; $2,026; C0007", "api-version=2023-11-01; NVDA could fall 10%; $2,026; C0007"),
])
def test_shift_examples(days, text, expected):
    assert sd.shift_text(text, days) == expected


def test_portfolio_data_moves_dates_not_numbers(tmp_path):
    script = ROOT / "data" / "hydration" / "documents" / "portfolio_series.py"
    notebook = ROOT / "data" / "fabric" / "notebooks" / "portfolio_data.py"

    def run(date: str) -> dict:
        out = tmp_path / f"{date}.json"
        env = {**os.environ, "STORY_MEETING_DATE": date}
        subprocess.run([sys.executable, str(script), str(notebook), str(out)], check=True, env=env)
        return json.loads(out.read_text(encoding="utf-8"))

    reference, moved = run("2026-10-22"), run("2027-01-07")
    assert reference["discussion_date"] == "2026-10-16"
    assert moved["discussion_date"] == "2027-01-01"
    assert [p["nvda_pct"] for p in moved["series"]] == [p["nvda_pct"] for p in reference["series"]]
    assert moved["holdings"] == reference["holdings"]
    assert moved["series"][-1]["date"] == "2027-01-01"
