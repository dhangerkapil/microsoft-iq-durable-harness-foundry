"""Tool-call arguments as one JSON document, as hosted-agent streams should carry them."""

from __future__ import annotations

import json


def call_arguments(text: str) -> str:
    """The tool call's arguments as a single JSON document.

    The preview Foundry hosting adapter (agent-framework-foundry-hosting 1.0.0b261015) streams a call's
    arguments and then sends them once more whole, so streams and stored responses carry '{...}{...}'.
    The tool itself runs with the right arguments; this keeps the one complete copy. Anything else that is
    not JSON is returned unchanged, so callers still reject it.
    """
    if not isinstance(text, str):
        return text
    try:
        json.loads(text)
        return text
    except json.JSONDecodeError:
        pass
    # The streamed copy (whole or partial) followed by the whole arguments.
    for index in range(1, len(text)):
        if text[index] in "{[" and text.startswith(text[:index], index):
            try:
                json.loads(text[index:])
            except json.JSONDecodeError:
                continue
            return text[index:]
    # The same document twice with different whitespace.
    try:
        value, end = json.JSONDecoder().raw_decode(text)
        if json.loads(text[end:]) == value:
            return text[:end]
    except json.JSONDecodeError:
        pass
    return text
