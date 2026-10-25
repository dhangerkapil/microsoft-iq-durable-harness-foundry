import json
from pathlib import Path

import pytest

from advisor_team import agent_config
from advisor_team.agent_config import baseline_dir, load_agent_settings

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def fresh_settings(monkeypatch):
    for name in ("OPTIMIZATION_CONFIG", "OPTIMIZATION_CANDIDATE_ID", "OPTIMIZATION_RESOLVE_ENDPOINT",
                 "OPTIMIZATION_LOCAL_DIR"):
        monkeypatch.delenv(name, raising=False)
    agent_config._cache.clear()
    yield
    agent_config._cache.clear()


def test_baseline_is_the_repository_agent_config():
    assert baseline_dir() == ROOT / ".agent_configs" / "solo" / "baseline"
    settings = load_agent_settings("my-deployment")
    assert settings.source.startswith("local:")
    assert settings.instructions.startswith("You are the research assistant of an investment advisor")
    assert "workiq___retrieve" in settings.instructions
    assert settings.skills_dir == (baseline_dir() / "skills").resolve()
    assert (settings.skills_dir / "client-meeting-prep" / "SKILL.md").is_file()
    # The deployment's model wins over the baseline's metadata.
    assert settings.model == "my-deployment"


def test_baseline_model_comes_from_metadata_without_a_deployment():
    assert load_agent_settings(None).model == "gpt-5.6-luna"


def test_every_baseline_skill_has_frontmatter_the_optimizer_can_read():
    from azure.ai.agentserver.optimization import load_skills_from_dir

    skills = load_skills_from_dir(baseline_dir() / "skills")
    folders = sorted(path.name for path in (baseline_dir() / "skills").iterdir() if path.is_dir())
    assert sorted(skill.name for skill in skills) == folders
    assert all(skill.description and skill.body for skill in skills)


def test_inline_candidate_supplies_model_instructions_and_skills(monkeypatch):
    candidate = {
        "instructions": "Candidate instructions.",
        "model": "gpt-6-sol",
        "skills": [{"name": "client-meeting-prep", "description": "Prepare a brief.", "body": "1. Do it."}],
    }
    monkeypatch.setenv("OPTIMIZATION_CONFIG", json.dumps(candidate))
    settings = load_agent_settings("gpt-5.6-luna")
    assert settings.source == "env:OPTIMIZATION_CONFIG"
    assert settings.instructions == "Candidate instructions."
    # A candidate under evaluation runs on its own model, so model selection can compare deployments.
    assert settings.model == "gpt-6-sol"
    text = (settings.skills_dir / "client-meeting-prep" / "SKILL.md").read_text(encoding="utf-8")
    assert text.startswith("---\nname: client-meeting-prep\ndescription: Prepare a brief.\n---")
    assert text.rstrip().endswith("1. Do it.")


def test_inline_candidate_without_skills_keeps_the_baseline_skills(monkeypatch):
    monkeypatch.setenv("OPTIMIZATION_CONFIG", json.dumps({"instructions": "Only instructions changed."}))
    settings = load_agent_settings("gpt-5.6-luna")
    assert settings.model == "gpt-5.6-luna"
    assert settings.skills_dir == baseline_dir() / "skills"


def test_candidate_skill_names_cannot_escape_the_skills_folder(monkeypatch):
    candidate = {"instructions": "x", "skills": [{"name": "../evil", "description": "d", "body": "b"}]}
    monkeypatch.setenv("OPTIMIZATION_CONFIG", json.dumps(candidate))
    with pytest.raises(ValueError, match="invalid skill name"):
        load_agent_settings("gpt-5.6-luna")


def test_settings_are_resolved_once_per_source(monkeypatch):
    first = load_agent_settings("gpt-5.6-luna")
    assert load_agent_settings("gpt-5.6-luna") is first
    monkeypatch.setenv("OPTIMIZATION_CONFIG", json.dumps({"instructions": "Different."}))
    assert load_agent_settings("gpt-5.6-luna") is not first


def test_missing_configuration_fails_clearly(monkeypatch, tmp_path):
    monkeypatch.setenv("OPTIMIZATION_LOCAL_DIR", str(tmp_path))
    with pytest.raises(RuntimeError, match="No agent configuration"):
        load_agent_settings("gpt-5.6-luna")
