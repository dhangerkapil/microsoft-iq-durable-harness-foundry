"""The review-packs wire format between the app and the hosted agent (workflow W3): a command in, the run's view out.

A step is a user message "review-packs: {json}" ({"op": "start" | "answer" | "next" | "view", ...}). The agent answers
with zero or more progress lines, then the run's view on its own last line: "review-packs-view: {json}".
"""

from __future__ import annotations

import json

COMMAND = "review-packs:"
VIEW = "review-packs-view:"


def command_text(command: dict) -> str:
    return COMMAND + json.dumps(command, ensure_ascii=False)


def view_text(view: dict) -> str:
    return VIEW + json.dumps(view, ensure_ascii=False)


def view_in(text: str) -> dict | None:
    """The step's result: the last line of the answer that carries the view (progress lines come before it)."""
    for line in reversed((text or "").splitlines()):
        if line.startswith(VIEW):
            try:
                value = json.loads(line[len(VIEW):])
            except json.JSONDecodeError:
                return None
            return value if isinstance(value, dict) else None
    return None
