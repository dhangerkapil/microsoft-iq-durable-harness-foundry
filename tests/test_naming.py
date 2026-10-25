import os
import subprocess
import sys

import pytest

from advisor_team import naming


def test_default_prefix_is_team(monkeypatch):
    monkeypatch.delenv("ADVISOR_STORE_PREFIX", raising=False)
    assert naming.store_name("client-memory") == "team-client-memory"
    assert naming.store_name("conversations") == "team-conversations"


def test_prefix_separates_a_second_agent_in_the_same_project(monkeypatch):
    monkeypatch.setenv("ADVISOR_STORE_PREFIX", "investment-advisor")
    assert naming.store_name("durable-runs") == "investment-advisor-durable-runs"


@pytest.mark.parametrize("prefix", ["Advisor", "a/b", "-x", "x" * 40])
def test_invalid_prefixes_are_rejected(monkeypatch, prefix):
    monkeypatch.setenv("ADVISOR_STORE_PREFIX", prefix)
    with pytest.raises(ValueError):
        naming.store_name("preferences")


def test_store_constants_follow_the_prefix_at_import():
    code = ("import advisor_team.advisor as a, advisor_team.durable as d, advisor_team.history as h; "
            "print(a.MEMORY_STORE_NAME, a.PREFERENCES_STORE_NAME, d.DURABLE_STORE_NAME, h.HISTORY_STORE_NAME)")
    env = {**os.environ, "ADVISOR_STORE_PREFIX": "candidate"}
    names = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, check=True)
    assert names.stdout.split() == [
        "candidate-client-memory", "candidate-preferences", "candidate-durable-runs", "candidate-conversations"]
