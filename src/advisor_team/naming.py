"""Names of the durable state stores this harness writes."""

from __future__ import annotations

import os
import re

_PREFIX = re.compile(r"^[a-z0-9][a-z0-9-]{0,30}$")


def store_name(suffix: str) -> str:
    """A durable state store name: ADVISOR_STORE_PREFIX (default "team") plus the store's suffix.

    A different prefix keeps a second agent in the same Foundry project, such as a candidate under evaluation, from
    sharing the advisor's memory, preferences, history and checkpoints with the live agent.
    """
    prefix = os.environ.get("ADVISOR_STORE_PREFIX", "").strip() or "team"
    if not _PREFIX.match(prefix):
        raise ValueError("ADVISOR_STORE_PREFIX must be lowercase letters, digits and hyphens.")
    return f"{prefix}-{suffix}"
