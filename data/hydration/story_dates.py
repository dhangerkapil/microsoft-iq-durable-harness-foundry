"""Story dates: moves the demo story's week to any Thursday client meeting.

The story was written for a client meeting on Thursday 2026-10-22: the recorded Q4 review on the Friday before,
the client's emails on Thursday and Saturday, the analyst's alert on Monday afternoon. STORY_MEETING_DATE
(yyyy-mm-dd, a Thursday) moves every story date by whole weeks, so weekday names, "tomorrow" and trading days
stay right; month names, quarters, seasons and the story year follow the move. Unset or 2026-10-22 changes
nothing. scripts/StoryDates.ps1 and hydration/documents/story-dates.cjs apply the same rules, and
scripts/Test-StoryDates.ps1 checks that the three agree.
"""

from __future__ import annotations

import datetime as dt
import os
import re

ORIGINAL_MEETING = dt.date(2026, 10, 22)
FULL = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
        "November", "December"]
ABBR = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
SEASON_MID = {"winter": (1, 15), "spring": (4, 15), "summer": (7, 15), "fall": (10, 15), "autumn": (10, 15)}
YEAR = "202[5-7]"
MONTHS = "|".join(FULL + ["Sept"] + [a for a in ABBR if a != "May"])
SEASONS = "spring|summer|fall|autumn|winter"
PATTERN = re.compile(
    rf"(?<![\d-])(?P<iy>{YEAR})-(?P<im>\d\d)-(?P<id>\d\d)(?!\d)"
    rf"|\b(?P<mn>{MONTHS})(?P<dot>\.?) (?P<d>\d{{1,2}})(?:, (?P<y>{YEAR}))?\b"
    rf"|\b(?P<mmn>{MONTHS}) (?P<my>{YEAR})\b"
    rf"|\b(?P<pre>since|from|until|through|before|after|in|by) (?P<bm>{'|'.join(FULL)})\b(?![ .]\d)"
    r"|\b(?P<sn>[Ss]pring|[Ss]ummer|[Ff]all|[Aa]utumn|[Ww]inter) "
    rf"(?P<sy>{YEAR})\b"
    rf"|\b(?P<bp>than|before|until|by) (?P<bs>{SEASONS})\b(?! \d)"
    r"|\bQ(?P<q>[1-4])\b"
    rf"|(?<![\d-])\b(?P<year>{YEAR})\b(?![-\d])"
)


def meeting_date(value: str | None = None) -> dt.date:
    """The story's client meeting: the argument, else STORY_MEETING_DATE, else 2026-10-22. Must be a Thursday."""
    date = dt.date.fromisoformat((value or os.environ.get("STORY_MEETING_DATE") or "").strip() or
                                 ORIGINAL_MEETING.isoformat())
    if date.weekday() != 3:
        raise ValueError(f"STORY_MEETING_DATE must be a Thursday; {date} is a {date:%A}.")
    return date


def shift_days(value: str | None = None) -> int:
    return (meeting_date(value) - ORIGINAL_MEETING).days


def _month_index(name: str) -> int:
    name = "Sep" if name == "Sept" else name
    return (FULL.index(name) if name in FULL else ABBR.index(name)) + 1


def _month_name(index: int, like: str) -> str:
    if like in FULL:
        return FULL[index - 1]
    return "Sept" if like == "Sept" and index == 9 else ABBR[index - 1]


def _nearest(month: int, day: int) -> dt.date:
    """The 2025-2027 date for a month and day without a year that is closest to the original meeting."""
    best = None
    for year in (2025, 2026, 2027):
        try:
            candidate = dt.date(year, month, day)
        except ValueError:
            continue
        if best is None or abs((candidate - ORIGINAL_MEETING).days) < abs((best - ORIGINAL_MEETING).days):
            best = candidate
    if best is None:
        raise ValueError(f"{month}/{day} is not a date in 2025-2027")
    return best


def _season(date: dt.date, like: str) -> str:
    name = ["winter", "winter", "spring", "spring", "spring", "summer", "summer", "summer", "fall", "fall", "fall",
            "winter"][date.month - 1]
    if name == "fall" and like.lower() == "autumn":
        name = "autumn"
    return name.capitalize() if like[:1].isupper() else name


def _next_season(name: str) -> dt.date:
    month, day = SEASON_MID[name.lower()]
    date = dt.date(ORIGINAL_MEETING.year, month, day)
    return date if date > ORIGINAL_MEETING else dt.date(ORIGINAL_MEETING.year + 1, month, day)


def shift_text(text: str, days: int | None = None) -> str:
    """Moves every story date in text by days (default: from STORY_MEETING_DATE). No-op when days is 0."""
    days = shift_days() if days is None else days
    if not days or not text:
        return text
    delta = dt.timedelta(days=days)
    year_delta = (ORIGINAL_MEETING + delta).year - ORIGINAL_MEETING.year

    def replace(match: re.Match) -> str:
        g = match.groupdict()
        try:
            if g["iy"]:
                return (dt.date(int(g["iy"]), int(g["im"]), int(g["id"])) + delta).isoformat()
            if g["mn"]:
                month, day = _month_index(g["mn"]), int(g["d"])
                moved = (dt.date(int(g["y"]), month, day) if g["y"] else _nearest(month, day)) + delta
                out = f"{_month_name(moved.month, g['mn'])}{g['dot']} {moved.day}"
                return f"{out}, {moved.year}" if g["y"] else out
            if g["mmn"]:
                moved = dt.date(int(g["my"]), _month_index(g["mmn"]), 15) + delta
                return f"{_month_name(moved.month, g['mmn'])} {moved.year}"
            if g["bm"]:
                moved = _nearest(_month_index(g["bm"]), 1) + delta
                return f"{g['pre']} {FULL[moved.month - 1]}"
            if g["sn"]:
                month, day = SEASON_MID[g["sn"].lower()]
                moved = dt.date(int(g["sy"]), month, day) + delta
                return f"{_season(moved, g['sn'])} {moved.year}"
            if g["bs"]:
                return f"{g['bp']} {_season(_next_season(g['bs']) + delta, g['bs'])}"
            if g["q"]:
                moved = dt.date(ORIGINAL_MEETING.year, 3 * int(g["q"]) - 2, 16) + delta
                return f"Q{(moved.month - 1) // 3 + 1}"
            return str(int(g["year"]) + year_delta)
        except ValueError:
            return match.group(0)

    return PATTERN.sub(replace, text)


if __name__ == "__main__":
    import sys

    # Usage: python story_dates.py <days|-> < text   (prints the shifted text; "-" reads STORY_MEETING_DATE)
    arg = sys.argv[1] if len(sys.argv) > 1 else "-"
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stdout.write(shift_text(sys.stdin.read(), None if arg == "-" else int(arg)))
