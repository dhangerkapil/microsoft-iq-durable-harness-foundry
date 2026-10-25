"""Recomputes Michael Scott's synthetic holdings and NVDA weight history from the Fabric notebook.

The portfolio overview PDF must match the lakehouse exactly, so this runs the notebook's pure-Python
data section (everything before the Spark schema) and writes the figures the PDF needs.
Usage: python portfolio_series.py <portfolio_data.py> <output.json>
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from story_dates import shift_days  # noqa: E402 - hydration/story_dates.py


def main(notebook: str, output: str) -> None:
    with open(notebook, encoding="utf-8") as handle:
        source = handle.read()
    code = source[:source.index("S, D, DT, B, I = T.StringType()")]
    code = code.replace("from pyspark.sql import types as T", "T = None")
    code = code.replace('"__STORY_SHIFT_DAYS__"', f'"{shift_days()}"')
    scope: dict = {}
    exec(compile(code, notebook, "exec"), scope)  # noqa: S102 - trusted notebook from this repository
    snap, days, sec, discussion = scope["SNAP"], scope["DAYS"], scope["SEC"], scope["DISCUSSION"]

    series = []
    for day in days:
        if day > discussion:
            continue
        holdings = snap[("C0007", day)]
        it = sum(w for t, (_, _, w) in holdings.items() if sec[t][2] == "Information Technology" and sec[t][3] == "Equity")
        series.append({"date": str(day), "nvda_pct": round(holdings["NVDA"][2], 2), "it_pct": round(it, 2)})

    holdings = sorted(
        ({"ticker": t, "name": sec[t][1], "sector": sec[t][2], "asset_class": sec[t][3],
          "market_value": round(v, 2), "weight_pct": round(w, 2)}
         for t, (_, v, w) in snap[("C0007", discussion)].items()),
        key=lambda h: -h["weight_pct"],
    )
    with open(output, "w", encoding="utf-8") as handle:
        json.dump({"discussion_date": str(discussion), "series": series, "holdings": holdings,
                   "total_value": round(sum(h["market_value"] for h in holdings), 2)}, handle, indent=1)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
